import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { IconChevron } from './icons';
import '../styles/think-progress.css';

/**
 * 流式期间的「思考进度」一行。
 *
 * 用户诉求：长时间生成时不知道是卡住了还是在工作 —— 所以这一行始终给出
 * 「状态词 · 用时 · 字数 · 最后一行预览 · 静默提示」，且默认收起只占一行，
 * 不挤压对话框；点右侧按钮才展开思考全文（展开体自身 max-height 滚动）。
 *
 * 只在本轮流式期间由 MessageBubble 渲染；流式结束后由 MessageBubble 恢复
 * 官方的持久 <details>💭 思考过程</details> 块（本组件不参与）。
 */
export interface ThinkStep {
  id: string;
  name: string;
  status: string;          // running | ok | failed | ...
  dur_ms?: number | null;
  title?: string;          // 这一步在干什么（模型自己写的一句话，如「清理嵌套进程」）
}

export interface ThinkProgressProps {
  thinking?: string;        // 本轮累计思考文本（可能被上游截断，仅用于预览/展开）
  progress?: string;        // 本轮累计的实时输出文本（SSE progress 事件，可能被截断）
  activity?: string;        // 工具/执行活动（换行分隔）
  stillWorking?: string;    // 防呆心跳提示（未卡住）：次级样式附着显示
  startedAt?: number;       // 本轮开始的 epoch ms；缺省回退组件首次挂载时间
  thinkingTotal?: number;   // 本轮累计思考字符数（不受截断影响）；<=0 时用实时输出长度兜底
  lastChunkAt?: number;     // 最近一次收到 token/thinking/activity 的 epoch ms
  steps?: ThinkStep[];      // 工具调用步骤流水（SSE steps 事件）：真正「做到第几步」的来源
}

/** 静默多久开始提示「可能卡住」（毫秒）。 */
const QUIET_MS = 8000;
/** 用时刷新间隔（毫秒）。 */
const TICK_MS = 250;
/** 判定「贴着底部」的容差，用于自动跟随尾部。 */
const FOLLOW_SLOP = 8;

/** 防御性取字符串：字段缺失/类型异常都不能让气泡白屏。 */
function asText(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

/** 防御性取有限数值。 */
function asNum(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** 用时：<60s 显示 "12.3s"，>=60s 显示 "1:23"。 */
function fmtElapsed(ms: number): string {
  const total = (Number.isFinite(ms) ? Math.max(0, ms) : 0) / 1000;
  if (total < 60) return `${total.toFixed(1)}s`;
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** 字数：千分位。 */
function fmtCount(n: number): string {
  const v = Number.isFinite(n) ? Math.max(0, Math.round(n)) : 0;
  try { return v.toLocaleString('en-US'); } catch { return String(v); }
}

/** 去掉行内 markdown 标记（**、`、标题/列表前缀等）并压平空白。 */
function cleanInline(line: string): string {
  return line
    .replace(/`{1,3}/g, '')
    .replace(/\*{1,3}/g, '')
    .replace(/^\s*(#{1,6}\s+|[-*+>]\s+|\d+[.)]\s+)/, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/** 单行预览的最大字数（超出从**尾部**截取，保证看到的是最新吐出的那段）。 */
const PREVIEW_MAX = 160;
/** 一句话之后多久没新字，就认为这句已经说完（毫秒）。 */
const SENTENCE_GAP_MS = 200;
/** 单句上限：超过这么多字还没等到句末标点，也把预览从尾部截断。 */
const SENTENCE_MAX = 120;

/** 取「当前正在生成的那一句」。
 *
 *  为什么不能直接取最后一个非空行：模型输出是**流式**的，`progress` 每来一个字
 *  变化一次，整段回答往往不含换行（实测 172 字里一个新段落才一个 \n）。于是
 *  `lastLinePreview` 会把「到目前为止写过的全部正文」都当成预览塞进那一行，
 *  结果进度行越写越长、把对话框顶开 —— 正好违反「不挤压对话框」这条硬要求。
 *
 *  这里的做法：先按换行取最后一个非空行，再在这一行里找**最后一个句末标点**
 *  （。！？；.!?;），从它后面开始算「当前句」。找不到句末标点（句子还没写完）就
 *  回退整行。两种情况都再从尾部截到 PREVIEW_MAX，保证长度恒定、显示最新内容。
 */
function lastSentencePreview(text: string): string {
  if (!text) return '';
  const lines = text.split('\n');
  let line = '';
  for (let i = lines.length - 1; i >= 0; i--) {
    const cleaned = cleanInline(lines[i]);
    if (cleaned) { line = cleaned; break; }
  }
  if (!line) line = cleanInline(text);
  if (!line) return '';

  const cut = Math.max(
    line.lastIndexOf('。'), line.lastIndexOf('！'), line.lastIndexOf('？'),
    line.lastIndexOf('；'), line.lastIndexOf('.'), line.lastIndexOf('!'),
    line.lastIndexOf('?'), line.lastIndexOf(';'),
  );
  // cut 指向标点本身：取它**后面**那截（当前句）；没有标点则整行就是当前句。
  let cur = cut >= 0 ? line.slice(cut + 1).trim() : line;
  if (!cur) return line.length > PREVIEW_MAX ? line.slice(-PREVIEW_MAX) : line;
  if (cur.length > SENTENCE_MAX) cur = cur.slice(-SENTENCE_MAX);
  if (cur.length > PREVIEW_MAX) cur = cur.slice(-PREVIEW_MAX);
  return cur;
}

/** 把新到的增量并进累计文本（上限 4000 字，与 App.tsx 的 progress 窗口一致）。 */
export function mergeProgress(prev: string, add: string): { text: string; changed: boolean } {
  if (!add) return { text: prev, changed: false };
  const next = (prev + add).slice(-4000);
  return { text: next, changed: next !== prev };
}

/** activity 步骤列表（换行分隔，去空行）。 */
function toSteps(activity: string): string[] {
  if (!activity) return [];
  return activity.split('\n').map((s) => s.trim()).filter(Boolean);
}

/** 工具名的人话说法：用户不该看到裸的 exec/process。 */
const TOOL_LABEL: Record<string, string> = {
  exec: '执行命令',
  process: '后台进程',
  read: '读文件',
  write: '写文件',
  edit: '改文件',
  glob: '找文件',
  grep: '搜内容',
  web_search: '联网搜索',
  web_fetch: '抓网页',
  browser: '浏览器操作',
  image: '生成图片',
  skill: '调用技能',
  todo_write: '更新待办',
  progress_card: '汇报进度',
  sessions_search: '检索历史会话',
  sessions_history: '读历史会话',
  ask_user: '向你确认',
  terminal: '开终端',
  task: '子任务',
};

/** 一步的耗时：<1s 用毫秒，否则秒，超过一分钟用 m:ss。 */
function fmtDur(ms: number | null | undefined): string {
  const v = asNum(ms);
  if (v === null || v < 0) return '';
  if (v < 1000) return `${Math.round(v)}ms`;
  if (v < 60000) return `${(v / 1000).toFixed(v < 10000 ? 1 : 0)}s`;
  const m = Math.floor(v / 60000);
  const s = Math.floor((v % 60000) / 1000);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** 标题是不是「路径/命令」这类不含人话内容的兜底值（模型没给 title 时后端会拿 path 顶上）。 */
function looksLikePathOrCommand(title: string): boolean {
  const t = title.trim();
  if (!t) return false;
  if (/^[A-Za-z]:[\\/]/.test(t)) return true;              // C:\xxx
  if (t.startsWith('\\\\')) return true;                    // \\server\share
  if (/^[\\/~.]*[\w.-]+([\\/][\w.-]+){2,}/.test(t)) return true; // a/b/c 深层相对路径
  if (/^[\w.-]+\.(tsx?|jsx?|py|json|md|css|html?|sql|ya?ml|toml)$/i.test(t)) return true; // 裸文件名
  return false;
}

/** 一步的显示名：模型写的 title → 人话工具名 → 工具原名；路径/命令一律不展示。 */
function stepLabel(s: ThinkStep): string {
  const title = (s.title || '').trim();
  if (title && !looksLikePathOrCommand(title)) return title;
  return TOOL_LABEL[s.name] || s.name;
}

/** 把步骤流水归一化（后端字段可能缺、可能不认识的状态一律降级为中性）。 */
function normalizeSteps(raw: unknown): ThinkStep[] {
  if (!Array.isArray(raw)) return [];
  const out: ThinkStep[] = [];
  for (const it of raw) {
    if (!it || typeof it !== 'object') continue;
    const rec = it as Record<string, unknown>;
    const name = typeof rec.name === 'string' && rec.name ? rec.name : 'tool';
    const id = typeof rec.id === 'string' && rec.id ? rec.id : `${name}-${out.length}`;
    const st = typeof rec.status === 'string' && rec.status ? rec.status : 'running';
    out.push({ id, name, status: st, dur_ms: asNum(rec.dur_ms), title: typeof rec.title === 'string' ? rec.title : '' });
  }
  return out;
}

/** 「4 次执行命令 · 2 次读文件」：把冗长的工具流水压成一眼能读的归类统计。 */
function stepSummary(list: ThinkStep[]): string {
  if (!list.length) return '';
  const counts = new Map<string, number>();
  for (const s of list) {
    const label = TOOL_LABEL[s.name] || s.name;
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([label, n]) => `${n} 次${label}`)
    .join(' · ');
}

/** 一句话之后多久没新字，就认为这句已经说完（毫秒）。导出供调用方判断。 */
export const THINK_SENTENCE_GAP_MS = SENTENCE_GAP_MS;

export default function ThinkProgress({
  thinking, progress, activity, stillWorking, startedAt, thinkingTotal, lastChunkAt, steps,
}: ThinkProgressProps) {
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const bodyId = useId();
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);
  const mountRef = useRef<number | null>(null);
  if (mountRef.current === null) mountRef.current = Date.now();

  const text = asText(thinking);
  const prog = asText(progress);
  // 文本源：thinking 优先，为空时用实时输出（progress）；预览与展开体都用它。
  const source = text || prog;
  const actText = asText(activity);
  const hint = asText(stillWorking);
  const rows = useMemo(() => toSteps(actText), [actText]);
  const stepList = useMemo(() => normalizeSteps(steps), [steps]);

  // 用时/静默提示：250ms tick（组件只在流式中存在，卸载即清理）。
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(t);
  }, []);

  // 展开体默认跟随尾部；用户往上滚则停止跟随，滚回底部恢复。
  useEffect(() => {
    if (!expanded) { followRef.current = true; return; }
    const el = bodyRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [expanded, text, prog, actText, stepList]);

  const onBodyScroll = () => {
    const el = bodyRef.current;
    if (!el) return;
    followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= FOLLOW_SLOP;
  };

  const started = asNum(startedAt);
  const start = started !== null && started > 0 ? started : (mountRef.current as number);
  const elapsed = Math.max(0, now - start);

  // 字数：优先本轮累计计数 thinkingTotal（不受截断影响）；本机 thinkingTotal 常为 0，
  // 此时用实时输出长度兜底；确实没有可计内容就**不显示**（旧版一直显示「0 字」，是噪音）。
  const total = asNum(thinkingTotal);
  const words = total !== null && total > 0
    ? total
    : (prog ? prog.length : (total === null ? text.length : 0));

  const lastChunk = asNum(lastChunkAt);
  const quietMs = lastChunk !== null ? now - lastChunk : -1;
  const quiet = quietMs >= QUIET_MS;

  // 状态词优先级：思考 → 实时输出(progress) → 工具步骤(steps) → 活动行(activity) → 等待模型输出
  const runningStep = [...stepList].reverse().find((s) => s.status === 'running');
  const status = text ? '思考中'
    : prog ? '实时输出'
      : runningStep ? `正在${TOOL_LABEL[runningStep.name] || runningStep.name}`
        : stepList.length > 0 ? '已执行'
          : actText ? '工作中' : '等待模型输出…';
  // 预览 = 当前正在生成的那一句（定长、只显示最新内容），不是整段正文。
  // 没有任何文本流时退回最近的工具动作，让「这一步在干什么」始终有内容可看。
  // 顺序刻意是「步骤优先」：用户在做什么探索时，最想看的是「第几步、在干什么」，
  // 而不是半句还没说完的正文；有真正的思考/输出时仍以正文为主。
  const lastStep = stepList[stepList.length - 1];
  const lastStepLabel = lastStep ? stepLabel(lastStep) : '';
  const stepPreview = lastStep
    ? `第 ${stepList.length} 步 · ${lastStepLabel}${lastStep.status === 'running' ? '（进行中）' : ''}`
    : rows[rows.length - 1] || '';
  const preview = stepPreview || (source ? lastSentencePreview(source) : '');
  // 「在做什么」的兜底：没有文本流时按工具归类给出人话统计，而不是一排裸英文名。
  const flowSummary = stepSummary(stepList);

  return (
    <div className="tp">
      <div className="tp-row">
        <span className="tp-spinner" aria-hidden="true" />
        <span className="tp-status">{status}</span>
        <span className="tp-meta">· {fmtElapsed(elapsed)}</span>
        {words > 0 && <span className="tp-meta">· {fmtCount(words)} 字</span>}
        {stepList.length > 0 && <span className="tp-meta">· {stepList.length} 步</span>}
        {!source && flowSummary && <span className="tp-meta">· {flowSummary}</span>}
        {preview && <span className="tp-preview" title={preview}>· {preview}</span>}
        {quiet && <span className="tp-quiet">· 静默 {Math.floor(quietMs / 1000)}s</span>}
        {hint && <span className="tp-still" title={hint}>{hint}</span>}
        <button
          type="button"
          className="tp-toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-controls={expanded ? bodyId : undefined}
          aria-label={expanded ? '收起思考过程' : '展开思考过程'}
          title={expanded ? '收起思考过程' : '展开思考过程'}
        >
          <IconChevron size={14} className={`tp-chev ${expanded ? 'on' : 'off'}`} />
        </button>
      </div>

      {expanded && (
        <div id={bodyId} className="tp-body" ref={bodyRef} onScroll={onBodyScroll}>
          {/* 执行流水放最上面：这是「进行到哪一步」的直接答案 */}
          {stepList.length > 0 && (
            <ol className="tp-flow">
              {stepList.map((s, i) => (
                <li
                  key={s.id}
                  className={`tp-flow-item tp-${s.status === 'running' ? 'running'
                    : s.status === 'ok' || s.status === 'succeeded' ? 'ok' : 'failed'}`}
                >
                  <span className="tp-flow-idx">{i + 1}</span>
                  <span className="tp-flow-name">{stepLabel(s)}</span>
                  {s.title && stepLabel(s) !== s.title.trim() && (
                    <span className="tp-flow-tier">{TOOL_LABEL[s.name] || s.name}</span>
                  )}
                  {s.status === 'running'
                    ? <span className="tp-flow-badge">进行中</span>
                    : s.status === 'ok' || s.status === 'succeeded'
                      ? null
                      : <span className="tp-flow-badge tp-flow-badge-bad">{s.status}</span>}
                  {fmtDur(s.dur_ms) && <span className="tp-flow-dur">{fmtDur(s.dur_ms)}</span>}
                </li>
              ))}
            </ol>
          )}
          {text ? <div className="tp-think">{text}</div> : null}
          {/* 实时输出与思考是两条通道：同时存在时都显示，完全相同则只显示一次 */}
          {prog && prog !== text ? <div className="tp-progress">{prog}</div> : null}
          {rows.length > 0 && (
            <ul className="tp-steps">
              {rows.map((s, i) => <li key={`${i}-${s.slice(0, 12)}`}>{s}</li>)}
            </ul>
          )}
          {!text && !prog && rows.length === 0 && stepList.length === 0 && (
            <div className="tp-empty">等待模型输出…</div>
          )}
        </div>
      )}
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChatMessage, ThinkStep } from '../lib/store';
import { renderMarkdown } from '../lib/sanitize';
import { linkifyOutputs, externalizeMediaLinks, rewriteLocalLinks } from '../lib/linkifyOutputs';
import { IconCopy, IconCheck, IconRetry, IconEdit } from './icons';
import { extractArtifacts } from '../lib/artifacts';
import ArtifactGallery from './ArtifactGallery';
import ThinkProgress from './ThinkProgress';
import '../styles/chat-edit.css';

export interface BubbleActions {
  onCopy: () => void;
  onRetry?: () => void;    // 仅最后一轮可用（append-only：不改写历史）
  // 保存编辑后的文本并重发：返回 true 表示已受理（退出编辑态），false 表示未受理
  //（例如父层弹确认被用户取消）——此时留在编辑态，草稿不丢。
  onEditSave?: (text: string) => boolean;
  canModify: boolean;      // 流式中禁用 retry / edit
}

interface MessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
  thinking?: string;
  progress?: string;       // 流式「实时输出」增量（SSE progress 事件，本机 thinking 为空时的唯一内容源）
  activity?: string;
  stillWorking?: string;   // 防呆心跳提示（未卡住）；仅流式时的独立提示，不替代思考/活动
  // ---- 流式「思考进度」用（由 App/store 提供，缺省即不显示对应片段）----
  startedAt?: number;      // 本轮开始的 epoch ms（切页不重置）
  thinkingTotal?: number;  // 本轮累计思考字符数（不受 thinking 截断影响）
  lastChunkAt?: number;    // 最近一次收到 token/thinking/activity 的 epoch ms
  steps?: ThinkStep[];     // 工具调用流水（SSE steps 事件）：进行到第几步
  actions?: BubbleActions;
}

function ActionBar({ actions, onEdit }: { actions: BubbleActions; onEdit?: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    actions.onCopy();
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="msg-actions">
      <button className="msg-action" onClick={copy} title="复制">
        {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}<span>{copied ? '已复制' : '复制'}</span>
      </button>
      {onEdit && actions.canModify && (
        <button className="msg-action" onClick={onEdit} title="编辑并重新发送"><IconEdit size={14} /><span>编辑</span></button>
      )}
      {actions.onRetry && actions.canModify && (
        <button className="msg-action" onClick={actions.onRetry} title="重新生成"><IconRetry size={14} /><span>重试</span></button>
      )}
    </div>
  );
}

/** textarea 随内容长高（上限 240px），与输入框观感一致。 */
function autosize(el: HTMLTextAreaElement) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 240) + 'px';
}

/**
 * 用户消息气泡。就地编辑：点「编辑」→ 气泡变 textarea → Enter 保存并重发 / Esc 取消。
 * 编辑态只存在于本组件内部，取消即丢弃草稿，绝不改动会话数据。
 */
function UserBubble({ message, actions }: { message: ChatMessage; actions?: BubbleActions }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);

  const onEditSave = actions?.onEditSave;
  const canEdit = !!onEditSave && !!actions?.canModify;

  // 流式生成中（canModify=false）不允许编辑：立刻退出编辑态，避免「保存」被后端会话锁拦下。
  useEffect(() => {
    if (!actions?.canModify) setEditing(false);
  }, [actions?.canModify]);

  // 进入编辑：预填原文后自动聚焦，并把光标放到末尾（接着改最顺手）。
  useEffect(() => {
    if (!editing) return;
    const el = taRef.current;
    if (!el) return;
    autosize(el);
    el.focus();
    const end = el.value.length;
    el.setSelectionRange(end, end);
  }, [editing]);

  const beginEdit = () => {
    if (!canEdit) return;
    setDraft(message.content);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setDraft('');
  };

  const saveEdit = () => {
    const text = draft.trim();
    if (!text || !onEditSave) return;   // 空内容不重发：留在编辑态，由用户决定改还是取消
    // 有后续消息时由父层先弹确认；被取消则留在编辑态，草稿保留。
    if (onEditSave(text)) { setEditing(false); setDraft(''); }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法组字确认候选词的回车交给输入法，不能触发保存。
    // 部分浏览器先触发 compositionend，此时仍需用 229 识别输入法按键（同 ChatPage 输入框写法）。
    if (composingRef.current || e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); return; }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(); }
  };

  if (editing) {
    return (
      <div className="message-row user">
        <div className="msg-col user">
          <div className="ce-editor">
            <textarea
              ref={taRef}
              className="ce-editor-input"
              value={draft}
              rows={1}
              onChange={(e) => { setDraft(e.target.value); autosize(e.currentTarget); }}
              onCompositionStart={() => { composingRef.current = true; }}
              onCompositionEnd={() => { composingRef.current = false; }}
              onBlur={() => { composingRef.current = false; }}
              onKeyDown={onKeyDown}
            />
            <div className="ce-editor-bar">
              <span className="ce-editor-hint">Enter 保存并重发 · Shift+Enter 换行 · Esc 取消</span>
              <span className="ce-editor-btns">
                <button className="btn btn-ghost btn-sm" onClick={cancelEdit}>取消</button>
                <button className="btn btn-primary btn-sm" onClick={saveEdit} disabled={!draft.trim()}>保存并重发</button>
              </span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="message-row user">
      <div className="msg-col user">
        <div className="message-bubble user">{message.content}</div>
        {actions && <ActionBar actions={actions} onEdit={canEdit ? beginEdit : undefined} />}
      </div>
    </div>
  );
}

export default function MessageBubble({ message, isStreaming, thinking, progress, activity, stillWorking, startedAt, thinkingTotal, lastChunkAt, steps, actions }: MessageBubbleProps) {
  const rendered = useMemo(() => {
    if (message.role === 'user') return { html: '', artifacts: [], archives: [] };
    // 助手正文里裸露的产物路径先转成前端可用链接（文件直开、图片内联、目录跳内容库）。
    // 流式中不转换：路径/围栏代码块可能被 SSE token 从中间切开（扩展名没收全会被
    // 误判成目录、未闭合的 ``` 会误伤命令示例里的 outputs/），转换只在流式结束后
    // 对完整正文做一次，避免闪烁成错误链接再跳变。
    const body = isStreaming ? message.content : linkifyOutputs(message.content);
    // rewriteLocalLinks：兜底把残留的本地绝对路径 href/src 改成站内地址，
    // 否则点开是 7860 的 {"detail":"Not Found"}（路径被当成站点相对路径）。
    const html = externalizeMediaLinks(rewriteLocalLinks(renderMarkdown(body)));
    return isStreaming ? { html, artifacts: [], archives: [] } : extractArtifacts(html);
  }, [message.content, message.role, isStreaming]);

  // ---- 用户消息 ----
  if (message.role === 'user') {
    // Attachment-only turns are intentionally invisible; the structured refs
    // remain in session state for retry but never leak paths into the chat UI.
    if (!message.content.trim()) return null;
    return <UserBubble message={message} actions={actions} />;
  }

  // ---- 助手消息 ----
  // 思考 / 活动：流式时用实时值；结束后用消息里持久化的值 —— 一直保留，不隐藏
  const effThinking = isStreaming ? (thinking || '') : (message.thinking || '');
  const liveActivity = isStreaming ? (activity || '') : '';
  const liveHint = isStreaming ? (stillWorking || '') : '';   // 防呆「未卡住」提示，附着显示、不顶掉真实状态
  const doneSteps = !isStreaming ? (message.activity || '') : '';

  // 流式期间：思考 / 活动全部收进 ThinkProgress 一行（默认收起，点按钮展开），
  // 不再渲染原来的 <details>💭 思考过程</details> 与独立 live-activity 行 ——
  // 避免同一份内容重复占位，也避免对话框被撑开。
  const progressRow = isStreaming ? (
    <ThinkProgress
      thinking={thinking}
      progress={progress}
      activity={activity}
      stillWorking={stillWorking}
      startedAt={startedAt}
      thinkingTotal={thinkingTotal}
      lastChunkAt={lastChunkAt}
      steps={steps}
    />
  ) : null;

  // 流式结束后的持久块：保持原样（外观与官方一致）
  const livePanel = !isStreaming && (doneSteps || effThinking) ? (
    <div className="live-panel">
      {doneSteps && (
        <details className="thinking-block">
          <summary>🧠 执行过程（{doneSteps.split('\n').length} 步）</summary>
          <div className="thinking-text">{doneSteps}</div>
        </details>
      )}
      {effThinking && (
        <details className="thinking-block">
          <summary>💭 思考过程</summary>
          <div className="thinking-text">{effThinking}</div>
        </details>
      )}
    </div>
  ) : null;

  // 等待回复中（还没有正文、思考、活动）：只渲染这一行（原来的打字 dots 占位已并入本行）
  if (isStreaming && !message.content && !effThinking && !liveActivity && !liveHint) {
    return (
      <div className="message-row assistant">
        <div className="msg-col assistant">
          <div className="message-bubble assistant">{progressRow}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="message-row assistant">
      <div className="msg-col assistant">
        <div className="message-bubble assistant">
          {progressRow}
          {livePanel}
          {message.content && <div dangerouslySetInnerHTML={{ __html: rendered.html }} />}
          {!isStreaming && (rendered.artifacts.length > 0 || rendered.archives.length > 0) && (
            <ArtifactGallery artifacts={rendered.artifacts} archives={rendered.archives} />
          )}
          {isStreaming && <span className="streaming-cursor" />}
        </div>
        {actions && !isStreaming && <ActionBar actions={actions} />}
      </div>
    </div>
  );
}

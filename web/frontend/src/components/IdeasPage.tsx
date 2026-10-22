import { useState, useEffect, useCallback, useMemo } from 'react';
import { fetchIdeas, createIdea, updateIdea, deleteIdea, createSchedule } from '../lib/api';
import type { Idea, IdeaInput, IdeaStats } from '../lib/api';
import { IconIdea, IconEdit, IconTrash, IconChat, IconCalendar, IconChevron } from './icons';

interface IdeasPageProps {
  onUseTopic: (title: string) => void;
}

const COLUMNS: { key: string; label: string; color: string }[] = [
  { key: 'pending', label: '待做', color: 'var(--text-tertiary)' },
  { key: 'doing', label: '进行中', color: 'var(--layer-attribute)' },
  { key: 'done', label: '已完成', color: 'var(--layer-publish)' },
];
const NEXT: Record<string, string> = { pending: 'doing', doing: 'done', done: 'pending' };
const EMPTY: IdeaInput = { title: '', note: '', source: '', status: 'pending' };

type SortKey = 'created' | 'views';
const SORTS: { key: SortKey; label: string }[] = [
  { key: 'created', label: '按时间' },
  { key: 'views', label: '按播放量' },
];

/** 外链小图标（本地内联，避免为单点需求改动 icons.tsx）。 */
const IconLinkOut = ({ size = 12 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="idea-link-icon">
    <path d="M15 3h6v6" />
    <path d="M10 14 21 3" />
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
  </svg>
);

/**
 * 互动数字格式化：16345000 → "1634.5万"，237000 → "23.7万"，小于 1 万返回原数。
 * null / undefined / NaN / Infinity 一律返回 null，交给调用方决定不渲染。
 */
export function formatCount(n: number | null | undefined): string | null {
  if (typeof n !== 'number' || !Number.isFinite(n)) return null;
  if (Math.abs(n) < 10000) return String(n);
  const w = (n / 10000).toFixed(1);
  return `${w.endsWith('.0') ? w.slice(0, -2) : w}万`;
}

/** 互动数据一行小字：null 的指标不显示；完全没有 stats（或全部为 null）时返回空串。 */
export function statsLine(stats: IdeaStats | null | undefined): string {
  if (!stats) return '';
  const parts: string[] = [];
  const push = (label: string, v: number | null | undefined) => {
    const t = formatCount(v);
    if (t !== null) parts.push(`${label} ${t}`);
  };
  push('播放', stats.views);
  push('点赞', stats.likes);
  push('评论', stats.comments);
  return parts.join(' · ');
}

/** 作品链接取值：缺失 / 空串 / 纯空白 / 非字符串 一律视为「没有链接」，此时不渲染 <a>。 */
export function ideaUrl(it: Idea): string {
  return typeof it.url === 'string' ? it.url.trim() : '';
}

/** 播放量取值：非数字 / null 一律视作「没有播放量」。 */
function viewCount(it: Idea): number | null {
  const v = it.stats?.views;
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * 组内排序（不打乱 pending / doing / done 分组结构）：
 * - created：created 降序，保持现有行为
 * - views：stats.views 降序；没有播放量的条目排到最后，且保持其组内原有顺序（用原下标做兜底比较）
 */
export function sortIdeas(list: Idea[], key: SortKey): Idea[] {
  const indexed = list.map((it, i) => ({ it, i, v: key === 'views' ? viewCount(it) : null }));
  if (key === 'created') {
    return indexed.sort((a, b) => b.it.created - a.it.created || a.i - b.i).map((x) => x.it);
  }
  return indexed
    .sort((a, b) => {
      if (a.v === null && b.v === null) return a.i - b.i;
      if (a.v === null) return 1;
      if (b.v === null) return -1;
      return b.v - a.v || a.i - b.i;
    })
    .map((x) => x.it);
}

export default function IdeasPage({ onUseTopic }: IdeasPageProps) {
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [form, setForm] = useState<IdeaInput | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [toast, setToast] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('created');

  const load = useCallback(() => { fetchIdeas().then(setIdeas).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load]);

  const showToast = (m: string) => { setToast(m); setTimeout(() => setToast(''), 2200); };
  const byStatus = useMemo<Record<string, Idea[]>>(() => {
    const g: Record<string, Idea[]> = { pending: [], doing: [], done: [] };
    for (const it of ideas) (g[it.status] || g.pending).push(it);
    return {
      pending: sortIdeas(g.pending, sortKey),
      doing: sortIdeas(g.doing, sortKey),
      done: sortIdeas(g.done, sortKey),
    };
  }, [ideas, sortKey]);

  const openNew = () => { setEditId(null); setForm({ ...EMPTY }); };
  const openEdit = (it: Idea) => {
    // url / stats 原样带回表单，保存时不被抹掉
    setEditId(it.id);
    setForm({ title: it.title, note: it.note, source: it.source, status: it.status, url: it.url, stats: it.stats });
  };
  const save = async () => {
    if (!form || !form.title.trim()) return;
    if (editId) await updateIdea(editId, form); else await createIdea(form);
    setForm(null); setEditId(null); load();
  };
  const advance = async (it: Idea) => { await updateIdea(it.id, { ...it, status: NEXT[it.status] }); load(); };
  const remove = async (it: Idea) => { await deleteIdea(it.id); load(); };
  const schedule = async (it: Idea) => {
    const d = new Date();
    await createSchedule({ title: it.title, date: d.toISOString().slice(0, 10), platform: '', time: '', status: 'idea', note: it.note });
    showToast('已加入日历（今天）');
  };

  return (
    <div className="page-scroll ideas-page">
      <div className="page-head">
        <div>
          <h1 className="page-title"><IconIdea size={21} /> 选题库</h1>
          <p className="page-subtitle">攒住每一个灵感——从热点收藏或手动新增，推进到「做内容」再进日历。</p>
        </div>
        <button className="btn btn-sm btn-primary" onClick={openNew}>+ 新建选题</button>
      </div>

      <div className="idea-sort">
        <span className="idea-sort-label">排序</span>
        {SORTS.map((s) => (
          <button key={s.key} className={`chip ${sortKey === s.key ? 'active' : ''}`}
            onClick={() => setSortKey(s.key)}>{s.label}</button>
        ))}
      </div>

      <div className="kanban">
        {COLUMNS.map((col) => (
          <div key={col.key} className="kanban-col">
            <div className="kanban-col-head">
              <span className="kanban-dot" style={{ background: col.color }} />
              {col.label}<span className="kanban-count">{byStatus[col.key].length}</span>
            </div>
            <div className="kanban-list">
              {byStatus[col.key].length === 0 && <div className="kanban-empty">拖点选题进来吧</div>}
              {byStatus[col.key].map((it) => {
                const url = ideaUrl(it);
                const line = statsLine(it.stats);
                return (
                  <div key={it.id} className="card idea-card">
                    <div className="idea-card-actions">
                      <button className="session-act" title="编辑" onClick={() => openEdit(it)}><IconEdit size={13} /></button>
                      <button className="session-act danger" title="删除" onClick={() => remove(it)}><IconTrash size={13} /></button>
                    </div>
                    {url ? (
                      <a className="idea-title idea-title-link" href={url} target="_blank" rel="noreferrer" title={url}>
                        {it.title}<IconLinkOut size={12} />
                      </a>
                    ) : (
                      <div className="idea-title">{it.title}</div>
                    )}
                    {it.source && <span className="badge" style={{ marginTop: 6 }}>{it.source}</span>}
                    {it.note && <div className="idea-note">{it.note}</div>}
                    {line && <div className="idea-stats">{line}</div>}
                    <div className="idea-foot">
                      <button className="idea-act" onClick={() => onUseTopic(it.title)}><IconChat size={13} /> 做内容</button>
                      <button className="idea-act" onClick={() => schedule(it)}><IconCalendar size={13} /> 排期</button>
                      <button className="idea-act next" onClick={() => advance(it)} title="推进状态">
                        {COLUMNS.find((c) => c.key === NEXT[it.status])?.label} <IconChevron size={12} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {form && (
        <div className="overlay" onClick={() => setForm(null)}>
          <div className="modal" style={{ width: 440, maxWidth: '100%' }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <h3 style={{ margin: 0 }}>{editId ? '编辑选题' : '新建选题'}</h3>
              <button className="icon-btn" onClick={() => setForm(null)}>×</button>
            </div>
            <label className="field-label">选题 *</label>
            <input className="field" value={form.title} autoFocus placeholder="想做的内容 / 角度"
              onChange={(e) => setForm({ ...form, title: e.target.value })} />
            <label className="field-label">备注 / 角度</label>
            <textarea className="field" style={{ minHeight: 70 }} value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })} />
            <label className="field-label">来源</label>
            <input className="field" value={form.source} placeholder="如：微博热搜 / 灵感"
              onChange={(e) => setForm({ ...form, source: e.target.value })} />
            <label className="field-label">状态</label>
            <div style={{ display: 'flex', gap: 7 }}>
              {COLUMNS.map((c) => (
                <button key={c.key} className={`chip ${form.status === c.key ? 'active' : ''}`}
                  onClick={() => setForm({ ...form, status: c.key })}>{c.label}</button>
              ))}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
              <button className="btn btn-sm" onClick={() => setForm(null)}>取消</button>
              <button className="btn btn-sm btn-primary" onClick={save} disabled={!form.title.trim()}>保存</button>
            </div>
          </div>
        </div>
      )}

      {toast && <div className="toast ok"><span className="toast-icon">✓</span>{toast}</div>}
    </div>
  );
}

import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchTrends, createIdea } from '../lib/api';
import type { TrendGroup, TrendMode } from '../lib/api';
import { IconFire, IconRefresh, IconBookmark, IconCheck } from './icons';

interface TrendsPageProps {
  onUseTopic: (title: string) => void;   // 一键做成内容 → 跳 chat
}

const ALL_PLATFORMS: { key: string; label: string }[] = [
  { key: 'weibo', label: '微博' },
  { key: 'douyin', label: '抖音' },
  { key: 'zhihu', label: '知乎' },
  { key: 'bilibili', label: 'B站' },
  { key: 'baidu', label: '百度' },
  { key: 'toutiao', label: '头条' },
];

/** 模式切换：默认「我的赛道」。key 与后端 mode 参数一一对应。 */
const MODES: { key: TrendMode; label: string }[] = [
  { key: 'resonance', label: '我的赛道' },
  { key: 'all', label: '全网热搜' },
];

export default function TrendsPage({ onUseTopic }: TrendsPageProps) {
  const [mode, setMode] = useState<TrendMode>('resonance');
  const [selected, setSelected] = useState<string[]>(['weibo', 'douyin', 'zhihu']);
  const [groups, setGroups] = useState<TrendGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [updated, setUpdated] = useState(0);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  // 快速切模式/平台时丢弃过期响应，避免旧结果覆盖新结果
  const reqId = useRef(0);

  const resonance = mode === 'resonance';

  const save = async (title: string, source: string) => {
    if (saved.has(title)) return;
    try {
      await createIdea({ title, source: `${source}热搜`, status: 'pending' });
      setSaved((prev) => new Set(prev).add(title));
    } catch { /* ignore */ }
  };

  const load = useCallback((pfs: string[], m: TrendMode) => {
    const id = ++reqId.current;
    if (pfs.length === 0) { setGroups([]); setUpdated(0); setError(''); setLoading(false); return; }
    setLoading(true);
    setError('');
    fetchTrends(pfs.join(','), 15, m)
      .then((d) => {
        if (id !== reqId.current) return;
        setGroups(d.trends);
        setUpdated(d.updated);
      })
      .catch(() => {
        if (id !== reqId.current) return;
        setError(m === 'resonance'
          ? '我的赛道拉取失败——请确认已配置外网代理（EASEL_PROXY）。'
          : '热点拉取失败——请确认已配置外网代理（EASEL_PROXY）。');
      })
      .finally(() => { if (id === reqId.current) setLoading(false); });
  }, []);

  useEffect(() => { load(selected, mode); }, [load, selected, mode]);

  const toggle = (k: string) =>
    setSelected((prev) => prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]);

  const noPlatform = selected.length === 0;
  const noResult = !loading && !error && !noPlatform && groups.length === 0;

  return (
    <div className="page-scroll trends-page">
      <div className="page-head">
        <div>
          <h1 className="page-title"><IconFire size={22} /> 热点雷达</h1>
          <p className="page-subtitle">
            {resonance
              ? '按你的画像从各平台捞出最能共鸣的热点，挑值得蹭的选题，一键交给 AI 做成你的内容。'
              : '多平台实时热搜，挑值得蹭的选题，一键交给 AI 做成你的内容。'}
            {updated > 0 && <span style={{ color: 'var(--text-tertiary)' }}> · {new Date(updated * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 更新</span>}
          </p>
        </div>
        <button className="btn btn-sm" onClick={() => load(selected, mode)} disabled={loading}>
          <IconRefresh size={14} /> {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <div className="trend-modes">
        {MODES.map((m) => (
          <button key={m.key} className={`chip ${mode === m.key ? 'active' : ''}`}
            onClick={() => setMode(m.key)}>{m.label}</button>
        ))}
      </div>

      <div className="trend-platforms">
        {ALL_PLATFORMS.map((p) => (
          <button key={p.key} className={`chip ${selected.includes(p.key) ? 'active' : ''}`}
            onClick={() => toggle(p.key)}>{p.label}</button>
        ))}
      </div>

      {error && <div className="notice-error">{error}</div>}

      {loading && <div className="trend-empty">{resonance ? '正在按你的赛道捞热点…' : '正在拉取全网热搜…'}</div>}
      {noPlatform && !loading && <div className="trend-empty">请至少选择一个平台。</div>}
      {noResult && <div className="trend-empty">{resonance ? '这次没捞到与你赛道共鸣的热点，换几个平台或稍后刷新试试。' : '这次没取到热搜数据，换几个平台或稍后刷新试试。'}</div>}

      {/* 我的赛道只返回单组时铺满；后端尚未落地该契约时退回多列，避免挤成一列 */}
      <div className={`trend-grid ${resonance && groups.length === 1 ? 'single' : ''}`}>
        {groups.map((g) => (
          <div key={g.platform} className="card trend-col">
            <div className="trend-col-head">{g.label}<span className="trend-count">{g.items.length}</span></div>
            <div className="trend-list">
              {g.items.length === 0 && !loading && (
                <div className="trend-empty">
                  {g.ok === false
                    ? '该平台暂时取不到（数据源不可用）'
                    : resonance ? '暂无共鸣热点，试试多选几个平台' : '暂无数据'}
                </div>
              )}
              {g.items.map((it, i) => (
                <div key={i} className="trend-item">
                  <span className={`trend-rank ${i < 3 ? 'top' : ''}`}>{i + 1}</span>
                  <div className="trend-main">
                    <a className="trend-title" href={it.url || undefined} target="_blank" rel="noreferrer"
                      title={it.title}>{it.title}</a>
                    {resonance ? (
                      <div className="trend-sub">
                        {it.label && <span className="trend-meta">{it.label}</span>}
                        {typeof it.score === 'number' && <span className="trend-score">共鸣 {it.score}</span>}
                        {it.hot && <span className="trend-hot">{it.hot}</span>}
                      </div>
                    ) : (
                      it.hot && <span className="trend-hot">{it.hot}</span>
                    )}
                  </div>
                  <button className="trend-save" title={saved.has(it.title) ? '已收藏到选题库' : '收藏到选题库'}
                    onClick={() => save(it.title, it.label || g.label)}>
                    {saved.has(it.title) ? <IconCheck size={14} /> : <IconBookmark size={14} />}
                  </button>
                  <button className="trend-use" title="做成内容"
                    onClick={() => onUseTopic(it.title)}>做内容</button>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

import { useState, useEffect, useCallback } from 'react';
import {
  fetchDouyinStatus, runDouyin, douyinDownloadUrl, douyinExportUrl,
} from '../lib/api';
import type { DouyinRun, DouyinResult, DouyinRunReq } from '../lib/api';
import { IconChart, IconRefresh } from './icons';

type Mode = 'user' | 'comments' | 'search' | 'feed';

const MODES: { id: Mode; label: string; hint: string; ph: string }[] = [
  { id: 'user', label: '博主作品', hint: '抓某个博主主页的作品列表，可翻页抓全量', ph: 'MS4wLjABAAAA…' },
  { id: 'comments', label: '作品评论', hint: '抓单条视频的评论', ph: '7676311289018207523' },
  { id: 'search', label: '关键词搜索', hint: '按关键词搜索作品', ph: '露营装备' },
  { id: 'feed', label: '推荐流', hint: '首页推荐流取样，不需要参数', ph: '' },
];

/** 大数格式化：12000 → 1.2万 */
function fmtNum(n: unknown): string {
  if (typeof n !== 'number' || Number.isNaN(n)) return '—';
  if (Math.abs(n) >= 10000) {
    return (n / 10000).toFixed(1).replace(/\.0$/, '') + '万';
  }
  return String(n);
}

function fmtTime(iso: unknown): string {
  if (typeof iso !== 'string' || !iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const p = (x: number) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function DouyinPage() {
  const [mode, setMode] = useState<Mode>('user');
  const [secUserId, setSecUserId] = useState('');
  const [awemeId, setAwemeId] = useState('');
  const [keyword, setKeyword] = useState('');
  const [limit, setLimit] = useState('20');
  const [push, setPush] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<DouyinResult | null>(null);
  const [err, setErr] = useState('');
  const [showLog, setShowLog] = useState(false);
  const [toast, setToast] = useState('');
  const [status, setStatus] = useState<{ has_token: boolean; runs: DouyinRun[] }>({
    has_token: false, runs: [],
  });

  const loadStatus = useCallback(() => {
    fetchDouyinStatus().then(setStatus).catch(() => { /* 后端未就绪时静默 */ });
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const flash = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(''), 2400);
  };

  const active = MODES.find((m) => m.id === mode) ?? MODES[0]!;
  const needParam = mode !== 'feed';
  const paramValue = mode === 'user' ? secUserId : mode === 'comments' ? awemeId : keyword;
  const canRun = !busy && status.has_token && (!needParam || paramValue.trim().length > 0);

  const run = async () => {
    setBusy(true); setErr(''); setRes(null);
    const payload: DouyinRunReq = { mode, limit: Number(limit) || 0, push };
    if (mode === 'user') payload.sec_user_id = secUserId.trim();
    if (mode === 'comments') payload.aweme_id = awemeId.trim();
    if (mode === 'search') payload.keyword = keyword.trim();
    try {
      const r = await runDouyin(payload);
      setRes(r);
      loadStatus();
      flash(`采集完成，共 ${r.count} 条`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : '采集失败');
    } finally {
      setBusy(false);
    }
  };

  const onModeChange = (m: Mode) => {
    setMode(m); setRes(null); setErr('');
    if (m === 'feed') setLimit('30');
    else if (m === 'comments') setLimit('200');
    else setLimit('20');
  };

  return (
    <div className="page-scroll">
      <div className="page-head">
        <div>
          <h1 className="page-title"><IconChart size={21} /> 抖音数据</h1>
          <p className="page-subtitle">
            走 TikHub 官方接口采集，不需要登录抖音、不开浏览器。产出统一格式并落盘
            <code style={{ margin: '0 4px' }}>outputs/_douyin/</code>。
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {status.has_token
            ? <span className="badge badge-ok">TikHub 已配置</span>
            : <span className="badge badge-warn">未配置 TIKHUB_API_KEY</span>}
          <button className="btn btn-sm" onClick={loadStatus} title="刷新记录">
            <IconRefresh size={14} />
          </button>
        </div>
      </div>

      {/* 模式选择 */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '16px 0 4px' }}>
        {MODES.map((m) => (
          <button key={m.id} className={`chip ${mode === m.id ? 'active' : ''}`}
            onClick={() => onModeChange(m.id)}>{m.label}</button>
        ))}
      </div>
      <p style={{ color: 'var(--text-tertiary)', fontSize: 12.5, marginBottom: 14 }}>{active.hint}</p>

      {/* 参数表单 */}
      <div className="card" style={{ padding: 16 }}>
        {needParam && (
          <label className="field-label">
            {mode === 'user' ? 'sec_user_id（博主主页 URL 里 MS4wLjABAAAA… 那串）'
              : mode === 'comments' ? 'aweme_id（视频 URL 里 /video/ 后面那串数字）'
              : '关键词'}
          </label>
        )}
        {needParam && (
          <input className="field" value={paramValue}
            onChange={(e) => {
              const v = e.target.value;
              if (mode === 'user') setSecUserId(v);
              else if (mode === 'comments') setAwemeId(v);
              else setKeyword(v);
            }}
            placeholder={active.ph} style={{ marginBottom: 12 }} />
        )}

        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ width: 170 }}>
            <label className="field-label">最多抓多少条</label>
            <input className="field" type="number" min={0} value={limit}
              onChange={(e) => setLimit(e.target.value)} />
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} />
            同时写入选题库
          </label>
          <div style={{ flex: 1 }} />
          <button className="btn btn-primary" disabled={!canRun} onClick={run}>
            {busy ? '采集中…' : '开始采集'}
          </button>
        </div>

        <p style={{ color: 'var(--text-tertiary)', fontSize: 12, marginTop: 10 }}>
          TikHub 按次计费约 $0.01/次，翻页会累加；limit 越小越省。
          {mode === 'user' && ' limit 填 0 = 抓到没有更多为止。'}
        </p>
      </div>

      {!status.has_token && (
        <div className="card" style={{ marginTop: 14, borderColor: 'var(--amber-soft)' }}>
          <div style={{ fontSize: 13.5 }}>
            还没配置 TikHub API Key。直接去左下角
            <strong style={{ margin: '0 4px' }}>技能库 →「抖音数据采集」</strong>
            里填写即可（写进项目根 <code>.env</code> 的
            <code style={{ margin: '0 4px' }}>TIKHUB_API_KEY</code>）；
            没有 key 的话到 <a href="https://tikhub.io" target="_blank" rel="noreferrer">tikhub.io</a> 注册获取。
          </div>
        </div>
      )}

      {err && (
        <div className="card" style={{ marginTop: 14, borderColor: 'var(--red-soft)', color: 'var(--red)' }}>
          {err}
        </div>
      )}

      {/* 结果 */}
      {res && (
        <div style={{ marginTop: 18 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
            {[
              { label: '采集条数', value: String(res.count) },
              { label: '总点赞', value: fmtNum(res.summary.total_likes) },
              { label: '最高点赞', value: fmtNum(res.summary.max_likes) },
              { label: '平均点赞', value: fmtNum(res.summary.avg_likes) },
            ].map((s) => (
              <div key={s.label} className="card" style={{ flex: '1 1 120px', padding: 14 }}>
                <div style={{ fontSize: 22, fontWeight: 600 }}>{s.value}</div>
                <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 2 }}>{s.label}</div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
            <a className="btn btn-sm" href={douyinDownloadUrl(res.file)} download>下载 JSON</a>
            <a className="btn btn-sm" href={douyinExportUrl(res.file)} download>导出 Excel</a>
            <button className="btn btn-sm btn-ghost" onClick={() => setShowLog((v) => !v)}>
              {showLog ? '隐藏日志' : '查看日志'}
            </button>
            <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>{res.file}</span>
          </div>

          {showLog && (
            <pre style={{
              background: 'var(--surface-2, #111)', border: '1px solid var(--border)',
              borderRadius: 8, padding: 12, fontSize: 12, maxHeight: 200,
              overflow: 'auto', whiteSpace: 'pre-wrap',
            }}>{res.log || '（无日志）'}</pre>
          )}

          <div className="card" style={{ padding: 0, overflow: 'auto', maxHeight: 460 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ position: 'sticky', top: 0, background: 'var(--surface-2, #16181d)' }}>
                  {['标题', '点赞', '评论', '作者', '发布时间', '链接'].map((h) => (
                    <th key={h} style={{
                      textAlign: 'left', padding: '9px 10px', fontWeight: 500,
                      color: 'var(--text-secondary)', fontSize: 12,
                      borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {res.items.map((it, i) => {
                  const st = (it.stats ?? {}) as Record<string, unknown>;
                  const url = typeof it.url === 'string' ? it.url : '';
                  return (
                    <tr key={String(it.id ?? i)}>
                      <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', maxWidth: 320 }}>
                        <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {String(it.title ?? '') || '（无标题）'}
                        </span>
                      </td>
                      <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{fmtNum(st.likes)}</td>
                      <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{fmtNum(st.comments)}</td>
                      <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{String(it._author_name ?? '—')}</td>
                      <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap', color: 'var(--text-secondary)', fontSize: 12 }}>{fmtTime(it.published)}</td>
                      <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>
                        {url ? <a href={url} target="_blank" rel="noreferrer">打开</a> : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {res.truncated && (
            <p style={{ color: 'var(--text-tertiary)', fontSize: 12, marginTop: 8 }}>
              表格只预览前 300 条，完整数据请下载 JSON / Excel。
            </p>
          )}
        </div>
      )}

      {/* 历史记录 */}
      <div style={{ marginTop: 24 }}>
        <h2 style={{ fontSize: 15, marginBottom: 10, color: 'var(--text-secondary)' }}>
          采集记录（{status.runs.length}）
        </h2>
        {status.runs.length === 0 ? (
          <div className="card" style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>
            还没有采集记录。选一个模式，点「开始采集」。
          </div>
        ) : (
          <div className="card" style={{ padding: 0, overflow: 'auto', maxHeight: 300 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: 'var(--surface-2, #16181d)' }}>
                  {['类型', '对象', '条数', '时间', '操作'].map((h) => (
                    <th key={h} style={{
                      textAlign: 'left', padding: '9px 10px', fontWeight: 500,
                      color: 'var(--text-secondary)', fontSize: 12,
                      borderBottom: '1px solid var(--border)',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {status.runs.map((r) => (
                  <tr key={r.file}>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>{r.kind}</td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.key || '—'}</td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>{r.count}</td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)', fontSize: 12, whiteSpace: 'nowrap' }}>{fmtTime(r.at)}</td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>
                      <a href={douyinDownloadUrl(r.file)} download style={{ marginRight: 10 }}>JSON</a>
                      <a href={douyinExportUrl(r.file)} download>Excel</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {toast && <div className="toast ok"><span className="toast-icon">✓</span>{toast}</div>}
    </div>
  );
}

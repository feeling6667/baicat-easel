import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import type { CSSProperties } from 'react';
import {
  fetchOutputs, fetchOutputContent, mediaUrl, deleteOutput, createOutputFolder, moveOutput,
  outputDownloadUrl,
} from '../lib/api';
import type { OutputNode, OutputMeta } from '../lib/api';
import { renderMarkdown } from '../lib/sanitize';
import { rewriteLocalLinks } from '../lib/linkifyOutputs';
import {
  IconOutputs, IconImage, IconVideo, IconMusic, IconFile, IconFolder, IconFolderPlus, IconMove,
  IconDownload, IconRefresh, IconChevron, IconTrash,
} from './icons';

const FILTERS: { key: string; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'image', label: '图片' },
  { key: 'video', label: '视频' },
  { key: 'audio', label: '音频' },
  { key: 'text', label: '文档' },
];

const KIND_LABEL: Record<string, string> = {
  article: '文章', 'xhs-note': '小红书', video: '视频', cards: '卡片',
  poster: '海报', audio: '音频', other: '其他',
};
const STATUS_LABEL: Record<string, string> = { draft: '草稿', ready: '待发', published: '已发' };
const STATUS_COLOR: Record<string, string> = { draft: 'var(--text-tertiary)', ready: 'var(--amber)', published: 'var(--green)' };

const badge: CSSProperties = {
  fontSize: 11, padding: '1px 7px', borderRadius: 999,
  background: 'var(--surface-hover)', color: 'var(--text-secondary)', whiteSpace: 'nowrap',
};
const statusBadge = (s: string): CSSProperties => ({
  ...badge,
  background: `color-mix(in srgb, ${STATUS_COLOR[s] || 'var(--text-tertiary)'} 13%, transparent)`,
  color: STATUS_COLOR[s] || 'var(--text-secondary)',
});

function kindIcon(kind: string | undefined, size = 30) {
  if (kind === 'video') return <IconVideo size={size} />;
  if (kind === 'audio') return <IconMusic size={size} />;
  if (kind === 'image') return <IconImage size={size} />;
  return <IconFile size={size} />;
}
const isHtml = (name: string) => /\.html?$/i.test(name);
const kindLabel = (f: OutputNode) =>
  f.kind === 'text' ? (isHtml(f.name) ? '卡片' : '文档')
    : f.kind === 'image' ? '图片' : f.kind === 'video' ? '视频' : f.kind === 'audio' ? '音频' : '文件';

/** 递归找目录下第一张图/视频作封面缩略图。 */
function firstMedia(node: OutputNode): OutputNode | null {
  if (node.type === 'file') return (node.kind === 'image' || node.kind === 'video') ? node : null;
  for (const c of node.children || []) {
    const m = firstMedia(c);
    if (m) return m;
  }
  return null;
}

/** 展示头声明的封面 → 伪 file 节点（供 Thumb 渲染）。 */
function coverNode(m?: OutputMeta): OutputNode | null {
  if (!m?.cover) return null;
  const kind = /\.(mp4|mov|webm|mkv)$/i.test(m.cover) ? 'video' : 'image';
  return { name: 'cover', type: 'file', path: m.cover, kind } as OutputNode;
}

/** 按名称路径解析到当前目录的 children（stackNames 稳定，刷新后仍有效）。 */
function resolvePath(roots: OutputNode[], names: string[]): OutputNode[] {
  let nodes = roots;
  for (const nm of names) {
    const found = nodes.find((n) => n.type === 'dir' && n.name === nm);
    if (!found) return nodes;   // 路径失效（被删/改）→ 停在能解析到的层
    nodes = found.children || [];
  }
  return nodes;
}

/** 把产物树里的全部文件夹拍平成「缩进层级」清单，供「移动到…」选择目标。 */
function flattenDirs(nodes: OutputNode[], depth = 0): { node: OutputNode; depth: number }[] {
  const out: { node: OutputNode; depth: number }[] = [];
  for (const n of nodes) {
    if (n.type !== 'dir') continue;
    out.push({ node: n, depth });
    if (n.children?.length) out.push(...flattenDirs(n.children, depth + 1));
  }
  return out;
}

/** 触发浏览器「另存为」：文件名与是否打包由后端 Content-Disposition 决定。 */
function triggerDownload(path: string) {
  const a = document.createElement('a');
  a.href = outputDownloadUrl(path);
  a.rel = 'noopener';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function Thumb({ f, big }: { f: OutputNode | null; big?: boolean }) {
  if (f && f.kind === 'image') return <img src={mediaUrl(f.path)} alt="" loading="lazy" />;
  if (f && f.kind === 'video') return <video src={mediaUrl(f.path)} preload="metadata" muted />;
  return <div className="gcard-ph">{kindIcon(f?.kind, big ? 34 : 30)}</div>;
}

interface OutputsPageProps {
  /** 从对话跳转进来的目录（outputs 相对路径，不含 outputs/ 前缀）；消费后由父层清空 */
  jumpPath?: string;
  onJumpHandled?: () => void;
}

export default function OutputsPage({ jumpPath, onJumpHandled }: OutputsPageProps) {
  const [roots, setRoots] = useState<OutputNode[]>([]);
  const [treeError, setTreeError] = useState('');
  const [stack, setStack] = useState<string[]>([]);   // 当前所在的文件夹名称路径
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState<OutputNode | null>(null);
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const reqSeq = useRef(0);

  // —— 新建文件夹 / 移动到另一个文件夹 ——
  const [creating, setCreating] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [moving, setMoving] = useState<OutputNode | null>(null);
  const [moveDest, setMoveDest] = useState('');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');

  // —— 图片灯箱：同文件夹内左右滑动浏览 ——
  const [viewer, setViewer] = useState<{ list: OutputNode[]; index: number } | null>(null);
  const [dragX, setDragX] = useState(0);
  const dragStart = useRef<number | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(''), 2600);
    return () => window.clearTimeout(t);
  }, [toast]);

  const load = useCallback(() => {
    setTreeError('');
    fetchOutputs().then(setRoots).catch(() => setTreeError('加载产物列表失败'));
  }, []);
  useEffect(() => { load(); }, [load]);

  const currentNodes = useMemo(() => resolvePath(roots, stack), [roots, stack]);
  const dirs = useMemo(
    () => currentNodes.filter((n) => n.type === 'dir').sort((a, b) => (b.mtime || 0) - (a.mtime || 0)),
    [currentNodes]);
  const files = useMemo(() => {
    const fs = currentNodes.filter((n) => n.type === 'file').sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
    return filter === 'all' ? fs : fs.filter((f) => f.kind === filter);
  }, [currentNodes, filter]);

  const atTop = stack.length === 0;
  const atProjectRoot = stack.length === 1;
  // 灯箱的浏览清单：当前文件夹里的全部图片（不受类型过滤影响），与卡片同序（新→旧）
  const folderImages = useMemo(
    () => currentNodes
      .filter((n) => n.type === 'file' && n.kind === 'image')
      .sort((a, b) => (b.mtime || 0) - (a.mtime || 0)),
    [currentNodes]);
  // 「移动到…」的目标清单：全树文件夹拍平；根目录不列（根不展示散文件，后端也会拒）
  const folderOptions = useMemo(() => flattenDirs(roots), [roots]);
  const movingParent = moving ? moving.path.split('/').slice(0, -1).join('/') : '';
  // 当前项目的展示头（进入项目后才有），用于「成品/素材」分区
  const projectMeta = useMemo(
    () => (stack.length >= 1 ? roots.find((r) => r.name === stack[0])?.meta : undefined),
    [roots, stack]);
  const deliverableSet = useMemo(
    () => new Set(atProjectRoot ? (projectMeta?.deliverablePaths || []) : []),
    [projectMeta, atProjectRoot]);
  const hasSplit = atProjectRoot && deliverableSet.size > 0;
  const deliverableFiles = useMemo(
    () => (hasSplit ? files.filter((f) => deliverableSet.has(f.path)) : []),
    [files, deliverableSet, hasSplit]);
  const restFiles = useMemo(
    () => (hasSplit ? files.filter((f) => !deliverableSet.has(f.path)) : files),
    [files, deliverableSet, hasSplit]);

  const enterDir = useCallback((name: string) => { setStack((s) => [...s, name]); setFilter('all'); }, []);
  const goTo = useCallback((depth: number) => { setStack((s) => s.slice(0, depth)); setFilter('all'); }, []);

  const remove = useCallback(async (node: OutputNode, e?: React.MouseEvent) => {
    e?.stopPropagation();
    const isDir = node.type === 'dir';
    const label = isDir ? `项目/文件夹「${node.meta?.title || node.name}」及其全部内容` : `文件「${node.name}」`;
    if (!window.confirm(`确定删除${label}？\n此操作不可恢复。`)) return;
    try {
      await deleteOutput(node.path);
      setSelected((cur) => (cur?.path === node.path ? null : cur));
      // 灯箱里删掉某张图：从浏览清单里移除；删空了就关掉灯箱
      setViewer((v) => {
        if (!v || !v.list.some((n) => n.path === node.path)) return v;
        const list = v.list.filter((n) => n.path !== node.path);
        return list.length ? { list, index: Math.min(v.index, list.length - 1) } : null;
      });
      load();
    } catch (err) {
      alert((err as Error).message || '删除失败');
    }
  }, [load]);

  /** 打开图片灯箱；不传 list 时用当前文件夹的图片清单（同文件夹内左右滑动）。 */
  const openViewer = useCallback((node: OutputNode, list?: OutputNode[]) => {
    const images = list && list.length > 0 ? list : folderImages;
    const source = images.some((n) => n.path === node.path) ? images : [node, ...images];
    const index = Math.max(0, source.findIndex((n) => n.path === node.path));
    setViewer({ list: source, index });
    setDragX(0);
  }, [folderImages]);

  const closeViewer = useCallback(() => { setViewer(null); setDragX(0); }, []);

  /** 左右切换一张（到边界即停）。 */
  const stepViewer = useCallback((delta: number) => {
    setViewer((v) => {
      if (!v) return v;
      const next = Math.min(v.list.length - 1, Math.max(0, v.index + delta));
      return next === v.index ? v : { ...v, index: next };
    });
    setDragX(0);
  }, []);

  // 灯箱快捷键：← → 切换，Esc 关闭
  useEffect(() => {
    if (!viewer) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); stepViewer(-1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); stepViewer(1); }
      else if (e.key === 'Escape') closeViewer();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [viewer, stepViewer, closeViewer]);

  // 灯箱手势：鼠标按住拖动 / 触屏左右滑；松手超过阈值就翻页
  const movedRef = useRef(false);
  const onStagePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    // 点按钮（上一张/下一张）不当作拖动手势：setPointerCapture 会把后续 click 重定向到 stage，
    // 按钮就永远收不到点击。
    if ((e.target as HTMLElement).closest('button')) return;
    dragStart.current = e.clientX;
    movedRef.current = false;
    setDragX(0);
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onStagePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragStart.current === null) return;
    const dx = e.clientX - dragStart.current;
    if (Math.abs(dx) > 6) movedRef.current = true;   // 真拖动过 → 松手后不当成「点空白关闭」
    setDragX(dx);
  };
  const onStagePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragStart.current === null) return;
    const dx = e.clientX - dragStart.current;
    dragStart.current = null;
    if (Math.abs(dx) > 60) stepViewer(dx < 0 ? 1 : -1);
    else setDragX(0);
  };

  const submitCreate = useCallback(async () => {
    const name = newFolderName.trim();
    if (!name || busy) return;
    setBusy(true);
    try {
      const res = await createOutputFolder(stack.join('/'), name);
      setCreating(false);
      setNewFolderName('');
      setToast(`已新建文件夹「${res.name}」`);
      load();
    } catch (err) {
      alert((err as Error).message || '新建文件夹失败');
    } finally {
      setBusy(false);
    }
  }, [newFolderName, busy, stack, load]);

  const openMove = useCallback((node: OutputNode) => {
    setMoving(node);
    setMoveDest('');
  }, []);

  /** 下载到本地：文件直接下，文件夹由后端打包成 zip 再下。 */
  const download = useCallback((node: OutputNode, e?: React.MouseEvent) => {
    e?.stopPropagation();
    triggerDownload(node.path);
    setToast(node.type === 'dir' ? `正在打包下载「${node.meta?.title || node.name}」…` : `正在下载「${node.name}」…`);
  }, []);

  const submitMove = useCallback(async () => {
    if (!moving || !moveDest || busy) return;
    setBusy(true);
    try {
      const res = await moveOutput(moving.path, moveDest);
      const destLabel = folderOptions.find((o) => o.node.path === moveDest)?.node.meta?.title
        || moveDest.split('/').filter(Boolean).pop() || moveDest;
      setSelected((cur) => (cur?.path === moving.path ? null : cur));
      // 灯箱里移动的是清单内的图 → 清单已失效，直接关掉
      setViewer((v) => (v && v.list.some((n) => n.path === moving.path) ? null : v));
      setMoving(null);
      setMoveDest('');
      setToast(res.unchanged ? '已经在这个文件夹里了' : `已移动到「${destLabel}」`);
      load();
    } catch (err) {
      alert((err as Error).message || '移动失败');
    } finally {
      setBusy(false);
    }
  }, [moving, moveDest, busy, load, folderOptions]);

  const open = useCallback(async (f: OutputNode) => {
    if (f.kind === 'image') { openViewer(f); return; }   // 图片走灯箱：可左右滑动看同文件夹其他图
    const seq = ++reqSeq.current;
    setSelected(f); setContent('');
    if (f.kind === 'text' && !isHtml(f.name)) {
      setLoading(true);
      try {
        const res = await fetchOutputContent(f.path);
        if (seq === reqSeq.current) setContent(res.isBinary ? '' : res.content);
      } finally { if (seq === reqSeq.current) setLoading(false); }
    }
  }, [openViewer]);

  // 等内容树加载后区分文件与目录；文件链接直接打开预览。
  useEffect(() => {
    if (!jumpPath || roots.length === 0) return;
    const parts = jumpPath.split('/').filter(Boolean);
    const parent: string[] = [];
    let nodes = roots;
    let node: OutputNode | undefined;
    for (const part of parts) {
      node = nodes.find(candidate => candidate.name === part);
      if (!node || node.type !== 'dir') break;
      parent.push(part);
      nodes = node.children || [];
    }
    if (node?.path !== parts.join('/')) node = undefined;
    setStack(parent);
    setFilter('all');
    if (node?.type === 'file') void open(node);
    else setSelected(null);
    onJumpHandled?.();
  }, [jumpPath, roots, open, onJumpHandled]);

  const preview = () => {
    if (!selected) return null;
    const url = mediaUrl(selected.path);
    if (selected.kind === 'image') return (
      <img src={url} alt={selected.name} onClick={() => openViewer(selected)} title="点开可左右滑动浏览同文件夹图片"
        style={{ maxWidth: '100%', borderRadius: 'var(--radius)', cursor: 'zoom-in' }} />
    );
    if (selected.kind === 'video') return <video src={url} controls style={{ maxWidth: '100%', borderRadius: 'var(--radius)' }} />;
    if (selected.kind === 'audio') return <audio src={url} controls style={{ width: '100%' }} />;
    if (selected.kind === 'text' && isHtml(selected.name)) return (
      <>
        {/* allow-scripts：让预览页自带的「复制到公众号」按钮(execCommand('copy'))能运行；
            allow="clipboard-write"：授予剪贴板写权限。不给 allow-same-origin —— iframe 保持
            opaque origin，脚本跑得起来但访问不到本站，安全。(修复：内嵌预览里复制按钮点了没反应) */}
        <iframe src={`${url}?v=${selected.mtime ?? 0}`} title={selected.name} sandbox="allow-scripts" allow="clipboard-write"
          style={{ width: '100%', height: '68vh', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: '#fff' }} />
        <div style={{ marginTop: 8 }}><a href={url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-start)', fontSize: 13 }}>在新标签打开 ↗</a></div>
      </>
    );
    if (selected.kind === 'text') {
      if (loading) return <div className="loading"><div className="spinner" />加载中…</div>;
      return <div className="outputs-viewer-content" dangerouslySetInnerHTML={{ __html: rewriteLocalLinks(renderMarkdown(content)) }} />;
    }
    return <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>无法预览。<a href={url} download style={{ color: 'var(--accent-start)' }}>下载 {selected.name}</a></div>;
  };

  /** 项目/文件夹卡片：顶层项目用展示头（标题/平台/状态/封面），嵌套子文件夹回退朴素样式。 */
  const renderDir = (d: OutputNode) => {
    const m = d.meta;
    const cover = coverNode(m) || firstMedia(d);
    return (
      <div key={d.path} className="card card-hover gcard" onClick={() => enterDir(d.name)}>
        <div className="gcard-thumb">
          <span className="gcard-kind">{m?.kind ? (KIND_LABEL[m.kind] || m.kind) : '文件夹'}</span>
          <button className="gcard-dl" title="下载到本地（文件夹打包为 zip）" onClick={(e) => download(d, e)}><IconDownload size={14} /></button>
          <button className="gcard-move" title="移动到其他文件夹" onClick={(e) => { e.stopPropagation(); openMove(d); }}><IconMove size={14} /></button>
          <button className="gcard-del" title="删除" onClick={(e) => remove(d, e)}><IconTrash size={14} /></button>
          {cover ? <Thumb f={cover} big /> : <div className="gcard-ph"><IconFolder size={38} /></div>}
        </div>
        <div className="gcard-meta">
          <div className="gcard-name" title={m?.title || d.name}>
            {!m && <IconFolder size={13} />} {m?.title || d.name}
          </div>
          <div className="gcard-sub" style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            {m?.platform && <span style={badge}>{m.platform}</span>}
            {m?.status && <span style={statusBadge(m.status)}>{STATUS_LABEL[m.status] || m.status}</span>}
            <span>{d.fileCount ?? 0} 个文件</span>
          </div>
        </div>
      </div>
    );
  };

  const renderFile = (f: OutputNode) => (
    <div key={f.path} className="card card-hover gcard" onClick={() => open(f)}>
      <div className="gcard-thumb">
        <span className="gcard-kind">{kindLabel(f)}</span>
        <button className="gcard-dl" title="下载到本地" onClick={(e) => download(f, e)}><IconDownload size={14} /></button>
        <button className="gcard-move" title="移动到其他文件夹" onClick={(e) => { e.stopPropagation(); openMove(f); }}><IconMove size={14} /></button>
        <button className="gcard-del" title="删除" onClick={(e) => remove(f, e)}><IconTrash size={14} /></button>
        <Thumb f={f} />
      </div>
      <div className="gcard-meta">
        <div className="gcard-name" title={f.name}>{f.name}</div>
      </div>
    </div>
  );

  const empty = dirs.length === 0 && files.length === 0;

  return (
    <div className="gallery-page">
      <div className="gallery-head">
        <div>
          <h1 className="page-title">
            <IconOutputs size={21} />
            <span className="crumb" onClick={() => goTo(0)}>内容库</span>
            {stack.map((name, i) => (
              <span key={i}>
                <span className="crumb-sep">/</span>
                {i === stack.length - 1
                  ? (projectMeta?.title && i === 0 ? projectMeta.title : name)
                  : <span className="crumb" onClick={() => goTo(i + 1)}>{name}</span>}
              </span>
            ))}
          </h1>
          <p className="page-subtitle">
            {atTop
              ? `按项目归档，共 ${roots.length} 个项目。点项目进去看成品与素材。`
              : `${dirs.length} 个文件夹 · ${files.length} 个文件（可继续点开子文件夹）`}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          {stack.length > 0 && (
            <button className="btn btn-sm" title="把当前文件夹打包成 zip 下载"
              onClick={() => { triggerDownload(stack.join('/')); setToast(`正在打包下载「${stack[stack.length - 1]}」…`); }}>
              <IconDownload size={14} /> 打包下载
            </button>
          )}
          <button className="btn btn-sm" onClick={() => { setNewFolderName(''); setCreating(true); }}>
            <IconFolderPlus size={14} /> 新建文件夹
          </button>
          <button className="btn btn-sm" onClick={load}><IconRefresh size={14} /> 刷新</button>
        </div>
      </div>

      {treeError && <div className="notice-error">{treeError}</div>}

      {/* 项目主题标签（进入项目根时展示） */}
      {atProjectRoot && projectMeta?.tags && projectMeta.tags.length > 0 && (
        <div className="gallery-filters" style={{ marginBottom: 4 }}>
          {projectMeta.tags.map((t) => <span key={t} style={badge}>#{t}</span>)}
        </div>
      )}

      {/* 面包屑返回 + 文件过滤（进入任意层后显示） */}
      {stack.length > 0 && (
        <div className="gallery-filters">
          <button className="btn btn-sm" onClick={() => goTo(stack.length - 1)}>
            <span style={{ transform: 'rotate(180deg)', display: 'inline-flex' }}><IconChevron size={13} /></span> 返回上级
          </button>
          {files.length > 0 && FILTERS.map((f) => (
            <button key={f.key} className={`chip ${filter === f.key ? 'active' : ''}`} onClick={() => setFilter(f.key)}>{f.label}</button>
          ))}
        </div>
      )}

      {empty && !treeError ? (
        <div className="empty-state" style={{ height: 300 }}>
          <div className="empty-icon"><IconOutputs size={44} /></div>
          <p>{atTop ? '还没有产物——去对话或技能库生成第一条内容吧' : '这个文件夹是空的'}</p>
        </div>
      ) : hasSplit ? (
        <>
          {/* 成品区 */}
          {deliverableFiles.length > 0 && (
            <>
              <div className="section-label" style={{ margin: '6px 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>
                成品 · {deliverableFiles.length}
              </div>
              <div className="gallery-grid">{deliverableFiles.map(renderFile)}</div>
            </>
          )}
          {/* 素材 / 过程文件区 */}
          {(dirs.length > 0 || restFiles.length > 0) && (
            <>
              <div className="section-label" style={{ margin: '18px 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--text-tertiary)' }}>
                素材 / 过程文件
              </div>
              <div className="gallery-grid">
                {dirs.map(renderDir)}
                {restFiles.map(renderFile)}
              </div>
            </>
          )}
        </>
      ) : (
        <div className="gallery-grid">
          {dirs.map(renderDir)}
          {files.map(renderFile)}
        </div>
      )}

      {selected && (
        <div className="drawer-overlay" onClick={() => setSelected(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ minWidth: 0 }}>
                <div className="skill-detail-title" style={{ fontSize: 16 }}>{selected.name}</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 3, fontFamily: "'SF Mono','Consolas',monospace" }}>{selected.path}</div>
              </div>
              <button className="icon-btn" onClick={() => setSelected(null)}>×</button>
            </div>
            <div className="drawer-body">{preview()}</div>
            <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              {selected.kind === 'image' && (
                <button className="btn btn-sm" onClick={() => openViewer(selected)}><IconImage size={13} /> 左右滑动浏览</button>
              )}
              <button className="btn btn-sm" onClick={() => download(selected)}><IconDownload size={13} /> 下载到本地</button>
              <button className="btn btn-sm" onClick={() => openMove(selected)}><IconMove size={13} /> 移动到…</button>
              <button className="btn btn-sm btn-danger" onClick={(e) => remove(selected, e)}><IconTrash size={13} /> 删除此文件</button>
            </div>
          </div>
        </div>
      )}
      {/* 新建文件夹 */}
      {creating && (
        <div className="overlay" onClick={() => { if (!busy) setCreating(false); }}>
          <div className="modal" style={{ width: 420, maxWidth: '92vw' }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: 0, fontSize: 17 }}>新建文件夹</h3>
            <p style={{ margin: '6px 0 14px', fontSize: 13, color: 'var(--text-secondary)' }}>
              位置：内容库{stack.length > 0 ? ` / ${stack.join(' / ')}` : ' 根目录'}
            </p>
            <input
              autoFocus
              className="move-input"
              placeholder="文件夹名称"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void submitCreate(); }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
              <button className="btn btn-sm" onClick={() => setCreating(false)} disabled={busy}>取消</button>
              <button className="btn btn-sm btn-primary" onClick={() => void submitCreate()}
                disabled={busy || !newFolderName.trim()}>
                {busy ? '创建中…' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 移动到另一个文件夹 */}
      {moving && (
        <div className="overlay" onClick={() => { if (!busy) setMoving(null); }}>
          <div className="modal" style={{ width: 540, maxWidth: '92vw' }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: 0, fontSize: 17 }}>移动{moving.type === 'dir' ? '文件夹' : '文件'}</h3>
            <p style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--text-secondary)' }}>
              「{moving.name}」→ 选择目标文件夹
            </p>
            <div className="move-list">
              {folderOptions.length === 0 && (
                <div style={{ padding: 14, fontSize: 13, color: 'var(--text-tertiary)' }}>
                  还没有可选的文件夹，先新建一个吧
                </div>
              )}
              {folderOptions.map(({ node, depth }) => {
                const insideSelf = moving.type === 'dir'
                  && (node.path === moving.path || node.path.startsWith(`${moving.path}/`));
                const isCurrent = node.path === movingParent;
                const active = moveDest === node.path;
                return (
                  <button
                    key={node.path}
                    type="button"
                    className={`move-row ${active ? 'active' : ''}`}
                    disabled={insideSelf || isCurrent}
                    style={{ paddingLeft: 12 + depth * 16 }}
                    onClick={() => setMoveDest(node.path)}
                  >
                    <IconFolder size={14} />
                    <span className="move-row-name">{node.meta?.title || node.name}</span>
                    <span className="move-row-path">{node.path}</span>
                    {isCurrent && <span className="move-row-tag">当前位置</span>}
                  </button>
                );
              })}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
              <button className="btn btn-sm" onClick={() => setMoving(null)} disabled={busy}>取消</button>
              <button className="btn btn-sm btn-primary" onClick={() => void submitMove()} disabled={busy || !moveDest}>
                {busy ? '移动中…' : '移动到这里'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 图片灯箱：同文件夹内左右滑动浏览 */}
      {viewer && (() => {
        const total = viewer.list.length;
        const cur = viewer.list[viewer.index];
        const dragging = dragStart.current !== null;
        return (
          <div className="lightbox" onClick={closeViewer}>
            <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
              <div className="lightbox-title">
                <span className="lightbox-name" title={cur.path}>{cur.name}</span>
                <span className="lightbox-count">{viewer.index + 1} / {total}</span>
              </div>
              <div className="lightbox-actions">
                <button className="lightbox-btn" onClick={() => download(cur)}><IconDownload size={14} /> 下载</button>
                <button className="lightbox-btn" onClick={() => { closeViewer(); openMove(cur); }}><IconMove size={14} /> 移动</button>
                <button className="lightbox-btn danger" onClick={() => void remove(cur)}><IconTrash size={14} /> 删除</button>
                <button className="lightbox-btn" onClick={closeViewer} title="关闭（Esc）">✕</button>
              </div>
            </div>

            <div
              className="lightbox-stage"
              onPointerDown={onStagePointerDown}
              onPointerMove={onStagePointerMove}
              onPointerUp={onStagePointerUp}
              onPointerCancel={onStagePointerUp}
              onClick={(e) => {
                e.stopPropagation();
                // 点图片外的空白处关闭；刚拖动过就不算点击
                if (!movedRef.current && (e.target as HTMLElement).tagName !== 'IMG') closeViewer();
              }}
            >
              <div
                className="lightbox-track"
                style={{
                  transform: `translateX(calc(${-viewer.index * 100}% + ${dragX}px))`,
                  transition: dragging ? 'none' : 'transform 0.26s var(--ease)',
                }}
              >
                {viewer.list.map((n, i) => (
                  <div className="lightbox-slide" key={n.path}>
                    {/* 只挂当前图与左右邻居，避免一次加载整文件夹 */}
                    {Math.abs(i - viewer.index) <= 1
                      ? <img src={mediaUrl(n.path)} alt={n.name} draggable={false} />
                      : null}
                  </div>
                ))}
              </div>
            </div>

            {/* 箭头放在 stage 之外：stage 上的 setPointerCapture 会把 click 重定向到自己，
                按钮若在里面就收不到点击（曾表现为「点箭头没反应」）。 */}
            {total > 1 && (
              <div className="lightbox-navs"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => e.stopPropagation()}>
                <button type="button" className="lightbox-nav prev" title="上一张（←）" aria-label="上一张"
                  disabled={viewer.index === 0}
                  onClick={(e) => { e.stopPropagation(); stepViewer(-1); }}>‹</button>
                <button type="button" className="lightbox-nav next" title="下一张（→）" aria-label="下一张"
                  disabled={viewer.index === total - 1}
                  onClick={(e) => { e.stopPropagation(); stepViewer(1); }}>›</button>
              </div>
            )}

            <div className="lightbox-hint" onClick={(e) => e.stopPropagation()}>
              {total > 1 ? '← → 键或左右拖动切换' : '这个文件夹里只有这一张图'} · Esc 关闭
            </div>
          </div>
        );
      })()}

      {toast && <div className="toast ok"><span className="toast-icon">✓</span>{toast}</div>}
    </div>
  );
}

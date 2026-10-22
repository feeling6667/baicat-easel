import { useState, useEffect, useMemo } from 'react';
import type { ComponentType } from 'react';
import { fetchSkills } from '../lib/api';
import type { SkillItem } from '../lib/api';
import SkillDrawer from './SkillDrawer';
import { displayName } from '../lib/skillDisplayNames';
import {
  IconSearch, IconCompass, IconSkills, IconSend, IconChart, IconLayers,
  IconVideo, IconImage, IconMusic, IconMic, IconText, IconLayout, IconProfile, IconOutputs,
  IconCaretDown,
} from './icons';

interface SkillPageProps {
  persona: string;
}

type IconC = ComponentType<{ size?: number }>;
type LayerMeta = { key: string; label: string; Icon: IconC; color: string };

const LAYERS: LayerMeta[] = [
  { key: 'discover', label: '发现', Icon: IconSearch, color: 'var(--layer-discover)' },
  { key: 'plan', label: '策划', Icon: IconCompass, color: 'var(--layer-plan)' },
  { key: 'produce', label: '制作', Icon: IconSkills, color: 'var(--layer-produce)' },
  { key: 'publish', label: '发布', Icon: IconSend, color: 'var(--layer-publish)' },
  { key: 'attribute', label: '归因', Icon: IconChart, color: 'var(--layer-attribute)' },
  { key: 'general', label: '通用', Icon: IconLayers, color: 'var(--layer-general)' },
];
const LAYER_META: Record<string, LayerMeta> = Object.fromEntries(LAYERS.map((l) => [l.key, l]));
const OTHER: LayerMeta = { key: 'other', label: '其他', Icon: IconLayers, color: 'var(--layer-general)' };

// 分区折叠状态的持久化 key：存「被折叠的 layer key 数组」
const COLLAPSE_KEY = 'easel_skills_collapsed';

// 读取被折叠的 layer key 列表。
// 容错：localStorage 不可用、内容不是合法 JSON、解析结果不是数组、元素不是字符串，
// 一律退回「全部展开」（返回空数组），绝不抛错。
function readCollapsedLayers(): string[] {
  try {
    const raw = window.localStorage.getItem(COLLAPSE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is string => typeof v === 'string');
  } catch {
    // 读取/解析失败时退回全部展开
    return [];
  }
}

// 写回折叠状态。仅在用户手动点击标题行时调用（搜索期的自动展开不写回）。
// 写入失败（隐私模式、配额满）时静默降级为「仅当前会话内存态生效」。
function writeCollapsedLayers(keys: string[]): void {
  try {
    window.localStorage.setItem(COLLAPSE_KEY, JSON.stringify(keys));
  } catch {
    // 忽略写入失败：不影响本次会话内的折叠交互
  }
}

// 按 skill 名关键词映射线性图标（无匹配退回层图标）
function iconFor(name: string, LayerIcon: IconC): IconC {
  const n = name.toLowerCase();
  const map: [RegExp, IconC][] = [
    [/video|clip|reframe|highlight|beat|slideshow|intro|chapter/, IconVideo],
    [/image|img|photo|poster|infographic|comparison|meme|remove-bg|green-screen|enhance/, IconImage],
    [/music|audio|bgm|mix|denoise/, IconMusic],
    [/voice|tts|clone|subtitle/, IconMic],
    [/card|xhs|xiaohongshu|note/, IconLayout],
    [/copy|writ|text|polish|condens|style|format|content/, IconText],
    [/chart|data|report|roi|analy|insight|scor/, IconChart],
    [/publish|upload|wechat|zhihu|bilibili|douyin|kuaishou|channel/, IconSend],
    [/rss|trend|news|discover|ugc|competitor|hot/, IconSearch],
    [/profile|persona|brand|position|audience/, IconProfile],
    [/schedul|calendar|plan|strategy|campaign|matrix/, IconCompass],
    [/asset|template|batch|doc|mindmap|link/, IconOutputs],
  ];
  for (const [re, C] of map) if (re.test(n)) return C;
  return LayerIcon;
}

const LAYER_DESC: Record<string, string> = {
  discover: '发现层技能', plan: '策划层技能', produce: '制作层技能',
  publish: '发布层技能', attribute: '归因层技能', general: '通用技能', other: '技能',
};

export default function SkillPage({ persona }: SkillPageProps) {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  // 用户手动折叠的分区 key（初始从 localStorage 容错读取；读不到即全部展开）
  const [collapsedLayers, setCollapsedLayers] = useState<string[]>(readCollapsedLayers);

  const load = () => {
    fetchSkills().then(setSkills).catch(() => setError('加载 SKILL 列表失败'));
  };
  useEffect(load, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((s) =>
      s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q) || displayName(s.name).toLowerCase().includes(q));
  }, [skills, query]);

  const grouped = useMemo(() => {
    const g: Record<string, SkillItem[]> = {};
    for (const s of filtered) {
      const key = LAYER_META[s.layer] ? s.layer : 'other';
      (g[key] ||= []).push(s);
    }
    return g;
  }, [filtered]);

  const orderedLayers = [...LAYERS, OTHER].filter((l) => grouped[l.key]?.length);
  const needApiCount = skills.filter((s) => s.needsApi && !s.apiConfigured).length;

  const collapsedSet = useMemo(() => new Set(collapsedLayers), [collapsedLayers]);
  // 搜索中：所有命中分区强制展开，不读手动折叠态，也不写回 localStorage
  const searching = query.trim().length > 0;
  const isCollapsed = (key: string) => !searching && collapsedSet.has(key);

  // 唯一的折叠态写入点：用户点击标题行。写 localStorage 只发生在这里。
  const toggleLayer = (key: string) => {
    const next = collapsedLayers.includes(key)
      ? collapsedLayers.filter((k) => k !== key)
      : [...collapsedLayers, key];
    setCollapsedLayers(next);
    writeCollapsedLayers(next);
  };

  return (
    <div className="skills-page">
      <div className="skills-page-head">
        <h1 className="page-title">技能库</h1>
        <p className="page-subtitle">
          共 {skills.length} 个技能，按流水线层分区浏览。点卡片查看说明、就地运行；
          标 <span className="badge badge-warn" style={{ padding: '1px 7px' }}>需 API</span> 的需先配置密钥
          {needApiCount > 0 && `（当前 ${needApiCount} 个待配置）`}。
        </p>
        <div className="skill-search">
          <span className="skill-search-ic"><IconSearch size={16} /></span>
          <input
            className="field"
            placeholder="搜索技能名或描述…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && <button className="btn btn-ghost btn-sm" onClick={() => setQuery('')}>清除</button>}
        </div>
      </div>

      {error && <div style={{ color: 'var(--red)', maxWidth: 1100, margin: '16px auto' }}>{error}</div>}

      <div className="skills-body">
        {orderedLayers.length === 0 && !error && (
          <div className="empty-state" style={{ height: 240 }}>
            <div className="empty-icon"><IconSearch size={40} /></div>
            <p>没有匹配「{query}」的技能</p>
          </div>
        )}

        {orderedLayers.map((layer) => {
          const open = !isCollapsed(layer.key);
          const gridId = `sk-grid-${layer.key}`;
          return (
            <section key={layer.key}>
              <button
                type="button"
                className="section-title sk-section-head"
                aria-expanded={open}
                aria-controls={gridId}
                onClick={() => toggleLayer(layer.key)}
              >
                <span className="section-ic" style={{ color: layer.color }}><layer.Icon size={15} /></span>
                {layer.label}
                <span className="sk-count" style={{ color: 'var(--text-tertiary)', fontWeight: 500 }}>· {grouped[layer.key].length}</span>
                <span className={`sk-caret${open ? '' : ' is-collapsed'}`} aria-hidden="true">
                  <IconCaretDown size={14} />
                </span>
              </button>
              <div id={gridId} className={`skill-grid sk-grid${open ? '' : ' is-collapsed'}`}>
                {grouped[layer.key].map((s) => {
                  const meta = LAYER_META[s.layer] || OTHER;
                  const Icon = iconFor(s.name, meta.Icon);
                  const alert = s.needsApi && !s.apiConfigured;
                  return (
                    <div
                      key={s.name}
                      className="card card-hover skill-card"
                      style={{ ['--layer-color' as string]: meta.color }}
                      onClick={() => setSelected(s.name)}
                    >
                      {alert && <div className="skill-card-alert" title="需要配置 API key">!</div>}
                      <div className="skill-card-icon" style={{ color: meta.color }}><Icon size={19} /></div>
                      <div className="skill-card-name">{displayName(s.name)}</div>
                      <div className="skill-card-rawname">{s.name}</div>
                      <div className="skill-card-desc">{s.description?.trim() || LAYER_DESC[layer.key]}</div>
                      <div className="skill-card-foot">
                        {s.needsApi && (s.apiConfigured
                          ? <span className="badge badge-ok">已配置</span>
                          : <span className="badge badge-warn">需 API</span>)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>

      {selected && (
        <SkillDrawer
          skillName={selected}
          persona={persona}
          onClose={() => setSelected(null)}
          onConfigured={load}
        />
      )}
    </div>
  );
}

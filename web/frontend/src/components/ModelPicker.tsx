import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchModels, selectModel } from '../lib/api';
import type { ModelOption } from '../lib/api';
import { IconModel, IconCaretDown, IconCheck, IconSearch, IconRefresh } from './icons';

/**
 * 模型选择器：在对话输入框下方选「这一轮/以后用哪个模型」。
 *
 * - 清单来自 magpie 本地中转的 /v1/models（后端缓存 60 秒）
 * - 选中即写后端 .env（重启不丢），并即时对后续每一轮生效
 * - disabled=true（流式中）时只看不可改
 */
interface ModelPickerProps {
  value: string;                       // 当前选中的模型 id（空 = 用默认）
  onChange: (modelId: string) => void;
  disabled?: boolean;
}

/** 把模型 id 按「供应商」分组，供下拉分节显示。 */
function groupModels(models: ModelOption[]): { group: string; items: ModelOption[] }[] {
  const map = new Map<string, ModelOption[]>();
  for (const m of models) {
    const g = m.id.split('/')[0] || '其它';
    if (!map.has(g)) map.set(g, []);
    map.get(g)!.push(m);
  }
  return [...map.entries()]
    .map(([group, items]) => ({ group, items }))
    .sort((a, b) => a.group.localeCompare(b.group));
}

/** 供应商的中文友好名。 */
const GROUP_LABEL: Record<string, string> = {
  clinepass: 'ClinePass',
  jiyuan: '基元',
  jiyuan2: '基元 2 号',
  group: '自动路由',
  openai: 'OpenAI 兼容',
};

export default function ModelPicker({ value, onChange, disabled }: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [saving, setSaving] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const load = async (refresh = false) => {
    setLoading(true);
    try {
      const r = await fetchModels(refresh);
      setModels(r.models);
      setError(r.error);
    } catch (e) {
      setError((e as Error).message || '拉取模型列表失败');
    } finally {
      setLoading(false);
    }
  };

  // 首次挂载拉一次（不必等用户点开，好让按钮上直接显示当前模型名）
  useEffect(() => { void load(false); }, []);

  // 点外面 / 按 Esc 关闭
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => { if (open) setTimeout(() => searchRef.current?.focus(), 30); }, [open]);

  const current = models.find((m) => m.id === value);
  const buttonLabel = current?.label || (value ? value.split('/').pop() || value : '默认模型');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return models;
    return models.filter((m) => m.id.toLowerCase().includes(q) || m.label.toLowerCase().includes(q));
  }, [models, query]);

  const sections = useMemo(() => groupModels(filtered), [filtered]);

  const pick = async (m: ModelOption | null) => {
    setOpen(false);
    setSaving(true);
    try {
      await selectModel(m ? m.id : '');
      onChange(m ? m.id : '');
    } catch (e) {
      setError((e as Error).message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="model-picker" ref={rootRef}>
      <button
        type="button"
        className={`model-picker-btn ${open ? 'is-open' : ''}`}
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled || saving}
        title={value ? `当前模型：${value}` : '使用默认模型（在配置里指定）'}
      >
        <IconModel size={13} />
        <span className="model-picker-label">{saving ? '切换中…' : buttonLabel}</span>
        <IconCaretDown size={12} className="model-picker-caret" />
      </button>

      {open && (
        <div className="model-picker-pop" role="dialog">
          <div className="model-picker-head">
            <IconSearch size={13} />
            <input
              ref={searchRef}
              className="model-picker-search"
              placeholder="搜模型…（如 kimi / cline / auto）"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              type="button" className="model-picker-refresh" title="刷新模型列表"
              onClick={() => void load(true)}
            >
              <IconRefresh size={13} />
            </button>
          </div>

          <div className="model-picker-list">
            {loading && <div className="model-picker-note">正在读取模型列表…</div>}

            {!loading && error && (
              <div className="model-picker-note err">
                连不上中转（{error}）<br />
                请确认 magpie 已启动，然后点右上角刷新。
              </div>
            )}

            {!loading && !error && models.length === 0 && (
              <div className="model-picker-note">中转没有返回任何模型。</div>
            )}

            {!loading && !error && filtered.length === 0 && models.length > 0 && (
              <div className="model-picker-note">没匹配到「{query}」。</div>
            )}

            {!loading && !error && sections.map((sec) => (
              <div className="model-picker-group" key={sec.group}>
                <div className="model-picker-group-title">{GROUP_LABEL[sec.group] || sec.group}</div>
                {sec.items.map((m) => (
                  <button
                    type="button"
                    key={m.id}
                    className={`model-picker-item ${m.id === value ? 'is-current' : ''}`}
                    onClick={() => void pick(m)}
                    title={m.id}
                  >
                    <span className="model-picker-item-name">{m.label}</span>
                    {m.id === value && <IconCheck size={13} />}
                  </button>
                ))}
              </div>
            ))}
          </div>

          {value && (
            <button type="button" className="model-picker-reset" onClick={() => void pick(null)}>
              恢复默认模型
            </button>
          )}
        </div>
      )}
    </div>
  );
}

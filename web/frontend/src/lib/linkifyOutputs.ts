import { mediaUrl } from './api';

/**
 * 对话正文里的「产物路径」→ 前端可用链接（渲染前纯文本变换，不改存储的消息）。
 *
 * 背景：agent 回复里习惯写 `outputs/<项目>/...` 的后端相对路径，偶尔还带
 * `D:\...\outputs\...` 这类绝对路径，纯文本透传给用户 —— 不能点、图不内联，
 * 也暴露了后端存储布局。
 *
 * 规则：
 * - 图片          → markdown 内联图（/api/media 直出，卡图直接显示在对话里）
 * - 其他文件      → 可点击链接（/api/media 直开/下载，新标签页）
 * - 目录          → `#/outputs/<路径>` 锚点，由 App 监听 hashchange 跳内容库对应层级
 * - 绝对路径      → 先归一成 outputs/ 相对路径再分类（盘符 / ~ / POSIX 前缀都收敛掉）
 * - 链接目标      → 单独再归一一次，覆盖 agent 写出的 `C:\…`、`/C:/…`（带前导斜杠）、
 *                   `file:///C:/…`、以及路径里带空格的形态。没这一层时这些链接会原样
 *                   进 <a href>，浏览器按站点相对路径打开 → 7860 返回 {"detail":"Not Found"}
 * - 围栏代码块 → 原样保留；已有 markdown 链接仅转换本地产物目标
 * - 行内代码      → agent 习惯把路径包在反引号里；仅当整个 code span 就是一个
 *                   产物路径时转为链接，路径嵌在命令中（如 `py x.py --output outputs/`）则不动
 */

const IMG_RE = /\.(png|jpe?g|gif|webp|svg|avif|bmp|ico)$/i;

/** 路径尾随的标点（顿号/句号等）不属于路径本身，匹配后剥掉 */
const TRAILING = /[.,;:!~～、，。；：！？]+$/;

/** 绝对路径前缀：盘符 / ~ / POSIX 路径，后面任意层级接到 outputs 分隔符为止。
 *  lookbehind 排除字母数字:.\/\\ —— 防 `https://host/outputs/` 这类 URL 被误伤。
 *  `\/?` 放在盘符前：agent 有时把路径写成 `/C:/Users/.../outputs/x`（前导斜杠 +
 *  盘符），浏览器会当成站点内路径，必须一起收敛。 */
const ABS_PREFIX = /(?<![A-Za-z0-9:.\/\\])(?:\/?[A-Za-z]:[\\/]|~|\/)(?:[^\s`|~～<>"'“”‘’，。；：！？、（）【】《》「」]*?[\\/])?outputs[\\/]/g;

/** outputs/ 之后允许出现在路径里的字符：排除空白、反引号、ASCII 竖线/波浪/括号引号、
 *  常见中西文标点（它们常紧跟路径出现，如「outputs/a.png，共 7 张」里的逗号）。
 *  注意必须放行全角竖线 ｜ 和反斜杠 —— 项目目录名与 Windows 相对路径会用到。 */
const REST = '[^\\s`|~～<>"\'“”‘’()\\[\\]{}:,;，。；：！？、（）【】《》「」]';
/** 同 ABS_PREFIX 的 lookbehind：前一个字符不能是字母数字/:/./— 否则任何恰好含
 *  「outputs/」子串的外部 URL 或无关路径（如 https://x.com/docs/outputs/readme）
 *  都会被当成本项目产物路径，把原文搅烂。 */
const OUT_RE = new RegExp(`(?<![A-Za-z0-9:.\\/])outputs\\/(?:${REST})+`, 'g');

/** 将代码与已有链接独立分段，避免把链接标签和代码当作裸路径处理。 */
const PROTECT = /(```[\s\S]*?```|`[^`\n]*`|\[[^\]]*\]\([^)\n]*\))/g;

function toMediaUrl(outputsPath: string): string {
  const rel = outputsPath.replace(/^outputs\//, '');
  return mediaUrl(rel);
}

function toJumpUrl(outputsPath: string): string {
  const rel = outputsPath.replace(/^outputs\//, '').replace(/\/+$/, '');
  return `#/outputs/${rel.split('/').map(encodeURIComponent).join('/')}`;
}

/** 可直出的媒体/预览类型：这些走 /api/media 原样输出，其余交给内容库预览。 */
const PREVIEWABLE_RE = /\.(?:png|jpe?g|gif|webp|svg|avif|bmp|ico|html?|mp4|webm|mov|m4v|mp3|wav|ogg|m4a|flac|pdf)$/i;

/** `outputs/` 这一段（含前面的分隔符）。要求分隔符边界，避免把 myoutputs/ 当成产物目录。 */
const OUTPUTS_SEG = /(?:^|[\\/])outputs[\\/]/i;

/**
 * 从任意本地绝对路径里截出 `outputs/` 之后的相对路径（不含 outputs/ 前缀）。
 * 覆盖 agent 实际会写出来的几种形态：
 *   `C:\a b\outputs\x.png`、`C:/a b/outputs/x.png`、`/C:/a b/outputs/x.png`、
 *   `file:///C:/a b/outputs/x.png`、`~/a/outputs/x.png`、`/home/a/outputs/x.png`
 * 不是产物路径 → null。这里**允许空格**（「WorkBuddy AI」这类目录名就有空格），
 * 所以调用方必须是「目标串已经是完整路径」的场合（链接目标 / 整段代码），
 * 不能用在自由正文里 —— 正文会一路吃到后面的句子。
 */
export function relFromLocalPath(raw: string): string | null {
  let t = (raw || '').trim();
  if (!t) return null;
  t = t.replace(/^file:\/{2,}/i, '');        // file:///C:/… → C:/…
  t = t.replace(/\\/g, '/');                 // 反斜杠统一成 /
  t = t.replace(/^\/(?=[A-Za-z]:\/)/, '');   // /C:/… → C:/…
  const m = OUTPUTS_SEG.exec(t);
  if (!m) return null;
  return t.slice(m.index + m[0].length);
}

/**
 * 链接目标 → `outputs/<相对路径>` token；不是本地产物路径就返回 null。
 * 外部 URL / 已转换的锚点 / /api 路由一律放过。
 */
function toOutputsToken(raw: string): string | null {
  let t = (raw || '').trim();
  if (!t) return null;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(t) && !/^file:\/\//i.test(t)) return null;
  if (t.startsWith('//') || t.startsWith('#') || t.startsWith('/api/')) return null;
  try { t = decodeURIComponent(t); } catch { return null; }   // %ZZ / 孤立代理项
  const rel = relFromLocalPath(t);
  return rel === null ? null : `outputs/${rel}`;
}

/** 相对路径本身是否合法（不许跳出 outputs/）。 */
function isSafeRel(rel: string): boolean {
  return !!rel && !rel.split('/').some(part => part === '..' || part === '.');
}


/** 单个 outputs 路径 token → markdown 链接 / 内联图。
 *  面向用户的文案用产品词汇：目录是「内容库 › 相对位置」，文件只显示文件名，
 *  `outputs/` 这类后端存储术语不再出现在界面上。 */
function renderPath(token: string): string {
  const t = token.replace(/\\/g, '/').replace(TRAILING, '');
  if (t === 'outputs/' || t === 'outputs') return token;
  try {
    // 目录：显式尾斜杠，或末段没有「.扩展名」
    const isDir = t.endsWith('/') || !/\/[^/]+\.[A-Za-z0-9]{1,8}$/.test(t);
    if (isDir) {
      const rel = t.replace(/^outputs\//, '').replace(/\/+$/, '');
      if (!rel) return token;
      return `[内容库 › ${rel}](${toJumpUrl(t)})`;
    }
    const name = t.slice(t.lastIndexOf('/') + 1);
    if (IMG_RE.test(t)) return `![${name}](${toMediaUrl(t)})`;
    return `[${name}](${toMediaUrl(t)})`;
  } catch {
    // encodeURIComponent 对孤立代理项等畸形字符会抛 URIError；这是渲染路径（useMemo
    // 同步执行），抛出去会打断整个消息气泡乃至整个对话页面。一段路径编不出链接，
    // 就原样展示那段文字，不能因为一个字符把整条消息渲染搞崩。
    return token;
  }
}

function transformSegment(seg: string): string {
  if (!seg.includes('outputs')) return seg;   // 快速路径
  let s = seg.replace(ABS_PREFIX, 'outputs/');
  s = s.replace(/outputs[\\/]/g, 'outputs/'); // 相对路径里的反斜杠分隔符
  return s.replace(OUT_RE, renderPath);
}

/** 整体恰好是一个产物路径（用于判定行内代码能否安全转链） */
const WHOLE_PATH_RE = new RegExp(`^outputs\\/(?:${REST})+$`);
function asWholePath(codeSpan: string): string | null {
  const inner = codeSpan.slice(1, -1).trim();
  if (!inner) return null;
  const token = toOutputsToken(inner);
  if (!token) return null;
  return WHOLE_PATH_RE.test(token) ? token : null;
}

/** 已有链接也可能由 agent 写成绝对文件路径，不能作为网站路径直接打开。
 *  以前只处理 `C:\…` 这种裸盘符形态，于是 `/C:/Users/…/outputs/…`（带前导斜杠）
 *  被原样留在 href 里 → 浏览器按站点相对路径请求 7860 → {"detail":"Not Found"}。 */
function transformLink(seg: string): string {
  const match = seg.match(/^(\[[^\]]*\])\((?:<([^>]+)>|([^\s]+))\)$/);
  if (!match) return seg;
  const token = toOutputsToken(match[2] || match[3]);
  if (!token) return seg;
  const rel = token.slice('outputs/'.length).replace(/\/+$/, '');
  if (!isSafeRel(rel)) return seg;
  // 文件进入内容库预览；图片/网页/音视频保留媒体目标，以便已有 ![图片] 正常内联。
  try {
    const url = PREVIEWABLE_RE.test(rel) ? toMediaUrl(token) : toJumpUrl(token);
    return `${match[1]}(${url})`;
  } catch {
    return seg;
  }
}

/** 入口：整段 markdown 先按受保护区切开；普通文本段做变换，
 *  行内代码仅在「整体就是产物路径」时整体替换为链接。 */
export function linkifyOutputs(md: string): string {
  if (!md || !md.includes('outputs')) return md;
  return md
    .split(PROTECT)
    .map((seg, i) => {
      if (i % 2 === 0) return transformSegment(seg);
      if (seg.length > 2 && seg.startsWith('`') && !seg.startsWith('```')) {
        const whole = asWholePath(seg);
        if (whole) return renderPath(whole);
      }
      return transformLink(seg);
    })
    .join('');
}

/** 渲染后的 HTML 里，把指向 /api/media 的链接改为新标签打开（点文件不离开工作台）。 */
export function externalizeMediaLinks(html: string): string {
  return html.replace(
    /<a href="([^"]*\/api\/media\/[^"]*)"/g,
    '<a href="$1" target="_blank" rel="noreferrer"',
  );
}

/**
 * 兜底：渲染完的 HTML 里若还残留「本地绝对路径」的 href/src（markdown 链接带 title、
 * 引用式链接、表格里的链接等 transformLink 没覆盖到的写法），就地改写成站内地址。
 * 不改写的话浏览器会把它当站点相对路径，点开就是 7860 的 {"detail":"Not Found"}。
 */
export function rewriteLocalLinks(html: string): string {
  if (!html || !html.includes('outputs')) return html;
  return html.replace(
    /(<(?:a|img)\b[^>]*?\b(?:href|src)=")([^"]+)(")/gi,
    (whole, pre: string, url: string, post: string) => {
      const token = toOutputsToken(url);
      if (!token) return whole;
      const rel = token.slice('outputs/'.length).replace(/\/+$/, '');
      if (!isSafeRel(rel)) return whole;
      try {
        const keepMedia = /^<img/i.test(whole) || PREVIEWABLE_RE.test(rel);
        return pre + (keepMedia ? toMediaUrl(token) : toJumpUrl(token)) + post;
      } catch {
        return whole;
      }
    },
  );
}

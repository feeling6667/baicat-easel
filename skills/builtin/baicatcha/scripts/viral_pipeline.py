#!/usr/bin/env python3
"""viral_pipeline.py — 爆款转手绘图文故事（baicatcha）的确定性流水线。

职责边界：LLM 负责爆点拆解 / 复刻改写 / 分镜与配图文案的创作（产出 JSON），
本脚本负责所有机械环节：输入归一化、yt-dlp 下载 + faster-whisper 转写、
FB-060 prompt 拼装与 ai_image 生图、文本纪律校验、产物落盘、/api/schedule 入队。
除 yt_dlp / faster-whisper / cv2 可选能力外纯标准库；生图复用 skills/shared/scripts/ai_image.py。

子命令（均支持 -h）：
  check_env   环境体检：python / yt_dlp / faster_whisper / cv2 / 生图与对话配置（脱敏）
  style       核对写死的风格与字体（零成本，不发请求）；带 --dir 可打印每页最终 prompt
  detect      输入归一化判定：url / text / topic
  ingest      建产物目录 outputs/viral-<yyyymmdd-hhmm>/；url 分支下载+转写，失败降级退出码 3
  storyboard  校验 storyboard.json（页标题≤12字、页内短句≤2条）→ storyboard.md + pages.json
  gen_pages   按 FB-060 模板逐页生图（3:4，1080x1440）→ images/page_N.png
  copy        校验 copy.json（标题需带 emoji）→ copy.md
  enqueue     POST /api/schedule（kind=content, status=draft, source=chat, platform=xiaohongshu）
  finish      产物完整性校验 + manifest 汇总 + 打印目录树

退出码约定：0 成功；2 参数/输入问题；3 解析失败（提示用户直接粘贴口播文案）；
4 生图全失败；5 入队失败（附手工 POST 命令）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ── 环境基础 ────────────────────────────────────────────────
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
ASSETS_DIR = SKILL_DIR / "assets"
FB060_JSON = ASSETS_DIR / "fb060-frazee.json"
FB060_REF = ASSETS_DIR / "FB-060.webp"


def _find_root() -> Path:
    configured = os.environ.get("EASEL_ROOT", "").strip()
    if configured:
        return Path(configured).resolve()
    # scripts/ → baicatcha → builtin → skills → 仓库根
    return SCRIPT_DIR.parents[3]


ROOT = _find_root()
os.environ.setdefault("EASEL_ROOT", str(ROOT))
SHARED = ROOT / "skills" / "shared" / "scripts"
SCHED_API = os.environ.get("EASEL_API_URL", "http://localhost:7860").rstrip("/") + "/api/schedule"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

ASR_MODEL = os.environ.get("VIRAL_ASR_MODEL", "base")
IMG_SIZES = ["3:4", "768x1024", "1024x1536"]  # 依次尝试，网关不认比例写法时落到像素
CANVAS_W, CANVAS_H = 1080, 1440

# 模型降级链兜底：网关 /v1/models 探测失败（网络抖动/权限不足）时用这份静态清单，
# 顺序按「先基线、后变体」——gpt-image-2 是 FB-060 风格校准的基线模型，风格保真度最好。
IMG_MODEL_FALLBACK = ["gpt-image-2.5", "gpt-image-2.5-flare", "gpt-image-2"]
# 网关动态模型清单的排序偏好：序号越小越先用；未列出的排最后。
IMG_MODEL_PRIORITY = ["gpt-image-2", "gpt-image-2.5", "gpt-image-2.5-flare"]

EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")

# ── 风格 / 字体写死（hard lock）─────────────────────────────
# 唯一来源是 assets/fb060-frazee.json；下面这些常量只做「校验 + 兜底」：
# 一旦 JSON 被换掉或漏字段，脚本直接报错退出，而不是悄悄用别的风格/字体出图。
STYLE_LOCK_INDEX = "FB-060"          # 只允许这一个风格编号
STYLE_LOCK_FIELDS = ("style_lock", "typography", "negative_prompt")
# 字体分两档：封面用 cover 档，正文/收尾用 body 档（乐米蜡笔体）。kind → 档位。
FONT_SLOT_BY_KIND = {"cover": "cover", "content": "body", "cta": "body"}
FONT_SLOT_DEFAULT = "body"
# 兜底串：仅在 JSON 的 typography 缺字段时使用（正常情况下以 JSON 为准）。
FONT_LOCK_COVER_FALLBACK = (
    "中文一律使用手写马克笔粗楷体：笔画圆润饱满、起收笔有笔锋、横细竖粗、略带手写歪斜、字距紧凑；"
    "墨黑 #1F1B16；关键词用同款手写体加粗填正红 #D93A2B（每页最多 1-2 个词）；"
    "禁止印刷体/宋体/黑体、禁止描边发光渐变立体字、禁止错别字与糊字。"
)
FONT_LOCK_BODY_FALLBACK = (
    "中文一律使用乐米蜡笔体式儿童蜡笔手写体（LemiLaBiTi）：笔画圆润厚实、粗细不匀、边缘带蜡笔毛感、"
    "字形略歪斜不工整、字距宽松；无衬线、无楷体毛笔的顿笔飞白；墨黑 #1F1B16；"
    "关键词用同款蜡笔手写体加粗填正红 #D93A2B（每页最多 1-2 个词）；"
    "禁止印刷体/宋体/黑体、禁止描边发光渐变立体字、禁止错别字与糊字。"
)
FONT_LOCK_FALLBACK = {"cover": FONT_LOCK_COVER_FALLBACK, "body": FONT_LOCK_BODY_FALLBACK}
# 文字硬性契约兜底（JSON 里 typography.hard_contract 缺失时用这份，语气与 JSON 一致）
TEXT_HARD_CONTRACT_FALLBACK = (
    "标题、关键词、短句必须同一支笔同一笔触；关键词只换颜色不换字形，短句只换字号不换字形。",
    "所有小字（短句/副标题/页脚）必须保持与标题相同的手写笔触，不许因为字小就变成印刷体/黑体/像素体/矢量字。",
    "字要长在纸上：笔画与纸纹、水彩同层，有颜料渗透与飞白；不许扁平字层、纯色平滑填字、锐利矢量边、抠图贴字感。",
    "不许叠字：每字只出现一次，禁重影/双影/叠印/字符重复；字不压字（字距≥字宽 12%），行不压行（行高≥字高 1.35 倍），标题与短句至少隔 1 个字高。",
    "文字只占画面上部 ≤35%，不压主体；放不下就缩小字号或减少字数，绝不叠压、绝不裁切、绝不换字体。",
    "关键词只改颜色：正红 #D93A2B 只换色，笔触粗细字形与同排其余字完全一致；禁加粗、禁换体、禁描边、禁底色块。",
)
NEGATIVE_LOCK_FALLBACK = "照片写实、3D 渲染、矢量描边、印刷体中文字、错别字、描边发光字、水印 logo、暗色霓虹背景"


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _load_json_file(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        die(f"JSON 文件不存在：{path}", 2)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))  # 容忍 Windows 工具的 UTF-8 BOM
    except (OSError, ValueError) as e:
        die(f"JSON 解析失败（{path}）：{e}", 2)
    if not isinstance(data, dict):
        die(f"JSON 顶层必须是对象：{path}", 2)
    return data


def _dump_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _manifest_path(out_dir: Path) -> Path:
    return out_dir / "manifest.json"


def _read_manifest(out_dir: Path) -> dict:
    p = _manifest_path(out_dir)
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            pass
    return {}


def _merge_manifest(out_dir: Path, **fields) -> dict:
    mf = _read_manifest(out_dir)
    for k, v in fields.items():
        if k in ("warnings", "errors") and isinstance(v, list):
            mf.setdefault(k, [])
            mf[k] = list(dict.fromkeys(mf[k] + v))
        else:
            mf[k] = v
    _dump_json(_manifest_path(out_dir), mf)
    return mf


def _new_output_dir() -> Path:
    outputs = ROOT / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    base = f"viral-{time.strftime('%Y%m%d-%H%M')}"
    out = outputs / base
    n = 2
    while out.exists():
        out = outputs / f"{base}-{n}"
        n += 1
    out.mkdir(parents=True)
    return out


# ── 输入归一化 ─────────────────────────────────────────────
URL_RE = re.compile(r"https?://[^\s\"'）】>，。；]+", re.I)


def classify_input(raw: str) -> dict:
    text = raw.strip()
    m = URL_RE.search(text)
    if m:
        return {"branch": "url", "url": m.group(0), "raw_len": len(text)}
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(text) >= 60 or len(lines) >= 2:
        return {"branch": "text", "raw_len": len(text), "lines": len(lines)}
    return {"branch": "topic", "raw_len": len(text)}


def cmd_detect(a) -> int:
    raw = _read_input_arg(a)
    print(json.dumps(classify_input(raw), ensure_ascii=False, indent=2))
    return 0


def _read_input_arg(a) -> str:
    if getattr(a, "input_file", None):
        p = Path(a.input_file)
        if not p.is_file():
            die(f"输入文件不存在：{a.input_file}", 2)
        return p.read_text(encoding="utf-8")
    if getattr(a, "input_raw", None) is not None:
        return a.input_raw
    if not sys.stdin.isatty():
        return sys.stdin.read()
    die("缺少输入：--input-raw / --input-file / stdin 三选一", 2)
    return ""


# ── 视频链接：下载 + 转写 ──────────────────────────────────
def _download_audio(url: str, work_dir: Path):
    """yt_dlp 下载音频轨，返回 ((audio_path, title), err)。"""
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return None, "yt_dlp 不可导入（需用 .venv python 运行本脚本）"
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(work_dir / "audio.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
    }
    if shutil.which("ffmpeg"):
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}
        ]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = (info or {}).get("title", "")
    except Exception as e:  # noqa: BLE001 — yt_dlp 异常类型多，统一降级
        return None, f"{type(e).__name__}: {str(e)[:300]}"
    files = sorted(work_dir.glob("audio.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files or files[0].stat().st_size < 1024:
        return None, "下载完成但音频文件缺失或过空"
    return (files[0], title), None


def _transcribe(audio: Path, transcript_path: Path):
    """复用 shared/scripts/asr.py 转写，返回 (ok, err)。"""
    cmd = [
        sys.executable, str(SHARED / "asr.py"), "transcribe",
        "-i", str(audio), "-o", str(transcript_path),
        "--format", "txt", "--model", ASR_MODEL, "--language", "zh",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1500,
                              cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return False, "转写超时（1500s）"
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
        return False, f"asr.py 退出码 {proc.returncode}：{tail[:300]}"
    if not transcript_path.is_file() or not transcript_path.read_text(encoding="utf-8").strip():
        return False, "转写结果为空（可能是静音/纯音乐）"
    return True, ""


def cmd_ingest(a) -> int:
    raw = _read_input_arg(a)
    cls = {"branch": a.branch, "url": a.url or ""} if a.branch else classify_input(raw)
    if cls["branch"] == "url" and not cls.get("url"):
        cls["url"] = raw.strip()
    out_dir = _new_output_dir()
    (out_dir / "images").mkdir()
    meta = {"branch": cls["branch"], "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _merge_manifest(out_dir, tool="baicatcha", version=1,
                    style={"display_name": "FB-060 · 绘本水彩",
                           "asset": str(FB060_JSON.relative_to(ROOT)).replace("\\", "/")},
                    input=meta, warnings=[], errors=[], status="ingested")

    if cls["branch"] == "url":
        url = cls["url"]
        print(f"[ingest] 视频链接：{url}")
        raw_dir = out_dir / "raw"
        raw_dir.mkdir(exist_ok=True)
        got, err = _download_audio(url, raw_dir)
        if err or got is None:
            _merge_manifest(out_dir, status="parse_failed", errors=[f"视频解析失败：{err}"])
            print(f"产物目录（含失败记录）：{out_dir}", file=sys.stderr)
            print(f"解析失败（{err}）。请直接粘贴口播文案重新执行，不要卡住。", file=sys.stderr)
            return 3
        audio, title = got
        print(f"[ingest] 音频已下载：{audio.name}（{audio.stat().st_size // 1024} KB）")
        tr = out_dir / "transcript.txt"
        ok, terr = _transcribe(audio, tr)
        if not ok:
            _merge_manifest(out_dir, status="parse_failed", errors=[f"转写失败：{terr}"])
            print(f"产物目录（含失败记录）：{out_dir}", file=sys.stderr)
            print(f"解析失败（{terr}）。请直接粘贴口播文案重新执行，不要卡住。", file=sys.stderr)
            return 3
        text = tr.read_text(encoding="utf-8").strip()
        (out_dir / "source.txt").write_text(text, encoding="utf-8")
        _merge_manifest(out_dir, source={"kind": "url", "url": url, "video_title": title,
                                         "transcript_chars": len(text)})
        print(f"[ingest] 转写完成：{len(text)} 字 → {tr.name}")
    elif cls["branch"] == "text":
        (out_dir / "source.txt").write_text(raw.strip(), encoding="utf-8")
        _merge_manifest(out_dir, source={"kind": "text", "chars": len(raw.strip())})
        print(f"[ingest] 粘贴文案：{len(raw.strip())} 字")
    else:
        (out_dir / "source.txt").write_text(raw.strip(), encoding="utf-8")
        _merge_manifest(out_dir, source={"kind": "topic", "topic": raw.strip()})
        print(f"[ingest] 热点标题/主题：{raw.strip()}（走「围绕主题的爆款结构生成」分支）")

    _merge_manifest(out_dir, status="ready")
    print(str(out_dir))
    return 0


# ── 拆解 + 复刻 + 分镜（校验 storyboard.json）───────────────
def _clip(s: str, n: int, warnings: list, what: str) -> str:
    s = " ".join(str(s).split())
    if len(s) > n:
        warnings.append(f"{what} 超长自动截断到 {n} 字：{s[:30]}…")
        s = s[:n]
    return s


def _page_kind(page: dict, idx: int, total: int) -> str:
    k = str(page.get("kind", "")).strip().lower()
    if k in ("cover", "content", "cta"):
        return k
    return "cover" if idx == 0 else ("cta" if idx == total - 1 else "content")


def _norm_characters(raw, warnings: list) -> list:
    """校验 storyboard.json 的 characters（人物一致性锁）。缺失/空 → []。

    每项必须 name + （look_zh 或 look_en）；落进 pages.json 后由 gen_pages 逐页注入 prompt。
    外观写得越具体（发型/发色/脸型/服装款式与颜色/体型/年龄），跨页越稳。
    """
    if not raw:
        return []
    if not isinstance(raw, list):
        die("storyboard.json 的 characters 必须是数组（每项至少 name + look_zh/look_en）", 2)
    out = []
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            die(f"characters[{i}] 不是对象", 2)
        name = str(c.get("name", "")).strip()
        look_zh = str(c.get("look_zh", "")).strip()
        look_en = str(c.get("look_en", "")).strip()
        if not name:
            die(f"characters[{i}].name 为空（人物锁需要角色名）", 2)
        if not (look_zh or look_en):
            die(f"characters[{i}]（{name}）缺少外观设定 look_zh / look_en", 2)
        out.append({"name": name, "role": str(c.get("role", "")).strip(),
                    "look_zh": look_zh, "look_en": look_en})
    names = [c["name"] for c in out]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        warnings.append(f"characters 名字重复：{'、'.join(sorted(dup))}（人物锁按第一条生效）")
    return out


def cmd_storyboard(a) -> int:
    out_dir = Path(a.dir)
    data = _load_json_file(a.json)
    warnings: list = []

    analysis = data.get("analysis")
    if not isinstance(analysis, dict):
        die("storyboard.json 缺少 analysis（爆点结构拆解表：hook/conflict/density/golden/cta）", 2)
    for key in ("hook", "conflict", "density", "golden", "cta"):
        if not str(analysis.get(key, "")).strip():
            die(f"analysis.{key} 为空；topic 分支可用「主题→爆款结构生成」标注，但不能留空", 2)

    rewrite = data.get("rewrite")
    if not isinstance(rewrite, dict):
        die("storyboard.json 缺少 rewrite（复刻结构的全改写文案：hook/buildup/expand/golden/cta）", 2)
    for key in ("hook", "buildup", "expand", "golden", "cta"):
        if not str(rewrite.get(key, "")).strip():
            die(f"rewrite.{key} 为空", 2)

    pages_raw = data.get("pages")
    if not isinstance(pages_raw, list) or not pages_raw:
        die("storyboard.json 缺少 pages 数组（至少 1 页）", 2)

    theme_color = str(data.get("theme_color", "")).strip() or "米白纸底 + 明黄/正红小面积重色"
    characters = _norm_characters(data.get("characters"), warnings)
    known = {c["name"] for c in characters}
    pages = []
    total = len(pages_raw)
    for i, p in enumerate(pages_raw):
        if not isinstance(p, dict):
            die(f"pages[{i}] 不是对象", 2)
        title = " ".join(str(p.get("title", "")).split())
        if not title:
            die(f"pages[{i}].title 为空（页标题必填）", 2)
        lines = p.get("lines", [])
        if lines is None:
            lines = []
        if not isinstance(lines, list):
            die(f"pages[{i}].lines 必须是数组", 2)
        lines = [" ".join(str(x).split()) for x in lines if str(x).strip()]
        visual_zh = str(p.get("visual_zh", "")).strip()
        visual_en = str(p.get("visual_en", "")).strip()
        if not visual_zh and not visual_en:
            die(f"pages[{i}] 缺少画面描述（visual_zh / visual_en 至少填一个）", 2)
        cast = [str(x).strip() for x in (p.get("cast") or []) if str(x).strip()]
        if cast and known:
            unknown = [n for n in cast if n not in known]
            if unknown:
                warnings.append(f"pages[{i}].cast 里有未在 characters 定义的角色：{'、'.join(unknown)}（已忽略）")
                cast = [n for n in cast if n in known]
        pages.append({
            "page": i + 1,
            "kind": _page_kind(p, i, total),
            "title": title,
            "cast": cast,
            "lines": lines,
            "visual_zh": visual_zh,
            "visual_en": visual_en,
            "theme_color": str(p.get("theme_color", "")).strip() or theme_color,
        })

    topic = str(data.get("topic", "")).strip() or "（未命名主题）"
    source_summary = str(data.get("source_summary", "")).strip()

    # storyboard.md：拆解表 + 复刻对照 + 分镜表
    md = [
        f"# 图文故事分镜 · {topic}", "",
        f"- 产物目录：`outputs/{out_dir.name}/`",
        f"- 画质风格：FB-060 · 绘本水彩（3:4，1080x1440）",
        f"- 原文摘要：{source_summary or '（略）'}", "",
        "## 一、爆点结构拆解表", "",
        "| 维度 | 原文识别 |",
        "|------|---------|",
        f"| 开头钩子 | {analysis['hook']} |",
        f"| 冲突铺垫 | {analysis['conflict']} |",
        f"| 信息密度节奏 | {analysis['density']} |",
        f"| 金句 | {analysis['golden']} |",
        f"| 结尾互动句式 | {analysis['cta']} |", "",
        "## 二、复刻结构 · 内容全重写", "",
        "| 节奏位 | 改写后文案 |",
        "|--------|-----------|",
        f"| 钩子 | {rewrite['hook']} |",
        f"| 铺垫 | {rewrite['buildup']} |",
        f"| 展开 | {rewrite['expand']} |",
        f"| 金句 | {rewrite['golden']} |",
        f"| 收尾互动 | {rewrite['cta']} |", "",
        f"全局主题色：{theme_color}", "",
        "## 三、分镜表", "",
        "| 页 | 类型 | 页标题(≤12字) | 页内短句(≤2条) | 画面描述(中) | 画面描述(英) | 主题色 |",
        "|----|------|--------------|----------------|-------------|-------------|--------|",
    ]
    for pg in pages:
        md.append("| {page} | {kind} | {title} | {lines} | {vz} | {ve} | {color} |".format(
            page=pg["page"], kind=pg["kind"], title=pg["title"],
            lines="<br>".join(pg["lines"]) or "—",
            vz=pg["visual_zh"] or "—", ve=pg["visual_en"] or "—", color=pg["theme_color"]))
    md.append("")

    # 人物一致性：characters 可选，但一旦给了就逐页锁定（storyboard → pages.json → gen_pages）
    if characters:
        md += ["## 四、人物一致性锁（跨页锁定，逐页注入 prompt）", "",
               "| 角色 | 定位 | 外观设定（每页必须一致） |", "|------|------|--------------------------|"]
        for c in characters:
            md.append(f"| {c['name']} | {c.get('role', '—')} | {c.get('look_zh', '') or c.get('look_en', '')} |")
        md.append("")

    (out_dir / "storyboard.md").write_text("\n".join(md), encoding="utf-8")
    _dump_json(out_dir / "pages.json", {"topic": topic, "source_summary": source_summary,
                                        "theme_color": theme_color, "characters": characters,
                                        "pages": pages})
    mf = _merge_manifest(out_dir, topic=topic, analysis=analysis, rewrite=rewrite,
                         pages=pages, characters=characters,
                         warnings=warnings, status="storyboarded")
    print(f"storyboard 落盘：{len(pages)} 页"
          f"（人物锁：{'、'.join(c['name'] for c in characters) if characters else '未设定'}）"
          f" → storyboard.md / pages.json")
    for w in warnings:
        print(f"  [warn] {w}", file=sys.stderr)
    print(str(out_dir))
    return 0


# ── FB-060 生图 ────────────────────────────────────────────
def _ai_image():
    sys.path.insert(0, str(SHARED))
    import ai_image  # noqa: PLC0415
    ai_image.load_env_file(ROOT / ".env")
    return ai_image


# ── 生图通道（方案二 / 方案三）──────────────────────────────
# 方案一 shim：本地 banana-shim（浏览器自动化，1312 免费额度）
# 方案二 relay：第三方中转 OpenAI 兼容接口，key 在 .env 的 IMG_RELAY_* 三键
CHANNELS = {"shim": "本地 shim", "relay": "中转 API"}


def _pick_channel(ai_image, channel: str) -> dict:
    """按通道取 base_url / model / api_key；配置缺失直接 die，不猜、不回退到另一条通道。

    回退会掩盖配置错误：想让用户用中转，结果悄悄走 shim 出了图，用户会以为中转通了。
    """
    channel = (channel or "shim").strip().lower()
    if channel not in CHANNELS:
        die(f"未知生图通道 {channel}（可选：{'/'.join(CHANNELS)}）", 2)
    if channel == "relay":
        keys = ("IMG_RELAY_BASE_URL", "IMG_RELAY_MODEL", "IMG_RELAY_API_KEY")
        vals = []
        for k in keys:
            v, src = ai_image.resolve_config(k)
            v = (v or os.environ.get(k, "")).strip()
            if not v:
                die(f"通道 relay 缺 {k}。在 .env 里补：\n"
                    f"  IMG_RELAY_BASE_URL=https://<中转域名>/v1\n"
                    f"  IMG_RELAY_MODEL=<中转上的图模型名>\n"
                    f"  IMG_RELAY_API_KEY=<中转的 key>\n"
                    f"（browser 自动化走方案一，把 --via 换成 shim 即可）", 2)
            vals.append(v)
        base, model, key = vals
        return {"channel": channel, "base_url": base.rstrip("/"),
                "model": model, "api_key": key, "env": keys}
    return {"channel": "shim",
            "base_url": ai_image.require_config(ai_image.ENV_BASE_URL).rstrip("/"),
            "model": ai_image.require_config(ai_image.ENV_MODEL),
            "api_key": ai_image.require_config(ai_image.ENV_API_KEY),
            "env": (ai_image.ENV_BASE_URL, ai_image.ENV_MODEL, ai_image.ENV_API_KEY)}


def _load_locked_style() -> dict:
    """读取并校验「写死」的风格资产：编号必须等于 STYLE_LOCK_INDEX，且锁字段齐全。

    这是风格的唯一入口。任何换风格/漏字段都会被硬拦在这里，不会带着别的风格去生图。
    """
    style = _load_json_file(str(FB060_JSON))
    idx = str(style.get("style_index", "")).strip()
    if idx != STYLE_LOCK_INDEX:
        die(f"风格资产与写死编号不符：期望 {STYLE_LOCK_INDEX}，实际 {idx or '(缺失)'}。"
            f"风格写死在 {FB060_JSON}，本技能不允许换风格。", 2)
    missing = [k for k in STYLE_LOCK_FIELDS if not style.get(k)]
    if missing:
        die(f"风格资产缺少写死字段 {missing}（风格锁/字体锁/负向约束）：{FB060_JSON}", 2)
    return style


def _font_slot(kind: str) -> str:
    """页面 kind → 字体档位：cover→cover（手写马克笔粗楷体），content/cta→body（乐米蜡笔体）。"""
    return FONT_SLOT_BY_KIND.get(str(kind or "").strip().lower(), FONT_SLOT_DEFAULT)


def _locked_contract(style: dict) -> str:
    """把「文字硬性契约」渲染成带编号的一段——编号 + "违反即判废图" 的语气对模型最有效。"""
    rules = (style.get("typography") or {}).get("hard_contract") or TEXT_HARD_CONTRACT_FALLBACK
    clean = [str(r).strip().rstrip("。；;.") for r in rules if str(r).strip()]
    if not clean:
        clean = list(TEXT_HARD_CONTRACT_FALLBACK)
    return "；".join(f"{i}) {r}" for i, r in enumerate(clean, 1))


def _locked_typography(style: dict, kind: str = "content") -> str:
    """把「写死字体 + 文字硬性契约」渲染成一段可直接进 prompt 的中文，按页面 kind 选档。

    兼容两种资产结构：新结构 typography.cover / typography.body（分档），
    旧结构 typography.zh_heading（单档，等价于 cover）。字段缺失时用兜底串。
    输出四段：字体 / 短句 / 硬性契约（编号）/ 禁止项——短句必须单独点名，
    否则模型最容易在小字上掉回机打体（实测过的老问题）。
    """
    t = style.get("typography") or {}
    slot = _font_slot(kind)
    shared = t.get("shared_rules") or {}
    block = t.get(slot) if isinstance(t.get(slot), dict) else None
    if block is None and isinstance(t.get("cover"), dict):
        block = t.get("cover")
    if block is None:
        block = t if t.get("zh_heading") else {}

    if not block.get("zh_heading"):
        head = FONT_LOCK_FALLBACK.get(slot, FONT_LOCK_BODY_FALLBACK)
        return f"【字体（写死）】{head}。\n【文字硬性契约（逐条遵守，违反即判废图）】{_locked_contract(style)}"

    parts = ["【字体（写死，同一支笔写出全部文字）】" + "；".join(p for p in (
        block.get("zh_heading", ""),
        block.get("zh_heading_color", ""),
        block.get("zh_emphasis", "") or shared.get("emphasis_color", ""),
    ) if p)]
    # 封面：标题大 + 短句约 45%（有大小层次）；正文/收尾：逐字同大，不设大标题。
    if slot == "cover":
        if block.get("zh_subline"):
            parts.append("【小字短句（同款字体，只把字号降到约 45%，绝不换字体）】" + block["zh_subline"])
        parts.append("【本页字号层次】封面页：标题最大，短句约为标题 45%，同一支笔、同一笔触。")
    else:
        if block.get("zh_layout"):
            parts.append("【本页排版（写死：无大标题、逐字同大）】" + block["zh_layout"])
        if block.get("zh_subline"):
            parts.append("【字号（全页统一，不许任何一行更大或更小）】" + block["zh_subline"])
    if block.get("zh_auto_fit"):
        parts.append("【字号自适应（按本页字数，不写死像素）】" + block["zh_auto_fit"])
    elif shared.get("auto_fit_note"):
        parts.append("【字号自适应（按本页字数，不写死像素）】" + shared["auto_fit_note"])
    parts.append("【文字硬性契约（逐条遵守，违反即判废图）】" + _locked_contract(style))
    correctness = block.get("zh_correctness") or shared.get("zh_correctness")
    if correctness:
        parts.append("【字符正确性】" + correctness)
    if shared.get("zh_style_forbidden"):
        parts.append("【文字禁止项】" + "、".join(shared["zh_style_forbidden"]))
    return "\n".join(parts)


def _locked_negative(style: dict) -> str:
    neg = style.get("negative_prompt") or []
    if isinstance(neg, str):
        return neg.strip() or NEGATIVE_LOCK_FALLBACK
    return "、".join(str(x).strip() for x in neg if str(x).strip()) or NEGATIVE_LOCK_FALLBACK


# 人物一致性兜底规则（资产 character_consistency.rules 缺失时使用）
CHARACTER_FALLBACK_RULES = (
    "同一角色在每一页必须是同一张脸、同一发型与发色、同一套衣服的款式与颜色、同一体型与年龄。",
    "禁止换装、换发型、换发色、换年龄、换性别、换画风。",
    "只画本页画面描述里提到的角色，禁止凭空增加陌生人或第二个孩子。",
    "多人同框时每个角色分别固定，不得互换外观、不得混搭衣服与发型。",
)


def _render_characters(style: dict, page: dict, characters: list | None) -> str:
    """渲染「人物一致性锁」段：把 characters 台词本逐页注入 prompt。

    characters 来自 storyboard.json（→ pages.json），每项 {name, role?, look_zh/look_en}。
    页面可用 page["cast"] 指定本页出场的角色名；不指定则整份名单都列出来，
    并在末尾要求"只画画面描述里需要的角色"。没有 characters 时返回空串（不占 prompt）。
    """
    if not characters:
        return ""
    cc = style.get("character_consistency") or {}
    wanted = [str(n).strip() for n in (page.get("cast") or []) if str(n).strip()]
    cast, used = [], []
    for c in characters:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        look = str(c.get("look_zh", "")).strip() or str(c.get("look_en", "")).strip()
        if not name or not look:
            continue
        if wanted and name not in wanted:
            continue
        role = str(c.get("role", "")).strip()
        cast.append(f"{name}（{role}）：{look}" if role else f"{name}：{look}")
        used.append(name)
    if not cast:
        return ""
    rules = cc.get("rules") or CHARACTER_FALLBACK_RULES
    rules_txt = "；".join(f"{i}) {str(r).strip().rstrip('。；;.')}" for i, r in enumerate(rules, 1))
    on_page = "、".join(used) + ("" if wanted else "（本页未指定名单，只画画面描述里出现的角色）")
    tpl = cc.get("template_zh") or (
        "【人物一致性（全篇跨页锁定，违反即判废图）】{cast}。{rules}【本页可出现的人物】{on_page}。")
    return tpl.format(cast="；".join(cast), rules=rules_txt, on_page=on_page)


def _build_prompt(style: dict, page: dict, characters: list | None = None) -> str:
    """按「写死的风格 + 写死的字体（按页 kind 分档） + 人物一致性 + 负向约束」拼 prompt。

    页数据只能提供 标题 / 短句 / 画面 / 主题色 / kind / cast；风格与字体一律取自锁定资产 style，
    page 里的同名字段无法覆盖它们——这样每一页、每一轮出图的风格与字体完全一致。
    字体档位：kind=cover → 封面档（手写马克笔粗楷体）；content / cta → 正文档（乐米蜡笔体）。
    人物：characters 来自 pages.json 的角色台词本，逐页注入，保证跨页同一批人。
    """
    traits = "、".join(style["traits"])
    visual = " / ".join(x for x in (page.get("visual_zh"), page.get("visual_en")) if x)
    lines = [str(x).strip() for x in (page.get("lines") or []) if str(x).strip()]
    if _font_slot(page.get("kind", "")) == "cover":
        # 封面：大标题 + 小短句，有大小层次
        parts = [f"大字标题「{page['title']}」"]
        if lines:
            parts.append("短句「" + " / ".join(lines) + "」")
        page_text = "；".join(parts) + (
            "（封面：标题最大，目标字符高度＝画面宽度 8%（1080px 画布约 86px），"
            "短句约标题 45%；同一支笔；字号按字数自适应。"
            "基准关系：正文/收尾页字号固定为封面标题的 65%＝画面宽度 5.2%）"
        )
    else:
        # 正文/收尾：整块统一字号，按行照抄（不区分"标题/短句"，第一行也不许更大）
        rows = [page["title"]] + lines
        listed = " ／ ".join('第{}行「{}」'.format(i, r) for i, r in enumerate(rows, 1))
        page_text = (
            "本页文字（同一字号的整块文字，共 " + str(len(rows)) + " 行，照抄不得增删字）："
            + listed + "。"
            + "字号规则：每行字符高度必须相等（误差不超过 3%），目标字符高度等于封面标题字号的 65%"
            + "（封面标题＝画面宽度 8%，故本页＝画面宽度 5.2%，1080px 画布约 56px，比封面标题明显小一号）；"
            + "字距相等、行距相等；第一行与其它行一样大，不得放大、不得加粗；"
            + "整块只随总字数整体缩放（放不下时整体缩小，最小不小于画面宽 4.5%），不许单独放大某一行；"
            + "关键词正红 #D93A2B、只换色不换字形。"
        )
    tpl = style.get("prompt_template_zh") or ""
    return tpl.format(traits=traits, page_visual=visual,
                      theme_color=page["theme_color"], page_text=page_text,
                      typography=_locked_typography(style, page.get("kind", "")),
                      characters=_render_characters(style, page, characters),
                      negative=_locked_negative(style))


# ── Web 端报错识别（要求 5）────────────────────────────────
# 生图网关是浏览器自动化时，失败不是 HTTP 码，而是页面文案 / 语义化 error.message。
# 这里把它们归成三类，并给出「自己解决」的动作：重试 / 换模型 / 中止。
ERROR_KINDS = {
    "blocked": (
        "violates our terms", "violate our terms", "terms of use", "content policy",
        "usage policies", "flagged", "not allowed", "moderation", "safety system",
    ),
    "upstream": (
        "something went wrong", "please try again", "try again later",
        "internal error", "upstream", "overloaded", "rate limit", "too many requests",
        "timed out", "timeout",
    ),
    "auth": (
        "unauthorized", "sign in", "log in", "login required", "session expired",
        "401", "403", "captcha",
    ),
}


def _classify_error(reason: str) -> str:
    """把网关原始错误文本归到 blocked / upstream / auth / conn / unknown。"""
    low = (reason or "").lower()
    for kind in ("blocked", "auth", "upstream"):
        if any(k in low for k in ERROR_KINDS[kind]):
            return kind
    if any(k in low for k in ("无法连接", "connection", "actively refused", "10061", "econnrefused")):
        return "conn"
    return "unknown"


def _error_action(kind: str) -> str:
    """每一类错误该干什么——写死在代码里，不在文档里靠人记。"""
    return {
        "blocked": "海报/提示词被上游判定违规 → 改写 visual_zh 里触发词（身体部位、动作指向），不要重试同一句",
        "upstream": "上游偶发失败 → 自动重试，无需改动",
        "auth": "登录态失效/需验证码 → 运行 banana-shim\\login-arena.py 重新登录",
        "conn": "shim 没起来 → 运行 banana-shim\\start-guard.cmd",
        "unknown": "未归类 → 看 manifest.images[].failures 原始文本",
    }.get(kind, "")


def _is_image_model(mid: str) -> bool:
    m = mid.lower()
    return ("image" in m or "dall" in m or "flux" in m or "sd" in m) and "video" not in m


def _discover_gateway_models(ai_image, base_url: str, api_key: str) -> tuple[list, str]:
    """GET /v1/models 动态发现网关上的图模型，按 IMG_MODEL_PRIORITY 排序。返回 (模型名列表, 失败原因)。"""
    endpoint = ai_image._api_url(base_url, "models")
    resp, err = _call_capturing(ai_image, ai_image.http_get, endpoint, api_key, timeout=20)
    if resp is None:
        return [], f"探测 {endpoint} 失败（{err}）"
    items = resp.get("data") if isinstance(resp, dict) else None
    if not isinstance(items, list):
        return [], f"{endpoint} 返回结构不含 data 数组"
    ids = []
    for it in items:
        mid = it.get("id") if isinstance(it, dict) else None
        if mid and _is_image_model(str(mid)):
            ids.append(str(mid))

    def rank(mid: str) -> tuple:
        low = mid.lower()
        for i, pref in enumerate(IMG_MODEL_PRIORITY):
            if low == pref:
                return (0, i, low)
        return (1, 0, low)  # 未在偏好表里的图模型排最后，但仍作为候选

    return sorted(set(ids), key=rank), ""


def _build_model_chain(ai_image, base_url: str, api_key: str, configured: str,
                       override: str = "") -> tuple[list, str]:
    """候选链 = [--model 覆盖] + [配置模型] + [网关动态发现模型] + [硬编码兜底]，去重保序。

    返回 (链, 探测备注)。override（--model）只影响"谁排第一"，降级链完整保留——
    这样既能强制某个模型优先，又不会因为有覆盖就失去自愈能力。
    """
    chain: list = []
    seen: set = set()

    def push(mid: str) -> None:
        mid = str(mid or "").strip()
        if mid and mid.lower() not in seen:
            seen.add(mid.lower())
            chain.append(mid)

    push(override)
    push(configured)
    discovered, derr = _discover_gateway_models(ai_image, base_url, api_key)
    for mid in discovered:
        push(mid)
    for mid in IMG_MODEL_FALLBACK:
        push(mid)
    note = f"网关发现 {len(discovered)} 个图模型" if discovered else f"网关发现失败：{derr}"
    return chain, note


def _call_capturing(ai_image, fn, *args, **kwargs):
    """调用 ai_image 的函数并把它的 stderr 抓回来。

    关键：ai_image.fail() 是「print 到 stderr + raise SystemExit(1)」——真实失败原因
    （HTTP 码 + 网关 error.message）只存在于 stderr，SystemExit.code 只是整数 1。
    不抓 stderr 就拿不到任何可用信息，正是上一轮 manifest 里只写「两次尝试均失败」的原因。
    返回 (结果, 错误文本)，二者恰有其一。
    """
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        return fn(*args, **kwargs), ""
    except SystemExit as e:
        code = getattr(e, "code", None)
        detail = " ".join(buf.getvalue().split())
        if not detail:
            detail = f"进程退出码 {code}" if code not in (None, 0) else "失败但无错误输出"
        return None, detail[:600]
    except Exception as e:  # noqa: BLE001 — 网络/解析异常统一归入降级
        detail = " ".join(buf.getvalue().split())
        tail = f"{type(e).__name__}: {e}"
        return None, (f"{detail} | {tail}" if detail else tail)[:600]
    finally:
        sys.stderr = old


def _gen_one(ai_image, base_url, model, api_key, prompt, use_ref: bool,
             style: dict, tmp_dir: Path):
    """单页生图（单模型）：--use-ref 时先试 /images/edits 垫图，失败回退文生图；尺寸按 IMG_SIZES 降级。

    返回 (路径, 实用尺寸, 是否垫图, 失败记录列表)。失败记录逐条带网关原始 error.message / HTTP 码。
    """
    fails: list = []

    if use_ref and FB060_REF.is_file():
        ref_prompt = prompt + "\n" + style.get("ref_isolation_instruction", "")
        ns = argparse.Namespace(size=IMG_SIZES[-1], image=str(FB060_REF), mask=None, n=1)
        result, err = _call_capturing(ai_image, ai_image._post_edits_multipart,
                                      base_url, api_key, model, ref_prompt, ns)
        if result is not None:
            paths, perr = _call_capturing(ai_image, ai_image.save_sync_data,
                                          result, str(tmp_dir), "png")
            if paths:
                return paths[0], IMG_SIZES[-1], True, fails
            err = perr or "接口返回无可用图片数据"
        fails.append({"model": model, "stage": "edits", "size": IMG_SIZES[-1],
                      "reason": err})
        print(f"[gen] model={model} 垫图 edits 失败（{err[:160]}），回退文生图", file=sys.stderr)

    endpoint = ai_image._api_url(base_url, "images/generations")
    for size in IMG_SIZES:
        payload = {"model": model, "prompt": prompt, "n": 1, "size": size}
        result, err = _call_capturing(ai_image, ai_image.http_post,
                                      endpoint, api_key, payload, timeout=240)
        if result is not None:
            paths, perr = _call_capturing(ai_image, ai_image.save_sync_data,
                                          result, str(tmp_dir), "png")
            if paths:
                return paths[0], size, False, fails
            err = perr or "接口返回无可用图片数据"
        fails.append({"model": model, "stage": "generations", "size": size, "reason": err})
        print(f"[gen] model={model} size={size} 失败：{err[:160]}", file=sys.stderr)
    return None, "", False, fails


def _force_ratio(src: Path, dst: Path) -> bool:
    """中心裁剪到 3:4 后缩放为 1080x1440（cv2）；失败返回 False 由调用方兜底原样移动。"""
    try:
        import cv2  # noqa: PLC0415
        img = cv2.imread(str(src))
        if img is None:
            return False
        h, w = img.shape[:2]
        ratio = CANVAS_W / CANVAS_H
        if abs(w / h - ratio) > 0.01:
            if w / h > ratio:
                nw = int(h * ratio)
                x0 = (w - nw) // 2
                img = img[:, x0:x0 + nw]
            else:
                nh = int(w / ratio)
                y0 = (h - nh) // 2
                img = img[y0:y0 + nh, :]
        img = cv2.resize(img, (CANVAS_W, CANVAS_H), interpolation=cv2.INTER_AREA)
        return bool(cv2.imwrite(str(dst), img))
    except Exception as e:  # noqa: BLE001
        print(f"[gen] 3:4 校正失败（原样保留）：{e}", file=sys.stderr)
        return False


def cmd_gen_pages(a) -> int:
    out_dir = Path(a.dir)
    pages_file = out_dir / "pages.json"
    if not pages_file.is_file():
        die(f"缺少 pages.json，先跑 storyboard：{pages_file}", 2)
    data = _load_json_file(str(pages_file))
    pages = data["pages"]
    characters = data.get("characters") or []
    if a.pages:
        wanted = {int(x) for x in a.pages.split(",")}
        pages = [p for p in pages if p["page"] in wanted]
        if not pages:
            die("--pages 过滤后无页可生成", 2)
    style = _load_locked_style()
    ai_image = _ai_image()
    ch = _pick_channel(ai_image, getattr(a, "via", "shim"))
    raw_base = ch["base_url"]
    # base URL 归一化：确保以 /v1 结尾（不要依赖网关对缺前缀路径的宽容）。
    base_url = ai_image.normalize_base_url(raw_base)
    configured = ch["model"]
    api_key = ch["api_key"]

    # --model 覆盖：只把指定模型提到候选链首位，降级链完整保留（不失去自愈能力）。
    override = (getattr(a, "model", "") or "").strip()
    chain, probe_note = _build_model_chain(ai_image, base_url, api_key, configured, override)
    _tp = style.get("typography") or {}
    _cover_font = (_tp.get("cover") or {}).get("font_name", "手写马克笔粗楷体")
    _body_font = (_tp.get("body") or {}).get("font_name", "乐米蜡笔体")
    print(f"[gen] 风格锁：{style['style_index']} · {style['display_name']}"
          f"（locked={style.get('locked', False)}，字体锁=on，画幅={CANVAS_W}x{CANVAS_H}）")
    print(f"[gen] 字体路由：封面 → {_cover_font}；正文/收尾 → {_body_font}"
          f"（关键词正红 #D93A2B，全部由模型直接生成）")
    print(f"[gen] 通道={ch['channel']}（{CHANNELS[ch['channel']]}，配置来自 {'/'.join(ch['env'])}）")
    print(f"[gen] base={raw_base} → {base_url}")
    if override:
        print(f"[gen] --model 覆盖：{override}（排在首选，失败仍按降级链切换）")
    print(f"[gen] 模型候选链（{len(chain)} 个，{probe_note}）：{' → '.join(chain)}")
    print(f"[gen] pages={len(pages)} use_ref={a.use_ref}")

    images_dir = out_dir / "images"
    images_dir.mkdir(exist_ok=True)
    tmp_dir = images_dir / "_tmp"
    tmp_dir.mkdir(exist_ok=True)
    records = []
    for pg in pages:
        dst = images_dir / f"page_{pg['page']}.png"
        if dst.is_file() and not a.force:
            print(f"[gen] page_{pg['page']} 已存在，跳过（--force 可重生成）")
            records.append({"page": pg["page"], "path": str(dst), "skipped": True})
            continue
        prompt = _build_prompt(style, pg, characters)
        got = None
        size_used = ""
        ref_used = False
        page_fails: list = []
        # 两级降级：外层遍历模型候选链，内层由 _gen_one 遍历 IMG_SIZES。
        for midx, model in enumerate(chain, 1):
            got, size_used, ref_used, fails = _gen_one(ai_image, base_url, model, api_key,
                                                       prompt, a.use_ref, style, tmp_dir)
            page_fails.extend(fails)
            if got:
                if midx > 1:
                    print(f"[gen] page_{pg['page']} 降级到 model={model} 成功（{len(fails)} 次失败）")
                break
            print(f"[gen] page_{pg['page']} model={model} 全尺寸失败"
                  f"（{len(fails)} 条记录），换下一个模型", file=sys.stderr)
        if not got:
            # 全部候选失败：按错误类型给出「自己解决」的动作，并把归类写进 manifest。
            kinds = sorted({_classify_error(x["reason"]) for x in page_fails}) or ["unknown"]
            reasons = "；".join(
                f"{f['model']}@{f.get('size','')}: {f['reason']}" for f in page_fails
            ) or "无候选模型"
            records.append({"page": pg["page"], "error": reasons, "kinds": kinds,
                            "actions": [_error_action(k) for k in kinds],
                            "failures": page_fails})
            print(f"[gen] ✗ page_{pg['page']} 全部 {len(chain)} 个候选模型 × {len(IMG_SIZES)} 尺寸均失败",
                  file=sys.stderr)
            for f in page_fails:
                print(f"[gen]   - {f['stage']} model={f['model']} size={f.get('size','')} "
                      f"→ {f['reason']}", file=sys.stderr)
            for k in kinds:
                print(f"[gen] 归类={k} → {_error_action(k)}", file=sys.stderr)
            if "blocked" in kinds:
                print("[gen] 本页被上游内容策略拦截，继续下一句是浪费额度 → 跳过本页，"
                      "改 visual_zh 后 --pages " + str(pg["page"]) + " --force 补图", file=sys.stderr)
            continue
        fixed = _force_ratio(got, dst)
        if not fixed:
            shutil.move(str(got), str(dst))
        records.append({"page": pg["page"], "path": str(dst), "model": model,
                        "size_param": size_used, "style_ref": ref_used,
                        "failures": page_fails,
                        "canvas": f"{CANVAS_W}x{CANVAS_H}" if fixed else "原始"})
        print(f"[gen] page_{pg['page']} ✓ model={model} size={size_used} "
              f"({dst.stat().st_size // 1024} KB)")
    shutil.rmtree(tmp_dir, ignore_errors=True)

    ok = [r for r in records if r.get("path") and not r.get("error")]
    # errors 在 _merge_manifest 里是「并集累积」，重跑成功也无法清掉上一轮的失败记录，
    # 会让 manifest 永远显示 image_failed 的假象。这里按本轮实际结果直接覆写。
    mf = _read_manifest(out_dir)
    mf["errors"] = [f"page_{r['page']} 生图失败" for r in records if r.get("error")]
    mf["images"] = records
    mf["image_models"] = chain
    mf["status"] = "generated" if ok else "image_failed"
    _dump_json(_manifest_path(out_dir), mf)
    print(f"生图完成：{len(ok)}/{len(pages)} 张")
    if not ok:
        print("ERROR: 一张图都没出，检查生图 API 后重跑 gen_pages", file=sys.stderr)
        return 4
    return 0


# ── 配图配文（校验 copy.json）───────────────────────────────
def cmd_copy(a) -> int:
    out_dir = Path(a.dir)
    data = _load_json_file(a.json)
    warnings: list = []
    title = " ".join(str(data.get("title", "")).split())
    if not title:
        die("copy.json 缺少 title（小红书标题，需带 1-2 个 emoji）", 2)
    if not EMOJI_RE.search(title):
        die("标题未带 emoji（需要 1-2 个），修 copy.json 后重跑 copy", 2)
    body = str(data.get("body", "")).strip()
    if not body:
        die("copy.json 缺少 body（正文 300-600 字、分短段、带互动问句）", 2)
    body_len = len(re.sub(r"\s", "", body))
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        die("tags 必须是数组", 2)
    tags = [str(t).strip().lstrip("#") for t in tags if str(t).strip()]
    note = " ".join(str(data.get("note", "")).split())

    md = [
        f"# {title}", "",
        body.split("\n\n")[0] if False else body, "",
        " ".join(f"#{t}" for t in tags), "",
    ]
    (out_dir / "copy.md").write_text("\n".join(md), encoding="utf-8")
    _merge_manifest(out_dir, copy={"title": title, "body_len": body_len, "tags": tags,
                                   "note": note}, warnings=warnings, status="copied")
    print(f"copy 落盘：标题 {len(title)} 字 / 正文 {body_len} 字 / {len(tags)} 个 tag → copy.md")
    for w in warnings:
        print(f"  [warn] {w}", file=sys.stderr)
    return 0


# ── 进发布队列 ─────────────────────────────────────────────
def cmd_enqueue(a) -> int:
    out_dir = Path(a.dir)
    mf = _read_manifest(out_dir)
    copy_info = mf.get("copy") or {}
    title = copy_info.get("title") or mf.get("topic") or "手绘图文故事"
    note_core = copy_info.get("note") or f"FB-060 手绘图文：{mf.get('topic', title)}"
    rel = f"outputs/{out_dir.name}"
    payload = {
        "title": title,
        "date": time.strftime("%Y-%m-%d"),
        "time": "",
        "kind": "content",
        "status": "draft",
        "source": "chat",
        "platform": "xiaohongshu",
        "url": rel,
        "note": f"{note_core}；文案见 {rel}/copy.md",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(SCHED_API, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _NO_PROXY.open(req, timeout=15) as resp:
            item = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        curl = (f"$body = '{json.dumps(payload, ensure_ascii=False)}'; "
                f"Invoke-RestMethod -Uri {SCHED_API} -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))")
        print(f"ERROR: 入队失败（{e}）。服务恢复后手工执行：\n{curl}", file=sys.stderr)
        _merge_manifest(out_dir, errors=[f"入队失败：{e}"], status="enqueue_failed")
        return 5
    _merge_manifest(out_dir, schedule=item, status="enqueued")
    print(f"已入发布队列：id={item.get('id')} status={item.get('status')} "
          f"source={item.get('source')} platform={item.get('platform')} url={item.get('url')}")
    return 0


# ── 产物验收 ───────────────────────────────────────────────
def cmd_finish(a) -> int:
    out_dir = Path(a.dir)
    errors: list = []
    for name in ("storyboard.md", "copy.md", "manifest.json", "pages.json"):
        if not (out_dir / name).is_file():
            errors.append(f"缺 {name}")
    pngs = sorted(out_dir.glob("images/page_*.png"))
    if not pngs:
        errors.append("没有 images/page_*.png")
    dims = []
    try:
        import cv2  # noqa: PLC0415
        for p in pngs:
            img = cv2.imread(str(p))
            if img is None:
                errors.append(f"{p.name} 无法解码")
                continue
            h, w = img.shape[:2]
            dims.append(f"{p.name}={w}x{h}")
            if abs(w / h - CANVAS_W / CANVAS_H) > 0.01:
                errors.append(f"{p.name} 比例 {w}x{h} 非 3:4")
    except ImportError:
        dims.append("(cv2 缺失，跳过画幅校验)")
    mf = _read_manifest(out_dir)
    if not (mf.get("schedule") or {}).get("id"):
        _merge_manifest(out_dir, status="done_no_enqueue",
                        warnings=["未入发布队列（enqueue 未跑或失败）"])
    else:
        _merge_manifest(out_dir, status="done")
    rel = Path(os.path.relpath(out_dir, ROOT)).as_posix() if out_dir.is_absolute() else out_dir.as_posix()
    summary = {
        "dir": rel, "images": [f"images/{p.name}" for p in pngs],
        "dimensions": dims, "errors": errors,
        "schedule_id": (mf.get("schedule") or {}).get("id", ""),
        "status": "PASS" if not errors else "FAIL",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 4


# ── 环境体检 ───────────────────────────────────────────────
def cmd_check_env(a) -> int:
    print(f"python: {sys.version.split()[0]} ({sys.executable})")
    for mod in ("yt_dlp", "faster_whisper", "cv2", "numpy"):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", None)
            if mod == "yt_dlp":
                ver = m.version.__version__
            print(f"{mod}: {ver or 'ok'}")
        except ImportError:
            print(f"{mod}: 未安装")
    ai_image = _ai_image()
    for name in ("IMG_BASE_URL", "IMG_MODEL", "IMG_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY"):
        value, src = ai_image.resolve_config(name if name.startswith("IMG_") else name)
        if not value:
            value = os.environ.get(name, "").strip()
            src = name if value else None
        shown = (value[:4] + "..." + value[-4:]) if "KEY" in name and len(value) > 8 else value
        print(f"{name}: {'[set] ' + shown if value else '[missing]'}" + (f"（{src}）" if src and src != name else ""))
    print(f"ffmpeg: {shutil.which('ffmpeg') or '未在 PATH'}")
    print(f"FB-060 资产: json={'ok' if FB060_JSON.is_file() else 'missing'} "
          f"ref={'ok' if FB060_REF.is_file() else 'missing'}")
    try:
        _st = _load_json_file(str(FB060_JSON))
        lock_ok = (str(_st.get("style_index", "")).strip() == STYLE_LOCK_INDEX
                   and all(_st.get(k) for k in STYLE_LOCK_FIELDS))
        _tp = _st.get("typography") or {}
        _font_on = all((_tp.get(k) or {}).get("zh_heading") for k in ("cover", "body")) \
            or bool(_tp.get("zh_heading"))
        print(f"风格/字体写死: {'ok' if lock_ok else 'BROKEN'} "
              f"（编号={_st.get('style_index', '?')}，字体锁={'on' if _font_on else 'off'}，"
              f"契约条数={len(_tp.get('hard_contract') or [])}）")
    except SystemExit:
        print("风格/字体写死: BROKEN（风格资产读取失败）")
    # 生图端点可达性：配置项存在 ≠ shim 活着。探一次 /v1/models，给一句明确结论。
    base, _ = ai_image.resolve_config(ai_image.ENV_BASE_URL)
    base = (base or os.environ.get(ai_image.ENV_BASE_URL, "")).strip()
    if base:
        probe = ai_image.normalize_base_url(base.rstrip("/")) + "/models"
        try:
            with urllib.request.urlopen(probe, timeout=8) as resp:
                ids = [d.get("id") for d in (json.loads(resp.read().decode("utf-8")).get("data") or [])
                       if isinstance(d, dict)]
            print(f"生图端点可达: ok（{probe}，模型 {len(ids)} 个：{'、'.join(str(i) for i in ids[:6])}）")
        except Exception as e:  # noqa: BLE001
            print(f"生图端点可达: DOWN（{probe} — {type(e).__name__}: {e}）")
            print("  → shim 没起来：先运行 banana-shim\\start-guard.cmd（或 start-shim.cmd），"
                  "再跑 check_env 确认，不要直接跑 gen_pages")
    else:
        print("生图端点可达: 未知（IMG_BASE_URL 未设置）")
    print(f"schedule API: {SCHED_API}")
    return 0


# ── 方案三：逐页出提示词（零成本，一张一张发）────────────────
def cmd_prompt(a) -> int:
    """出第 N 页的完整生图提示词，一次一页；用户说"继续"再出下一页。

    不调接口、不落图。提示词与 gen_pages 实际发送的逐字一致（同走 _build_prompt），
    所以拿去别处生成的效果与走 shim 完全对齐。复制块直接用，不要改写。
    """
    out_dir = Path(a.dir)
    pages_file = out_dir / "pages.json"
    if not pages_file.is_file():
        die(f"缺少 pages.json，先跑 storyboard：{pages_file}", 2)
    data = _load_json_file(str(pages_file))
    pages = data["pages"]
    characters = data.get("characters") or []
    style = _load_locked_style()

    by_no = {int(p["page"]): p for p in pages}
    total = len(pages)
    n = int(a.page)
    if n not in by_no:
        die(f"没有第 {n} 页（本篇共 {total} 页：{sorted(by_no)}）", 2)
    pg = by_no[n]
    slot = _font_slot(pg.get("kind", ""))
    prompt = _build_prompt(style, pg, characters)

    print(f"===== 第 {n}/{total} 页 · {pg.get('kind')} · 字体档={slot} =====")
    print(f"标题：{pg.get('title', '')}")
    for i, ln in enumerate(pg.get("lines") or [], 1):
        print(f"文字{i}：{ln}")
    print(f"参考图：{FB060_REF.name}（{'存在' if FB060_REF.is_file() else '缺失'}）")
    print("")
    print(prompt)
    print("")
    nxt = n + 1
    if nxt in by_no:
        print(f"[继续] 说出「继续」出第 {nxt} 页提示词；或直接 python ... prompt --dir \"<DIR>\" --page {nxt}")
    else:
        print(f"[结束] 已经是最后一页（共 {total} 页）")
    return 0


# ── 风格/字体写死核对（零成本，不发请求）────────────────────
def cmd_style(a) -> int:
    style = _load_locked_style()
    print(f"风格锁：{'ON' if style.get('locked') else 'OFF'}  "
          f"编号={style['style_index']}  名称={style['display_name']}")
    print(f"锁版本：{style.get('lock_version', '-')}  画幅：{style['canvas']['ratio']} "
          f"{style['canvas']['width']}x{style['canvas']['height']}")
    print(f"资产文件：{FB060_JSON}")
    print("\n--- 风格特征（写死）---")
    for t in style["traits"]:
        print(f"  · {t}")
    print("\n--- 字体锁（写死，全部由模型直接画进图，不做后期叠字）---")
    route = (style.get("typography") or {}).get("route") or {}
    print(f"  分档路由：cover → {route.get('cover', 'cover')}；"
          f"content → {route.get('content', 'body')}；cta → {route.get('cta', 'body')}")
    print(f"  [封面 cover] {_locked_typography(style, 'cover')}")
    print(f"  [正文 body ] {_locked_typography(style, 'content')}")
    print("\n--- 负向约束（写死）---")
    print(f"  {_locked_negative(style)}")
    cc = style.get("character_consistency") or {}
    if cc:
        print("\n--- 人物一致性锁（跨页锁定，逐页注入）---")
        for i, r in enumerate(cc.get("rules") or CHARACTER_FALLBACK_RULES, 1):
            print(f"  {i}) {r}")

    out_dir = Path(a.dir) if getattr(a, "dir", "") else None
    if out_dir:
        pages_file = out_dir / "pages.json"
        if not pages_file.is_file():
            die(f"--dir 下没有 pages.json，先跑 storyboard：{pages_file}", 2)
        payload = _load_json_file(str(pages_file))
        pages = payload["pages"]
        characters = payload.get("characters") or []
        print(f"\n--- 本目录人物台词本：{'、'.join(c['name'] for c in characters) if characters else '（未设定，人物不锁）'} ---")
        if getattr(a, "pages", ""):
            wanted = {int(x) for x in a.pages.split(",")}
            pages = [p for p in pages if p["page"] in wanted]
        for pg in pages:
            kind = pg.get("kind", "")
            print(f"\n===== page_{pg['page']}（kind={kind} → 字体档 "
                  f"{_font_slot(kind)}）最终 prompt（逐字即实际发送内容）=====")
            print(_build_prompt(style, pg, characters))
    return 0


# ── CLI ────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="爆款转手绘图文故事流水线（baicatcha）")
    sub = ap.add_subparsers(dest="cmd", metavar="<子命令>", required=True)

    p = sub.add_parser("check_env", help="环境体检")
    p.set_defaults(fn=cmd_check_env)


    p = sub.add_parser("style", help="核对写死的风格与字体（零成本，不发请求）")
    p.add_argument("--dir", help="可选：产物目录，给了就额外打印该目录每页的最终 prompt")
    p.add_argument("--pages", help="配合 --dir 使用，只看指定页，如 1,3")
    p.set_defaults(fn=cmd_style)

    p = sub.add_parser("detect", help="输入归一化判定 url/text/topic")
    p.add_argument("--input-raw")
    p.add_argument("--input-file")
    p.set_defaults(fn=cmd_detect)

    p = sub.add_parser("ingest", help="建产物目录；url 分支下载+转写")
    p.add_argument("--input-raw")
    p.add_argument("--input-file")
    p.add_argument("--branch", choices=("url", "text", "topic"), help="强制分支（默认自动判定）")
    p.add_argument("--url", help="branch=url 时的链接")
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("storyboard", help="校验 storyboard.json → storyboard.md + pages.json")
    p.add_argument("--dir", required=True)
    p.add_argument("--json", required=True)
    p.set_defaults(fn=cmd_storyboard)

    p = sub.add_parser("gen_pages", help="按 FB-060 模板逐页生图 → images/page_N.png")
    p.add_argument("--dir", required=True)
    p.add_argument("--pages", help="只生成指定页，如 1,3（默认全部）")
    p.add_argument("--model",
                   help="指定首选生图模型（提到候选链首位，覆盖 IMG_MODEL；失败仍按降级链切换）")
    p.add_argument("--via", choices=["shim", "relay"], default="shim",
                   help="生图通道：shim=本地 banana-shim（浏览器自动化），relay=第三方中转 API（默认 shim）")
    p.add_argument("--use-ref", action="store_true", default=False,
                   help="先试 /images/edits 垫图（FB-060.webp + 隔离指令），失败自动回退文生图（默认关闭）")
    p.add_argument("--force", action="store_true", help="覆盖已存在的 page_N.png")
    p.set_defaults(fn=cmd_gen_pages)

    p = sub.add_parser("prompt", help="方案三：出第 N 页的生图提示词（一次一页，不发请求）")
    p.add_argument("--dir", required=True)
    p.add_argument("--page", required=True, type=int, help="第几页，从 1 开始")
    p.set_defaults(fn=cmd_prompt)

    p = sub.add_parser("copy", help="校验 copy.json → copy.md")
    p.add_argument("--dir", required=True)
    p.add_argument("--json", required=True)
    p.set_defaults(fn=cmd_copy)

    p = sub.add_parser("enqueue", help="POST /api/schedule（draft/chat/xiaohongshu）")
    p.add_argument("--dir", required=True)
    p.set_defaults(fn=cmd_enqueue)

    p = sub.add_parser("finish", help="产物完整性校验 + 汇总")
    p.add_argument("--dir", required=True)
    p.set_defaults(fn=cmd_finish)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

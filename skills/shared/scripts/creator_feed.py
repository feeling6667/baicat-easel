#!/usr/bin/env python3
"""对标作者作品采集 —— YouTube 适配器（纯标准库，零第三方依赖）。

数据源全部为公开内容，不读取、不使用任何平台账号或登录态：
  * 频道 RSS：https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxx
    含 <yt:videoId> / <title> / <link href> / <published>
    以及 <media:group><media:community> 下的 <media:statistics views="N"/>、
    <media:starRating count="N"/>（后者近似点赞量，填进 stats.likes）
  * @handle 或频道 URL：抓频道页 HTML，提取 channel_id（UC 开头 24 位）

统一 JSON 输出（与 B站适配器共用同一冻结格式）：
  {
    "platform": "youtube",
    "author": {"id": "UC...", "name": "...", "url": "https://www.youtube.com/channel/UC..."},
    "items": [{"id": "...", "title": "...", "url": "...",
               "published": "...", "stats": {"views": 123, "likes": null, "comments": null}}],
    "fetched_at": 1792000000
  }
RSS 拿不到的指标一律写 null，不用 0 冒充。

子命令：
  fetch  --platform youtube --author <UC..|@handle|URL> --limit 20 [--json]
  authors add --platform youtube --author <标识> --name <名字>
  authors list
  push   --platform youtube --author <标识> --limit 20 --min-views <N> [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
CHANNEL_URL = "https://www.youtube.com/channel/{cid}"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 25
CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_\-]{22}")
HTML_ID_PATTERNS = (
    re.compile(r'itemprop=["\']identifier["\'][^>]*content=["\'](UC[A-Za-z0-9_\-]{22})["\']'),
    re.compile(r'content=["\'](UC[A-Za-z0-9_\-]{22})["\'][^>]*itemprop=["\']identifier["\']'),
    re.compile(r"channel_id=(UC[A-Za-z0-9_\-]{22})"),
    re.compile(r'"externalId":"(UC[A-Za-z0-9_\-]{22})"'),
    re.compile(r'"channelId":"(UC[A-Za-z0-9_\-]{22})"'),
    re.compile(r"/channel/(UC[A-Za-z0-9_\-]{22})"),
)
URL_IN_NOTE_RE = re.compile(r"https?://\S+")

PLATFORM_LABELS = {"youtube": "YouTube", "bilibili": "B站"}
SUPPORTED_PLATFORMS = ("youtube",)


class CreatorFeedError(RuntimeError):
    """可预期的运行错误：网络、解析、标识无法解析等。"""


# --------------------------------------------------------------------------- 路径

def project_root() -> Path:
    configured = os.environ.get("EASEL_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "outputs").is_dir():
            return parent
    return here.parents[3]


def outputs_dir() -> Path:
    root = project_root() / "outputs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ideas_file() -> Path:
    return outputs_dir() / "_ideas.json"


def creators_file() -> Path:
    return outputs_dir() / "_creators.json"


def _atomic_write_json(path: Path, data) -> None:
    """tmp + replace 原子写，格式与 web/app.py 的 _write_ideas 保持一致。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json_list(path: Path) -> list:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


# --------------------------------------------------------------------------- 网络

def http_get(url: str, *, retries: int = 2) -> bytes:
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = CreatorFeedError(f"HTTP {exc.code} 请求失败：{url}")
        except urllib.error.URLError as exc:
            last = CreatorFeedError(f"网络不可达：{url}（{exc.reason}）")
        except Exception as exc:  # noqa: BLE001
            last = CreatorFeedError(f"请求失败：{url}（{exc}）")
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


# --------------------------------------------------------------------------- 标识解析

def resolve_channel_id(author: str) -> tuple[str, str]:
    """把 channel_id / @handle / 频道 URL 统一解析成 (channel_id, 来源URL)。"""
    raw = (author or "").strip()
    if not raw:
        raise CreatorFeedError("--author 为空：请给 channel_id（UC 开头）、@handle 或频道 URL")

    if CHANNEL_ID_RE.fullmatch(raw):
        return raw, CHANNEL_URL.format(cid=raw)

    if raw.startswith("http://") or raw.startswith("https://"):
        url = raw
    elif raw.startswith("@"):
        url = f"https://www.youtube.com/{raw}"
    elif raw.startswith("youtube.com/") or raw.startswith("www.youtube.com/"):
        url = "https://" + raw.lstrip("/")
    else:
        # 既不是 UC id、也不是 handle/URL：按 handle 兜底尝试
        url = f"https://www.youtube.com/@{raw.lstrip('@')}"

    direct = re.search(r"/channel/(UC[A-Za-z0-9_\-]{22})", url)
    if direct:
        return direct.group(1), url

    html = http_get(url).decode("utf-8", errors="replace")
    for pattern in HTML_ID_PATTERNS:
        found = pattern.search(html)
        if found:
            return found.group(1), url
    raise CreatorFeedError(
        f"无法从 {url} 提取 channel_id（页面里没有 itemprop=identifier / channel_id / externalId）。"
        "请改用频道 URL（https://www.youtube.com/channel/UC...）或直接给 UC 开头的 channel_id。"
    )


# --------------------------------------------------------------------------- 采集

def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _stat_count(entry: ET.Element, tag: str) -> int | None:
    """递归取 media: 命名空间下某个统计节点的数值属性；取不到返回 None（不用 0 冒充）。"""
    node = next(entry.iter(f"{MEDIA}{tag}"), None)
    if node is None:
        return None
    raw = (node.get("views") or node.get("count") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _views(entry: ET.Element) -> int | None:
    # <media:statistics views="N"/> 实际嵌在 <media:group><media:community> 内，递归查找
    return _stat_count(entry, "statistics")


def _likes(entry: ET.Element) -> int | None:
    # <media:starRating count="N"/> 同为 media:community 子节点，近似点赞量；没有就 null
    return _stat_count(entry, "starRating")


def fetch_channel(author: str, limit: int) -> dict:
    """拉取频道 RSS，返回冻结格式的 dict。"""
    channel_id, _ = resolve_channel_id(author)
    payload = http_get(RSS_URL.format(cid=channel_id))
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise CreatorFeedError(f"RSS 解析失败（channel_id={channel_id}）：{exc}") from exc

    name = _text(root.find(f"{ATOM}author/{ATOM}name")) or _text(root.find(f"{ATOM}title"))
    items: list[dict] = []
    for entry in root.findall(f"{ATOM}entry"):
        if len(items) >= max(0, limit):
            break
        vid = _text(entry.find(f"{YT}videoId"))
        link = ""
        for ln in entry.findall(f"{ATOM}link"):
            if ln.get("rel") in (None, "alternate"):
                link = ln.get("href") or ""
                if ln.get("rel") == "alternate":
                    break
        url = link or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
        items.append(
            {
                "id": vid,
                "title": _text(entry.find(f"{ATOM}title")),
                "url": url,
                "published": _text(entry.find(f"{ATOM}published")),
                "stats": {"views": _views(entry), "likes": _likes(entry), "comments": None},
            }
        )

    if not items:
        raise CreatorFeedError(
            f"频道 {channel_id} 的 RSS 里没有条目（可能频道不存在或没有公开视频）。"
        )

    return {
        "platform": "youtube",
        "author": {"id": channel_id, "name": name, "url": CHANNEL_URL.format(cid=channel_id)},
        "items": items,
        "fetched_at": int(time.time()),
    }


# --------------------------------------------------------------------------- 展示

def format_count(value: int | None) -> str:
    if value is None:
        return "-"
    if value >= 100_000_000:
        text = f"{value / 100_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}亿"
    if value >= 10_000:
        text = f"{value / 10_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}万"
    return str(value)


def print_human(feed: dict) -> None:
    author = feed["author"]
    print(f"{author['name']} ({author['id']})")
    print(f"  {author['url']}  ·  共 {len(feed['items'])} 条")
    for idx, item in enumerate(feed["items"], 1):
        print(
            f"  {idx:>2}. [{format_count(item['stats']['views'])}] "
            f"{item['title']}\n      {item['published']}  {item['url']}"
        )


# --------------------------------------------------------------------------- 子命令

def cmd_fetch(args: argparse.Namespace) -> int:
    require_platform(args.platform)
    feed = fetch_channel(args.author, args.limit)
    if args.json:
        print(json.dumps(feed, ensure_ascii=False, indent=2))
    else:
        print_human(feed)
    return 0


def cmd_authors_add(args: argparse.Namespace) -> int:
    require_platform(args.platform)
    raw = (args.author or "").strip()
    if not raw:
        raise CreatorFeedError("--author 为空")
    platform = args.platform.lower()

    # 唯一键是 channel_id：写入前先解析（UC id 直取，@handle / URL 抓频道页）
    channel_id, _ = resolve_channel_id(raw)
    name = (args.name or "").strip() or _channel_title(channel_id) or channel_id

    path = creators_file()
    entries = _read_json_list(path)          # 读最新（可能与 B站适配器并发写）
    entries = normalize_creators(entries)    # 合并历史别名（@handle / URL → channel_id）

    for entry in entries:
        if (
            str(entry.get("platform", "")).lower() == platform
            and str(entry.get("author", "")).strip() == channel_id
        ):
            entry["name"] = name
            _atomic_write_json(path, entries)
            print(json.dumps({"ok": True, "action": "updated", "entry": entry}, ensure_ascii=False))
            return 0

    entry = {"platform": platform, "author": channel_id, "name": name, "added": int(time.time())}
    entries.append(entry)
    _atomic_write_json(path, entries)
    print(json.dumps({"ok": True, "action": "added", "entry": entry}, ensure_ascii=False))
    return 0


def _channel_title(channel_id: str) -> str:
    """尽力取频道名；失败就返回空串（调用方回退到 channel_id）。"""
    try:
        return fetch_channel(channel_id, 1)["author"]["name"]
    except CreatorFeedError:
        return ""


def normalize_creators(entries: list) -> list:
    """把同一个人（channel_id / @handle / URL 混存）合并成一条，键为 (platform, channel_id)。

    youtube 条目尽量解析成 channel_id；解析失败（网络问题等）原样保留，不丢数据。
    非 youtube 条目（如 B站适配器写的）只按原字符串去重，字段不动。
    """
    merged: list[dict] = []
    index: dict[tuple[str, str], dict] = {}
    native: dict[tuple[str, str], bool] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        platform = str(item.get("platform", "")).lower()
        author = str(item.get("author", "")).strip()
        was_id = bool(CHANNEL_ID_RE.fullmatch(author))
        if platform == "youtube" and author and not was_id:
            try:
                resolved, _ = resolve_channel_id(author)
            except CreatorFeedError:
                resolved = ""
            if resolved:
                # 别名解析成功：统一成 channel_id，名字保留已有的非空值
                item["author"] = resolved
        key = (platform, str(item.get("author", "")).lower())
        existing = index.get(key)
        if existing is None:
            index[key] = item
            native[key] = was_id
            merged.append(item)
            continue
        # 合并同一人：以 channel_id 那条为准（名字、added 都优先它），否则保留先出现的非空名字
        if (was_id and not native[key]) or (
            not str(existing.get("name", "")).strip() and str(item.get("name", "")).strip()
        ):
            existing["name"] = item["name"]
            if was_id and not native[key]:
                existing["added"] = item.get("added", existing.get("added"))
                native[key] = True
    return merged


def cmd_authors_list(args: argparse.Namespace) -> int:
    entries = _read_json_list(creators_file())
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    if not entries:
        print("对标清单为空（outputs/_creators.json）")
        return 0
    for entry in entries:
        print(f"  [{entry.get('platform')}] {entry.get('name')}  <{entry.get('author')}>")
    print(f"共 {len(entries)} 位")
    return 0


def _norm_url(value) -> str:
    """归一化 URL 以便比较：去空白与 note 拼接残留的尾随标点。"""
    return str(value or "").strip().rstrip("·,，。;；:：)）]】>》\"' ")


def _int_or_none(value) -> int | None:
    """统计量取整数原始值；取不到返回 None（不用 0 冒充）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def existing_keys(items: list) -> set[str]:
    """已存在条目的去重键：优先 url 字段，兜底 note 里的链接（老数据只有 note）。"""
    keys: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        candidates = [item.get("url")]
        candidates.extend(URL_IN_NOTE_RE.findall(str(item.get("note") or "")))
        for candidate in candidates:
            key = _norm_url(candidate)
            if key:
                keys.add(key)
    return keys


def cmd_push(args: argparse.Namespace) -> int:
    require_platform(args.platform)
    feed = fetch_channel(args.author, args.limit)
    threshold = args.min_views
    selected = [
        item
        for item in feed["items"]
        if threshold is None or (item["stats"]["views"] or 0) >= threshold
    ]

    path = ideas_file()
    ideas = _read_json_list(path)
    seen = existing_keys(ideas)
    added: list[dict] = []
    skipped = 0
    now = int(time.time())
    label = PLATFORM_LABELS.get(args.platform.lower(), args.platform)

    for item in selected:
        url = item["url"] or ""
        key = _norm_url(url)
        if not key or key in seen:
            skipped += 1
            continue
        seen.add(key)
        views = item["stats"]["views"]
        note_parts = []
        if views is not None:
            note_parts.append(f"播放 {format_count(views)}")
        note_parts.append(url)
        added.append(
            {
                "id": uuid.uuid4().hex[:12],
                "title": (item["title"] or "未命名选题")[:60],
                "note": " · ".join(note_parts),
                "source": f"对标-{label}",
                "status": "pending",
                "created": now,
                "url": url,
                "stats": {
                    "views": _int_or_none(item["stats"].get("views")),
                    "likes": _int_or_none(item["stats"].get("likes")),
                    "comments": None,
                },
            }
        )

    if added:
        _atomic_write_json(path, added + ideas)

    result = {
        "ok": True,
        "platform": args.platform.lower(),
        "author": feed["author"],
        "matched": len(selected),
        "added": len(added),
        "skipped": skipped,
        "ideas_file": str(path),
        "items": added,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"{feed['author']['name']}：命中 {len(selected)} 条，新增 {len(added)} 条，"
            f"去重跳过 {skipped} 条 → {path}"
        )
        for item in added:
            print(f"  + {item['title']}  ({item['note']})")
    return 0


def require_platform(platform: str) -> None:
    if (platform or "").lower() not in SUPPORTED_PLATFORMS:
        raise CreatorFeedError(
            f"本适配器仅支持 --platform youtube，收到 {platform!r}"
        )


# --------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="creator_feed.py", description="对标作者作品采集（YouTube 适配器，纯标准库）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="拉取对标作者的最新作品")
    p_fetch.add_argument("--platform", default="youtube")
    p_fetch.add_argument("--author", required=True, help="channel_id / @handle / 频道 URL")
    p_fetch.add_argument("--limit", type=int, default=20)
    p_fetch.add_argument("--json", action="store_true")
    p_fetch.set_defaults(func=cmd_fetch)

    p_authors = sub.add_parser("authors", help="对标清单管理")
    a_sub = p_authors.add_subparsers(dest="authors_command", required=True)
    a_add = a_sub.add_parser("add", help="加入对标清单")
    a_add.add_argument("--platform", default="youtube")
    a_add.add_argument("--author", required=True)
    a_add.add_argument("--name", default="")
    a_add.set_defaults(func=cmd_authors_add)
    a_list = a_sub.add_parser("list", help="列出对标清单")
    a_list.add_argument("--json", action="store_true")
    a_list.set_defaults(func=cmd_authors_list)

    p_push = sub.add_parser("push", help="按播放量阈值推入选题库")
    p_push.add_argument("--platform", default="youtube")
    p_push.add_argument("--author", required=True)
    p_push.add_argument("--limit", type=int, default=20)
    p_push.add_argument("--min-views", type=int, default=None)
    p_push.add_argument("--json", action="store_true")
    p_push.set_defaults(func=cmd_push)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 cp936：标题里的 emoji 等字符不可编码时降级替换，不要让打印炸掉整个命令
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CreatorFeedError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

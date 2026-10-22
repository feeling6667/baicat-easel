#!/usr/bin/env python3
"""tikhub_search.py — 抖音关键词搜索采集（TikHub API，按次计费）。

通过 TikHub 的抖音搜索接口按关键词拉取高赞作品，写入 Easel 选题库。
计费说明：fetch_video_search_v3 每次 $0.01（需账户余额，不走免费额度）。

子命令：
    search --keyword "婆媳" --limit 20 [--json]     只看结果，不入库
    push   --keyword "婆媳" --limit 20 --min-likes 50000   筛选后入选题库

注意：
- 搜索结果里只有点赞数（likes），没有播放量/评论数。筛选用 --min-likes。
- 本脚本不管理 TikHub API key：从项目根 .env 的 TIKHUB_API_KEY 读取。
- 每次 push 前打印预计消耗，confirm 标志后才真正调用（防误烧余额）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
IDEAS_FILE = ROOT / "outputs" / "_ideas.json"
ENV_FILE = ROOT / ".env"

BASE_URL = "https://api.tikhub.io"
SEARCH_URL = BASE_URL + "/api/v1/douyin/search/fetch_video_search_v3"

# 单次搜索调用的价格（美元）
COST_PER_SEARCH = 0.01


# ── 配置 ──────────────────────────────────────────────────

def _load_token() -> str:
    """从 .env 读 TIKHUB_API_KEY。"""
    if not ENV_FILE.is_file():
        raise SystemExit("缺少 .env 文件：%s" % ENV_FILE)
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("TIKHUB_API_KEY="):
            token = line.split("=", 1)[1].strip()
            if token:
                return token
    raise SystemExit(".env 里没有 TIKHUB_API_KEY")


def _token() -> str:
    return _load_token()


# ── HTTP ──────────────────────────────────────────────────

def _http_post(url: str, body: dict, token: str, timeout: int = 90) -> dict:
    """POST JSON，返回解析后的 dict。中文关键词必须走 UTF-8 字节。"""
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "Easel/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── 搜索 ──────────────────────────────────────────────────

def search_videos(keyword: str, limit: int = 20, token: str = "",
                  date_type: int = 0, label_type: int = 0) -> list[dict]:
    """调 fetch_video_search_v3，返回归一化后的作品列表。

    date_type: 0=不限, 3=近3天, 7=近7天, 30=近一个月
    label_type: 0=不限, 1=低粉爆款, 2=高完播率, 3=高涨粉率, 4=高点赞率
    """
    if not token:
        token = _token()

    body = {
        "query": keyword,
        "offset": 0,
        "page": 1,
        "date_type": date_type,
        "label_type": label_type,
        "duration_type": 0,
    }
    resp = _http_post(SEARCH_URL, body, token)

    if resp.get("code") != 200:
        raise RuntimeError("搜索失败：%s" % resp.get("message", "unknown"))

    data = resp.get("data", {})
    inner = data.get("data", [])
    if not isinstance(inner, list):
        return []

    items = []
    for it in inner[:limit]:
        items.append(_norm_item(it))
    return items


def _norm_item(raw: dict) -> dict:
    """把抖音搜索结果转成统一格式（与 creator_feed.py / bili_creator.py 一致）。"""
    item_id = str(raw.get("itemId", ""))
    title = (raw.get("title") or "").strip()
    url = raw.get("url") or ""
    if not url and item_id:
        url = "https://www.douyin.com/video/" + item_id

    # 时间戳转 ISO
    ct = raw.get("createTime")
    published = None
    if isinstance(ct, (int, float)) and ct > 0:
        try:
            from datetime import datetime, timezone
            published = datetime.fromtimestamp(ct, tz=timezone.utc).isoformat()
        except Exception:
            pass

    # 作者信息
    nickname = (raw.get("nickname") or "").strip()
    fans = _int_or_none(raw.get("fans"))

    # 互动数据：搜索结果只有 likes
    likes = _int_or_none(raw.get("likes"))

    # 附加标记
    tags = []
    if raw.get("isLowFansButHot"):
        tags.append("低粉爆款")
    if raw.get("isHighLikeRate"):
        tags.append("高点赞率")
    if raw.get("isHighPlayOverRate"):
        tags.append("高完播")

    return {
        "id": item_id,
        "title": title,
        "url": url,
        "published": published,
        "stats": {
            "views": None,       # 搜索结果没有播放量
            "likes": likes,
            "comments": None,     # 搜索结果没有评论数
        },
        "_author_name": nickname,
        "_author_fans": fans,
        "_tags": tags,
    }


def _int_or_none(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ── 选题库入库 ────────────────────────────────────────────

def _load_ideas() -> list:
    if not IDEAS_FILE.is_file():
        return []
    try:
        data = json.loads(IDEAS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_ideas(items: list) -> None:
    tmp = IDEAS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(IDEAS_FILE)


_URL_IN_NOTE = re.compile(r"(https?://\S+)")


def _existing_keys(ideas: list) -> set:
    """去重 key 集合：url 优先，note 兜底。"""
    keys = set()
    for it in ideas:
        url = (it.get("url") or "").strip()
        if url:
            keys.add(url)
            continue
        note = it.get("note") or ""
        m = _URL_IN_NOTE.search(note)
        if m:
            keys.add(m.group(1))
    return keys


def _format_count(n) -> str:
    if n is None:
        return ""
    if n >= 10000:
        v = n / 10000
        return ("%.1f万" % v).rstrip("0").rstrip(".")
    return str(n)


def _make_idea(item: dict) -> dict:
    """把搜索结果转成选题库条目。"""
    title = item["title"][:60] if item["title"] else "（无标题）"
    stats = item["stats"]
    parts = []
    if stats.get("likes") is not None:
        parts.append("点赞 " + _format_count(stats["likes"]))
    if item.get("_author_fans") is not None:
        parts.append("作者粉丝 " + _format_count(item["_author_fans"]))
    if item.get("_author_name"):
        parts.append("作者 " + item["_author_name"])
    if item.get("_tags"):
        parts.append(" ".join(item["_tags"]))
    note = " · ".join(parts) + (" · " + item["url"] if item["url"] else "")

    return {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "note": note,
        "source": "搜索-抖音",
        "status": "pending",
        "created": int(time.time()),
        "url": item["url"],
        "stats": item["stats"],
    }


# ── 子命令 ────────────────────────────────────────────────

def cmd_search(args: argparse.Namespace) -> int:
    token = _token()
    print("搜索关键词：%s（预计消耗 $%.2f）" % (args.keyword, COST_PER_SEARCH))
    items = search_videos(args.keyword, args.limit, token,
                          date_type=args.date_type, label_type=args.label_type)

    if args.json:
        out = {
            "platform": "douyin",
            "author": {"id": "search", "name": "关键词：" + args.keyword, "url": ""},
            "items": items,
            "fetched_at": int(time.time()),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if not items:
            print("（无结果）")
            return 0
        print("共 %d 条：\n" % len(items))
        for i, it in enumerate(items, 1):
            s = it["stats"]
            line = "  [%d] %s" % (i, it["title"])
            print(line)
            parts = []
            if s.get("likes") is not None:
                parts.append("点赞 " + _format_count(s["likes"]))
            if it.get("_author_name"):
                parts.append("作者 " + it["_author_name"])
            if it.get("_author_fans") is not None:
                parts.append("粉丝 " + _format_count(it["_author_fans"]))
            if it.get("_tags"):
                parts.append(" ".join(it["_tags"]))
            if parts:
                print("      " + " · ".join(parts))
            print("      " + it["url"])
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    token = _token()
    min_likes = args.min_likes

    print("搜索关键词：%s（预计消耗 $%.2f）" % (args.keyword, COST_PER_SEARCH))
    items = search_videos(args.keyword, args.limit, token,
                          date_type=args.date_type, label_type=args.label_type)

    if not items:
        print("（无搜索结果）")
        return 0

    # 筛选
    filtered = [it for it in items if (it["stats"].get("likes") or 0) >= min_likes]
    skipped_low = len(items) - len(filtered)

    if not filtered:
        print("共 %d 条搜索结果，全部低于 %s 点赞门槛，无入库。" % (len(items), _format_count(min_likes)))
        return 0

    # 去重入库
    ideas = _load_ideas()
    existing = _existing_keys(ideas)

    added = 0
    skipped_dup = 0
    for it in filtered:
        url = it["url"]
        if url in existing:
            skipped_dup += 1
            continue
        idea = _make_idea(it)
        ideas.insert(0, idea)
        existing.add(url)
        added += 1
        print("  + %s" % idea["note"])

    if added > 0:
        _save_ideas(ideas)

    print("\n共 %d 条搜索结果，筛选后 %d 条达标（>= %s 点赞）。" % (
        len(items), len(filtered), _format_count(min_likes)))
    print("新增 %d 条，去重跳过 %d 条，低于门槛跳过 %d 条 → %s" % (
        added, skipped_dup, skipped_low, IDEAS_FILE))
    return 0


# ── CLI ───────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="抖音关键词搜索采集（TikHub，按次计费 $0.01/次）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("search", help="搜索并展示结果（不入库）")
    p1.add_argument("--keyword", required=True, help="搜索关键词")
    p1.add_argument("--limit", type=int, default=20, help="返回条数（默认 20）")
    p1.add_argument("--label-type", type=int, default=0,
                    help="视频类型筛选：0=不限, 1=低粉爆款, 2=高完播率, 3=高涨粉率, 4=高点赞率")
    p1.add_argument("--date-type", type=int, default=0,
                    help="发布时间筛选：0=不限, 3=近3天, 7=近7天, 30=近一个月")
    p1.add_argument("--json", action="store_true", help="输出 JSON")
    p1.set_defaults(fn=cmd_search)

    p2 = sub.add_parser("push", help="搜索并筛选后写入选题库")
    p2.add_argument("--keyword", required=True, help="搜索关键词")
    p2.add_argument("--limit", type=int, default=20, help="返回条数（默认 20）")
    p2.add_argument("--min-likes", type=int, default=50000,
                    help="最低点赞数门槛（默认 50000 = 5万）")
    p2.add_argument("--label-type", type=int, default=0,
                    help="视频类型筛选：0=不限, 1=低粉爆款, 2=高完播率, 3=高涨粉率, 4=高点赞率")
    p2.add_argument("--date-type", type=int, default=0,
                    help="发布时间筛选：0=不限, 3=近3天, 7=近7天, 30=近一个月")
    p2.set_defaults(fn=cmd_push)

    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

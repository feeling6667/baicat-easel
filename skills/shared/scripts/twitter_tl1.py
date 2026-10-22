#!/usr/bin/env python3
"""twitter_tl1.py — 抓取 TL1 (tl1.com/trending) 中文推特实时爆款与趋势长帖，入库选题库。

特点：
- 零 API 费用、无需 Twitter/X 官方 key、无需任何账号登录
- 采集中文区真实爆款推文，含实时互动数据 (currentStats: viewCount / likeCount / replyCount / retweetCount)
- 提取正文故事、作者信息与原推链接

用法：
    python twitter_tl1.py fetch [--limit 20] [--json]
    python twitter_tl1.py push [--keyword 关键词] [--min-views 100000] [--min-likes 1000] [--limit 20]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Windows 控制台默认 GBK，推特热帖常含多语言字符，这里强制成 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
IDEAS_FILE = ROOT / "outputs" / "_ideas.json"

TL1_URL = "https://tl1.com/trending"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _fetch_html() -> str:
    cmd = [
        "curl.exe", "-s", "-L",
        "-A", UA,
        "--max-time", "25",
        TL1_URL
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.stdout or ""


def fetch_trending_tweets(limit: int = 50) -> list[dict]:
    """从 tl1.com HTML 提取 window.__BOOTSTRAP__ 中的热帖列表。"""
    html = _fetch_html()
    if not html:
        raise RuntimeError("无法访问 tl1.com，请检查网络连接")
    
    m = re.search(r'window\.__BOOTSTRAP__\s*=\s*(\{.*?\});', html, re.DOTALL)
    if not m:
        raise RuntimeError("未能从 tl1.com 页面提取到热帖数据")
    
    try:
        data = json.loads(m.group(1))
    except Exception as e:
        raise RuntimeError(f"解析 tl1 数据失败: {e}")
    
    raw_items = data.get("trending", {}).get("items", [])
    if not isinstance(raw_items, list):
        return []
    
    tweets = []
    for raw in raw_items[:limit]:
        tweets.append(_norm_tweet(raw))
    return tweets


def _norm_tweet(raw: dict) -> dict:
    tweet_id = str(raw.get("tweetId", ""))
    handle = str(raw.get("authorHandle", "")).strip()
    name = str(raw.get("displayName", "")).strip() or handle
    content = str(raw.get("content", "")).strip()
    
    # 提取互动量
    stats_raw = raw.get("currentStats") or {}
    views = stats_raw.get("viewCount")
    likes = stats_raw.get("likeCount")
    replies = stats_raw.get("replyCount")
    retweets = stats_raw.get("retweetCount")
    
    url = f"https://x.com/{handle}/status/{tweet_id}" if handle and tweet_id else ""
    
    # 清洗短标题：取第一行或前40字作为标题
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    first_line = lines[0] if lines else "推特热帖"
    # 去除结尾的链接 t.co
    first_line = re.sub(r'https?://t\.co/\S+', '', first_line).strip()
    title = first_line[:50] if first_line else "推特热帖"
    
    return {
        "id": tweet_id,
        "title": title,
        "content": content,
        "url": url,
        "published": raw.get("publishTime"),
        "author": {
            "name": name,
            "handle": handle,
            "followers": raw.get("followersCount")
        },
        "stats": {
            "views": views,
            "likes": likes,
            "comments": replies,
            "retweets": retweets
        },
        "heat": raw.get("heat", "HOT")
    }


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
    stats = item["stats"]
    parts = []
    if stats.get("views") is not None:
        parts.append("浏览 " + _format_count(stats["views"]))
    if stats.get("likes") is not None:
        parts.append("点赞 " + _format_count(stats["likes"]))
    if stats.get("comments") is not None:
        parts.append("评论 " + _format_count(stats["comments"]))
    
    author = item.get("author") or {}
    if author.get("name"):
        parts.append(f"作者 {author['name']} (@{author.get('handle', '')})")
    
    # 附带推文前段摘要
    content_clean = item["content"].replace("\n", " ")
    content_clean = re.sub(r'https?://t\.co/\S+', '', content_clean).strip()
    if content_clean:
        parts.append(content_clean[:80])
        
    note = " · ".join(parts) + (f" · {item['url']}" if item["url"] else "")
    
    return {
        "id": uuid.uuid4().hex[:12],
        "title": item["title"],
        "note": note,
        "source": "热帖-Twitter",
        "status": "pending",
        "created": int(time.time()),
        "url": item["url"],
        "stats": item["stats"]
    }


def cmd_fetch(args: argparse.Namespace) -> int:
    tweets = fetch_trending_tweets(args.limit)
    if args.json:
        print(json.dumps(tweets, ensure_ascii=False, indent=2))
        return 0
        
    print(f"从 tl1.com 成功获取 {len(tweets)} 条实时热帖：\n")
    for i, it in enumerate(tweets, 1):
        s = it["stats"]
        print(f"[{i}] {it['title']}")
        print(f"    作者: {it['author']['name']} (@{it['author']['handle']})")
        print(f"    数据: 浏览 {_format_count(s['views'])} · 点赞 {_format_count(s['likes'])} · 评论 {_format_count(s['comments'])}")
        print(f"    内容: {it['content'][:90]}...")
        print(f"    链接: {it['url']}\n")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    tweets = fetch_trending_tweets(50)
    keyword = (args.keyword or "").strip()
    min_views = args.min_views
    min_likes = args.min_likes
    
    filtered = []
    for it in tweets:
        # 关键词过滤（若指定）
        if keyword and (keyword not in it["content"] and keyword not in it["title"]):
            continue
        v = it["stats"].get("views") or 0
        l = it["stats"].get("likes") or 0
        if v < min_views or l < min_likes:
            continue
        filtered.append(it)
        
    filtered = filtered[:args.limit]
    
    if not filtered:
        print(f"本次未筛选出符合门槛 (关键词='{keyword}', 浏览>={min_views}, 点赞>={min_likes}) 的推文。")
        return 0
        
    ideas = _load_ideas()
    existing = _existing_keys(ideas)
    
    added = 0
    skipped_dup = 0
    for it in filtered:
        if it["url"] in existing:
            skipped_dup += 1
            continue
        idea = _make_idea(it)
        ideas.insert(0, idea)
        existing.add(it["url"])
        added += 1
        # Windows 控制台可能是 GBK，闭眼打印多语文本会抛 UnicodeEncodeError
        def _safe(s: str) -> str:
            return (s or "").encode("utf-8", "replace").decode("utf-8", "replace")
        print(f"  + [{_safe(idea['source'])}] {_safe(idea['title'])} ({_format_count(it['stats']['views'])} 浏览)")
        
    if added > 0:
        _save_ideas(ideas)
        
    print(f"\n推特热帖筛选: 达标 {len(filtered)} 条, 新增入库 {added} 条, 去重跳过 {skipped_dup} 条。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="TL1 中文推特趋势热帖抓取（完全免费、无API依赖）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    
    p1 = sub.add_parser("fetch", help="查看实时推特热帖")
    p1.add_argument("--limit", type=int, default=20, help="获取条数 (默认 20)")
    p1.add_argument("--json", action="store_true", help="JSON 格式输出")
    p1.set_defaults(fn=cmd_fetch)
    
    p2 = sub.add_parser("push", help="筛选高爆推特热帖入选题库")
    p2.add_argument("--keyword", default="", help="关键词筛选 (支持家庭/故事/工作等，留空不限)")
    p2.add_argument("--min-views", type=int, default=50000, help="最低浏览量门槛 (默认 5万)")
    p2.add_argument("--min-likes", type=int, default=500, help="最低点赞数门槛 (默认 500)")
    p2.add_argument("--limit", type=int, default=20, help="最大入库条数")
    p2.set_defaults(fn=cmd_push)
    
    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""douyin_data.py — 抖音结构化数据采集（TikHub API，按次计费）。

补齐 tikhub_search.py 之外的三类采集：
    user      博主主页作品列表
    comments  单条作品评论
    feed      首页推荐流
    search    关键词搜索（转调 tikhub_search.py，保持单一实现）

TikHub API key 统一从项目根 .env 的 TIKHUB_API_KEY 读取，本脚本不管理 key。

用法：
    python douyin_data.py user     --sec-user-id MS4wLjABAAAA... [--limit 50]
    python douyin_data.py comments --aweme-id 7xxxxxxxxxx [--limit 200]
    python douyin_data.py feed     [--limit 30]
    python douyin_data.py search   --keyword 露营装备 [--limit 20]

输出：统一 item 格式（与 creator_feed.py / bili_creator.py 一致），
      落盘 outputs/_douyin/{kind}_{key}_{时间戳}.json，--json 时同时打印到 stdout。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / ".env"
OUT_DIR = ROOT / "outputs" / "_douyin"
IDEAS_FILE = ROOT / "outputs" / "_ideas.json"

BASE_URL = "https://api.tikhub.io"
# 用 APP 版接口而不是 WEB 版：TikHub 文档明确提示 WEB 版不稳定（实测 400），
# 且 APP 版 max_cursor 是 integer，与 WEB 版的 string 不同。
API_USER_POSTS = BASE_URL + "/api/v1/douyin/app/v3/fetch_user_post_videos"
API_COMMENTS = BASE_URL + "/api/v1/douyin/web/fetch_video_comments"
API_FEED = BASE_URL + "/api/v1/douyin/web/fetch_home_feed"
API_PROFILE = BASE_URL + "/api/v1/douyin/web/handler_user_profile"

# 按次计费单价（美元），仅用于提示
COST_PER_CALL = 0.01


# ── 配置 ──────────────────────────────────────────────────
def _load_token() -> str:
    """从项目根 .env 读 TIKHUB_API_KEY。"""
    if not ENV_FILE.is_file():
        raise SystemExit("缺少 .env 文件：%s" % ENV_FILE)
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("TIKHUB_API_KEY="):
            tok = line.split("=", 1)[1].strip().strip("'\"")
            if tok:
                return tok
    raise SystemExit(".env 里没有 TIKHUB_API_KEY")


# ── HTTP ──────────────────────────────────────────────────
def _headers(token: str) -> dict:
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "User-Agent": "Easel/1.0",
    }


def _http_get(url: str, params: dict, token: str, timeout: int = 90) -> dict:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url + "?" + qs, headers=_headers(token), method="GET")
    return _read(req, timeout)


def _http_post(url: str, body: dict, token: str, timeout: int = 90) -> dict:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = _headers(token)
    h["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=payload, headers=h, method="POST")
    return _read(req, timeout)


def _read(req: urllib.request.Request, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        if e.code == 401:
            raise SystemExit("TikHub 401：API key 无效或已过期。检查 .env 的 TIKHUB_API_KEY")
        if e.code == 429:
            raise SystemExit("TikHub 429：触发限流，降低频率或检查余额")
        raise SystemExit("TikHub HTTP %s：%s" % (e.code, body))
    except urllib.error.URLError as e:
        raise SystemExit("网络错误：%s" % e)
    if data.get("code") not in (0, 200, None):
        raise SystemExit("接口返回 code=%s：%s" % (data.get("code"), str(data.get("message"))[:200]))
    return data


# ── 归一化 ────────────────────────────────────────────────
def _int_or_none(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _iso(ts) -> str | None:
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return None
    return None


def norm_aweme(a: dict) -> dict:
    """作品对象 → 统一 item 格式。"""
    aid = str(a.get("aweme_id") or "")
    desc = (a.get("desc") or "").strip()
    author = a.get("author") or {}
    stat = a.get("statistics") or {}
    video = a.get("video") or {}
    dur = video.get("duration")
    tags = [t.get("cha_name") for t in (a.get("text_extra") or []) if t.get("cha_name")]

    return {
        "id": aid,
        "title": desc[:200],
        "url": "https://www.douyin.com/video/" + aid if aid else "",
        "published": _iso(a.get("create_time")),
        "stats": {
            "views": _int_or_none(stat.get("play_count")),
            "likes": _int_or_none(stat.get("digg_count")),
            "comments": _int_or_none(stat.get("comment_count")),
            "shares": _int_or_none(stat.get("share_count")),
            "collects": _int_or_none(stat.get("collect_count")),
        },
        "_author_name": (author.get("nickname") or "").strip(),
        "_author_sec_id": author.get("sec_uid") or "",
        "_author_fans": _int_or_none(author.get("follower_count")),
        "_duration_ms": _int_or_none(dur),
        "_tags": tags,
        "_is_top": bool(a.get("is_top")),
    }


def norm_comment(c: dict, aweme_id: str = "") -> dict:
    """评论对象 → 统一 item 格式。"""
    user = c.get("user") or {}
    cid = str(c.get("cid") or "")
    return {
        "id": cid,
        "title": (c.get("text") or "").strip()[:200],
        "url": "https://www.douyin.com/video/" + aweme_id if aweme_id else "",
        "published": _iso(c.get("create_time")),
        "stats": {
            "likes": _int_or_none(c.get("digg_count")),
            "replies": _int_or_none(c.get("reply_comment_total")),
        },
        "_aweme_id": aweme_id or str(c.get("aweme_id") or ""),
        "_author_name": (user.get("nickname") or "").strip(),
        "_author_sec_id": user.get("sec_uid") or "",
        "_ip_label": c.get("ip_label") or "",
        "_is_author": bool(c.get("is_author")),
        "_parent_id": c.get("reply_id") or 0,
    }


# ── 响应解包 ──────────────────────────────────────────────
def _data(resp: dict) -> dict:
    d = resp.get("data")
    return d if isinstance(d, dict) else {}


def _aweme_list(resp: dict) -> list:
    d = _data(resp)
    for key in ("aweme_list", "data", "item_list"):
        v = d.get(key)
        if isinstance(v, list) and v:
            if isinstance(v[0], dict) and "aweme_info" in v[0]:
                return [x["aweme_info"] for x in v if isinstance(x, dict) and x.get("aweme_info")]
            return [x for x in v if isinstance(x, dict)]
    return []


# ── 采集器 ────────────────────────────────────────────────
def fetch_user_posts(sec_user_id: str, limit: int, token: str, verbose: bool = True) -> list:
    """翻页抓博主主页作品。limit<=0 表示抓到没有更多为止。"""
    out, cursor, page = [], 0, 0
    while True:
        resp = _http_get(API_USER_POSTS, {
            "sec_user_id": sec_user_id,
            "max_cursor": cursor,      # APP 版要求 integer
            "count": 20,
            "sort_type": 0,
        }, token)
        d = _data(resp)
        items = _aweme_list(resp)
        if not items:
            break
        out.extend(norm_aweme(x) for x in items)
        page += 1
        if verbose:
            print("  第 %d 页，累计 %d 条" % (page, len(out)), file=sys.stderr)
        more = d.get("has_more")
        try:
            nxt = int(d.get("max_cursor") or 0)
        except (TypeError, ValueError):
            break
        if limit and len(out) >= limit:
            break
        if more not in (1, True) or nxt == cursor or nxt == 0:
            break
        cursor = nxt
        time.sleep(0.4)
    return out[:limit] if limit else out


def fetch_comments(aweme_id: str, limit: int, token: str, verbose: bool = True) -> list:
    out, cursor, page = [], 0, 0
    while len(out) < limit:
        resp = _http_get(API_COMMENTS, {
            "aweme_id": aweme_id, "cursor": cursor, "count": 20,
        }, token)
        d = _data(resp)
        raw = d.get("comments")
        if not isinstance(raw, list) or not raw:
            break
        out.extend(norm_comment(c, aweme_id) for c in raw if isinstance(c, dict))
        page += 1
        if verbose:
            print("  第 %d 页，累计 %d 条" % (page, len(out)), file=sys.stderr)
        nxt = d.get("cursor")
        more = d.get("has_more")
        try:
            nxt = int(nxt)
        except (TypeError, ValueError):
            break
        if more not in (1, True) or nxt == cursor:
            break
        cursor = nxt
        time.sleep(0.4)
    return out[:limit]


def fetch_feed(limit: int, token: str, verbose: bool = True) -> list:
    out, refresh = [], 0
    while len(out) < limit and refresh < 40:
        resp = _http_post(API_FEED, {"count": 10, "refresh_index": refresh}, token)
        items = _aweme_list(resp)
        if not items:
            break
        out.extend(norm_aweme(x) for x in items)
        refresh += 1
        if verbose:
            print("  第 %d 批，累计 %d 条" % (refresh, len(out)), file=sys.stderr)
        time.sleep(0.3)
    return out[:limit]


def fetch_search(keyword: str, limit: int, token: str) -> list:
    """转调 tikhub_search.py，保持搜索只有一份实现。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import tikhub_search as TS
    except Exception as e:
        raise SystemExit("无法导入 tikhub_search.py：%s" % e)
    return TS.search_videos(keyword, limit, token)


# ── 落盘 ──────────────────────────────────────────────────
def save(items: list, kind: str, key: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in (key or "all")[:32] if ch.isalnum() or ch in "-_") or "all"
    path = OUT_DIR / ("%s_%s_%d.json" % (kind, safe, int(time.time())))
    path.write_text(json.dumps({
        "kind": kind,
        "key": key,
        "count": len(items),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def push_ideas(items: list, source: str) -> int:
    """写入选题库 outputs/_ideas.json，按 url 去重。"""
    ideas = []
    if IDEAS_FILE.is_file():
        try:
            data = json.loads(IDEAS_FILE.read_text(encoding="utf-8"))
            ideas = data if isinstance(data, list) else []
        except Exception:
            ideas = []
    seen = {(i.get("url") or "").strip() for i in ideas if i.get("url")}
    added = 0
    for it in items:
        url = (it.get("url") or "").strip()
        if not url or url in seen:
            continue
        st = it.get("stats") or {}
        parts = []
        if st.get("likes") is not None:
            parts.append("点赞 %s" % st["likes"])
        if st.get("comments") is not None:
            parts.append("评论 %s" % st["comments"])
        if it.get("_author_name"):
            parts.append("作者 %s" % it["_author_name"])
        ideas.append({
            "id": uuid.uuid4().hex[:12],
            "title": (it.get("title") or "（无标题）")[:60],
            "note": " · ".join(parts) + " · " + url,
            "source": source,
            "status": "pending",
            "created": int(time.time()),
            "url": url,
            "stats": st,
        })
        seen.add(url)
        added += 1
    if added:
        IDEAS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = IDEAS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ideas, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(IDEAS_FILE)
    return added


# ── 输出 ──────────────────────────────────────────────────
def emit(items: list, as_json: bool):
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=2))


def summary(items: list, kind: str, label: str):
    print("%s：%s（%d 条）" % (kind, label, len(items)))
    for it in items[:10]:
        st = it.get("stats") or {}
        bits = []
        if st.get("likes") is not None:
            bits.append("赞 %s" % st["likes"])
        if st.get("comments") is not None:
            bits.append("评 %s" % st["comments"])
        print("  - %s%s  %s" % (
            (it.get("title") or "")[:46],
            ("  [" + " ".join(bits) + "]") if bits else "",
            it.get("url") or "",
        ))
    if len(items) > 10:
        print("  … 还有 %d 条，见落盘 JSON" % (len(items) - 10))


# ── 子命令 ────────────────────────────────────────────────
def cmd_user(args) -> int:
    token = _load_token()
    print("采集博主作品：%s（每次调用约 $%.2f）" % (args.sec_user_id, COST_PER_CALL))
    items = fetch_user_posts(args.sec_user_id, args.limit, token)
    path = save(items, "user", args.sec_user_id)
    summary(items, "博主作品", args.sec_user_id)
    print("已保存：" + str(path))
    if args.push:
        print("写入选题库：%d 条" % push_ideas(items, "采集-抖音博主"))
    emit(items, args.json)
    return 0


def cmd_comments(args) -> int:
    token = _load_token()
    print("采集评论：%s（每次调用约 $%.2f）" % (args.aweme_id, COST_PER_CALL))
    items = fetch_comments(args.aweme_id, args.limit, token)
    path = save(items, "comments", args.aweme_id)
    summary(items, "评论", args.aweme_id)
    print("已保存：" + str(path))
    emit(items, args.json)
    return 0


def cmd_feed(args) -> int:
    token = _load_token()
    print("采集推荐流（每次调用约 $%.2f）" % COST_PER_CALL)
    items = fetch_feed(args.limit, token)
    path = save(items, "feed", "home")
    summary(items, "推荐流", "home")
    print("已保存：" + str(path))
    if args.push:
        print("写入选题库：%d 条" % push_ideas(items, "采集-抖音推荐流"))
    emit(items, args.json)
    return 0


def cmd_search(args) -> int:
    token = _load_token()
    print("搜索关键词：%s（每次调用约 $%.2f）" % (args.keyword, COST_PER_CALL))
    items = fetch_search(args.keyword, args.limit, token)
    path = save(items, "search", args.keyword)
    summary(items, "搜索结果", args.keyword)
    print("已保存：" + str(path))
    if args.push:
        print("写入选题库：%d 条" % push_ideas(items, "搜索-抖音"))
    emit(items, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="抖音结构化数据采集（TikHub API）")
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("user", help="博主主页作品列表")
    u.add_argument("--sec-user-id", required=True, help="博主 sec_user_id（MS4wLjABAAAA...）")
    u.add_argument("--limit", type=int, default=0, help="最多抓多少条，0=全部")
    u.add_argument("--json", action="store_true", help="把结果打印到 stdout")
    u.add_argument("--push", action="store_true", help="写入 outputs/_ideas.json 选题库")
    u.set_defaults(func=cmd_user)

    c = sub.add_parser("comments", help="单条作品的评论")
    c.add_argument("--aweme-id", required=True, help="作品 id")
    c.add_argument("--limit", type=int, default=200, help="最多抓多少条")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_comments)

    f = sub.add_parser("feed", help="首页推荐流")
    f.add_argument("--limit", type=int, default=30, help="最多抓多少条")
    f.add_argument("--json", action="store_true")
    f.add_argument("--push", action="store_true")
    f.set_defaults(func=cmd_feed)

    s = sub.add_parser("search", help="关键词搜索（转调 tikhub_search.py）")
    s.add_argument("--keyword", required=True, help="搜索关键词")
    s.add_argument("--limit", type=int, default=20, help="最多抓多少条")
    s.add_argument("--json", action="store_true")
    s.add_argument("--push", action="store_true")
    s.set_defaults(func=cmd_search)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

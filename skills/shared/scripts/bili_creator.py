#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Easel · 对标作者作品采集 — B站适配器

纯游客态公开接口（不读取、不使用任何账号或登录态），零第三方依赖（urllib + hashlib + json）。

用法:
  python bili_creator.py fetch   --author <mid|空间URL> --limit 20 [--json]
  python bili_creator.py authors add --author <mid> --name <名字>
  python bili_creator.py authors list
  python bili_creator.py push    --author <mid> --limit 20 --min-views <N>

统一输出格式（与 YouTube 适配器一致）:
  {"platform":"bilibili","author":{"id","name","url"},"items":[{"id","title","url",
   "published","stats":{"views","likes","comments"}}],"fetched_at":<int>}
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------- 路径 / 常量

ROOT = Path(__file__).resolve().parents[3]
OUTPUTS = ROOT / "outputs"
CREATORS_FILE = OUTPUTS / "_creators.json"
IDEAS_FILE = OUTPUTS / "_ideas.json"

API = "https://api.bilibili.com"
SPACE_REFERER = "https://space.bilibili.com/"
WWW_REFERER = "https://www.bilibili.com/"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

CST = timezone(timedelta(hours=8))

URL_IN_NOTE_RE = re.compile(r"https?://\S+")  # 老数据去重：从 note 里抠链接

# 关键词搜索
SEARCH_URL = f"{API}/x/web-interface/wbi/search/type"
SEARCH_PAGE_SIZE = 50                      # 接口单页上限
SEARCH_ORDER = "click"                     # 默认「最多播放」，服务“找高赞作品”的诉求
SEARCH_ORDER_CHOICES = ("click", "totalrank", "pubdate", "dm", "stow")
SEARCH_REFERER = "https://search.bilibili.com/"
EM_TAG_RE = re.compile(r"</?em\b[^>]*>", re.I)   # 搜索高亮标签
ANY_TAG_RE = re.compile(r"<[^>]+>")
COUNT_WAN_RE = re.compile(r"^([\d.]+)\s*万$")
COUNT_YI_RE = re.compile(r"^([\d.]+)\s*亿$")

# wbi 混淆表（64 下标），见 bilibili-API-collect docs/misc/sign/wbi.md
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]
CHR_FILTER = "!'()*"

# 文档给出的自检向量: img_key+sub_key -> mixin_key
MIXIN_KEY_SELFTEST = (
    "7cd084941338484aae1ad9425b84077c",
    "4932caff0ff746eab6f01bf08b70ac45",
    "ea1db124af3c7062474693fa704f4ff8",
)


class BiliError(RuntimeError):
    """接口层错误；payload 保留原始响应，便于如实回报。"""

    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload


# ---------------------------------------------------------------- HTTP 层

_COOKIES: dict[str, str] = {}
_WBI_CACHE: dict[str, str] = {}


def _absorb_set_cookie(headers):
    for raw in headers.get_all("Set-Cookie") or []:
        head = raw.split(";", 1)[0]
        if "=" in head:
            k, v = head.split("=", 1)
            _COOKIES[k.strip()] = v.strip()


def _base_headers(referer, origin=None):
    """浏览器等价的最小头部集；游客态，不含任何账号凭证。"""
    headers = {
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Connection": "keep-alive",
    }
    if origin:
        headers["Origin"] = origin
    if _COOKIES:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in _COOKIES.items())
    return headers


def _http_get(url, params=None, referer=WWW_REFERER, timeout=20, origin=None):
    """GET 一个 URL，返回 (bytes, http_status)。游客态：只带公开 UA/Referer/匿名 cookie。"""
    if params:
        qs = parse.urlencode(params, quote_via=parse.quote, safe="")
        url = f"{url}{'&' if '?' in url else '?'}{qs}"
    headers = _base_headers(referer, origin)
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            _absorb_set_cookie(resp.headers)
            return raw, resp.status
    except HTTPError as exc:  # 4xx/5xx 也把响应体带回来，便于诊断
        raw = exc.read()
        _absorb_set_cookie(exc.headers)
        return raw, exc.code
    except URLError as exc:
        raise BiliError(f"网络不可达: {exc.reason}") from exc


def _json_get(url, params=None, referer=WWW_REFERER, allow_codes=()):
    raw, status = _http_get(url, params, referer)
    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        snippet = text[:300].replace("\n", " ")
        raise BiliError(
            f"返回非 JSON (HTTP {status})，疑似风控/HTML 页面: {snippet}"
        )
    code = data.get("code")
    if code != 0 and code not in allow_codes:
        raise BiliError(
            f"接口 {url} 返回 code={code} message={data.get('message')!r}",
            data,
        )
    return data


def _http_post(url, body=b"", referer=WWW_REFERER, timeout=20, origin=None):
    headers = _base_headers(referer, origin)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            _absorb_set_cookie(resp.headers)
            return raw, resp.status
    except HTTPError as exc:
        raw = exc.read()
        _absorb_set_cookie(exc.headers)
        return raw, exc.code
    except URLError as exc:
        raise BiliError(f"网络不可达: {exc.reason}") from exc


def _ensure_anonymous_cookie():
    """取匿名 buvid3/buvid4（纯游客指纹，与账号无关），提高空间接口成功率。"""
    if "buvid3" in _COOKIES:
        return
    try:
        data = _json_get(f"{API}/x/frontend/finger/spi", referer=WWW_REFERER)
    except BiliError:
        return
    payload = data.get("data") or {}
    if payload.get("b_3"):
        _COOKIES["buvid3"] = payload["b_3"]
    if payload.get("b_4"):
        _COOKIES["buvid4"] = payload["b_4"]


# bili_ticket 的公开 web 签发 key（游客态，无需登录）
_TICKET_KEY = b"XgwSnGZ1p"


def gen_bili_ticket():
    """申请匿名 bili_ticket（降低空间接口风控概率），顺带返回 nav 里的 wbi 图床名。"""
    ts = int(time.time())
    hexsign = hmac.new(_TICKET_KEY, f"ts{ts}".encode(), hashlib.sha256).hexdigest()
    url = (f"{API}/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
           f"?key_id=ec02&hexsign={hexsign}&context%5Bts%5D={ts}&csrf=")
    raw, status = _http_post(url, b"", referer=WWW_REFERER, origin=WWW_REFERER)
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    if data.get("code") != 0:
        return None
    payload = data.get("data") or {}
    if payload.get("ticket"):
        _COOKIES["bili_ticket"] = payload["ticket"]
        if payload.get("ttl"):
            _COOKIES["bili_ticket_expires"] = str(int(payload.get("created_at", ts)) + int(payload["ttl"]))
    nav = payload.get("nav") or {}
    return nav or None


# ---------------------------------------------------------------- wbi 签名


def mixin_key_of(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def enc_wbi(params: dict, img_key: str, sub_key: str, wts=None) -> dict:
    """按 wbi 文档为请求参数签名，返回带 wts / w_rid 的新 dict。"""
    mixin_key = mixin_key_of(img_key, sub_key)
    signed = {k: v for k, v in params.items() if v is not None}
    signed["wts"] = int(wts if wts is not None else time.time())
    signed = dict(sorted(signed.items()))
    signed = {
        k: "".join(c for c in str(v) if c not in CHR_FILTER)
        for k, v in signed.items()
    }
    query = parse.urlencode(signed, quote_via=parse.quote, safe="")
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return signed


def _keys_from_urls(img_url, sub_url):
    img_key = (img_url or "").rsplit("/", 1)[-1].split(".")[0]
    sub_key = (sub_url or "").rsplit("/", 1)[-1].split(".")[0]
    return img_key, sub_key


def get_wbi_keys(refresh=False):
    """取实时 img_key / sub_key（未登录时 nav 的 code=-101，但 data.wbi_img 仍可用）。"""
    if _WBI_CACHE and not refresh:
        return _WBI_CACHE["img_key"], _WBI_CACHE["sub_key"]
    _ensure_anonymous_cookie()

    nav = gen_bili_ticket() or {}
    img_key, sub_key = _keys_from_urls(nav.get("img"), nav.get("sub"))
    if not img_key or not sub_key:
        data = _json_get(f"{API}/x/web-interface/nav",
                         referer=WWW_REFERER, allow_codes=(-101,))
        wbi_img = (data.get("data") or {}).get("wbi_img") or {}
        img_key, sub_key = _keys_from_urls(wbi_img.get("img_url"), wbi_img.get("sub_url"))
    if not img_key or not sub_key:
        raise BiliError("未能取到 wbi img_key/sub_key")
    _WBI_CACHE["img_key"] = img_key
    _WBI_CACHE["sub_key"] = sub_key
    return img_key, sub_key


# ---------------------------------------------------------------- 数据抓取


def resolve_mid(author: str) -> str:
    s = str(author or "").strip()
    m = re.search(r"space\.bilibili\.com/(\d+)", s) or re.search(r"(\d{3,})", s)
    if not m:
        raise BiliError(f"无法从 {author!r} 解析出 mid（需纯数字 mid 或 space.bilibili.com/<mid> URL）")
    return m.group(1)


def _author_name_from_card(mid: str):
    try:
        data = _json_get(f"{API}/x/web-interface/card",
                         {"mid": mid, "photo": "false"}, referer=SPACE_REFERER)
    except BiliError:
        return None
    card = (data.get("data") or {}).get("card") or {}
    return card.get("name") or None


def _author_name_from_view(bvid: str):
    """用稿件详情接口取 UP 主昵称（公开接口，通常无风控）。"""
    if not bvid:
        return None
    try:
        data = _json_get(f"{API}/x/web-interface/view", {"bvid": bvid}, referer=WWW_REFERER)
    except BiliError:
        return None
    return ((data.get("data") or {}).get("owner") or {}).get("name") or None


def resolve_author_name(mid: str, bvid: str = None):
    return _author_name_from_view(bvid) or _author_name_from_card(mid)


# 风控类返回码：换新匿名指纹 + 退避重试
_RISK_CODES = (-352, -412, -403)
_RETRY_DELAYS = (4, 12, 30, 60)


def _wbi_json_get(url: str, build_params, referer: str, validate=None) -> dict:
    """带 wbi 签名的 GET，遇风控自动换匿名指纹 + 退避重试（全站共用一套策略）。

    validate(data) 用于识别「HTTP/code 都正常但其实是风控」的软失败（如只回 v_voucher），
    由它抛 BiliError 触发同一套重试。
    """
    last = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        if attempt:
            _COOKIES.clear()
            _WBI_CACHE.clear()
            time.sleep(_RETRY_DELAYS[attempt - 1])
        img_key, sub_key = get_wbi_keys(refresh=bool(attempt))
        params = enc_wbi(build_params(), img_key, sub_key)
        try:
            data = _json_get(url, params, referer=referer)
            if validate is not None:
                validate(data)
            return data
        except BiliError as exc:
            last = exc
            if (exc.payload or {}).get("code") not in _RISK_CODES:
                raise
    raise last


def _space_search(mid: str, ps: int) -> dict:
    """调 x/space/wbi/arc/search（带 wbi 签名）；遇风控自动刷新指纹并退避重试。"""
    return _wbi_json_get(
        f"{API}/x/space/wbi/arc/search",
        lambda: {
            "mid": mid,
            "ps": ps,
            "pn": 1,
            "order": "pubdate",
            "tid": 0,
            "platform": "web",
            "web_location": 1550101,
        },
        referer=f"https://space.bilibili.com/{mid}/video",
    )


def _require_search_result(data: dict):
    """搜索接口的软失败识别：code=0 但只回 v_voucher / 没有 result 列表 = 风控，不是「无结果」。"""
    payload = data.get("data") or {}
    if isinstance(payload.get("result"), list):
        return
    raise BiliError(
        "搜索接口 code=0 但未返回 result 列表（软风控）: "
        + json.dumps(data, ensure_ascii=False)[:300],
        {"code": -352, "message": "search result missing", "data": payload},
    )


def _search_page(keyword: str, page: int, page_size: int, order: str = SEARCH_ORDER) -> dict:
    """调 x/web-interface/wbi/search/type（视频搜索，带 wbi 签名）。"""
    return _wbi_json_get(
        SEARCH_URL,
        lambda: {
            "search_type": "video",
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "order": order,
            "platform": "web",
            "web_location": 1430650,
        },
        referer=SEARCH_REFERER,
        validate=_require_search_result,
    )


def _fallback_search(mid: str, ps: int) -> list:
    """风控兜底：x/series/recArchivesByKeywords（公开、无 wbi、无风控校验），返回 archives 列表。"""
    data = _json_get(f"{API}/x/series/recArchivesByKeywords",
                     {"mid": mid, "keywords": "", "ps": ps, "pn": 1, "orderby": "pubdate"},
                     referer=f"https://space.bilibili.com/{mid}/video")
    return ((data.get("data") or {}).get("archives")) or []


def _norm_item(v: dict) -> dict:
    """把 wbi vlist / 兜底 archives 的条目归一化为统一 item。"""
    bvid = v.get("bvid") or ""
    aid = v.get("aid")
    vid = bvid or (f"av{aid}" if aid else "")
    if not vid:
        return {}
    stat = v.get("stat") if isinstance(v.get("stat"), dict) else {}
    views = stat.get("view")
    likes = stat.get("like")
    comments = stat.get("reply")
    if views is None:
        views = v.get("play")          # wbi vlist 字段名
    if comments is None:
        comments = v.get("comment")    # wbi vlist 的评论数字段
    created = v.get("created")
    if not isinstance(created, (int, float)) or created <= 0:
        created = v.get("pubdate")     # 兜底接口字段名
    published = None
    if isinstance(created, (int, float)) and created > 0:
        published = datetime.fromtimestamp(created, CST).isoformat(timespec="seconds")
    return {
        "id": vid,
        "title": v.get("title") or "",
        "url": (f"https://www.bilibili.com/video/{bvid}" if bvid
                else f"https://www.bilibili.com/video/av{aid}"),
        "published": published,
        "stats": {
            "views": views if isinstance(views, int) else None,
            "likes": likes if isinstance(likes, int) else None,
            "comments": comments if isinstance(comments, int) else None,
        },
    }


def fetch_videos(mid: str, limit: int) -> dict:
    """拉取某 UP 主的投稿列表，返回统一格式 dict。主用 wbi 接口，风控时降级到公开兜底接口。"""
    mid = resolve_mid(mid)
    limit = max(1, int(limit))
    ps = min(limit, 50)  # 本期 limit<=50 只取第一页

    name, items, source = None, [], "wbi"
    try:
        data = _space_search(mid, ps)
        vlist = (((data.get("data") or {}).get("list")) or {}).get("vlist") or []
        for v in vlist[:limit]:
            if isinstance(v, dict):
                name = name or v.get("author") or None
                item = _norm_item(v)
                if item:
                    items.append(item)
    except BiliError as exc:
        if (exc.payload or {}).get("code") not in _RISK_CODES:
            raise
        source = "recArchivesByKeywords"
        for v in _fallback_search(mid, ps)[:limit]:
            if isinstance(v, dict):
                item = _norm_item(v)
                if item:
                    items.append(item)

    if not items:
        raise BiliError(f"未取到 mid={mid} 的投稿（来源 {source}）")
    if not name:
        first_id = items[0].get("id") or ""
        name = resolve_author_name(mid, first_id if first_id.startswith("BV") else None)
    print(f"[bili] 投稿列表来源: {source}  items={len(items)}", file=sys.stderr)

    return {
        "platform": "bilibili",
        "author": {
            "id": mid,
            "name": name,
            "url": f"https://space.bilibili.com/{mid}",
        },
        "items": items,
        "fetched_at": int(time.time()),
    }


# ---------------------------------------------------------------- 关键词搜索


def strip_highlight(text) -> str:
    """剥掉搜索结果的 <em class="keyword"> 高亮标签，并还原 HTML 实体。"""
    if text is None:
        return ""
    cleaned = ANY_TAG_RE.sub("", EM_TAG_RE.sub("", str(text)))
    return html.unescape(cleaned).strip()


def count_of(value):
    """统计量解析：数字直取；'12.3万' 这类文案按单位换算；取不到返回 None。"""
    n = int_or_none(value)
    if n is not None:
        return n
    if isinstance(value, str):
        s = value.strip()
        m = COUNT_WAN_RE.match(s)
        if m:
            return int(float(m.group(1)) * 10_000)
        m = COUNT_YI_RE.match(s)
        if m:
            return int(float(m.group(1)) * 100_000_000)
    return None


def _norm_search_item(v: dict) -> dict:
    """把搜索结果的条目归一化为统一 item（拿不到的指标一律 None，不用 0 冒充）。"""
    bvid = v.get("bvid") or ""
    aid = v.get("id") or v.get("aid")
    vid = bvid or (f"av{aid}" if aid else "")
    if not vid:
        return {}
    created = v.get("pubdate")
    if not isinstance(created, (int, float)) or created <= 0:
        created = v.get("senddate") or v.get("ctime")
    published = None
    if isinstance(created, (int, float)) and created > 0:
        published = datetime.fromtimestamp(created, CST).isoformat(timespec="seconds")
    return {
        "id": vid,
        "title": strip_highlight(v.get("title")),
        "url": (f"https://www.bilibili.com/video/{bvid}" if bvid
                else f"https://www.bilibili.com/video/av{aid}"),
        "published": published,
        "stats": {
            "views": count_of(v.get("play")),
            "likes": count_of(v.get("like")),
            "comments": count_of(v.get("review")),
        },
    }


def search_videos(keyword: str, limit: int, order: str = SEARCH_ORDER) -> dict:
    """按关键词搜索视频，输出格式与 fetch_videos 完全一致。"""
    keyword = (keyword or "").strip()
    if not keyword:
        raise BiliError("--keyword 不能为空")
    limit = max(1, int(limit))
    page_size = min(limit, SEARCH_PAGE_SIZE)

    items, page, total_pages, num_results = [], 1, None, None
    while True:
        data = _search_page(keyword, page, page_size, order)
        payload = data.get("data") or {}
        result = payload.get("result") or []
        num_results = payload.get("numResults")
        total_pages = int_or_none(payload.get("numPages")) or page
        if not isinstance(result, list) or not result:
            break
        for v in result:
            if isinstance(v, dict):
                item = _norm_search_item(v)
                if item:
                    items.append(item)
        if len(items) >= limit or page >= total_pages:
            break
        page += 1
        time.sleep(0.6)  # 翻页间隔，降低风控概率

    if not items:
        raise BiliError(
            f"关键词 {keyword!r} 未搜到任何视频（page={page}, pages={total_pages}, "
            f"numResults={num_results}）"
        )
    print(f"[bili] 关键词搜索: {keyword!r}  order={order}  pages={page}  items={len(items)}",
          file=sys.stderr)

    return {
        "platform": "bilibili",
        "author": {"id": "search", "name": f"关键词：{keyword}", "url": ""},
        "items": items[:limit],
        "fetched_at": int(time.time()),
    }


# ---------------------------------------------------------------- 文件读写（原子 + 合并）


def read_json_array(path: Path):
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def atomic_write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{secrets.token_hex(3)}")
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def merge_json_array(path: Path, mutate):
    """读最新 → 交给 mutate 追加 → 原子替换。保留其它 platform 的条目，绝不整文件覆盖。"""
    arr = read_json_array(path)
    result = mutate(arr)
    if result is False:
        return arr, False
    atomic_write_json(path, arr)
    return arr, True


# ---------------------------------------------------------------- 子命令


def fmt_count(n) -> str:
    if n is None:
        return "N/A"
    n = int(n)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿".replace(".0亿", "亿")
    if n >= 10_000:
        return f"{n / 10_000:.1f}万".replace(".0万", "万")
    return str(n)


def note_of(item: dict) -> str:
    stats = item.get("stats") or {}
    parts = [f"播放 {fmt_count(stats.get('views'))}"]
    if stats.get("likes") is not None:
        parts.append(f"点赞 {fmt_count(stats.get('likes'))}")
    if stats.get("comments") is not None:
        parts.append(f"评论 {fmt_count(stats.get('comments'))}")
    parts.append(item.get("url") or "")
    return " · ".join(p for p in parts if p)


def norm_url(value) -> str:
    """归一化 URL 以便比较：去空白与 note 拼接残留的尾随标点。"""
    return str(value or "").strip().rstrip("·,，。;；:：)）]】>》\"' ")


def int_or_none(value):
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


def existing_keys(arr) -> set:
    """已存在条目的去重键：优先 url 字段，兜底 note 里的链接（老数据只有 note）。"""
    keys = set()
    for e in arr:
        if not isinstance(e, dict):
            continue
        candidates = [e.get("url")]
        candidates.extend(URL_IN_NOTE_RE.findall(str(e.get("note") or "")))
        for candidate in candidates:
            key = norm_url(candidate)
            if key:
                keys.add(key)
    return keys


def idea_entry(item: dict, source: str = "对标-B站") -> dict:
    """按冻结契约生成选题库条目：note 原样保留，另带结构化 url + stats。"""
    stats = item.get("stats") or {}
    title = (item.get("title") or "").strip()
    # B站标题常带几十个话题标签，超长的截到 60 字，与其它适配器保持一致
    if len(title) > 60:
        title = title[:60]
    return {
        "id": secrets.token_hex(6),
        "title": title,
        "note": note_of(item),
        "source": source,
        "status": "pending",
        "created": int(time.time()),
        "url": item.get("url") or "",
        "stats": {
            "views": int_or_none(stats.get("views")),
            "likes": int_or_none(stats.get("likes")),
            "comments": int_or_none(stats.get("comments")),
        },
    }


def print_human(result: dict):
    author = result["author"]
    print(f"platform=bilibili  author={author['name'] or '(未知)'}  id={author['id']}")
    print(f"url={author['url']}  items={len(result['items'])}  fetched_at={result['fetched_at']}")
    for i, it in enumerate(result["items"], 1):
        st = it["stats"]
        print(f"{i:>2}. [{it['id']}] {it['title']}")
        print(f"    {it['published']}  播放={fmt_count(st['views'])} "
              f"点赞={fmt_count(st['likes'])} 评论={fmt_count(st['comments'])}")
        print(f"    {it['url']}")


def cmd_fetch(args):
    result = fetch_videos(args.author, args.limit)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)
    return 0


def cmd_authors_add(args):
    mid = resolve_mid(args.author)
    name = args.name
    entry = {"platform": "bilibili", "author": mid, "name": name, "added": int(time.time())}
    state = {"added": False}

    def mutate(arr):
        for e in arr:
            if (isinstance(e, dict) and str(e.get("platform")) == "bilibili"
                    and str(e.get("author")) == mid):
                if name:
                    e["name"] = name
                return True
        arr.append(entry)
        state["added"] = True
        return True

    merge_json_array(CREATORS_FILE, mutate)
    print(f"{'added' if state['added'] else 'updated'}: bilibili/{mid} {name or ''}".strip())
    print(f"file={CREATORS_FILE}")
    return 0


def cmd_authors_list(args):
    arr = read_json_array(CREATORS_FILE)
    mine = [e for e in arr if isinstance(e, dict) and str(e.get("platform")) == "bilibili"]
    print(f"file={CREATORS_FILE}  total={len(arr)}  bilibili={len(mine)}")
    for e in mine:
        print(f"  {e.get('author')}\t{e.get('name')}\t{e.get('added')}")
    return 0


def _push_items(items, min_views: int, source: str, label: str):
    """按播放量阈值筛选后写入选题库：读最新 → 用 existing_keys 去重 → 原子替换。"""
    added, skipped_low, skipped_dup = [], [], []

    def mutate(arr):
        seen = existing_keys(arr)
        for item in items:
            views = (item.get("stats") or {}).get("views")
            if views is None or views < min_views:
                skipped_low.append(item)
                continue
            key = norm_url(item.get("url") or "")
            if not key or key in seen:
                skipped_dup.append(item)
                continue
            seen.add(key)
            arr.append(idea_entry(item, source=source))
            added.append(item)
        return bool(added)

    merge_json_array(IDEAS_FILE, mutate)

    print(f"file={IDEAS_FILE}  {label}  fetched={len(items)} min_views={min_views}")
    print(f"added={len(added)}  skipped_low={len(skipped_low)}  skipped_dup={len(skipped_dup)}")
    for item in added:
        print(f"  + {note_of(item)}")
    return added


def cmd_push(args):
    result = fetch_videos(args.author, args.limit)
    label = f"author={result['author']['name'] or result['author']['id']}"
    _push_items(result["items"], int(args.min_views), "对标-B站", label)
    return 0


def cmd_search(args):
    result = search_videos(args.keyword, args.limit, args.order)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)
    return 0


def cmd_push_search(args):
    result = search_videos(args.keyword, args.limit, args.order)
    label = f"keyword={args.keyword}"
    _push_items(result["items"], int(args.min_views), "搜索-B站", label)
    return 0


# ---------------------------------------------------------------- CLI


def build_parser():
    p = argparse.ArgumentParser(
        prog="bili_creator.py",
        description="B站对标作者作品采集（游客态公开接口，无账号依赖）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="拉取某 UP 主投稿列表")
    f.add_argument("--author", required=True, help="mid 或 space.bilibili.com/<mid> URL")
    f.add_argument("--limit", type=int, default=20)
    f.add_argument("--json", action="store_true", help="输出统一 JSON")
    f.set_defaults(func=cmd_fetch)

    a = sub.add_parser("authors", help="对标清单（outputs/_creators.json）")
    asub = a.add_subparsers(dest="acmd", required=True)
    aa = asub.add_parser("add")
    aa.add_argument("--author", required=True)
    aa.add_argument("--name", default="")
    aa.set_defaults(func=cmd_authors_add)
    al = asub.add_parser("list")
    al.set_defaults(func=cmd_authors_list)

    pu = sub.add_parser("push", help="拉取并按播放量阈值写入选题库")
    pu.add_argument("--author", required=True)
    pu.add_argument("--limit", type=int, default=20)
    pu.add_argument("--min-views", type=int, required=True)
    pu.set_defaults(func=cmd_push)

    se = sub.add_parser("search", help="按关键词搜索视频（垂类选题参考）")
    se.add_argument("--keyword", required=True)
    se.add_argument("--limit", type=int, default=20)
    se.add_argument("--order", choices=SEARCH_ORDER_CHOICES, default=SEARCH_ORDER,
                    help="排序：click=最多播放（默认）、totalrank=综合、pubdate=最新")
    se.add_argument("--json", action="store_true", help="输出统一 JSON")
    se.set_defaults(func=cmd_search)

    pse = sub.add_parser("push-search", help="关键词搜索并按播放量阈值写入选题库")
    pse.add_argument("--keyword", required=True)
    pse.add_argument("--limit", type=int, default=20)
    pse.add_argument("--min-views", type=int, required=True)
    pse.add_argument("--order", choices=SEARCH_ORDER_CHOICES, default=SEARCH_ORDER)
    pse.set_defaults(func=cmd_push_search)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BiliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if exc.payload is not None:
            print("raw: " + json.dumps(exc.payload, ensure_ascii=False)[:500], file=sys.stderr)
        return 1


def _selftest():
    img_key, sub_key, expect = MIXIN_KEY_SELFTEST
    got = mixin_key_of(img_key, sub_key)
    assert got == expect, f"mixin_key 自检失败: {got} != {expect}"
    signed = enc_wbi({"foo": "114", "bar": "514", "zab": 1919810}, img_key, sub_key,
                     wts=1702204169)
    assert signed["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4", signed["w_rid"]
    return True


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())

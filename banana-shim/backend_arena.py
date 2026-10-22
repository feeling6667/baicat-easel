# -*- coding: utf-8 -*-
r"""arena.ai 后端: 用 Playwright 驱动网页, 把提示词投进去, 把图捞回来。

为什么走网页而不是抓 API: arena.ai 挂在 Cloudflare 后面, cf_clearance cookie 绑定
IP + UA, Python 裸重放必然失效; 真浏览器天然绕开这一层, 也不用管 token 刷新。

三个必须守住的点(都是踩过坑的):
  1. 重建浏览器前必须 pw.stop() 旧的 Playwright 实例 —— 否则同一线程起第二个事件
     循环, 报 "Sync API inside the asyncio loop", 之后整个后端永久失效。
  2. 页面离开预期状态(窗口被关/掉登录/导航走)要能自愈: 先重载, 再整体重建。
  3. arena 弹条款拦截时要立刻报错, 不能傻等超时 —— 否则只看到"等图超时", 真相全丢。

自检:   ..\.venv\Scripts\python.exe backend_arena.py
测试:   ..\.venv\Scripts\python.exe backend_arena.py --test "a red apple"
"""

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import browser_driver as bd  # noqa: E402

NAME = "arena"
MODELS = ["gpt-image-2.5-sunburst", "arena"]
URL = os.environ.get("ARENA_URL",
                     "https://arena.ai/image/direct?model_a=gpt-image-2.5-sunburst")
DEBUG = HERE / "debug"

PROMPT_SELECTORS = [
    "textarea[placeholder*='Describe the image']",
    "textarea[placeholder]",
    "div[contenteditable='true']",
    "textarea",
]

SEND_SELECTORS = [
    "button[aria-label='Send message']",
    "button[aria-label*='Send']",
    "button[type='submit']",
]

AVATAR_HINTS = ("/images/avatars/", "avatar", "logo")

# arena / 上游模型的拦截文案。命中即立刻失败, 不进超时循环。
VIOLATION_MARKERS = [
    "violates our terms of use",
    "violates our terms",
    "violates our usage polic",
    "violates our polic",
    "against our polic",
    "content polic",
    "may violate",
]

# 上游生成失败(非条款拦截): 也要立刻失败, 否则干等 240s 超时, 真相全丢。
# arena 对这种情况显示 "Something went wrong with this response, please try again."
UPSTREAM_ERROR_MARKERS = [
    "something went wrong with this response",
    "please try again",
    "failed to generate",
]

_PW = None
_CTX = None
_PAGE = None


# ---------- 浏览器生命周期 ----------

def _teardown() -> None:
    """拆掉上下文 + Playwright 实例。stop() 是关键, 漏了就会毒化整个进程。"""
    global _PW, _CTX, _PAGE
    try:
        if _CTX is not None:
            _CTX.close()
    except Exception:
        pass
    try:
        if _PW is not None:
            _PW.stop()
    except Exception:
        pass
    _CTX = None
    _PAGE = None
    _PW = None


def _boot():
    global _PW, _CTX, _PAGE
    from playwright.sync_api import sync_playwright
    _PW = sync_playwright().start()
    _CTX = bd.launch(_PW, "arena")
    # 持久化 profile 会恢复上次的标签页。必须开新页 + 关掉旧的, 否则拿到的是上一轮
    # 会话: 里面的旧图会污染 before 快照, 新图被判成"已存在"而漏抓。
    _PAGE = _CTX.new_page()
    for other in list(_CTX.pages):
        if other is not _PAGE:
            try:
                other.close()
            except Exception:
                pass
    _PAGE.goto(URL, wait_until="domcontentloaded", timeout=90000)
    _PAGE.wait_for_timeout(4000)
    return _PAGE


def _prompt_box(page):
    box, sel = bd.first_visible(page, PROMPT_SELECTORS)
    return box, sel


def _ensure_page():
    """拿到一个可用的页面。快路径直接复用; 不行就先重载, 再不行整体重建。"""
    global _PAGE
    if _PAGE is not None:
        try:
            if not _PAGE.is_closed() and _prompt_box(_PAGE)[0] is not None:
                return _PAGE
        except Exception:
            pass
        # 页面还活着但状态不对 -> 先试着导航回原位
        try:
            if not _PAGE.is_closed():
                _PAGE.goto(URL, wait_until="domcontentloaded", timeout=60000)
                _PAGE.wait_for_timeout(3500)
                if _prompt_box(_PAGE)[0] is not None:
                    return _PAGE
        except Exception:
            pass
    _teardown()
    return _boot()


def close() -> None:
    _teardown()


# ---------- 取图 ----------

def _img_srcs(page):
    try:
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('img'))"
            ".map(e => e.currentSrc || e.src || '').filter(Boolean)")
    except Exception:
        return []


def _is_result(src: str) -> bool:
    low = src.lower()
    if any(h in low for h in AVATAR_HINTS):
        return False
    return low.startswith("data:image") or low.startswith("blob:") or low.startswith("http")


def _bytes_from_src(page, src: str):
    if src.startswith("data:"):
        return base64.b64decode(src.split(",", 1)[1])
    if src.startswith("blob:"):
        b64 = page.evaluate(
            "async (u) => { const r = await fetch(u); const b = await r.arrayBuffer();"
            " const bytes = new Uint8Array(b); let s = ''; const c = 0x8000;"
            " for (let i = 0; i < bytes.length; i += c) {"
            " s += String.fromCharCode.apply(null, bytes.subarray(i, i + c)); }"
            " return btoa(s); }", src)
        return base64.b64decode(b64)
    return page.request.get(src).body()


# ---------- 拦截检测 ----------

def _body_text(page) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        return ""


def _violation(page):
    """返回命中的拦截标记; 没有则 None。"""
    low = _body_text(page).lower()
    for marker in VIOLATION_MARKERS:
        if marker in low:
            return marker
    return None


def _shot(page, tag: str) -> str:
    try:
        DEBUG.mkdir(parents=True, exist_ok=True)
        path = DEBUG / (tag + "-" + str(int(time.time())) + ".png")
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return "(截图失败)"


# ---------- 主流程 ----------

def generate(prompt: str, size=None, images=None, n: int = 1) -> list:
    DEBUG.mkdir(parents=True, exist_ok=True)
    page = _ensure_page()

    box, sel = _prompt_box(page)
    if box is None:
        # 自愈第二次机会: 整体重建
        _teardown()
        page = _boot()
        box, sel = _prompt_box(page)
    if box is None:
        raise RuntimeError("没找到提示词输入框(重建后仍失败)。截图: " + _shot(page, "no-prompt"))

    # 取图双路: 网络响应为主(不受 shadow DOM / URL 复用影响), DOM 差异为兜底。
    catcher = bd.ImageCatcher(page, min_bytes=20000)
    before = set(_img_srcs(page))

    # 先填提示词, 再挂参考图。顺序反了会炸: arena 上传附件后会重建输入区,
    # 之前抓到的 textarea 句柄失效 -> click 超时。
    try:
        box.click(timeout=15000)
        if sel.startswith(("textarea", "input")):
            box.fill(prompt)
        else:
            box.type(prompt)
    except Exception as exc:
        raise RuntimeError("输入提示词失败: " + str(exc).splitlines()[0]
                           + " | 截图: " + _shot(page, "fill-fail"))

    ref_hashes = set()
    for raw in images or []:
        ref_hashes.add(hashlib.md5(raw).hexdigest())
        try:
            fin = page.locator("input[type='file']").first
            if fin.count() > 0:
                tmp = DEBUG / ("arena-ref-" + str(int(time.time())) + ".png")
                tmp.write_bytes(raw)
                fin.set_input_files(str(tmp))
                page.wait_for_timeout(3000)
        except Exception:
            pass

    # 上传完成后必须清空捕获表 + 重拍 DOM 快照, 否则参考图缩略图会被当成生成结果
    # (症状: 十几秒就"成功", 拿到的其实是刚上传的那张图)。
    page.wait_for_timeout(1500)
    catcher.clear()
    before = set(_img_srcs(page))

    send, _ = bd.first_visible(page, SEND_SELECTORS)
    if send is None:
        page.keyboard.press("Enter")
    else:
        send.click()

    budget = int(os.environ.get("ARENA_TIMEOUT", "240"))
    deadline = time.time() + budget
    out = []
    while len(out) < max(1, n) and time.time() < deadline:
        page.wait_for_timeout(2500)

        hit = _violation(page)
        if hit:
            shot = _shot(page, "arena-blocked")
            raise RuntimeError(
                "arena 拦截了这条提示词 (命中: " + hit + ")。"
                "通常是: 未成年角色 + 敏感物品同框 / 指名在世画师风格。"
                "截图: " + shot)

        low = _body_text(page).lower()
        for marker in UPSTREAM_ERROR_MARKERS:
            if marker in low:
                shot = _shot(page, "arena-upstream-error")
                raise RuntimeError(
                    "arena 端生成失败(非条款拦截): " + marker
                    + "。多为模型侧偶发错误, 重试即可。截图: " + shot)

        grabbed = catcher.latest()
        if grabbed:
            catcher.clear()
            if hashlib.md5(grabbed).hexdigest() not in ref_hashes:
                out.append(grabbed)
            continue

        for src in _img_srcs(page):
            if src in before or not _is_result(src):
                continue
            try:
                data = _bytes_from_src(page, src)
            except Exception:
                continue
            if data and len(data) > 20000 and hashlib.md5(data).hexdigest() not in ref_hashes:
                before.add(src)
                out.append(data)

    if not out:
        shot = _shot(page, "arena-timeout")
        raise RuntimeError("等图超时(" + str(budget) + "s)。截图: " + shot)
    return out


def selfcheck() -> dict:
    prof = bd.profile_dir("arena")
    return {
        "ok": prof.exists() and any(prof.iterdir()),
        "backend": NAME,
        "url": URL,
        "profile": str(prof),
        "models": MODELS,
        "提示": "首次使用先跑 login-arena.py 登录; 之后复用同一 profile。",
    }


def _test(prompt: str) -> int:
    DEBUG.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        data = generate(prompt, n=1)
    except Exception as exc:
        print("FAIL 用时 " + format(time.time() - started, ".1f") + "s : " + str(exc), flush=True)
        close()
        return 1
    out = DEBUG / ("arena-test-" + str(int(time.time())) + ".png")
    out.write_bytes(data[0])
    print("OK 用时 " + format(time.time() - started, ".1f") + "s -> " + str(out), flush=True)
    close()
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        idx = sys.argv.index("--test")
        text = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "a red apple on a white table"
        raise SystemExit(_test(text))
    print(json.dumps(selfcheck(), ensure_ascii=False, indent=2))

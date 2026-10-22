# -*- coding: utf-8 -*-
"""Flow 网页后端: 用 Playwright 驱动 labs.google Flow, 消耗 Gemini Pro 订阅额度。

关键设计:
  * 持久化用户目录(user_data_dir) - 第一次手动登录 Google, 之后复用登录态。
  * 不读你正在用的 Chrome 主配置(会被锁), 用独立 profile 目录。
  * 取图两条路, 谁先到用谁:
      1) 点下载按钮, 接 Playwright 的 download 事件(最稳);
      2) 监听网络响应, 抓 content-type=image/* 且体积够大的 body(兜底)。
  * 选择器是候选表, Google 改版后大概率要更新 - 用下面的 dump 模式抓一次真实页面再改。

风险提示: 自动化操作 Google 网页版属于灰色使用, 可能触发验证码或影响账号。
想稳定就把 BANANA_BACKEND 换成 gemini(同一个模型, 官方接口)。
"""

import json
import os
import sys
import time
from pathlib import Path

NAME = "flow-web"
MODELS = ["nano-banana", "flow-banana"]

FLOW_URL = os.environ.get("FLOW_URL", "https://flow.google.com/")
PROFILE_DIR = Path(os.environ.get("FLOW_PROFILE_DIR",
                                  str(Path.home() / ".banana-shim" / "chrome-profile")))
DEBUG_DIR = Path(__file__).resolve().parent / "debug"

PROMPT_SELECTORS = [
    "textarea[placeholder]",
    "div[contenteditable='true']",
    "textarea",
    "input[type='text']",
]

SEND_SELECTORS = [
    "button[aria-label*='enerate']",
    "button[aria-label*='ubmit']",
    "button:has-text('Generate')",
    "button:has-text('Create')",
    "button[type='submit']",
]

DOWNLOAD_SELECTORS = [
    "button[aria-label*='ownload']",
    "a[aria-label*='ownload']",
    "button:has-text('Download')",
]

EDITOR_HINT = "labs.google/fx"


def selfcheck() -> dict:
    try:
        import playwright  # noqa: F401
        has_pw = True
    except ImportError:
        has_pw = False
    profiles = [p.name for p in PROFILE_DIR.glob("*")] if PROFILE_DIR.exists() else []
    return {
        "ok": has_pw and bool(profiles),
        "backend": NAME,
        "playwright": "已安装" if has_pw else "缺失: pip install playwright && playwright install chrome",
        "profile_dir": str(PROFILE_DIR),
        "profile_ready": bool(profiles),
        "提示": "首次运行会打开浏览器, 手动登录 Google 后保持窗口开着; 登录态存在 profile_dir。",
    }


def _launch(pw):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    arg_list = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    kwargs = dict(user_data_dir=str(PROFILE_DIR), headless=False, args=arg_list,
                  viewport={"width": 1440, "height": 900})
    last = None
    for extra in (dict(channel="chrome"), dict()):
        try:
            return pw.chromium.launch_persistent_context(**kwargs, **extra)
        except Exception as exc:
            last = exc
    raise RuntimeError("启动 Chrome 失败: " + str(last))


def _first(page, selectors):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return loc, sel
        except Exception:
            continue
    return None, None


def _wait_image(page, captured, deadline):
    """轮询捕获表, 同时试一次下载按钮。返回 (bytes, 来源)。"""
    tried_download = False
    while time.time() < deadline:
        if captured:
            return captured[-1], "network"
        if not tried_download:
            tried_download = True
            btn, _ = _first(page, DOWNLOAD_SELECTORS)
            if btn is not None:
                try:
                    with page.expect_download(timeout=20000) as info:
                        btn.click()
                    hmm = info.value
                    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                    tmp = DEBUG_DIR / ("dl-" + str(int(time.time())) + ".png")
                    hmm.save_as(str(tmp))
                    return tmp.read_bytes(), "download"
                except Exception:
                    pass
        page.wait_for_timeout(1000)
    return None, None


def generate(prompt: str, size=None, images=None, n: int = 1) -> list:
    from playwright.sync_api import sync_playwright

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    with sync_playwright() as pw:
        ctx = _launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        captured = []

        def on_response(resp):
            try:
                ctype = (resp.headers or {}).get("content-type", "")
                if not ctype.startswith("image/"):
                    return
                body = resp.body()
                if body and len(body) > 20000:
                    captured.append(body)
            except Exception:
                return

        page.on("response", on_response)
        page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(1500)

        box, sel_used = _first(page, PROMPT_SELECTORS)
        if box is None:
            shot = DEBUG_DIR / ("no-prompt-" + str(int(time.time())) + ".png")
            page.screenshot(path=str(shot), full_page=True)
            raise RuntimeError("没找到提示词输入框(可能未登录)。已截图: " + str(shot) +
                               " ; 当前 URL=" + page.url)

        box.click()
        if sel_used.startswith(("textarea", "input")):
            box.fill(prompt)
        else:
            box.type(prompt)

        for raw in images or []:
            try:
                fin = page.locator("input[type='file']").first
                if fin.count() > 0:
                    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                    tmp = DEBUG_DIR / ("ref-" + str(int(time.time())) + ".png")
                    tmp.write_bytes(raw)
                    fin.set_input_files(str(tmp))
                    page.wait_for_timeout(3000)
            except Exception:
                pass

        send, _ = _first(page, SEND_SELECTORS)
        if send is None:
            page.keyboard.press("Enter")
        else:
            send.click()

        budget = int(os.environ.get("FLOW_TIMEOUT", "240"))
        deadline = time.time() + budget
        for _ in range(max(1, n)):
            data, source = _wait_image(page, captured, deadline)
            if not data:
                raise RuntimeError("等图超时(" + str(budget) + "s)。页面可能改版或需要验证码, 看 debug/ 里的截图。")
            out.append(data)
            captured.clear()

        ctx.close()
    return out


def _dump():
    """抓一次真实 Flow 页面结构, 供调选择器用。"""
    from playwright.sync_api import sync_playwright

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = _launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(6000)
        stamp = str(int(time.time()))
        (DEBUG_DIR / ("flow-" + stamp + ".html")).write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(DEBUG_DIR / ("flow-" + stamp + ".png")), full_page=True)
        rows = []
        for el in page.locator("textarea, input, button, [contenteditable='true']").all()[:120]:
            try:
                rows.append({"tag": el.evaluate("e=>e.tagName"),
                             "text": (el.inner_text() or "")[:60],
                             "aria": el.get_attribute("aria-label"),
                             "ph": el.get_attribute("placeholder")})
            except Exception:
                continue
        (DEBUG_DIR / ("flow-" + stamp + ".json")).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已导出到 " + str(DEBUG_DIR) + " , 文件前缀 flow-" + stamp)
        input("看完按回车关闭浏览器...")
        ctx.close()


if __name__ == "__main__":
    if "--dump" in sys.argv:
        _dump()
    else:
        print(json.dumps(selfcheck(), ensure_ascii=False, indent=2))

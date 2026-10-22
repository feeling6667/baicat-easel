# -*- coding: utf-8 -*-
"""共享浏览器驱动层: 常驻 Chrome + 双路取图 + 页面结构导出。

设计目标: 换一个站点只需要写一个新的 backend_<站点>.py (选择器表 + 流程),
浏览器启动、取图捕获、dump 这些通用活都留在这里。

被 login-*.py / *-probe.py / backend_arena.py 复用。
"""

import json
import os
import time
from pathlib import Path

DEFAULT_VIEWPORT = {"width": 1440, "height": 900}

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=IsolateOrigins,site-per-process",
]


def profile_root() -> Path:
    return Path(os.environ.get("SHIM_PROFILE_ROOT", str(Path.home() / ".banana-shim")))


def profile_dir(name: str) -> Path:
    return profile_root() / (name + "-profile")


def launch(pw, profile_name: str, headless: bool = False):
    """启动持久化 Chrome。优先用系统安装的 Chrome (channel=chrome), 失败退回自带 chromium。"""
    target = profile_dir(profile_name)
    target.mkdir(parents=True, exist_ok=True)
    kwargs = dict(user_data_dir=str(target), headless=headless,
                  args=list(LAUNCH_ARGS), viewport=dict(DEFAULT_VIEWPORT))
    last = None
    for extra in (dict(channel="chrome"), dict()):
        try:
            return pw.chromium.launch_persistent_context(**kwargs, **extra)
        except Exception as exc:
            last = exc
    raise RuntimeError("启动 Chrome 失败: " + str(last))


def first_visible(page, selectors):
    """按候选表找第一个可见元素, 返回 (locator, 命中的选择器)。"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return loc, sel
        except Exception:
            continue
    return None, None


class ImageCatcher:
    """监听网络响应, 收集体积够大的图片字节。取图第一路。"""

    def __init__(self, page, min_bytes: int = 20000):
        self.items = []
        self.min_bytes = min_bytes
        page.on("response", self._on_response)

    def _on_response(self, resp):
        try:
            ctype = (resp.headers or {}).get("content-type", "")
            if not ctype.startswith("image/"):
                return
            body = resp.body()
            if body and len(body) > self.min_bytes:
                self.items.append(body)
        except Exception:
            return

    def latest(self):
        return self.items[-1] if self.items else None

    def clear(self):
        self.items.clear()


def dump_page(page, out_dir: Path, tag: str) -> dict:
    """导出 HTML / 截图 / 可交互元素清单, 供校准选择器。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(int(time.time()))
    html = out_dir / (tag + "-" + stamp + ".html")
    png = out_dir / (tag + "-" + stamp + ".png")
    html.write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(png), full_page=True)

    rows = []
    sel = "textarea, input, button, [contenteditable='true'], img, [role='button']"
    for el in page.locator(sel).all()[:150]:
        try:
            rows.append({
                "tag": el.evaluate("e=>e.tagName"),
                "text": (el.inner_text() or "")[:60],
                "aria": el.get_attribute("aria-label"),
                "ph": el.get_attribute("placeholder"),
                "src": (el.get_attribute("src") or "")[:90],
                "cls": (el.get_attribute("class") or "")[:70],
            })
        except Exception:
            continue

    js = out_dir / (tag + "-" + stamp + ".json")
    js.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"html": str(html), "png": str(png), "json": str(js),
            "elements": len(rows), "url": page.url}

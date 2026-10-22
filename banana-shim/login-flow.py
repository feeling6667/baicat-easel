# -*- coding: utf-8 -*-
r"""打开 Flow 登录窗口: 首次登录 Google 用。

登录态存在 FLOW_PROFILE_DIR (默认 %USERPROFILE%\.banana-shim\chrome-profile),
之后 backend_flow.py 复用同一个目录, 不需要再登。

用法: ..\.venv\Scripts\python.exe login-flow.py
登录完成后不要关窗口, 回对话说一声; 调完选择器再关。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend_flow  # noqa: E402


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = backend_flow._launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(backend_flow.FLOW_URL, wait_until="domcontentloaded", timeout=90000)
        print("FLOW_URL=" + page.url, flush=True)
        print("PROFILE=" + str(backend_flow.PROFILE_DIR), flush=True)
        print("请在这个窗口登录 Google 账号; 登录后窗口别关。", flush=True)

        last = ""
        for _ in range(400):
            time.sleep(3)
            try:
                if not ctx.pages:
                    break
                now = page.url
                if now != last:
                    last = now
                    print("URL=" + now, flush=True)
                    if "accounts.google.com" not in now and "labs.google" in now:
                        print("已进入 Flow 页面, 登录态就绪。", flush=True)
            except Exception:
                break
        print("窗口已关闭。登录态保存在: " + str(backend_flow.PROFILE_DIR), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

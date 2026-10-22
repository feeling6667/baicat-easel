# -*- coding: utf-8 -*-
r"""打开 arena.ai 登录窗口。

登录态存在独立 profile (~/.banana-shim/arena-profile), 之后 backend_arena.py 复用。
登录完成后不要关窗口, 回对话说一声。
"""

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import browser_driver as bd  # noqa: E402

URL = "https://arena.ai/image/direct?model_a=gpt-image-2.5-sunburst"


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = bd.launch(pw, "arena")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        print("URL=" + page.url, flush=True)
        print("PROFILE=" + str(bd.profile_dir("arena")), flush=True)
        print("请在这个窗口登录 arena.ai; 登录后窗口别关。", flush=True)

        last = ""
        for _ in range(600):
            time.sleep(3)
            try:
                if not ctx.pages:
                    break
                now = page.url
                if now != last:
                    last = now
                    print("URL=" + now, flush=True)
            except Exception:
                break
        print("窗口已关闭。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

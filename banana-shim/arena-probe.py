# -*- coding: utf-8 -*-
r"""抓 arena.ai 真实页面结构, 供校准选择器。登录后运行。

产物: debug/arena-<时间戳>.html / .png / .json
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import browser_driver as bd  # noqa: E402

URL = "https://arena.ai/image/direct?model_a=gpt-image-2.5-sunburst"
DEBUG = HERE / "debug"


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = bd.launch(pw, "arena")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(9000)
        info = bd.dump_page(page, DEBUG, "arena")
        print(json.dumps(info, ensure_ascii=False, indent=2), flush=True)
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
r"""把 gen_pages 的 --use-ref 默认值改为 True（垫图成为默认行为）。

原因: 提示词模板已改为『风格以所附参考图为准, 不指名画师』。若不垫图,
风格锚点丢失, 出图会漂。保留 --no-ref 用于对比调试。
"""

import io
import shutil
from pathlib import Path

VP = Path(r"C:\Users\Administrator\WorkBuddy AI\2026-09-17-16-10-42\Easel"
          r"\skills\builtin\baicatcha\scripts\viral_pipeline.py")

NEW_BLOCK = '''    # 默认垫图: 模板已改为『风格以所附参考图为准, 不指名画师』；不垫图风格会漂。
    # 要对比无图效果用 --no-ref。
    p.add_argument("--use-ref", action="store_true", default=True,
                   help="先试 /images/edits 垫图（FB-060.webp + 隔离指令），失败自动回退文生图（默认开启）")
    p.add_argument("--no-ref", dest="use_ref", action="store_false",
                   help="关闭垫图, 纯文生图（仅用于对比调试）")
'''


def main() -> int:
    shutil.copy2(VP, str(VP) + ".bak.before-useref-20261022")
    lines = io.open(VP, encoding="utf-8").read().splitlines(True)

    start = None
    for i, line in enumerate(lines):
        if "p.add_argument" in line and "--use-ref" in line:
            start = i
            break
    if start is None:
        print("FAIL: 未找到 --use-ref 定义")
        return 1

    # 吃掉原定义（可能是一行，也可能续到下一行）
    end = start + 1
    if not lines[start].rstrip().endswith(")"):
        while end < len(lines) and not lines[end].rstrip().endswith(")"):
            end += 1
        end += 1

    out = lines[:start] + [NEW_BLOCK] + lines[end:]
    io.open(VP, "w", encoding="utf-8", newline="").writelines(out)

    print("replaced lines " + str(start + 1) + "-" + str(end))
    for i, l in enumerate(io.open(VP, encoding="utf-8").read().splitlines(), 1):
        if "--use-ref" in l or "--no-ref" in l:
            print(str(i) + "| " + l.strip()[:120])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

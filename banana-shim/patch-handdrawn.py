# -*- coding: utf-8 -*-
r"""清理 fb060-handdrawn.json 里指名在世画师的内容（提示词 + 元数据）。

与 baicatcha 的 fb060-frazee.json 同源问题: 指名 'Marla Frazee' 会让 arena /
上游模型直接返回 "This content violates our Terms of Use"。改为描述性风格词,
风格由参考图锚定。
"""

import io
import json
import shutil
import sys
import time
from pathlib import Path

P = Path(r"C:\Users\Administrator\WorkBuddy AI\2026-09-17-16-10-42\Easel"
         r"\skills\builtin\skill-wechat-publisher\assets\image-styles\fb060-handdrawn.json")

REPLACEMENTS = [
    ("Style: Marla Frazee playful childlike pencil & watercolor storybook illustration.",
     "Style: playful childlike hand-drawn storybook illustration; loose pencil sketch lines "
     "with transparent watercolor washes. Match the attached reference image for style only."),
    ("Marla Frazee playful childlike pencil & watercolor storybook illustration",
     "playful childlike pencil & watercolor storybook illustration"),
    ("Marla Frazee 风绘本水彩", "童趣绘本水彩"),
    ("FB-060 · Marla Frazee（绘本水彩", "FB-060 · 绘本水彩（"),
    ("Marla Frazee（绘本水彩", "（绘本水彩"),
    ("Marla Frazee", "（风格参考，不指名艺术家）"),
]


def clean(text: str) -> str:
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    return text


def walk(node, path=""):
    changed = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and "Marla Frazee" in v:
                node[k] = clean(v)
                changed.append(path + "." + k if path else k)
            else:
                changed += walk(v, (path + "." + k) if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str) and "Marla Frazee" in v:
                node[i] = clean(v)
                changed.append(path + "[" + str(i) + "]")
            else:
                changed += walk(v, path + "[" + str(i) + "]")
    return changed


def main() -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = P.with_suffix(".json.bak.before-noname-" + stamp)
    shutil.copy2(P, bak)
    print("backup: " + str(bak))

    data = json.load(io.open(P, encoding="utf-8-sig"))
    changed = walk(data)

    with io.open(P, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("changed fields: " + str(len(changed)))
    for c in changed:
        print("  " + c)

    raw = io.open(P, encoding="utf-8").read()
    print("residual 'Marla Frazee': " + str(raw.count("Marla Frazee")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
r"""按工作台文案试生首页: A/B 分离"画师名"与"画面内容"两个拦截来源。

用法: python try-cover.py orig    # 去掉画师名, 保留原始画面文案
      python try-cover.py fixed   # 去掉画师名 + 改写画面文案
"""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backend_arena as ba  # noqa: E402

RUN = Path(r"C:\Users\Administrator\WorkBuddy AI\2026-09-17-16-10-42\Easel\outputs\viral-20261022-0034")
REF = Path(r"C:\Users\Administrator\WorkBuddy AI\2026-09-17-16-10-42\Easel\skills\builtin\baicatcha\assets\FB-060.webp")

# 风格: 去掉 "Marla Frazee" 指名, 改为纯描述 + 参考图定调
STYLE = (
    "playful childlike storybook illustration; loose pencil sketch lines with visible "
    "hand-drawn texture; transparent watercolor washes with soft blooming edges; "
    "cream paper ground with visible grain; bright small-area accents (warm yellow, "
    "true red) on a low-saturation base; generous white space, airy composition, "
    "subject offset to one side leaving room for text; lively childlike characters "
    "with big gestures. Match the attached reference image for style only."
)

FIXED_VISUAL = (
    "中式家庭客厅，背景有绿植、电视柜和茶几；一个穿宽松大裤衩、居家 T 恤、头发微乱的中国爸爸，"
    "满头冷汗，正战战兢兢地往头上套一顶深色铁质、带铜铆钉的武侠风头盔「玄铁头套」；"
    "旁边一个扎着双羊角辫、穿粉色小裙子的三岁女儿，双手举着一根发光的塑料仙女魔法棒，好奇地看着爸爸。"
)

PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")
JPG_MAGIC = bytes.fromhex("ffd8ff")


def ext_of(data: bytes) -> str:
    if data[:8] == PNG_MAGIC:
        return ".png"
    if data[:3] == JPG_MAGIC:
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


def build(visual: str, page: dict) -> str:
    lines = "；".join(page.get("lines") or [])
    return (
        STYLE + "\n\n"
        + "画面：" + visual + "\n"
        + "画面文字：大字标题「" + str(page.get("title") or "") + "」；短句「" + lines + "」\n"
        + "主题色：" + str(page.get("theme_color") or "") + "\n"
        + "画幅：3:4 竖版"
    )


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "fixed"
    pages = json.loads((RUN / "pages.json").read_text(encoding="utf-8"))
    page = pages["pages"][0]

    visual = page["visual_zh"] if mode == "orig" else FIXED_VISUAL
    prompt = build(visual, page)

    ref = REF.read_bytes() if REF.is_file() else None
    print("MODE=" + mode + "  REF=" + (REF.name if ref else "none")
          + "  PROMPT_LEN=" + str(len(prompt)), flush=True)

    started = time.time()
    try:
        data = ba.generate(prompt, size="3:4", images=[ref] if ref else None, n=1)
    except Exception as exc:
        print("BLOCKED/FAIL " + format(time.time() - started, ".1f") + "s : " + str(exc), flush=True)
        ba.close()
        return 2

    out = ba.DEBUG / ("cover-" + mode + "-" + str(int(time.time())) + ext_of(data[0]))
    out.write_bytes(data[0])
    print("OK " + format(time.time() - started, ".1f") + "s -> " + str(out), flush=True)
    ba.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
r"""用改后的真实风格档模板生成定稿封面（端到端验证流水线路径）。

与 try-cover.py 的区别: 这里直接读 fb060-frazee.json 的 prompt_template_zh,
按流水线的方式填充占位符, 并附上 FB-060.webp 参考图 —— 走的就是生产路径。

用法: python gen-final-cover.py
"""

import io
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backend_arena as ba  # noqa: E402

EASEL = Path(r"C:\Users\Administrator\WorkBuddy AI\2026-09-17-16-10-42\Easel")
STYLE = EASEL / "skills/builtin/baicatcha/assets/fb060-frazee.json"
REF = EASEL / "skills/builtin/baicatcha/assets/FB-060.webp"
RUN = EASEL / "outputs/viral-20261022-0034"

# 定稿画面: 保留 A 的梗(护具), 采纳 B 的构图(居中、留白、标题带装饰)
VISUAL = (
    "中式家庭客厅，背景有绿植、电视柜和茶几；画面居中是一个穿宽松大裤衩、居家 T 恤、"
    "头发微乱的中国爸爸，坐在客厅地毯上，满头冷汗，正把一条深色铁质、带铜铆钉的"
    "武侠风护具往腰上系；旁边站着一个扎着双羊角辫、穿粉色小裙子的三岁女儿，"
    "双手举着一根发光的塑料仙女魔法棒，笑得开心，看着他。"
)

TITLE = "铁裤头奶爸图鉴"
LINE = "为什么当爹的在家都需要它？"
THEME = "米白纸底 + 明黄/正红小面积重色"


def ext_of(data: bytes) -> str:
    if data[:8] == bytes.fromhex("89504e470d0a1a0a"):
        return ".png"
    if data[:3] == bytes.fromhex("ffd8ff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


def main() -> int:
    style = json.load(io.open(STYLE, encoding="utf-8-sig"))
    tpl = style["prompt_template_zh"]

    characters = (
        "角色设定（每页必须完全一致）：\n"
        "- 爸爸：头发微乱的中国爸爸，圆脸、疲惫又宠溺的眼神；宽松大裤衩 + 居家 T 恤。\n"
        "- 女儿：3 岁女孩，扎两个羊角辫的黑发女孩，圆脸、弯月眼；粉色小裙子、白色小皮鞋；"
        "道具为发光塑料仙女魔法棒。\n"
    )

    prompt = tpl.format(
        traits="、".join(style.get("traits") or []),
        typography="封面用马克笔手写粗楷体，正文用儿童蜡笔手写体，关键词用正红色；所有文字由模型直接画进图里。",
        characters=characters,
        page_visual=VISUAL,
        theme_color=THEME,
        page_text="大字标题「" + TITLE + "」；短句「" + LINE + "」",
        negative="、".join(style.get("negative_prompt") or ["照片写实", "3D 渲染", "矢量扁平插画"]),
    )

    ref = REF.read_bytes() if REF.is_file() else None
    print("TEMPLATE_USED=fb060-frazee.json  REF=" + (REF.name if ref else "none"), flush=True)
    print("PROMPT_HEAD=" + prompt[:100], flush=True)

    started = time.time()
    try:
        data = ba.generate(prompt, size="3:4", images=[ref] if ref else None, n=1)
    except Exception as exc:
        print("FAIL " + format(time.time() - started, ".1f") + "s : " + str(exc), flush=True)
        ba.close()
        return 2

    out = ba.DEBUG / ("final-cover-" + str(int(time.time())) + ext_of(data[0]))
    out.write_bytes(data[0])
    print("OK " + format(time.time() - started, ".1f") + "s -> " + str(out), flush=True)
    ba.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
r"""重跑被拦的两页: 把"冲撞下腹"改写成"撞肚子/撞怀里", 保留喜剧感但不指向裆部。"""

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
OUT = RUN / "story"

# 改写版画面: 冲击点从"下腹"移到"肚子/胸口", 去掉"护住下腹"的暗示
REWRITE = {
    2: "客厅门口，爸爸刚下班进门，蹲低身体、双手叉腰，脸色发白、瞳孔地震、额头冒冷汗；"
       "扎双羊角辫、穿粉色小裙子的女儿深深低着头、下巴收向胸口、身体前倾成一条斜线，"
       "紧闭双眼像小炮弹一样朝爸爸的肚子冲过去，她坚硬的小脑门正对爸爸的肚子，"
       "冲击处飞出速度线与飞溅水花。",
    3: "爸爸毫无防备地仰面躺在客厅沙发上刷手机，女儿穿着粉色小裙子从沙发靠背上高高跃起，"
       "双膝并拢，带着泰山压顶的气势朝爸爸的肚子落下去。",
}


def ext_of(b: bytes) -> str:
    if b[:8] == bytes.fromhex("89504e470d0a1a0a"):
        return ".png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


def main() -> int:
    nums = [int(x) for x in sys.argv[1:]] or [2, 3]
    pages = json.load(io.open(RUN / "pages.json", encoding="utf-8-sig"))
    style = json.load(io.open(STYLE, encoding="utf-8-sig"))
    tpl = style["prompt_template_zh"]
    chars = pages.get("characters") or []

    block = ["角色设定（每一页都必须完全一致，不得换装/换发型/换年龄/换体型）："]
    for c in chars:
        block.append("- " + c["role"] + "：" + c["look_zh"] + "。")
    block.append("同一角色在不同页必须是同一张脸、同一发型发色、同一套衣服款式与颜色。")
    char_block = "\n".join(block) + "\n"

    ref = REF.read_bytes() if REF.is_file() else None
    by_num = {int(p["page"]): p for p in pages["pages"]}

    for n in nums:
        page = by_num.get(n)
        if not page:
            continue
        text = ("大字标题「" + str(page.get("title") or "") + "」；短句「"
                + "；".join(page.get("lines") or []) + "」")
        prompt = tpl.format(
            traits="、".join(style.get("traits") or []),
            typography=("封面用马克笔手写粗楷体，正文用儿童蜡笔手写体，关键词用正红色；"
                        "所有文字由模型直接画进图里。"),
            characters=char_block,
            page_visual=REWRITE.get(n, page.get("visual_zh") or ""),
            theme_color=page.get("theme_color") or "",
            page_text=text,
            negative="、".join(style.get("negative_prompt") or ["照片写实", "3D 渲染"]),
        )
        t0 = time.time()
        try:
            data = ba.generate(prompt, size="3:4", images=[ref] if ref else None, n=1)
        except Exception as exc:
            print("p" + str(n) + " FAIL " + format(time.time() - t0, ".1f") + "s : "
                  + str(exc).splitlines()[0][:140], flush=True)
            continue
        out = OUT / ("p" + str(n).zfill(2) + "-" + str(page.get("kind")) + ext_of(data[0]))
        out.write_bytes(data[0])
        print("p" + str(n) + " OK " + format(time.time() - t0, ".1f") + "s -> " + out.name, flush=True)

    ba.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

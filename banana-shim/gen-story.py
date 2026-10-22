# -*- coding: utf-8 -*-
r"""整本故事跑图: 按工作台 pages.json 逐页生成, 验证跨页角色一致性。

走真实风格档模板(fb060-frazee.json 的 prompt_template_zh) + 参考图 FB-060.webp。
角色设定每页复用同一段文本, 这是 baicat 规范里保证一致性的关键。

用法: python gen-story.py [起始页] [结束页]
"""

import io
import json
import os
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
OUT = EASEL / "outputs/viral-20261022-0034/story"

RETRIES = int(os.environ.get("STORY_RETRIES", "3"))   # 每页最多尝试次数
RETRY_WAIT = int(os.environ.get("STORY_RETRY_WAIT", "10"))  # 两次尝试间隔(秒)


def ext_of(data: bytes) -> str:
    if data[:8] == bytes.fromhex("89504e470d0a1a0a"):
        return ".png"
    if data[:3] == bytes.fromhex("ffd8ff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


def characters_block(chars) -> str:
    """每页复用同一段角色设定 —— 跨页一致性靠它, 不能逐页改写。"""
    lines = ["角色设定（每一页都必须完全一致，不得换装/换发型/换年龄/换体型）："]
    for c in chars:
        lines.append("- " + c["role"] + "：" + c["look_zh"] + "。")
    lines.append("同一角色在不同页必须是同一张脸、同一发型发色、同一套衣服款式与颜色。")
    return "\n".join(lines) + "\n"


def main() -> int:
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 999

    pages = json.load(io.open(RUN / "pages.json", encoding="utf-8-sig"))
    style = json.load(io.open(STYLE, encoding="utf-8-sig"))
    tpl = style["prompt_template_zh"]
    chars = pages.get("characters") or []
    char_block = characters_block(chars)
    ref = REF.read_bytes() if REF.is_file() else None

    OUT.mkdir(parents=True, exist_ok=True)
    results = []

    for page in pages["pages"]:
        n = int(page.get("page") or 0)
        if n < start or n > end:
            continue

        text = "大字标题「" + str(page.get("title") or "") + "」；短句「" + "；".join(page.get("lines") or []) + "」"
        prompt = tpl.format(
            traits="、".join(style.get("traits") or []),
            typography=("封面用马克笔手写粗楷体，正文用儿童蜡笔手写体，关键词用正红色；"
                        "所有文字由模型直接画进图里。"),
            characters=char_block,
            page_visual=page.get("visual_zh") or "",
            theme_color=page.get("theme_color") or "",
            page_text=text,
            negative="、".join(style.get("negative_prompt") or ["照片写实", "3D 渲染", "矢量扁平插画"]),
        )

        data = None
        last_err = ""
        for attempt in range(1, RETRIES + 1):
            t0 = time.time()
            try:
                data = ba.generate(prompt, size="3:4", images=[ref] if ref else None, n=1)
                print("p" + str(n) + " OK " + format(time.time() - t0, ".1f") + "s "
                      + format(len(data[0]) / 1048576, ".2f") + "MB"
                      + (" (第 " + str(attempt) + " 次)" if attempt > 1 else ""), flush=True)
                break
            except Exception as exc:
                last_err = str(exc).splitlines()[0][:160]
                print("p" + str(n) + " 第 " + str(attempt) + "/" + str(RETRIES)
                      + " 次失败 " + format(time.time() - t0, ".1f") + "s : " + last_err, flush=True)
                # 条款拦截重试无用, 直接记失败; 其余(超时/上游偶发)重试
                if "拦截" in last_err:
                    break
                if attempt < RETRIES:
                    time.sleep(RETRY_WAIT)

        if data is None:
            results.append((n, "FAIL", last_err))
            continue

        out = OUT / ("p" + str(n).zfill(2) + "-" + str(page.get("kind")) + ext_of(data[0]))
        out.write_bytes(data[0])
        results.append((n, "OK", out.name))

    ok = sum(1 for r in results if r[1] == "OK")
    print("DONE ok=" + str(ok) + "/" + str(len(results)), flush=True)
    ba.close()
    return 0 if ok == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())

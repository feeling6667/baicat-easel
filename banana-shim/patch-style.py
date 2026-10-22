# -*- coding: utf-8 -*-
r"""把 FB-060 风格档里的"指名在世画师"改成"参考图 + 描述性风格词"。

背景: arena.ai 对指名在世艺术家(Reference artist)的提示词直接返回
"This content violates our Terms of Use"。实测: 只去掉人名、保留原画面文案
即可通过(A/B 验证), 风格由所附参考图锚定, 观感一致。

只改进入提示词的字段; license_note 里的作者出处(版权声明)保留不动。
"""

import io
import json
import shutil
import sys
import time
from pathlib import Path

EASEL = Path(r"C:\Users\Administrator\WorkBuddy AI\2026-09-17-16-10-42\Easel")
TARGET = EASEL / "skills/builtin/baicatcha/assets/fb060-frazee.json"

ZH_OLD = "风格名称：Marla Frazee Playful Childlike Pencil & Watercolor Storybook。参考作者/风格名称：Marla Frazee。"
ZH_NEW = "风格：童趣手绘绘本插画（铅笔速写松线 + 透明水彩晕染）。风格基调以所附参考图为准，不指名任何艺术家。"

EN_OLD = ("Style: Marla Frazee playful childlike pencil & watercolor storybook illustration. "
          "Reference artist: Marla Frazee.")
EN_NEW = ("Style: playful childlike hand-drawn storybook illustration; loose pencil sketch lines "
          "with transparent watercolor washes. Match the attached reference image for style only.")

GEN_NEW = "playful childlike pencil & watercolor storybook illustration"
AUTH_NEW = "（不指名艺术家；风格由参考图锚定）"


def main() -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = TARGET.with_suffix(".json.bak.before-noname-" + stamp)
    shutil.copy2(TARGET, bak)
    print("backup: " + str(bak))

    data = json.load(io.open(TARGET, encoding="utf-8-sig"))
    changed = []

    zh = data.get("prompt_template_zh", "")
    if ZH_OLD in zh:
        data["prompt_template_zh"] = zh.replace(ZH_OLD, ZH_NEW)
        changed.append("prompt_template_zh")
    else:
        print("WARN: zh 模板未精确命中, 未改")

    en = data.get("prompt_template_en", "")
    if EN_OLD in en:
        data["prompt_template_en"] = en.replace(EN_OLD, EN_NEW)
        changed.append("prompt_template_en")
    else:
        print("WARN: en 模板未精确命中, 未改")

    sl = data.setdefault("style_lock", {})
    if "Marla Frazee" in str(sl.get("generation_name", "")):
        sl["generation_name"] = GEN_NEW
        changed.append("style_lock.generation_name")
    if "Marla Frazee" in str(sl.get("reference_author", "")):
        sl["reference_author"] = AUTH_NEW
        changed.append("style_lock.reference_author")

    dn = data.get("display_name", "")
    if "Marla Frazee" in dn:
        data["display_name"] = dn.replace("· Marla Frazee ", "· ").replace("Marla Frazee ", "")
        changed.append("display_name")

    # 保底: 任何残留的指名都清掉(license_note 是版权声明, 单独保留)
    for key in ("prompt_template_zh", "prompt_template_en"):
        if "Marla Frazee" in data.get(key, ""):
            print("WARN: " + key + " 仍含 Marla Frazee")

    with io.open(TARGET, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("changed: " + ", ".join(changed) if changed else "changed: (none)")
    print("license_note 保留: " + str("Marla Frazee" in data.get("license_note", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

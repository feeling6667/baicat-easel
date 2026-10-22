# -*- coding: utf-8 -*-
r"""把整本故事的成品拼成一张对照图, 便于肉眼比对跨页角色一致性。"""
import sys
from pathlib import Path
from PIL import Image

OUT = Path(r"C:\Users\Administrator\WorkBuddy AI\2026-09-17-16-10-42\Easel\outputs\viral-20261022-0034\story")
files = sorted(OUT.glob("p*.png"))
if not files:
    print("no images")
    raise SystemExit(1)

THUMB_W = 360
thumbs = []
for f in files:
    im = Image.open(f).convert("RGB")
    ratio = THUMB_W / im.width
    im = im.resize((THUMB_W, int(im.height * ratio)), Image.LANCZOS)
    thumbs.append((f.stem, im))

cols = 5
rows = (len(thumbs) + cols - 1) // cols
tw = thumbs[0][1].width
th = max(t.height for _, t in thumbs)
PAD = 8
canvas = Image.new("RGB", (cols * (tw + PAD) + PAD, rows * (th + PAD) + PAD), (245, 245, 245))
for i, (name, t) in enumerate(thumbs):
    r, c = divmod(i, cols)
    canvas.paste(t, (PAD + c * (tw + PAD), PAD + r * (th + PAD)))
out = OUT / "_montage.png"
canvas.save(out, "PNG")
print("MONTAGE " + str(len(thumbs)) + " imgs -> " + str(out))
print("size=" + str(canvas.size))

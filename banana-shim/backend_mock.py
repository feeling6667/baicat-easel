# -*- coding: utf-8 -*-
"""不出网的占位后端: 画一张带提示词的图, 用来验证 EASEL -> shim 链路。"""

import io

NAME = "mock"
MODELS = ["nano-banana", "mock-banana"]


def selfcheck():
    return {"ok": True, "backend": NAME, "note": "占位后端, 不联网"}


def _png(prompt: str) -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
    img = Image.new("RGB", (768, 768), (18, 18, 22))
    draw = ImageDraw.Draw(img)
    draw.rectangle([16, 16, 752, 752], outline=(240, 190, 60), width=4)
    draw.text((40, 60), "banana-shim / mock", fill=(240, 190, 60))
    text = (prompt or "")[:600]
    y = 120
    while text:
        line, text = text[:42], text[42:]
        draw.text((40, y), line, fill=(230, 230, 230))
        y += 22
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def generate(prompt: str, size=None, images=None, n: int = 1) -> list:
    return [_png(prompt) for _ in range(max(1, n))]

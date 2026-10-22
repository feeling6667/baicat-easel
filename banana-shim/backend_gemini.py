# -*- coding: utf-8 -*-
"""Google AI Studio 后端: gemini-2.5-flash-image(即 Nano Banana)。

需要 GEMINI_API_KEY / GOOGLE_API_KEY, 从 aistudio.google.com 取。
官方接口, 稳定, 按张计费; 和 Flow 网页用的是同一个模型。
"""

import base64
import json
import os
import urllib.error
import urllib.request

NAME = "gemini-api"
MODELS = ["nano-banana", "gemini-2.5-flash-image"]
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RATIO_BY_PIXELS = {
    "1024x1024": "1:1", "1536x1536": "1:1", "2048x2048": "1:1",
    "1344x768": "16:9", "1920x1080": "16:9", "1024x576": "16:9",
    "768x1344": "9:16", "1080x1920": "9:16", "576x1024": "9:16",
    "1152x896": "4:3", "896x1152": "3:4",
    "832x1248": "2:3", "1248x832": "3:2",
    "1568x672": "21:9",
}


def api_key() -> str:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "IMG_GEMINI_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def model_name() -> str:
    return (os.environ.get("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image").strip()


def aspect_ratio(size) -> str:
    value = (str(size) or "").strip().lower()
    if not value:
        return ""
    if ":" in value:
        return value
    return RATIO_BY_PIXELS.get(value, "")


def _mime(raw: bytes) -> str:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def selfcheck() -> dict:
    key = api_key()
    return {
        "ok": bool(key),
        "backend": NAME,
        "model": model_name(),
        "api_key": ("已配置 " + key[:6] + "...") if key else "缺失: 设置 GEMINI_API_KEY",
        "提示": "" if key else "到 aistudio.google.com/apikey 取 key, 写进 banana-shim 的 .env",
    }


def generate(prompt: str, size=None, images=None, n: int = 1) -> list:
    key = api_key()
    if not key:
        raise RuntimeError("缺少 GEMINI_API_KEY, 无法调用 Google AI Studio")

    parts = [{"text": prompt}]
    for raw in images or []:
        parts.append({"inlineData": {"mimeType": _mime(raw),
                                     "data": base64.b64encode(raw).decode("ascii")}})

    body = {"contents": [{"role": "user", "parts": parts}]}
    ratio = aspect_ratio(size)
    if ratio:
        body["generationConfig"] = {"imageConfig": {"aspectRatio": ratio}}

    url = ENDPOINT.format(model=model_name())
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError("Gemini API " + str(exc.code) + ": " + detail) from exc

    out = []
    for cand in payload.get("candidates") or []:
        for part in ((cand.get("content") or {}).get("parts") or []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                out.append(base64.b64decode(inline["data"]))
    if not out:
        blocked = (payload.get("promptFeedback") or {}).get("blockReason")
        raise RuntimeError("Gemini 没有返回图片" + ("(被拦截: " + str(blocked) + ")" if blocked else ""))
    return out

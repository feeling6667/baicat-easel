#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""banana-shim - 把 EASEL 的 OpenAI 兼容生图请求转发到 Google Banana(Nano Banana)。

上游(EASEL 的 skills/shared/scripts/ai_image.py 等)看到的是标准 OpenAI 图片接口:
    GET  /v1/models
    POST /v1/images/generations     JSON
    POST /v1/images/edits           multipart/form-data
    POST /v1/images/variations      multipart/form-data
    GET  /healthz

下游(真正生图)是可插拔后端, 由 BANANA_BACKEND 选择:
    gemini   走 Google AI Studio API, 模型 gemini-2.5-flash-image(即 Nano Banana)
    flow     走 Playwright 驱动 labs.google Flow 网页, 消耗 Gemini Pro 订阅额度
    mock     不出网, 本地画占位图, 用来验证链路是否通
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PUBLIC_DIR = HERE / "public"
LOG_DIR = HERE / "logs"
PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / ("shim-" + time.strftime("%Y%m%d") + ".log")

BACKEND = None
BACKEND_LOCK = threading.Lock()
PUBLIC_BASE = "http://127.0.0.1:8790"


def load_env_file() -> None:
    """读同目录的 shim.env(其次 .env)。已存在的环境变量优先, 不覆盖。"""
    for name in ("shim.env", ".env"):
        path = HERE / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        log("已加载配置: " + name)
        return


def log(msg: str) -> None:
    line = "[" + time.strftime("%H:%M:%S") + "] " + str(msg)
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_backend(name: str):
    name = (name or "gemini").strip().lower()
    if name == "mock":
        import backend_mock as mod
    elif name == "arena":
        import backend_arena as mod
    elif name in ("flow", "flow-web", "playwright"):
        import backend_flow as mod
    else:
        import backend_gemini as mod
    log("backend = " + mod.NAME + " (" + name + ")")
    return mod


# ---------- 输入解析 ----------

def decode_image_ref(value):
    """data:URI 或 http(s) URL -> bytes; 失败返回 None。"""
    if isinstance(value, dict):
        value = value.get("url") or value.get("b64_json") or ""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("data:"):
        _, _, b64 = value.partition(",")
        try:
            return base64.b64decode(b64)
        except Exception:
            return None
    if value.startswith("http://") or value.startswith("https://"):
        try:
            with urllib.request.urlopen(value, timeout=60) as resp:
                return resp.read()
        except Exception as exc:
            log("下载输入图失败 " + value[:80] + " : " + str(exc))
            return None
    return None


def collect_images(payload: dict) -> list:
    """从 generations 请求体里挖输入图: image / images / extra_body.image。"""
    out = []
    raw = []
    for key in ("image", "images"):
        if key in payload:
            raw.append(payload[key])
    extra = payload.get("extra_body")
    if isinstance(extra, dict) and "image" in extra:
        raw.append(extra["image"])
    for item in raw:
        items = item if isinstance(item, list) else [item]
        for one in items:
            data = decode_image_ref(one)
            if data:
                out.append(data)
    return out


def parse_multipart(body: bytes, content_type: str):
    """极简 multipart 解析, 只认 name / filename, 够 edits / variations 用。"""
    fields, files = {}, {}
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match or not body:
        return fields, files
    boundary = b"--" + match.group(1).strip().encode()
    for chunk in body.split(boundary):
        if not chunk or chunk in (b"--", b"--\r\n", b"\r\n"):
            continue
        chunk = chunk.lstrip(b"\r\n")
        if b"\r\n\r\n" not in chunk:
            continue
        head, data = chunk.split(b"\r\n\r\n", 1)
        data = data.rstrip(b"\r\n")
        head_s = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', head_s)
        if not name:
            continue
        filename = re.search(r'filename="([^"]*)"', head_s)
        if filename and filename.group(1):
            files.setdefault(name.group(1), []).append(data)
        else:
            fields[name.group(1)] = data.decode("utf-8", "replace")
    return fields, files


# ---------- 输出封装 ----------

def build_data(images: list, prompt: str, want_url: bool) -> list:
    data = []
    for raw in images:
        item = {"revised_prompt": prompt}
        if want_url:
            name = uuid.uuid4().hex + ".png"
            (PUBLIC_DIR / name).write_bytes(raw)
            item["url"] = PUBLIC_BASE + "/files/" + name
        else:
            item["b64_json"] = base64.b64encode(raw).decode("ascii")
        data.append(item)
    return data


class ShimServer(HTTPServer):
    """兜底: 任何未捕获异常都不许杀掉服务。单个请求炸了, shim 必须继续活着。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        lines = traceback.format_exc().strip().splitlines()
        log("未捕获异常(已忽略, 服务继续): " + (lines[-1] if lines else "unknown"))


class Handler(BaseHTTPRequestHandler):
    server_version = "banana-shim/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _send(self, code: int, payload: dict, raw=None, ctype="application/json"):
        # 客户端可能已断开; 往死 socket 写会抛二次异常, 必须吞掉, 否则能杀掉服务。
        try:
            body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as exc:
            log("客户端已断开, 响应丢弃: " + type(exc).__name__)

    def _error(self, code: int, message: str):
        log("ERROR " + str(code) + " " + message[:400])
        self._send(code, {"error": {"message": message, "type": "banana_shim_error"}})

    def _note(self, method: str, path: str) -> None:
        ua = (self.headers.get("User-Agent") or "-")[:70]
        log(method + " " + path + "  ua=" + ua)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        self._note("GET", path)
        if "/images/" in path:
            # 有客户端会 GET 一下端点做探活; 回空列表比 404 友好。
            return self._send(200, {"object": "list", "data": []})
        if path in ("/healthz", ""):
            return self._send(200, {"ok": True, "backend": BACKEND.NAME, "models": BACKEND.MODELS})
        if path == "/v1/models":
            return self._send(200, {
                "object": "list",
                "data": [{"id": m, "object": "model", "created": int(time.time()),
                          "owned_by": "banana-shim"} for m in BACKEND.MODELS],
            })
        if path.startswith("/files/"):
            target = PUBLIC_DIR / Path(path).name
            if target.is_file():
                return self._send(200, {}, raw=target.read_bytes(), ctype="image/png")
        return self._error(404, "no route: " + path)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        self._note("POST", path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            ctype = self.headers.get("Content-Type", "")
            prompt, size, want_url, images, count = "", None, False, [], 1

            if path.endswith("/images/generations"):
                payload = json.loads(body.decode("utf-8") or "{}")
                prompt = str(payload.get("prompt") or "")
                size = payload.get("size")
                count = max(1, int(payload.get("n") or 1))
                want_url = str(payload.get("response_format") or "").lower() == "url"
                images = collect_images(payload)
            elif path.endswith("/images/edits") or path.endswith("/images/variations"):
                fields, upfiles = parse_multipart(body, ctype)
                prompt = fields.get("prompt") or ""
                size = fields.get("size")
                want_url = (fields.get("response_format") or "").lower() == "url"
                count = max(1, int(fields.get("n") or 1))
                for key in ("image", "image[]", "images"):
                    images.extend(upfiles.get(key, []))
            else:
                return self._error(404, "no route: " + path)

            if not prompt and not images:
                return self._error(400, "prompt 与输入图都为空")
            if not images:
                prompt = prompt or "a picture"

            log("生成请求 backend=" + BACKEND.NAME + " n=" + str(count) +
                " size=" + str(size) + " imgs=" + str(len(images)) +
                " prompt=" + prompt[:70].replace("\n", " "))

            started = time.time()
            results = []
            with BACKEND_LOCK:
                for _ in range(count):
                    results.extend(BACKEND.generate(prompt=prompt, size=size, images=images, n=1))
            log("完成 " + str(len(results)) + " 张, 用时 " + format(time.time() - started, ".2f") + "s")

            return self._send(200, {"created": int(time.time()),
                                    "data": build_data(results, prompt, want_url)})
        except Exception as exc:
            traceback.print_exc()
            return self._error(500, str(exc))


def main():
    global BACKEND, PUBLIC_BASE
    load_env_file()
    ap = argparse.ArgumentParser(description="EASEL -> Google Banana 生图 shim")
    ap.add_argument("--host", default=os.environ.get("BANANA_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("BANANA_PORT", "8790")))
    ap.add_argument("--backend", default=os.environ.get("BANANA_BACKEND", "gemini"))
    ap.add_argument("--check", action="store_true", help="只做后端自检, 不启动服务")
    args = ap.parse_args()

    BACKEND = load_backend(args.backend)
    PUBLIC_BASE = "http://" + args.host + ":" + str(args.port)

    if args.check:
        report = BACKEND.selfcheck()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 2

    # 单线程: 生成本来就是串行的(BACKEND_LOCK), 且 Playwright sync API 绑定创建它的线程。
    server = ShimServer((args.host, args.port), Handler)
    log("listening on " + PUBLIC_BASE + "  (Ctrl+C 退出)")
    log("把 EASEL 的 .env 指过来: IMG_BASE_URL=" + PUBLIC_BASE + "/v1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

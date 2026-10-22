"""检查 gateway 的「原始流」+「默认模型」是否就位（Easel 对话能正常出字的两道命脉）。

背景（2026-10-13 实测结论）：
  原始流（关于"卡在正在思考…"）：
    * `openclaw agent` 是瘦客户端，**不写**原始流；只有常驻 gateway 会写。
    * gateway 必须在启动时带 `--raw-stream --raw-stream-path <file>`（或等价环境变量），
      否则那个文件永远不会被创建，web 前端就永远停在「🧠 正在思考…」——
      模型其实答完了，一个字都推不出来。
    * 本机由 scripts/gateway.ps1 的「本机补丁 4/4」负责把这两个参数带上，
      文件固定在 <配置目录>/logs/raw-stream.jsonl。

  默认模型（关于"报 Gemini MCP 安全警告"）：
    * HTTP 路径若没带 `x-openclaw-model` 头，网关会落到 agents.defaults.model.primary。
      若它指向 `google-gemini-cli/*`，一跑就撞 Gemini MCP 在 Temp 的权限告警，整轮报错。
    * 本机已把它改成 `magpie/group/auto-glm-5-3-flash`。这里顺带复核一眼。

输出一个英文 token（永远 exit 0，这是诊断工具）：
  OK / EMPTY / MISSING / NOGATEWAY  [+ `|DEF=<primary>|GEMINI|MAGPIE`]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SUSPECT_PREFIX = "google-gemini-cli/"


def _raw_stream_path() -> Path:
    env = os.environ.get("EASEL_RAW_STREAM_PATH", "").strip()
    if env:
        return Path(env)
    try:
        from easel.openclaw_workspace import state_dir
        return state_dir() / "logs" / "raw-stream.jsonl"
    except Exception:  # noqa: BLE001
        import tempfile
        return Path(tempfile.gettempdir()) / "easel-raw-stream.jsonl"


def _gateway_listening() -> bool:
    """问 easel 的端口解析器 + 直连健康探针；不复刻端口推导逻辑。"""
    try:
        from easel.gateway_endpoint import resolve_gateway_port
        port = int(resolve_gateway_port())
    except Exception:  # noqa: BLE001
        return False
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=3) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def _default_primary() -> str:
    """读 openclaw.json 里 agents.defaults.model.primary；读不到返回空串。"""
    try:
        from easel.openclaw_workspace import config_path
        cfg = Path(config_path())
        if not cfg.is_file():
            return ""
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return str(((data.get("agents") or {}).get("defaults") or {})
                   .get("model", {}).get("primary") or "")
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    if not _gateway_listening():
        print("NOGATEWAY")
        return 0
    raw = _raw_stream_path()
    try:
        size = raw.stat().st_size
        state = "OK" if size > 0 else "EMPTY"
    except OSError:
        state = "MISSING"
    primary = _default_primary()
    # ⚠️ 分隔符必须避开 cmd 的元字符（尤其 `|`：在 `echo %VAR% | findstr` 里会被当成管道，
    # 把整条命令拆烂）。这里用逗号拼接，调用方（start-easel.cmd）按子串匹配即可。
    if primary:
        flag = "GEMINI" if primary.startswith(_SUSPECT_PREFIX) else "MAGPIE"
        print(f"{state},DEF={primary},{flag}")
    else:
        print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

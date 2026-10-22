"""Resolve how to invoke the openclaw CLI as an argv prefix.

Windows pitfall this solves: `openclaw` on PATH is a `.cmd` shim. Python's
CreateProcess cannot run it directly, and wrapping in cmd.exe /c breaks
messages containing newlines (cmd treats the rest of the line as a separate
command -> "your message got cut off" / silently truncated input). Running
`node openclaw.mjs` directly avoids both problems.

On Linux/macOS `openclaw` on PATH is a real executable (a symlink to
`openclaw.mjs` with a `#!/usr/bin/env node` shebang), so running it directly
is fine. The resolution below therefore prefers `node + openclaw.mjs` when it
can locate the script (robust everywhere, mandatory on Windows), and falls
back to the PATH `openclaw` executable rather than hard-failing.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

# ---- 本机 Node 缺陷的绕行补丁（Easel 官方代码没有这段，是本机专属）----------
# 本机 spawnSync 在需要为子进程创建 stdin 管道时必然抛 EBUSY，openclaw 读状态库
# 时会用到，导致 gateway 起不来（表现为 `Cannot read properties of undefined
# (reading 'trim')` —— openclaw 自己的报错格式化 bug 把真实原因吞了）。
# 这里把 spawn-shim 挂进 NODE_OPTIONS，让 openclaw 及其子进程都带上。
# 卸载：把 EASEL_SPAWN_SHIM 环境变量设为空字符串（""）即可跳过。
SPAWN_SHIM = Path(
    os.environ.get("EASEL_SPAWN_SHIM")
    or r"C:\Users\Administrator\.easel-tools\spawn-shim.cjs"
)


def _apply_spawn_shim() -> None:
    """把 spawn-shim 追加进 NODE_OPTIONS（幂等）。文件不存在则什么都不做。

    EASEL_SPAWN_SHIM 显式设为空字符串时直接跳过（用于临时关闭补丁）。
    """
    if not os.environ.get("EASEL_SPAWN_SHIM", None) and "EASEL_SPAWN_SHIM" in os.environ:
        return
    if not SPAWN_SHIM.is_file():
        return
    flag = f"--require {SPAWN_SHIM}"
    current = os.environ.get("NODE_OPTIONS", "")
    if flag in current:
        return
    os.environ["NODE_OPTIONS"] = f"{current} {flag}".strip()


def _npmrc_prefix(path: Path) -> Path | None:
    """从 .npmrc 读 prefix=（支持 #/; 注释与尾随空白）；文件不存在或未配置返回 None。"""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith(("prefix=", "prefix =")):
                val = s.split("=", 1)[1].strip().strip('"')
                if val:
                    p = Path(val).expanduser()
                    return p.resolve() if p.is_absolute() else p
    except OSError:
        pass
    return None


@lru_cache(maxsize=1)
def openclaw_base_cmd() -> list[str]:
    """Return the argv prefix for invoking openclaw.

    Prefers ``[node, /path/to/openclaw.mjs]``; falls back to ``[openclaw]`` on
    PATH. Raises FileNotFoundError only when openclaw cannot be located at all.
    """
    _apply_spawn_shim()   # 本机专属：先挂 spawn 绕行补丁，再解析命令行
    node = shutil.which("node")
    oc = shutil.which("openclaw")

    # 1) PATH `openclaw` that resolves to the .mjs (Unix symlink, or a direct
    #    .mjs on PATH): run it through node explicitly.
    if oc:
        resolved = Path(oc).resolve()
        if resolved.suffix == ".mjs" and resolved.is_file():
            adjacent_node = Path(oc).parent / ("node.exe" if os.name == "nt" else "node")
            if adjacent_node.is_file() and os.access(adjacent_node, os.X_OK):
                return [str(adjacent_node), str(resolved)]
            if node:
                return [node, str(resolved)]

    # 1.5) PATH `openclaw` is a Windows npm .cmd shim: the shim sits next to
    #      node.exe + node_modules/openclaw/openclaw.mjs (standard npm global
    #      layout — see the shim body). Running the shim routes the argv through
    #      cmd.exe, which truncates a multiline `--message` at the first newline
    #      ("消息只剩第一行/画像前缀" 的根因，实测于 PATH 里 nvm node 在前的机器)。
    #      这里解析出 shim 同目录的真实 node + .mjs，完全绕开 cmd.exe。
    if oc:
        shim = Path(oc).resolve()
        if shim.suffix.lower() in (".cmd", ".bat"):
            shim_dir = shim.parent
            mjs = shim_dir / "node_modules" / "openclaw" / "openclaw.mjs"
            node_exe = shim_dir / "node.exe"
            if mjs.is_file() and node_exe.is_file():
                return [str(node_exe), str(mjs)]

    # 2) Hunt for openclaw.mjs under the known npm global layouts.
    if node:
        node_dir = Path(node).resolve().parent
        candidates = [
            # Unix standard: <prefix>/bin/node -> <prefix>/lib/node_modules/...
            node_dir.parent / "lib" / "node_modules" / "openclaw" / "openclaw.mjs",
            # npm global prefix == node dir (zip / some nvm-style installs)
            node_dir / "node_modules" / "openclaw" / "openclaw.mjs",
            # Windows standard: npm global prefix == %APPDATA%/npm
            Path.home() / "AppData" / "Roaming" / "npm" / "node_modules" / "openclaw" / "openclaw.mjs",
        ]
        for cand in candidates:
            if cand.is_file():
                return [node, str(cand)]

        # npm 自定义全局前缀（官方推荐的免 sudo 装法 `npm config set prefix
        # ~/.npm-global`）：此时 node 在系统路径、openclaw 在用户前缀下 ——
        # 上面的 node 相对布局全部落空。按可靠性逐级探测：
        # 显式环境变量 > .npmrc 的 prefix 键（项目级覆盖用户级，与 npm 同序）
        # > npm_config_prefix 环境变量（仅在 npm 脚本环境导出）> ~/.npm-global 约定。
        prefix_roots: list[Path] = []
        explicit = os.environ.get("EASEL_NPM_GLOBAL_PREFIX", "").strip()
        if explicit:
            prefix_roots.append(Path(explicit))
        npm_cfg = os.environ.get("npm_config_prefix", "").strip()
        if npm_cfg:
            prefix_roots.append(Path(npm_cfg))
        for npmrc in (Path(".npmrc"), Path.home() / ".npmrc"):
            val = _npmrc_prefix(npmrc)
            if val:
                prefix_roots.append(val)
        prefix_roots.append(Path.home() / ".npm-global")
        for root in prefix_roots:
            for cand in (
                root / "lib" / "node_modules" / "openclaw" / "openclaw.mjs",
                root / "node_modules" / "openclaw" / "openclaw.mjs",
            ):
                if cand.is_file():
                    return [node, str(cand)]

    # 3) Fallback: run the PATH `openclaw` directly. On Unix this is a real
    #    executable and works. On Windows this is the `.cmd` shim (only reached
    #    when the .mjs truly can't be found) — still better than hard-failing.
    if oc:
        return [oc]

    raise FileNotFoundError(
        "openclaw CLI not found: no 'node'+openclaw.mjs and no 'openclaw' on PATH。"
        "请先安装：npm i -g openclaw（若用自定义全局前缀，可设 EASEL_NPM_GLOBAL_PREFIX 指向该前缀）"
    )

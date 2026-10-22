# -*- coding: utf-8 -*-
r"""banana-shim 守护进程: shim 挂掉就自动拉起。

为什么需要它: backend_arena 用 Playwright 常驻浏览器, 关闭窗口 / 上游崩溃 /
长跑内存问题都可能让 shim 进程退出。exit 后 8790 就空了, EASEL 生图全部失败。
守护器负责把它重新拉起来, 并记录退出原因。

用法:
    python guard-shim.py            # 前台守护, Ctrl+C 停止
    python guard-shim.py --once     # 只拉起一次, 不管后续(调试用)

日志: logs/guard-YYYYMMDD.log
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SHIM = HERE / "shim.py"
PYTHON = sys.executable
LOCK = HERE / "guard.lock"

BACKOFF = [2, 5, 10, 20, 30]      # 连续失败退避(秒)
STABLE_SECONDS = 120              # 跑满这么久算"稳住了", 退避重置


def acquire_single_instance():
    """单实例锁: 第二个守护进程必须立刻退出。

    没有这把锁时, 两个守护会互相触发——A 拉起 shim 占住端口, B 的 shim 绑不上就退出,
    B 立刻重启, A 的 shim 又被打断……无限循环, 表现为「清掉 2 秒后又变 4 个」。
    锁文件里记 pid; 持锁进程已死就接管(手动清理/崩溃残留的情况)。
    """
    if LOCK.is_file():
        try:
            old = int(LOCK.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            print("[guard] 已有守护进程在跑 (pid=" + str(old) + "), 本实例退出", flush=True)
            return False
        LOCK.unlink(missing_ok=True)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq " + str(pid), "/NH"],
                             capture_output=True, text=True, timeout=8).stdout
        return str(pid) in out
    except Exception:  # noqa: BLE001
        return False


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def log(msg: str) -> None:
    line = "[" + time.strftime("%Y-%m-%d %H:%M:%S") + "] " + str(msg)
    print(line, flush=True)
    try:
        with (LOG_DIR / ("guard-" + time.strftime("%Y%m%d") + ".log")).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def run_once() -> int:
    port = int(os.environ.get("BANANA_PORT", "8790"))
    # 端口已经有人在听 = 已有 shim 活着, 不要再起一个(否则两个抢同一端口, 一起死)
    if port_busy(port):
        log("端口 " + str(port) + " 已被占用, 视为 shim 存活, 30s 后复查")
        time.sleep(30)
        return 0
    cmd = [PYTHON, str(SHIM), "--host", "127.0.0.1", "--port", str(port)]
    log("启动 shim: " + " ".join(cmd))
    started = time.time()
    proc = subprocess.Popen(cmd, cwd=str(HERE),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)

    # 把 shim 的输出转发到守护日志, 同时保留它自己的日志文件
    try:
        for line in proc.stdout or []:
            print(line, end="", flush=True)
    except Exception:
        pass

    code = proc.wait()
    lived = time.time() - started
    log("shim 退出 code=" + str(code) + " 存活 " + format(lived, ".1f") + "s")
    return code


def main() -> int:
    once = "--once" in sys.argv
    if not acquire_single_instance():
        return 0
    fails = 0
    log("守护启动 pid=" + str(os.getpid()) + " (Ctrl+C 退出)")
    try:
        while True:
            started = time.time()
            code = run_once()
            lived = time.time() - started

            if once:
                log("--once 模式, 不重启")
                return code

            fails = 0 if lived >= STABLE_SECONDS else fails + 1
            delay = BACKOFF[min(fails, len(BACKOFF) - 1)]
            log("将在 " + str(delay) + "s 后重启 (连续异常 " + str(fails) + " 次)")
            time.sleep(delay)
    except KeyboardInterrupt:
        log("收到 Ctrl+C, 守护退出")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

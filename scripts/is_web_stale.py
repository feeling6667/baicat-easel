"""判断 Web 控制台上跑的是不是最新代码。

给 start-easel.cmd 用：如果后台进程的启动时间早于关键源码文件的修改时间，
说明改完代码没重启 —— 该脚本据此决定要不要自动重启。

输出（stdout，单行，无中文以免编码麻烦）：
    STALE      旧代码，需要重启
    FRESH      已是最新，不用重启
    NOLISTEN   7860 没有监听（服务没在跑）
    UNKNOWN    查不出来（权限等），调用方自行决定

退出码恒为 0，避免把 cmd 的 errorlevel 判断搅乱。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PORT = int(os.environ.get("EASEL_STALENESS_PORT", "7860"))
ROOT = Path(__file__).resolve().parents[1]

# 关键源码：改了这些就必须重启后端
WATCH = [
    ROOT / "web" / "app.py",
    ROOT / "web" / "frontend" / "dist" / "index.html",
    ROOT / "easel" / "openclaw_cmd.py",
]


def newest_watch_mtime() -> float | None:
    """返回被监视文件里最新的修改时间；一个都不存在则 None。"""
    stamps = [p.stat().st_mtime for p in WATCH if p.is_file()]
    return max(stamps) if stamps else None


def listening_pid() -> int | None:
    """找正在监听 PORT 的进程号。netstat 输出是 OEM 编码，按 latin-1 兜底读。"""
    try:
        raw = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, timeout=15,
        ).stdout.decode("latin-1", "replace")
    except Exception:
        return None
    needle = f":{PORT}"
    for line in raw.splitlines():
        upper = line.upper()
        if needle in line and "LISTENING" in upper:
            parts = line.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1])
    return None


def process_start_time(pid: int) -> float | None:
    """进程启动时间（epoch 秒）。用 wmic（本机可用），失败再用 tasklist 兜底。"""
    # 首选：wmic 能直接给 CreationDate
    try:
        out = subprocess.run(
            ["wmic", "process", "where", f"ProcessId={pid}",
             "get", "CreationDate", "/value"],
            capture_output=True, timeout=15,
        ).stdout.decode("latin-1", "replace")
        for line in out.splitlines():
            if line.startswith("CreationDate="):
                stamp = line.split("=", 1)[1].strip()
                if len(stamp) >= 14 and stamp[:14].isdigit():
                    import datetime
                    return datetime.datetime.strptime(
                        stamp[:14], "%Y%m%d%H%M%S").timestamp()
    except Exception:
        pass
    # 兜底：PowerShell（换成单引号最少、最不容易被 cmd 吃掉的写法）
    try:
        ps = ("(Get-Process -Id %d -ErrorAction SilentlyContinue)"
              ".StartTime.ToFileTimeUtc()" % pid)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=20,
        ).stdout.decode("utf-8", "replace").strip()
        if out.isdigit():
            # FILETIME(100ns，1601 起) → epoch 秒
            return int(out) / 1e7 - 11644473600
    except Exception:
        pass
    return None


def main() -> int:
    pid = listening_pid()
    if not pid:
        print("NOLISTEN")
        return 0
    src_mtime = newest_watch_mtime()
    if src_mtime is None:
        print("UNKNOWN")
        return 0
    started = process_start_time(pid)
    if started is None:
        print("UNKNOWN")
        return 0
    # 留 2 秒余量：同一次操作里"写文件"和"起进程"可能几乎同时
    print("STALE" if started < src_mtime - 2 else "FRESH")
    return 0


if __name__ == "__main__":
    sys.exit(main())

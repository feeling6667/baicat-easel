#!/usr/bin/env python3
"""harvest_all.py — 批量对标采集：读清单 → 导入对标作者 → 采集高赞作品 → 入选题库。

清单文件格式（UTF-8 文本，每行一个作者）：
    <平台> | <作者标识> | <备注名字>

    - 平台：youtube 或 bilibili
    - 作者标识：
        YouTube  → @handle / 频道完整URL / UC 开头的 channel_id
        B站      → mid 数字 / https://space.bilibili.com/<mid>
    - 备注名字：可省略（省略时用标识本身）
    - 以 # 开头的是注释行，空行忽略

用法：
    python harvest_all.py                          # 用默认清单（桌面）
    python harvest_all.py --list <文件路径>
    python harvest_all.py --dry-run                # 只解析并打印，不实际采集
    python harvest_all.py --min-views-youtube 50000 --min-views-bilibili 300000

说明：本脚本只做调度，真正的抓取由同目录下的两个适配器完成：
    creator_feed.py   —— YouTube（频道 RSS，零依赖、无需登录）
    bili_creator.py   —— B站（游客态 wbi 签名）
全程不使用任何平台账号。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CREATOR_FEED = HERE / "creator_feed.py"
BILI_CREATOR = HERE / "bili_creator.py"

PLATFORMS = {"youtube", "bilibili"}
DEFAULT_YOUTUBE_MIN_VIEWS = 100_000
DEFAULT_BILIBILI_MIN_VIEWS = 300_000
FETCH_LIMIT = 30


def default_list_path() -> Path:
    """桌面上的对标清单.txt；找不到就退回脚本同目录。"""
    desktop = Path.home() / "Desktop"
    cand = desktop / "对标清单.txt"
    if cand.is_file():
        return cand
    return HERE / "对标清单.txt"


def parse_list(path: Path) -> list[tuple[str, str, str]]:
    """解析清单 → [(platform, author, name)]。格式错误就抛 ValueError。"""
    if not path.is_file():
        raise ValueError("清单文件不存在：%s" % path)
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    # utf-8-sig：记事本 / PowerShell 存出来的 UTF-8 常带 BOM，不去掉会把首行的 # 认成普通字符
    text = path.read_text(encoding="utf-8-sig")
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 清单是给人看的：说明文字、装饰线这类不含竖线的行直接忽略，不当成格式错误
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            raise ValueError(
                "第 %d 行格式不对：%r\n"
                "正确格式：<平台> | <作者标识> | <备注名字>（名字可省略）" % (lineno, raw)
            )
        platform = parts[0].lower()
        author = parts[1]
        name = parts[2] if len(parts) > 2 and parts[2] else author
        # 说明文字里常出现竖线（例如"用竖线 | 分成三段"）。这类行的第一段是中文或带空格，
        # 不像平台名，直接忽略；只有"长得像平台名却不认识"的才报错，免得漏掉写错的平台。
        if not platform.isascii() or " " in platform:
            continue
        if platform not in PLATFORMS:
            raise ValueError(
                "第 %d 行平台不支持：%r（只支持 %s）"
                % (lineno, platform, " / ".join(sorted(PLATFORMS)))
            )
        if not author:
            raise ValueError("第 %d 行作者标识为空" % lineno)
        key = (platform, author)
        if key in seen:
            print("  [跳过] 清单内重复：%s | %s" % (platform, author))
            continue
        seen.add(key)
        rows.append((platform, author, name))
    return rows


def run(script: Path, args: list[str]) -> tuple[int, str]:
    """调用适配器脚本，返回 (退出码, 输出)。"""
    cmd = [sys.executable, str(script)] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def import_author(platform: str, author: str, name: str) -> tuple[bool, str]:
    if platform == "youtube":
        code, out = run(CREATOR_FEED, ["authors", "add", "--platform", "youtube",
                                       "--author", author, "--name", name])
    else:
        code, out = run(BILI_CREATOR, ["authors", "add", "--author", author, "--name", name])
    return code == 0, out


def push_author(platform: str, author: str, min_views: int) -> tuple[bool, str]:
    if platform == "youtube":
        code, out = run(CREATOR_FEED, ["push", "--platform", "youtube", "--author", author,
                                       "--limit", str(FETCH_LIMIT), "--min-views", str(min_views)])
    else:
        code, out = run(BILI_CREATOR, ["push", "--author", author,
                                       "--limit", str(FETCH_LIMIT), "--min-views", str(min_views)])
    return code == 0, out


def main() -> int:
    ap = argparse.ArgumentParser(description="批量对标采集：清单 → 导入 → 采集高赞 → 入选题库")
    ap.add_argument("--list", dest="list_file", default="", help="清单文件路径（默认桌面上的 对标清单.txt）")
    ap.add_argument("--min-views-youtube", type=int, default=DEFAULT_YOUTUBE_MIN_VIEWS,
                    help="YouTube 入选题库的最低播放量（默认 %d）" % DEFAULT_YOUTUBE_MIN_VIEWS)
    ap.add_argument("--min-views-bilibili", type=int, default=DEFAULT_BILIBILI_MIN_VIEWS,
                    help="B站入选题库的最低播放量（默认 %d）" % DEFAULT_BILIBILI_MIN_VIEWS)
    ap.add_argument("--dry-run", action="store_true", help="只解析清单并打印，不实际采集")
    args = ap.parse_args()

    list_path = Path(args.list_file) if args.list_file else default_list_path()
    print("清单文件：%s" % list_path)

    try:
        rows = parse_list(list_path)
    except ValueError as exc:
        print("\n[清单有问题] %s" % exc, file=sys.stderr)
        return 2

    if not rows:
        print("\n清单里还没有作者。打开该文件，把「# youtube | @mkbhd | MKBHD」"
              "这类示例行的 # 去掉、改成你的对标作者，保存后再运行。")
        return 0

    print("解析到 %d 个对标作者：\n" % len(rows))
    for platform, author, name in rows:
        print("  [%s] %s  (%s)" % (platform, name, author))
    print()

    if args.dry_run:
        print("（--dry-run：只解析，未采集）")
        return 0

    ok = fail = 0
    for idx, (platform, author, name) in enumerate(rows, 1):
        min_views = args.min_views_youtube if platform == "youtube" else args.min_views_bilibili
        print("─" * 60)
        print("[%d/%d] %s · %s" % (idx, len(rows), name, platform))

        added, out = import_author(platform, author, name)
        if not added:
            print("  [导入失败] %s" % out.replace("\n", " ")[:200])
            fail += 1
            continue

        pushed, out2 = push_author(platform, author, min_views)
        if not pushed:
            print("  [采集失败] %s" % out2.replace("\n", " ")[:200])
            fail += 1
            continue

        summary = out2.strip().splitlines()[-1] if out2.strip() else "(无输出)"
        print("  门槛 %s 播放 → %s" % (min_views, summary))
        ok += 1

    print("─" * 60)
    print("\n完成：成功 %d 个，失败 %d 个。" % (ok, fail))
    print("打开 Easel 的「选题库」页面即可看到新入库的高赞作品。")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""search_all.py — 按关键词批量搜高赞作品，自动入选题库。

词表文件格式（UTF-8 文本，每行一个词）：
    <平台> | <关键词> | <最低播放量>

    - 平台：bilibili（免费，无需账号）| douyin（走 TikHub，$0.01/次，需账户余额）
    - 最低播放量/点赞可省略，省略时用该平台的默认门槛
    - bilibili 用播放量门槛；douyin 搜索结果没有播放量，用点赞数门槛
    - 以 # 开头的是注释行；不含竖线的行（说明文字、装饰线）会被忽略

用法：
    python search_all.py                       # 用默认词表（桌面 搜索词表.txt）
    python search_all.py --list <文件路径>
    python search_all.py --dry-run             # 只解析并打印，不实际搜索

说明：本脚本只做调度，真正的抓取交给同目录下的平台适配器：
    bili_creator.py   —— B站（游客态 wbi 签名，免费）
    tikhub_search.py  —— 抖音（TikHub API，按次计费）
全程不使用任何平台账号登录态。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BILI = HERE / "bili_creator.py"
TIKHUB = HERE / "tikhub_search.py"
TWITTER = HERE / "twitter_tl1.py"

PLATFORMS = {"bilibili", "douyin", "twitter"}
DEFAULT_MIN_VIEWS = {"bilibili": 500_000, "douyin": 10_000, "twitter": 100_000}


def default_list_path() -> Path:
    """桌面上的搜索词表.txt；找不到就退回脚本同目录。"""
    cand = Path.home() / "Desktop" / "搜索词表.txt"
    if cand.is_file():
        return cand
    return HERE / "搜索词表.txt"


def parse_list(path: Path) -> list[tuple[str, str, int]]:
    """解析词表 → [(platform, keyword, min_views)]。"""
    if not path.is_file():
        raise ValueError("词表文件不存在：%s" % path)
    rows: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    # utf-8-sig：记事本 / PowerShell 存出来的 UTF-8 常带 BOM
    text = path.read_text(encoding="utf-8-sig")
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 说明文字、装饰线这类不含竖线的行直接忽略，不当成格式错误
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        platform = parts[0].lower()
        # 第一段是中文或带空格 → 说明文字，跳过；只有"像平台名却不认识"的才报错
        if not platform.isascii() or " " in platform:
            continue
        if platform not in PLATFORMS:
            raise ValueError(
                "第 %d 行平台不支持：%r（只支持 %s）"
                % (lineno, platform, " / ".join(sorted(PLATFORMS)))
            )
        keyword = parts[1] if len(parts) > 1 else ""
        # twitter 的关键词可以留空（TL1 趋势流本身就按热度排）
        if not keyword and platform != "twitter":
            raise ValueError("第 %d 行关键词为空" % lineno)
        min_views = DEFAULT_MIN_VIEWS[platform]
        if len(parts) > 2 and parts[2]:
            digits = parts[2].replace(",", "").replace("，", "").strip()
            if not digits.isdigit():
                raise ValueError(
                    "第 %d 行最低播放量不是数字：%r" % (lineno, parts[2])
                )
            min_views = int(digits)
        key = (platform, keyword)
        if key in seen:
            print("  [跳过] 词表内重复：%s | %s" % (platform, keyword))
            continue
        seen.add(key)
        rows.append((platform, keyword, min_views))
    return rows


def run(script: Path, args: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, str(script)] + args
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def search_one(platform: str, keyword: str, min_views: int) -> tuple[bool, str]:
    if platform == "bilibili":
        code, out = run(BILI, ["push-search", "--keyword", keyword,
                               "--limit", "20", "--min-views", str(min_views)])
    elif platform == "twitter":
        # 推特：TL1 趋势热帖，完全免费、零 API 成本
        code, out = run(TWITTER, ["push", "--keyword", keyword,
                                  "--limit", "20", "--min-views", str(min_views)])
    else:
        # 抖音：TikHub 搜索，注意它筛的是点赞数不是播放量
        code, out = run(TIKHUB, ["push", "--keyword", keyword,
                                 "--limit", "20", "--min-likes", str(min_views),
                                 "--label-type", "1"])
    return code == 0, out


def main() -> int:
    ap = argparse.ArgumentParser(description="按关键词批量搜高赞作品并入库")
    ap.add_argument("--list", dest="list_file", default="",
                    help="词表文件路径（默认桌面上的 搜索词表.txt）")
    ap.add_argument("--dry-run", action="store_true", help="只解析词表，不实际搜索")
    args = ap.parse_args()

    path = Path(args.list_file) if args.list_file else default_list_path()
    print("词表文件：%s" % path)

    try:
        rows = parse_list(path)
    except ValueError as exc:
        print("\n[词表有问题] %s" % exc, file=sys.stderr)
        return 2

    if not rows:
        print("\n词表里还没有关键词。打开该文件，把示例行前面的 # 去掉、"
              "改成你要搜的词（育儿 / 婆媳 / 家庭 / 婚姻 …），保存后再运行。")
        return 0

    print("解析到 %d 个搜索词：\n" % len(rows))
    for platform, keyword, min_views in rows:
        kw_part = " %-12s" % ("“%s”" % keyword if keyword else "(全量)")
        print("  [%s]%s (门槛 %s)" % (platform, kw_part.rjust(14), min_views))
    print()

    if args.dry_run:
        print("（--dry-run：只解析，未搜索）")
        return 0

    ok = fail = 0
    for idx, (platform, keyword, min_views) in enumerate(rows, 1):
        print("─" * 60)
        if keyword:
            print("[%d/%d] %s · %s（门槛 %s）" % (idx, len(rows), keyword, platform, min_views))
        else:
            print("[%d/%d] %s（全量最热，门槛 %s）" % (idx, len(rows), platform, min_views))
        success, out = search_one(platform, keyword, min_views)
        if not success:
            print("  [失败] %s" % out.replace("\n", " ")[:220])
            fail += 1
            continue
        tail = [l for l in out.splitlines() if l.strip()][-1:] or ["(无输出)"]
        print("  %s" % tail[0])
        ok += 1

    print("─" * 60)
    print("\n完成：成功 %d 个词，失败 %d 个。" % (ok, fail))
    print("打开 Easel 的「选题库」页面即可看到新入库的高赞作品。")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

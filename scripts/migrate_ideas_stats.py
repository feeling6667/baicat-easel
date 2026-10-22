"""一次性迁移：把 outputs/_ideas.json 的 note 文本回填到结构化字段 url / stats。

note 现有格式形如：
    播放 502.5万 · 评论 3732 · https://www.bilibili.com/video/BV1yEaJ6CEUk
    播放 646.6万 · https://www.youtube.com/watch?v=QVv3mQzlrN0

规则：
- url：正则提取作品链接，没有则 ""
- stats：键固定 views / likes / comments，取不到的指标写 null，整体无数据写 {}
- note 字段原样保留，不清空
- 幂等：已有非空 url 的条目跳过；无实际变化时不写文件
- 原子写：tmp + replace，json.dumps(..., ensure_ascii=False, indent=2)

用法：
    .venv\\Scripts\\python.exe scripts\\migrate_ideas_stats.py [ideas.json 路径]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parents[1] / "outputs" / "_ideas.json"
STAT_KEYS = ("views", "likes", "comments")

URL_RE = re.compile(r"https?://[^\s，。、）)】\]\"']+")
METRIC_RES = {
    "views": re.compile(r"播放(?:量)?\s*[:：]?\s*([\d.]+)\s*([万亿]?)"),
    "likes": re.compile(r"点赞(?:数|量)?\s*[:：]?\s*([\d.]+)\s*([万亿]?)"),
    "comments": re.compile(r"评论(?:数|量)?\s*[:：]?\s*([\d.]+)\s*([万亿]?)"),
}
UNIT = {"": 1, "万": 10000, "亿": 100000000}


def parse_note(note: str) -> tuple[str, dict]:
    """从 note 文本提取 (url, stats)。解析不出的指标保持 null，整体无数据则 stats={}。"""
    text = note or ""
    m = URL_RE.search(text)
    url = m.group(0) if m else ""
    stats: dict = {}
    for key in STAT_KEYS:
        mm = METRIC_RES[key].search(text)
        if not mm:
            continue
        try:
            stats[key] = int(round(float(mm.group(1)) * UNIT.get(mm.group(2), 1)))
        except (TypeError, ValueError):
            continue
    if not stats:
        return url, {}
    return url, {k: stats.get(k) for k in STAT_KEYS}


def migrate(items: list, path: Path) -> dict:
    """回填 url/stats，返回统计。无变化时不落盘（保证可重复执行）。"""
    report = {
        "total": 0, "with_url_before": 0, "skipped_has_url": 0,
        "changed": 0, "unchanged": 0, "wrote": "no",
    }
    if not isinstance(items, list):
        return report
    report["total"] = len(items)
    report["with_url_before"] = sum(
        1 for it in items if isinstance(it, dict) and str(it.get("url") or "").strip()
    )
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("url") or "").strip():
            report["skipped_has_url"] += 1
            continue
        url, stats = parse_note(str(it.get("note") or ""))
        before = (it.get("url"), it.get("stats"))
        if before == (url, stats):
            report["unchanged"] += 1
            continue
        it["url"] = url
        it["stats"] = stats
        report["changed"] += 1
    if report["changed"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        report["wrote"] = "yes"
    report["with_url_after"] = sum(
        1 for it in items if isinstance(it, dict) and str(it.get("url") or "").strip()
    )
    return report


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_FILE
    if not path.is_file():
        print(f"[migrate] file={path}\n[migrate] ERROR: file not found")
        return 1
    items = json.loads(path.read_text(encoding="utf-8"))
    rep = migrate(items, path)
    print(f"[migrate] file={path}")
    print(f"[migrate] total={rep['total']} with_url_before={rep['with_url_before']}")
    print(f"[migrate] skipped_has_url={rep['skipped_has_url']} changed={rep['changed']} "
          f"unchanged={rep['unchanged']} wrote={rep['wrote']}")
    print(f"[migrate] with_url_after={rep['with_url_after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

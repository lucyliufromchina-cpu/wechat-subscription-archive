#!/usr/bin/env python3
"""抓订阅公众号 — Step 3: 生成 INDEX.md。

读取 ~/wechat-archive/<公众号>/ 下所有 metadata.json，按发布时间倒序，
生成一个 INDEX.md：每条 = 标题（链到 article.md）+ 日期 + 摘要。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_archive_dir  # noqa: E402


def collect_metadata(account_dir: Path):
    metas = []
    for meta_path in account_dir.glob("*/metadata.json"):
        try:
            m = json.loads(meta_path.read_text(encoding="utf-8"))
            m["_dir"] = meta_path.parent.name
            metas.append(m)
        except Exception as e:
            print(f"  [skip] {meta_path}: {e}")
    metas.sort(key=lambda x: x.get("create_time", 0), reverse=True)
    return metas


def build(account_dir: Path):
    metas = collect_metadata(account_dir)
    if not metas:
        print(f"[!] {account_dir} 下没有 metadata.json")
        return

    nickname = account_dir.name
    lines = [
        f"# {nickname} 文章索引",
        "",
        f"共收录 {len(metas)} 篇文章，按发布时间倒序。最近更新：{datetime.now():%Y-%m-%d %H:%M}",
        "",
        "---",
        "",
    ]

    # 按月份分组
    by_month = {}
    for m in metas:
        ts = m.get("create_time", 0)
        ym = datetime.fromtimestamp(ts).strftime("%Y-%m") if ts else "未知"
        by_month.setdefault(ym, []).append(m)

    for ym in sorted(by_month.keys(), reverse=True):
        lines.append(f"## {ym}")
        lines.append("")
        for m in by_month[ym]:
            title = m.get("title", "无标题")
            d = m.get("publish_date", "")
            digest = m.get("digest", "").strip()
            dir_name = m["_dir"]
            link = f"./{dir_name}/article.md"
            lines.append(f"### [{title}]({link})")
            lines.append("")
            meta_bits = [d]
            if m.get("image_count"):
                meta_bits.append(f"{m['image_count']} 张图")
            lines.append(f"*{' · '.join(meta_bits)}*")
            lines.append("")
            if digest:
                d_short = digest if len(digest) <= 100 else digest[:100] + "..."
                lines.append(f"> {d_short}")
                lines.append("")
        lines.append("")

    out = account_dir / "INDEX.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 写入 {out} ({len(metas)} 篇，{len(by_month)} 个月份)")


def main():
    ap = argparse.ArgumentParser(description="抓订阅公众号 — 生成 INDEX.md")
    ap.add_argument("nickname", nargs="?", help="公众号名（不传则给所有公众号都生成）")
    args = ap.parse_args()

    base = get_archive_dir()
    if args.nickname:
        build(base / args.nickname)
    else:
        for d in base.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                build(d)


if __name__ == "__main__":
    main()

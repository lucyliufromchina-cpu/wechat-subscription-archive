#!/usr/bin/env python3
"""抓订阅公众号 — Step 2: 逐篇下载全文 HTML，转 Markdown，下载图片到本地。

读取 ~/wechat-archive/<公众号>/_list.json，对每篇文章：
    1. 已存在目录则跳过（增量友好）
    2. requests.get(link) 拉公开 HTML
    3. BeautifulSoup 解析 #js_content
    4. 下载所有 img[data-src] 到 images/，替换为本地相对路径
    5. markdownify 转 Markdown
    6. 写 article.md + metadata.json

依赖：requests、beautifulsoup4、markdownify、lxml
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

sys.path.insert(0, str(Path(__file__).parent))
from config import get_archive_dir  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Referer": "https://mp.weixin.qq.com/"}

INVALID_FN_CHARS = re.compile(r'[\\/:*?"<>|\n\r\t]')


def safe_filename(s: str, maxlen: int = 60) -> str:
    s = INVALID_FN_CHARS.sub("_", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:maxlen] if len(s) > maxlen else s


def fetch_html(url: str, retries: int = 3) -> str:
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            print(f"    [HTTP {r.status_code}] retry {i+1}/{retries}")
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = str(e)
            print(f"    [err] {e} retry {i+1}/{retries}")
        time.sleep(2 + i * 2)
    raise RuntimeError(f"fetch failed: {last}")


def img_ext_from_url(url: str, fallback: str = "jpg") -> str:
    """从 mmbiz 图片 URL 推断扩展名。
    URL 形如 .../mmbiz_jpg/xxx/0?wx_fmt=jpeg，要从路径或 query 推断。
    """
    m = re.search(r"wx_fmt=([a-z]+)", url, re.I)
    if m:
        return m.group(1).lower().replace("jpeg", "jpg")
    m = re.search(r"mmbiz_(jpg|png|gif|webp|jpeg)", url, re.I)
    if m:
        return m.group(1).lower().replace("jpeg", "jpg")
    path_ext = Path(urlparse(url).path).suffix.lstrip(".").lower()
    if path_ext in {"jpg", "jpeg", "png", "gif", "webp"}:
        return path_ext.replace("jpeg", "jpg")
    return fallback


def download_image(url: str, save_path: Path) -> bool:
    if save_path.exists():
        return True
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, stream=True)
        if r.status_code != 200:
            print(f"      [img HTTP {r.status_code}] {url[:80]}")
            return False
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with save_path.open("wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"      [img err] {e} {url[:80]}")
        return False


def process_article(article: dict, account_dir: Path) -> str:
    """处理单篇文章，返回状态字符串：'skip' / 'ok' / 'fail:reason'"""
    title = article.get("title", "无标题")
    link = article.get("link", "")
    create_ts = article.get("create_time", 0)
    if not link:
        return "fail:no-link"

    date_str = datetime.fromtimestamp(create_ts).strftime("%Y-%m-%d") if create_ts else "no-date"
    dir_name = f"{date_str}_{safe_filename(title)}"
    article_dir = account_dir / dir_name
    md_path = article_dir / "article.md"

    if md_path.exists():
        return "skip"

    article_dir.mkdir(parents=True, exist_ok=True)

    # 1. 拉 HTML
    try:
        html = fetch_html(link)
    except Exception as e:
        return f"fail:fetch:{e}"

    soup = BeautifulSoup(html, "lxml")

    # 2. 检查文章是否被删除/违规
    content = soup.select_one("#js_content")
    if not content:
        # 可能是被屏蔽的文章页
        warn = soup.select_one(".weui-msg__title, #js_share_content_page_hd")
        reason = warn.get_text(strip=True) if warn else "无 #js_content"
        return f"fail:no-content:{reason}"

    # 3. 处理图片：找 img[data-src]（懒加载）和 img[src]
    img_dir = article_dir / "images"
    seq = 0
    for img in content.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = "https:" + src
        seq += 1
        ext = img_ext_from_url(src)
        local_name = f"{seq:02d}.{ext}"
        local_path = img_dir / local_name
        ok = download_image(src, local_path)
        if ok:
            # 替换为本地相对路径，去掉 data-src，标准化 src
            img["src"] = f"images/{local_name}"
            for attr in ("data-src", "data-w", "data-ratio", "data-type", "data-s"):
                if attr in img.attrs:
                    del img[attr]
        else:
            # 下载失败保留原链接
            img["src"] = src

    # 4. 转 Markdown
    md_text = md(str(content), heading_style="ATX", strip=["script", "style"])
    # 清理过多空行
    md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip()

    # 5. 提取作者 / 发布时间（页面里的）
    author_el = soup.select_one("#js_name") or soup.select_one(".rich_media_meta_nickname")
    author = author_el.get_text(strip=True) if author_el else ""
    # #publish_time 在公众号文章页是 JS 渲染的，requests 拉到时通常为空。
    # 优先用元素文本，如果空则 fallback 到 list 里的 create_time 解出的 date_str。
    pub_el = soup.select_one("#publish_time")
    publish_text = (pub_el.get_text(strip=True) if pub_el else "") or date_str

    # 6. 写 article.md（带 frontmatter）
    front = (
        "---\n"
        f"title: {title!r}\n"
        f"author: {author!r}\n"
        f"publish: {publish_text!r}\n"
        f"create_time: {create_ts}\n"
        f"link: {link}\n"
        "---\n\n"
        f"# {title}\n\n"
    )
    md_path.write_text(front + md_text, encoding="utf-8")

    # 7. 写 metadata.json
    meta = {
        "title": title,
        "link": link,
        "digest": article.get("digest", ""),
        "cover": article.get("cover", ""),
        "create_time": create_ts,
        "publish_date": date_str,
        "author": author,
        "image_count": seq,
        "dir": dir_name,
    }
    (article_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return "ok"


def main():
    ap = argparse.ArgumentParser(description="抓订阅公众号 — 下载全文")
    ap.add_argument("nickname", help="目标公众号名（必须先跑过 scrape.py 生成 _list.json）")
    ap.add_argument("--delay", type=float, default=2.0, help="文章间隔秒数")
    ap.add_argument("--max", type=int, default=0, help="只下载前 N 篇（0 = 全量）")
    args = ap.parse_args()

    account_dir = get_archive_dir() / args.nickname
    list_path = account_dir / "_list.json"
    if not list_path.exists():
        print(f"ERROR: 找不到 {list_path}，请先运行 scrape.py 抓列表。")
        sys.exit(1)

    data = json.loads(list_path.read_text(encoding="utf-8"))
    articles = data.get("articles", [])
    if args.max > 0:
        articles = articles[: args.max]
    total = len(articles)
    print(f"=== 下载 [{args.nickname}] 全文，共 {total} 篇 ===\n")

    stats = {"ok": 0, "skip": 0, "fail": 0}
    for i, a in enumerate(articles, 1):
        title_short = (a.get("title", "")[:36] + "...") if len(a.get("title", "")) > 36 else a.get("title", "")
        print(f"[{i}/{total}] {title_short}")
        try:
            status = process_article(a, account_dir)
        except Exception as e:
            status = f"fail:exc:{e}"

        if status == "ok":
            stats["ok"] += 1
            print(f"    -> OK")
        elif status == "skip":
            stats["skip"] += 1
            print(f"    -> skip (已存在)")
        else:
            stats["fail"] += 1
            print(f"    -> {status}")

        if i < total and status != "skip":
            time.sleep(args.delay)

    print(f"\n=== 完成: ok={stats['ok']}, skip={stats['skip']}, fail={stats['fail']} ===")
    print(f"     下一步: python3 scripts/build_index.py \"{args.nickname}\"")


if __name__ == "__main__":
    main()

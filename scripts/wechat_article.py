"""公众号文章：解析（parse_article）、下载节奏（Fetcher）、去重库与落盘（Store / save_article）。

被 wechat_links.py（直存 / 合集 / 顺藤摸瓜 / 外链）和 wechat_sniff.py 复用。

    python3 scripts/wechat_article.py stats
    python3 scripts/wechat_article.py import-local --src <旧归档目录> --account <公众号名>

公众号清单：~/.config/抓订阅公众号/accounts.json（name + __biz）
数据落地：<归档目录>/<公众号>/<日期>_<标题>/{article.md, metadata.json, source.html, images/}
去重库：<归档目录>/wechat.sqlite（按 __biz+mid+idx）

边界：程序直连文章页间隔 ≥ 8 秒；遇微信"环境异常"验证页立即停止，**不做任何绕过**。
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ACCOUNTS_PATH, get_archive_dir  # noqa: E402

RAW_DIR = str(get_archive_dir())        # 归档目录（默认 ~/wechat-archive，可在 ~/.config/抓订阅公众号/config.json 改）
ROOT = RAW_DIR
DB_PATH = os.path.join(RAW_DIR, "wechat.sqlite")
INDEX_CSV = os.path.join(RAW_DIR, "INDEX.csv")
KEYWORDS_PATH = str(ACCOUNTS_PATH)      # 公众号清单：名字 + __biz
LOG_DIR = os.path.join(RAW_DIR, "_logs")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
INTERVALS = {"mp.weixin.qq.com": (8, 4), "mmbiz.qpic.cn": (0.5, 0.5)}  # 基础间隔秒 + 随机抖动

log = logging.getLogger("wechat_article")


class WechatVerify(Exception):
    """微信对本机请求弹出"环境异常，完成验证后继续访问"页面：停止，不做任何绕过。"""


# ---------------------------------------------------------------- 解析


def is_wechat_verify(html):
    return "secitptpage/verify" in html


def _var(html, name, pattern=r'"([^"]+)"'):
    for m in re.finditer(r"var %s\s*=\s*%s" % (name, pattern), html):
        if m.group(1):
            return m.group(1)
    return ""


IMG_EXT = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif", "webp": "webp"}


class _ContentParser(HTMLParser):
    """把 div#js_content 转成简单 Markdown：段落换行、图片按顺序编号。"""

    BLOCK = {"p", "div", "section", "br", "li", "h1", "h2", "h3", "h4", "tr", "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.inside = False
        self.skip = 0
        self.md, self.txt, self.images = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if not self.inside:
            if tag == "div" and a.get("id") == "js_content":
                self.inside, self.depth = True, 1
            return
        if tag == "div":
            self.depth += 1
        if tag in ("script", "style"):
            self.skip += 1
        if tag in self.BLOCK:
            self.md.append("\n")
            self.txt.append("\n")
        if tag == "img":
            src = a.get("data-src") or a.get("src") or ""
            if src.startswith("http"):
                self.images.append(src)
                fmt = urllib.parse.parse_qs(urllib.parse.urlparse(src).query).get("wx_fmt", ["jpg"])[0]
                self.md.append("\n![](images/%02d.%s)\n" % (len(self.images), IMG_EXT.get(fmt, "jpg")))

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if tag in ("script", "style"):
            self.skip -= 1
        if tag in self.BLOCK:
            self.md.append("\n")
            self.txt.append("\n")
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.inside = False

    def handle_data(self, data):
        if self.inside and not self.skip:
            self.md.append(data)
            self.txt.append(data)


def _tidy(chunks):
    lines = [l.strip() for l in "".join(chunks).split("\n")]
    out, blank = [], False
    for l in lines:
        if l:
            out.append(l)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def parse_article(html):
    biz, mid, idx, sn = _var(html, "biz"), _var(html, "mid"), _var(html, "idx"), _var(html, "sn")
    title_m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    nick = _var(html, "nickname", r'htmlDecode\("([^"]*)"\)') or _var(html, "nickname")
    ts = _var(html, "ct", r'"(\d{10})"')
    p = _ContentParser()
    p.feed(html)
    perm = "https://mp.weixin.qq.com/s?__biz=%s&mid=%s&idx=%s&sn=%s" % (biz, mid, idx, sn) if biz and mid and sn else ""  # 缺 sn 的页面拼不出可用的永久链接（会"参数错误"），宁可留空
    return {
        "title": unescape(title_m.group(1)) if title_m else "",
        "nickname": unescape(nick),
        "biz": biz, "mid": mid, "idx": idx, "sn": sn,
        "key": "%s_%s_%s" % (biz, mid, idx) if biz and mid else "",
        "perm_url": perm,
        "ts": int(ts) if ts else 0,
        "markdown": _tidy(p.md),
        "text": _tidy(p.txt),
        "images": p.images,
    }


def safe_name(s, maxlen=60):
    s = re.sub(r'[/\\:*?"<>|\n\r\t]', "_", s).strip()
    return s[:maxlen]


# ---------------------------------------------------------------- 网络


class Fetcher:
    def __init__(self):
        os.makedirs(RAW_DIR, exist_ok=True)
        self.opener = urllib.request.build_opener()
        self.last = {}

    def get(self, url, referer=None, binary=False):
        url = urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*[]~")
        host = urllib.parse.urlparse(url).netloc
        base, jitter = next((v for k, v in INTERVALS.items() if host.endswith(k)), (1, 0))
        wait = base + random.uniform(0, jitter) - (time.time() - self.last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self.last[host] = time.time()
        headers = {"User-Agent": UA}
        if referer:
            headers["Referer"] = referer
        with self.opener.open(urllib.request.Request(url, headers=headers), timeout=30) as resp:
            final_url, data = resp.geturl(), resp.read()
        if binary:
            return data
        text = data.decode("utf-8", errors="ignore")
        if host.endswith("mp.weixin.qq.com") and is_wechat_verify(text):
            raise WechatVerify(final_url)
        return text


# ---------------------------------------------------------------- 存储


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (key TEXT PRIMARY KEY, account TEXT, title TEXT, ts INTEGER,
    perm_url TEXT, dir TEXT, source TEXT, keyword TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS seen (account TEXT, title TEXT, ts INTEGER, key TEXT,
    PRIMARY KEY (account, title, ts));
"""


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self):
        os.makedirs(RAW_DIR, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH)
        self.db.executescript(SCHEMA)

    def one(self, sql, args=()):
        return self.db.execute(sql, args).fetchone()

    def exec(self, sql, args=()):
        self.db.execute(sql, args)
        self.db.commit()

    def has_article(self, key):
        return self.one("SELECT 1 FROM articles WHERE key=?", (key,)) is not None

    def seen(self, account, title, ts):
        return self.one("SELECT 1 FROM seen WHERE account=? AND title=? AND ts=?", (account, title, ts)) is not None


def save_article(account, art, raw_html, fetcher, images=True, source="link"):
    date = datetime.fromtimestamp(art["ts"]).strftime("%Y-%m-%d") if art["ts"] else "unknown"
    d = os.path.join(RAW_DIR, account, "%s_%s" % (date, safe_name(art["title"] or art["key"])))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "source.html"), "w", encoding="utf-8") as f:
        f.write(raw_html)
    img_records = []
    if images and art["images"]:
        os.makedirs(os.path.join(d, "images"), exist_ok=True)
        for i, url in enumerate(art["images"], 1):
            fmt = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("wx_fmt", ["jpg"])[0]
            fname = "%02d.%s" % (i, IMG_EXT.get(fmt, "jpg"))
            try:
                data = fetcher.get(url, referer="https://mp.weixin.qq.com/", binary=True)
                with open(os.path.join(d, "images", fname), "wb") as f:
                    f.write(data)
                img_records.append({"file": "images/" + fname, "url": url})
            except Exception as e:  # noqa: BLE001 — 单张图片失败不影响正文
                img_records.append({"file": "images/" + fname, "url": url, "error": str(e)[:200]})
    meta = {k: art[k] for k in ("title", "nickname", "biz", "mid", "idx", "sn", "key", "perm_url", "ts")}
    meta.update({"account": account, "publish_date": date, "fetched_at": now_iso(),
                 "source": source, "images": img_records,
                 "text_sha256": hashlib.sha256(art["text"].encode("utf-8")).hexdigest()})
    with open(os.path.join(d, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    header = "---\ntitle: '%s'\nauthor: '%s'\npublish: '%s'\nlink: %s\n---\n\n# %s\n\n" % (
        art["title"].replace("'", "’"), art["nickname"], date, art["perm_url"], art["title"])
    with open(os.path.join(d, "article.md"), "w", encoding="utf-8") as f:
        f.write(header + art["markdown"] + "\n")
    return d


# ---------------------------------------------------------------- 主流程


def load_config():
    if not os.path.exists(KEYWORDS_PATH):
        sys.exit("缺少公众号清单 %s：参照 scripts/accounts.example.json 创建" % KEYWORDS_PATH)
    with open(KEYWORDS_PATH, encoding="utf-8") as f:
        return json.load(f)


def import_local(src, account):
    """把旧版 mp-data 抓取的归档（article.md + metadata.json + images/）导入，并登记去重键。"""
    store = Store()
    src = os.path.expanduser(src)
    n = 0
    for name in sorted(os.listdir(src)):
        meta_path = os.path.join(src, name, "metadata.json")
        if not os.path.exists(meta_path):
            continue
        with open(meta_path, encoding="utf-8") as f:
            m = json.load(f)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(m.get("link", "")).query)
        biz, mid, idx, sn = (q.get(k, [""])[0] for k in ("__biz", "mid", "idx", "sn"))
        key = "%s_%s_%s" % (biz, mid, idx)
        if mid:  # 登记"标题 + 发布时间"作为第二重去重键
            store.exec("INSERT OR IGNORE INTO seen VALUES (?,?,?,?)",
                       (account, m.get("title", ""), int(m.get("create_time") or 0), key))
        if not mid or store.has_article(key):
            continue
        dest = os.path.join(RAW_DIR, account, name)
        if not os.path.exists(dest):
            shutil.copytree(os.path.join(src, name), dest)
        perm = "https://mp.weixin.qq.com/s?__biz=%s&mid=%s&idx=%s&sn=%s" % (biz, mid, idx, sn)
        store.exec("INSERT OR REPLACE INTO articles VALUES (?,?,?,?,?,?,?,?,?)",
                   (key, account, m.get("title", ""), int(m.get("create_time") or 0), perm,
                    os.path.relpath(dest, RAW_DIR), "import:" + src, "", now_iso()))
        n += 1
    write_index(store)
    print("导入 %d 篇到 %s" % (n, os.path.join(RAW_DIR, account)))


def write_index(store):
    rows = store.db.execute("SELECT account, ts, title, perm_url, dir, source, keyword FROM articles "
                            "ORDER BY account, ts DESC").fetchall()
    with open(INDEX_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["公众号", "发布日期", "标题", "永久链接", "本地目录", "来源", "命中关键词"])
        for acc, ts, title, url, d, src, kw in rows:
            w.writerow([acc, datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "", title, url, d, src, kw])


def print_stats():
    store = Store()
    for acc, n, lo, hi in store.db.execute(
            "SELECT account, count(*), min(ts), max(ts) FROM articles GROUP BY account"):
        fmt = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else "?"
        print("%s：%d 篇（%s ～ %s）" % (acc, n, fmt(lo), fmt(hi)))


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout),
              logging.FileHandler(os.path.join(LOG_DIR, "wechat_article.log"), encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


def main(argv=None):
    ap = argparse.ArgumentParser(description="公众号文章归档：统计 / 导入旧归档")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("import-local")
    i.add_argument("--src", required=True)
    i.add_argument("--account", required=True)
    sub.add_parser("stats")
    args = ap.parse_args(argv)
    if args.cmd == "stats":
        print_stats()
    else:
        import_local(args.src, args.account)


if __name__ == "__main__":
    main()

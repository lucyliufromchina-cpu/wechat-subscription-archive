#!/usr/bin/env python3
"""抓订阅公众号（2026-08 起的新方法）：搜狗微信搜索「公众号名 + 关键词」→ 还原文章链接 → 下载全文 + 图片。

为什么换方法：微信于 2026-07-30 关闭了公众号后台「超链接 → 引用其他公众号文章」背后的接口
（appmsg list_ex 恒返回 200013 freq control），scrape.py 的老方法失效；
微信读书网页端的文章列表接口也已废弃（-2041），只能拿到最新一篇。

关键经验：只搜公众号名，命中率约 1%（搜狗搜的是正文提及）；「名字 + 主题词」命中率接近 100%。
每个查询最多 10 页（约 100 条），所以要用一组关键词覆盖。

用法：
    python3 sogou_collect.py "菜花来了" --keywords 税,股权,社保
    python3 sogou_collect.py "菜花来了" --keywords 税 --max-requests 100 --no-images

输出与老方法一致：~/wechat-archive/<公众号>/<日期>_<标题>/{article.md, metadata.json, images/}
断点状态：~/wechat-archive/<公众号>/_sogou_state.sqlite（已完成的页、已下载的文章不会重复请求）
只用 Python 标准库。

礼貌与边界：搜狗请求间隔 25–35 秒；遇到验证码就暂停（默认 2 小时）后重试，**不做任何验证码绕过**。
"""

import argparse
import http.cookiejar
import json
import random
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import CONFIG_DIR, get_archive_dir  # noqa: E402

SOGOU = "https://weixin.sogou.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_PAGES = 10
INTERVALS = {"weixin.sogou.com": (25, 10), "mp.weixin.qq.com": (3, 2), "mmbiz.qpic.cn": (0.5, 0.5)}
IMG_EXT = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif", "webp": "webp"}


class Captcha(Exception):
    pass


# ---------------------------------------------------------------- 解析


def _clean(s):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


_ROW_RE = re.compile(
    r'<h3>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>.*?'
    r'<span class="all-time-y2">(.*?)</span>.*?timeConvert\(\'(\d+)\'\)', re.S)


def parse_search(html):
    return [{"link": unescape(link), "title": _clean(title), "account": _clean(acc), "ts": int(ts)}
            for link, title, acc, ts in _ROW_RE.findall(html)]


def is_captcha(html):
    return "antispider" in html or "请输入验证码" in html or "seccodeImage" in html


def resolve_sogou_redirect(page):
    parts = re.findall(r"url \+= '([^']*)'", page)
    return "".join(parts).replace("@", "") if parts else None


def _var(html, name, pattern=r'"([^"]+)"'):
    for m in re.finditer(r"var %s\s*=\s*%s" % (name, pattern), html):
        if m.group(1):
            return m.group(1)
    return ""


class _ContentParser(HTMLParser):
    BLOCK = {"p", "div", "section", "br", "li", "h1", "h2", "h3", "h4", "tr", "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.inside, self.skip = 0, False, 0
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
    out, blank = [], False
    for line in (l.strip() for l in "".join(chunks).split("\n")):
        if line:
            out.append(line)
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
    return {
        "title": unescape(title_m.group(1)) if title_m else "",
        "nickname": unescape(nick),
        "biz": biz, "mid": mid, "idx": idx, "sn": sn,
        "key": "%s_%s_%s" % (biz, mid, idx) if biz and mid else "",
        "perm_url": "https://mp.weixin.qq.com/s?__biz=%s&mid=%s&idx=%s&sn=%s" % (biz, mid, idx, sn)
        if biz and mid else "",
        "ts": int(ts) if ts else 0,
        "markdown": _tidy(p.md),
        "text": _tidy(p.txt),
        "images": p.images,
    }


def safe_name(s, maxlen=60):
    return re.sub(r'[/\\:*?"<>|\n\r\t]', "_", s).strip()[:maxlen]


# ---------------------------------------------------------------- 网络


class Fetcher:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.cookie_path = CONFIG_DIR / "sogou_cookies.txt"
        self.jar = http.cookiejar.MozillaCookieJar(str(self.cookie_path))
        if self.cookie_path.exists():
            try:
                self.jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.last, self.sogou_requests = {}, 0

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
        if host.endswith("weixin.sogou.com"):
            self.sogou_requests += 1
            self.jar.save(ignore_discard=True, ignore_expires=True)
        if binary:
            return data
        text = data.decode("utf-8", errors="ignore")
        if "antispider" in final_url or (host.endswith("sogou.com") and is_captcha(text)):
            raise Captcha(final_url)
        return text


# ---------------------------------------------------------------- 状态与落地


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (key TEXT PRIMARY KEY, title TEXT, ts INTEGER, dir TEXT, keyword TEXT);
CREATE TABLE IF NOT EXISTS seen (title TEXT, ts INTEGER, PRIMARY KEY (title, ts));
CREATE TABLE IF NOT EXISTS pages (keyword TEXT, page INTEGER, rows INTEGER, hits INTEGER, PRIMARY KEY (keyword, page));
CREATE TABLE IF NOT EXISTS keywords_done (keyword TEXT PRIMARY KEY);
"""


class State:
    def __init__(self, account_dir):
        account_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(account_dir / "_sogou_state.sqlite"))
        self.db.executescript(SCHEMA)
        self._register_existing(account_dir)

    def _register_existing(self, account_dir):
        """登记老方法已抓的文章（metadata.json 里的 link 含 __biz/mid/idx），避免重复下载。"""
        for meta_path in account_dir.glob("*/metadata.json"):
            try:
                m = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            q = urllib.parse.parse_qs(urllib.parse.urlparse(m.get("link", "")).query)
            biz, mid, idx = (q.get(k, [""])[0] for k in ("__biz", "mid", "idx"))
            key = m.get("key") or ("%s_%s_%s" % (biz, mid, idx) if mid else "")
            if key:
                self.exec("INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?)",
                          (key, m.get("title", ""), int(m.get("create_time") or 0), meta_path.parent.name, ""))
                self.exec("INSERT OR IGNORE INTO seen VALUES (?,?)", (m.get("title", ""), int(m.get("create_time") or 0)))

    def exec(self, sql, args=()):
        self.db.execute(sql, args)
        self.db.commit()

    def has(self, sql, args):
        return self.db.execute(sql, args).fetchone() is not None


def save_article(account_dir, art, fetcher, keyword, images):
    date = datetime.fromtimestamp(art["ts"]).strftime("%Y-%m-%d") if art["ts"] else "unknown"
    dir_name = "%s_%s" % (date, safe_name(art["title"] or art["key"]))
    d = account_dir / dir_name
    d.mkdir(parents=True, exist_ok=True)
    n_img = 0
    if images and art["images"]:
        (d / "images").mkdir(exist_ok=True)
        for i, url in enumerate(art["images"], 1):
            fmt = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("wx_fmt", ["jpg"])[0]
            try:
                (d / "images" / ("%02d.%s" % (i, IMG_EXT.get(fmt, "jpg")))).write_bytes(
                    fetcher.get(url, referer="https://mp.weixin.qq.com/", binary=True))
                n_img += 1
            except Exception as e:
                print("  [!] 图片 %d 下载失败：%s" % (i, e))
    meta = {  # 字段与 download_full.py 一致，build_index.py 可直接使用
        "title": art["title"], "link": art["perm_url"], "digest": art["text"][:120], "cover": "",
        "create_time": art["ts"], "publish_date": date, "author": art["nickname"],
        "image_count": n_img, "dir": dir_name,
        "biz": art["biz"], "mid": art["mid"], "idx": art["idx"], "sn": art["sn"], "key": art["key"],
        "source": "sogou", "keyword": keyword, "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    (d / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    header = "---\ntitle: '%s'\nauthor: '%s'\npublish: '%s'\ncreate_time: %s\nlink: %s\n---\n\n# %s\n\n" % (
        art["title"].replace("'", "’"), art["nickname"], date, art["ts"], art["perm_url"], art["title"])
    (d / "article.md").write_text(header + art["markdown"] + "\n", encoding="utf-8")
    return dir_name


def collect_keyword(fetcher, state, account_dir, name, biz, kw, images, budget):
    query = ("%s %s" % (name, kw)).strip()
    for page in range(1, MAX_PAGES + 1):
        if state.has("SELECT 1 FROM pages WHERE keyword=? AND page=?", (kw, page)):
            continue
        if fetcher.sogou_requests >= budget:
            return False
        search_url = "%s/weixin?type=2&page=%d&query=%s" % (SOGOU, page, urllib.parse.quote(query))
        rows = parse_search(fetcher.get(search_url, referer=SOGOU + "/"))
        if not rows:
            print("[i] 「%s」第 %d 页无结果，该关键词完成" % (query, page))
            break
        mine = [r for r in rows if r["account"] == name]
        hits = 0
        for row in mine:
            if state.has("SELECT 1 FROM seen WHERE title=? AND ts=?", (row["title"], row["ts"])):
                continue
            url = resolve_sogou_redirect(fetcher.get(SOGOU + row["link"], referer=search_url))
            if not url:
                print("  [!] 跳转链接解析失败：%s" % row["title"])
                continue
            art = parse_article(fetcher.get(url))
            if art["nickname"] != name and (not biz or art["biz"] != biz):
                print("  [!] 作者不符（%s），跳过：%s" % (art["nickname"], row["title"]))
                continue
            if art["key"] and not state.has("SELECT 1 FROM articles WHERE key=?", (art["key"],)):
                d = save_article(account_dir, art, fetcher, kw, images)
                state.exec("INSERT OR REPLACE INTO articles VALUES (?,?,?,?,?)", (art["key"], art["title"], art["ts"], d, kw))
                hits += 1
                print("  + %s %s" % (datetime.fromtimestamp(art["ts"]).date() if art["ts"] else "?", art["title"]))
            state.exec("INSERT OR IGNORE INTO seen VALUES (?,?)", (row["title"], row["ts"]))
        state.exec("INSERT OR REPLACE INTO pages VALUES (?,?,?,?)", (kw, page, len(rows), hits))
        print("[i] 「%s」第 %d 页：%d 条结果，其中本号 %d 条，新增 %d 篇" % (query, page, len(rows), len(mine), hits))
    state.exec("INSERT OR REPLACE INTO keywords_done VALUES (?)", (kw,))
    return True


def main():
    ap = argparse.ArgumentParser(description="抓订阅公众号（搜狗微信搜索：公众号名 + 关键词）")
    ap.add_argument("name", help="目标公众号名称（须与文章署名完全一致）")
    ap.add_argument("--keywords", required=True, help="逗号分隔的主题关键词，如 税,股权,社保（越靠前越优先）")
    ap.add_argument("--biz", help="公众号 __biz（可选，作者校验的备用依据）")
    ap.add_argument("--max-requests", type=int, default=300, help="本次运行最多发多少次搜狗请求")
    ap.add_argument("--cooldown", type=int, default=7200, help="遇到验证码后暂停秒数")
    ap.add_argument("--max-cooldowns", type=int, default=3)
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    account_dir = get_archive_dir() / args.name
    state, fetcher = State(account_dir), Fetcher()
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    cooldowns = 0
    for kw in keywords:
        if state.has("SELECT 1 FROM keywords_done WHERE keyword=?", (kw,)):
            continue
        while True:
            try:
                finished = collect_keyword(fetcher, state, account_dir, args.name, args.biz, kw,
                                           not args.no_images, args.max_requests)
                break
            except Captcha:
                cooldowns += 1
                if cooldowns > args.max_cooldowns:
                    print("[X] 验证码已出现 %d 次，本次结束；稍后重跑同一命令会从断点继续。" % cooldowns)
                    return
                print("[!] 遇到搜狗验证码，暂停 %d 分钟后重试（第 %d/%d 次）" % (args.cooldown // 60, cooldowns,
                                                                    args.max_cooldowns))
                time.sleep(args.cooldown)
        if not finished:
            print("[i] 达到本次请求上限 %d，结束；重跑同一命令会从断点继续。" % args.max_requests)
            return
    n = state.db.execute("SELECT count(*) FROM articles").fetchone()[0]
    print("[OK] 关键词全部完成，%s 目录现有 %d 篇。可运行 build_index.py 生成索引。" % (args.name, n))


if __name__ == "__main__":
    main()

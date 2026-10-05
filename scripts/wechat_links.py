"""按链接下载公众号文章：剪贴板监听 / 链接文件批量。

单篇公众号文章页是公开的，按链接直接下载。
最省事的用法：在电脑版微信里逐篇右键「复制链接」，本工具自动发现并下载。

    python3 scripts/wechat_links.py watch            # 监听剪贴板（Ctrl+C 结束）
    python3 scripts/wechat_links.py file links.txt   # 批量下载文件里的链接（一行一个或混在文字里都行）

文章按署名自动归到 <归档目录>/<公众号>/，共用去重库（按 __biz+mid+idx），重复的跳过。
复制自微信客户端的链接带 sn，能生成可打开的永久链接。
"""

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from html import unescape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_sniff as sn  # noqa: E402
import wechat_article as ws  # noqa: E402

LINK_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s(?:/[A-Za-z0-9_-]+|\?[^\s\"'<>#]+)")
LINKS_LOG = os.path.join(ws.RAW_DIR, "_copied_links.txt")


def _unescape_all(s):
    """网页里的链接有时被转义多次（&amp;amp;），或写在脚本里（\\x26、\\/）：反复还原直到稳定。"""
    prev = None
    while s != prev:
        prev = s
        s = unescape(s.replace("\\x26", "&").replace("\\/", "/"))
    return s


def extract_links(text):
    out = []
    for m in LINK_RE.finditer(_unescape_all(text or "")):
        url = m.group(0).replace("http://", "https://", 1).rstrip("。，,.;；)）")
        if url not in out:
            out.append(url)
    return out


def link_key(url):
    """带 __biz/mid/idx/sn 的完整链接 → 去重键；短链或缺 sn（打不开）返回 None。"""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    biz, mid, idx, sn = (q.get(k, [""])[0] for k in ("__biz", "mid", "idx", "sn"))
    return "%s_%s_%s" % (biz, mid, idx) if biz and mid and idx and sn else None


def internal_links(html, bizset):
    """文章页里指向指定公众号其他文章的完整链接（往期推荐、系列文章、引用旧文等）。"""
    out = []
    for url in extract_links(html):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if q.get("__biz", [""])[0] in bizset and link_key(url) and url not in out:
            out.append(url)
    return out


def _save(art, raw, fetcher, store, images, source):
    d = ws.save_article(art["nickname"], art, raw, fetcher, images, source)
    store.exec("INSERT OR REPLACE INTO articles VALUES (?,?,?,?,?,?,?,?,?)",
               (art["key"], art["nickname"], art["title"], art["ts"], art["perm_url"],
                os.path.relpath(d, ws.RAW_DIR), source, "", ws.now_iso()))
    store.exec("INSERT OR IGNORE INTO seen VALUES (?,?,?,?)", (art["nickname"], art["title"], art["ts"], art["key"]))


def download(url, fetcher, store, images=True, source="link"):
    """下载一篇，返回 (状态, 说明, 页面 HTML)。"""
    raw = fetcher.get(url)
    art = ws.parse_article(raw)
    if not art["key"] or not art["nickname"]:
        return "fail", "页面里没有文章信息（可能已删除或链接失效）", raw
    if store.has_article(art["key"]):
        return "dup", "%s《%s》已存在" % (art["nickname"], art["title"]), raw
    _save(art, raw, fetcher, store, images, source)
    date = datetime.fromtimestamp(art["ts"]).date() if art["ts"] else "?"
    return "ok", "%s %s《%s》" % (art["nickname"], date, art["title"]), raw


def _ding(ok=True, msg=""):
    """提示音 + 系统通知（右上角弹窗显示标题），比单独的声音更不容易错过。"""
    sound = "/System/Library/Sounds/%s.aiff" % ("Glass" if ok else "Basso")
    if os.path.exists(sound):
        subprocess.Popen(["afplay", sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    title = "公众号采集：已保存" if ok else "公众号采集：失败"
    text = msg.replace('"', "'").replace("\\", "")[:120]
    subprocess.Popen(["osascript", "-e", 'display notification "%s" with title "%s"' % (text, title)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


PAUSE_SECONDS = 30 * 60


def _process(urls, fetcher, store, images, ding=False):
    """逐个下载；遇到微信验证页立即停止，返回未处理的链接（含当前这条），由调用方稍后重试。"""
    for n, url in enumerate(urls):
        with open(LINKS_LOG, "a", encoding="utf-8") as f:
            f.write(url + "\n")
        for attempt in (1, 2):  # 微信偶尔临时返回异常页，等几秒重试一次
            try:
                status, msg, _ = download(url, fetcher, store, images)
            except ws.WechatVerify:
                print("  ⏸ 微信要求验证（访问太频繁），暂停 %d 分钟，未下载的链接已排队" % (PAUSE_SECONDS // 60), flush=True)
                if ding:
                    _ding(False, "微信要求验证，暂停 %d 分钟；继续复制的链接会排队，稍后自动下载" % (PAUSE_SECONDS // 60))
                ws.write_index(store)
                return urls[n:]
            except Exception as e:  # noqa: BLE001 — 单篇失败不中断
                status, msg = "fail", "%s：%s" % (url[:80], e)
            if status != "fail" or attempt == 2:
                break
            time.sleep(8)
        if status == "fail":
            msg += "  " + url
        print({"ok": "  ✅ ", "dup": "  ↩︎ 已有 ", "fail": "  ❌ "}[status] + msg, flush=True)
        if ding:
            _ding(status != "fail", msg)
    ws.write_index(store)
    return []


def watch(images=True):
    fetcher, store = ws.Fetcher(), ws.Store()
    print("正在监听剪贴板：在微信里对文章右键「复制链接」即可，按 Ctrl+C 结束。", flush=True)
    last, done, pending, paused_until = None, set(), [], 0
    while True:
        try:
            text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
        except Exception:  # noqa: BLE001
            text = ""
        if text != last:
            last = text
            found = extract_links(text)
            new = [u for u in found if u not in done]
            if found and not new:  # 复制了本次已处理过的链接：也给个反馈，免得以为没反应
                print("  ↩︎ 这篇本次已处理过：%s" % found[0], flush=True)
                _ding(True, "这篇已经下载过了")
            done.update(new)
            pending.extend(new)
            if new and time.time() < paused_until:
                print("  ⏳ 暂停中，已排队：%s" % new[0], flush=True)
        if pending and time.time() >= paused_until:
            pending = _process(pending, fetcher, store, images, ding=True)
            if pending:
                paused_until = time.time() + PAUSE_SECONDS
        time.sleep(0.5)


def _drain_spool(fetcher, store, images):
    """把旁听落盘的正文页解析入库（不再下载正文，只取图片）；处理过的移到 done/，解析不出的移到 bad/。"""
    if not os.path.isdir(sn.SPOOL):
        return 0
    n = 0
    for name in sorted(os.listdir(sn.SPOOL)):
        f = os.path.join(sn.SPOOL, name)
        if not name.endswith(".html") or not os.path.isfile(f):
            continue
        with open(f, encoding="utf-8", errors="ignore") as fh:
            raw = fh.read()
        art = ws.parse_article(raw)
        dest = "done"
        if not art["key"] or not art["nickname"]:
            dest = "bad"
            print("  ❌ 落盘页解析不出文章信息：%s" % name, flush=True)
        elif store.has_article(art["key"]):
            print("  ↩︎ 已有 %s《%s》" % (art["nickname"], art["title"]), flush=True)
        else:
            try:
                _save(art, raw, fetcher, store, images, "sniff")
                date = datetime.fromtimestamp(art["ts"]).date() if art["ts"] else "?"
                print("  ✅ %s %s《%s》（客户端页面直存）" % (art["nickname"], date, art["title"]), flush=True)
                n += 1
            except Exception as e:  # noqa: BLE001 — 单篇失败不中断
                dest = "bad"
                print("  ❌ 入库失败 %s：%s" % (name, e), flush=True)
        os.makedirs(os.path.join(sn.SPOOL, dest), exist_ok=True)
        os.replace(f, os.path.join(sn.SPOOL, dest, name))
    if n:
        ws.write_index(store)
    return n


def sniffed(images=True):
    """消费旁听队列（scripts/wechat_sniff.py 写入）：新链接到了就下载，已有的直接跳过，遇微信验证暂停后续。"""
    fetcher, store = ws.Fetcher(), ws.Store()
    print("正在等待旁听队列 %s（另一个终端跑 mitmdump，微信里滚动公众号「全部消息」），按 Ctrl+C 结束。"
          % os.path.relpath(sn.QUEUE, ws.ROOT), flush=True)
    pos, pending, paused_until, skipped = 0, [], 0, 0
    while True:
        _drain_spool(fetcher, store, images)
        urls, pos = sn.read_queue(sn.QUEUE, pos)
        for u in urls:
            k = link_key(u)
            if not k:  # 旁听只入队带完整参数的链接；没键的是历史残留或模板占位符
                continue
            if store.has_article(k):
                skipped += 1
            elif u not in pending:
                pending.append(u)
        if urls:
            # 客户端打开的文章页已由旁听直接保存，所以通常"已有"= 新增；待下载只指需要程序联网补下的链接
            pending = [u for u in pending if not (link_key(u) and store.has_article(link_key(u)))]
            wait = ("，暂停中，预计 %s 重试" % datetime.fromtimestamp(paused_until).strftime("%H:%M")
                    if pending and time.time() < paused_until else "")
            print("  📥 新增 %d（已保存 %d）｜另有 %d 篇需程序联网下载%s" % (len(urls), skipped, len(pending), wait),
                  flush=True)
            skipped = 0
        if pending and time.time() >= paused_until:
            pending = [u for u in pending if not (link_key(u) and store.has_article(link_key(u)))]  # 正文页可能刚直存
            pending = _process(pending, fetcher, store, images)
            if pending:
                paused_until = time.time() + PAUSE_SECONDS
        time.sleep(2)


def from_file(path, images=True):
    with open(path, encoding="utf-8") as f:
        urls = extract_links(f.read())
    print("文件里找到 %d 个文章链接" % len(urls))
    left = _process(urls, ws.Fetcher(), ws.Store(), images)
    if left:
        print("有 %d 个链接因微信验证未下载，稍后重跑同一命令即可" % len(left))


def expand(max_new=300, images=True):
    """顺藤摸瓜：从已存文章里找同号其他文章的完整链接，下载后继续找，直到没有新链接或达到上限。
    只访问公开文章页，间隔沿用 mp.weixin.qq.com 的 3–5 秒。"""
    cfg = ws.load_config()
    bizset = {a["biz"] for a in cfg["accounts"] if a.get("biz")}
    names = [a["name"] for a in cfg["accounts"]]
    fetcher, store = ws.Fetcher(), ws.Store()
    queue, queued = [], set()

    def enqueue(html):
        for url in internal_links(html, bizset):
            key = link_key(url)
            if key not in queued and not store.has_article(key):
                queued.add(key)
                queue.append(url)

    for name in names:
        account_dir = os.path.join(ws.RAW_DIR, name)
        for sub in sorted(os.listdir(account_dir)) if os.path.isdir(account_dir) else []:
            src = os.path.join(account_dir, sub, "source.html")
            if os.path.exists(src):
                with open(src, encoding="utf-8", errors="ignore") as f:
                    enqueue(f.read())
    print("从已存文章中找到 %d 个待下载的同号链接" % len(queue), flush=True)
    new, verified = 0, False
    while queue and new < max_new:
        url = queue.pop(0)
        try:
            status, msg, raw = download(url, fetcher, store, images, source="expand")
        except ws.WechatVerify:
            print("⏸ 微信要求验证（访问太频繁），本轮停止；稍后重跑 expand 会从剩余链接继续", flush=True)
            verified = True
            break
        except Exception as e:  # noqa: BLE001
            status, msg, raw = "fail", "%s：%s" % (url[:80], e), ""
        print({"ok": "  ✅ ", "dup": "  ↩︎ 已有 ", "fail": "  ❌ "}[status] + msg, flush=True)
        if status == "ok":
            new += 1
            enqueue(raw)
        if new and new % 20 == 0:
            ws.write_index(store)
    ws.write_index(store)
    print("完成：新增 %d 篇，队列剩余 %d 个链接" % (new, len(queue)), flush=True)
    return "verify" if verified else "done"


def album_ids(html):
    """文章页里出现的合集 ID（"收录于合集"等处）。"""
    out = []
    for m in re.finditer(r'album_id[=":\\\s]+(\d{15,20})', _unescape_all(html or "")):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def parse_album(text):
    """公开合集列表接口（action=getalbum&f=json）→ (文章列表, 是否还有下一页, 合集名, 总篇数)。"""
    import json
    resp = json.loads(text).get("getalbum_resp", {})
    items, raw_list = [], resp.get("article_list") or []
    if isinstance(raw_list, dict):  # 合集只有 1 篇时接口返回单个对象
        raw_list = [raw_list]
    for a in raw_list:
        url = _unescape_all(a.get("url", "")).replace("http://", "https://", 1)
        items.append({"url": url, "title": a.get("title", ""), "create_time": int(a.get("create_time") or 0),
                      "msgid": a.get("msgid"), "itemidx": a.get("itemidx")})
    info = resp.get("base_info") or {}
    return items, str(resp.get("continue_flag")) == "1", info.get("title", ""), int(info.get("article_count") or 0)


def albums(max_new=500, images=True):
    """按合集下载：从已存文章里发现目标号的合集，逐页读取合集列表，下载缺的文章；遇微信验证即停。"""
    cfg = ws.load_config()
    fetcher, store = ws.Fetcher(), ws.Store()
    new = 0
    for acc in cfg["accounts"]:
        biz, account_dir = acc.get("biz"), os.path.join(ws.RAW_DIR, acc["name"])
        if not biz or not os.path.isdir(account_dir):
            continue
        found = []
        for sub in sorted(os.listdir(account_dir)):
            src = os.path.join(account_dir, sub, "source.html")
            if os.path.exists(src):
                with open(src, encoding="utf-8", errors="ignore") as f:
                    found += [i for i in album_ids(f.read()) if i not in found]
        print("[%s] 发现 %d 个合集" % (acc["name"], len(found)), flush=True)
        for aid in found:
            base = ("https://mp.weixin.qq.com/mp/appmsgalbum?__biz=%s&action=getalbum&album_id=%s&count=20&f=json"
                    % (biz, aid))
            url, listed, todo, title, total = base, [], [], "", 0
            try:
                while True:
                    items, more, t, n = parse_album(fetcher.get(url))
                    title, total = title or t, total or n
                    listed += items
                    if not more or not items:
                        break
                    last = items[-1]
                    url = base + "&begin_msgid=%s&begin_itemidx=%s" % (last["msgid"], last["itemidx"])
            except ws.WechatVerify:
                print("⏸ 微信要求验证（访问太频繁），本轮停止；稍后重跑 albums 会继续", flush=True)
                ws.write_index(store)
                return "verify"
            todo = [i["url"] for i in listed if link_key(i["url"]) and not store.has_article(link_key(i["url"]))]
            print("[%s] 合集《%s》共 %d 篇，列表读到 %d 篇，缺 %d 篇" % (acc["name"], title, total, len(listed), len(todo)),
                  flush=True)
            for u in todo:
                if new >= max_new:
                    break
                try:
                    status, msg, _ = download(u, fetcher, store, images, source="album")
                except ws.WechatVerify:
                    print("⏸ 微信要求验证（访问太频繁），本轮停止；稍后重跑 albums 会继续", flush=True)
                    ws.write_index(store)
                    return "verify"
                except Exception as e:  # noqa: BLE001
                    status, msg = "fail", "%s：%s" % (u[:80], e)
                print({"ok": "  ✅ ", "dup": "  ↩︎ 已有 ", "fail": "  ❌ "}[status] + msg, flush=True)
                new += status == "ok"
    ws.write_index(store)
    print("完成：按合集新增 %d 篇" % new, flush=True)
    return "done"


# ---------------------------------------------------------------- 外部链接（只追一层）

SKIP_HOSTS = ("res.wx.qq.com", "mmbiz.qpic.cn", "mmbiz.qlogo.cn", "wx.qlogo.cn", "mp.weixin.qq.com")
CONTENT_END_MARKERS = ('id="js_pc_qr_code"', 'id="js_tags"', "js_article_bottom_bar", 'id="content_bottom_area"')
EXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS externals (url TEXT PRIMARY KEY, from_key TEXT, kind TEXT, status TEXT, file TEXT,
    content_type TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS ext_done (key TEXT PRIMARY KEY, done_at TEXT);
"""
MAX_EXTERNAL_BYTES = 200 * 1024 * 1024


def _content_region(html):
    start = html.find('id="js_content"')
    if start < 0:
        return ""
    ends = [i for i in (html.find(m, start) for m in CONTENT_END_MARKERS) if i > 0]
    return html[start:min(ends) if ends else start + 400000]


def external_links(html, bizset):
    """正文与"阅读原文"里的外部链接 → (其他公众号文章, 网页 / 文件)。目标号自己的文章不算外部。"""
    html = _unescape_all(html)
    urls = []
    m = re.search(r"var msg_source_url\s*=\s*['\"]([^'\"]+)['\"]", html)
    if m:
        urls.append(m.group(1).strip())
    urls += re.findall(r'href="(https?://[^"]+)"', _content_region(html))
    other, web = [], []
    for u in urls:
        u = u.split("#")[0].strip()
        host = urllib.parse.urlparse(u).netloc.lower()
        if not host:
            continue
        if host == "mp.weixin.qq.com":
            biz = urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("__biz", [""])[0]
            if (biz and biz not in bizset and link_key(u)) or ("/s/" in u and not biz):
                u = u.replace("http://", "https://", 1)
                if u not in other:
                    other.append(u)
        elif not host.endswith(SKIP_HOSTS) and u not in web:
            web.append(u)
    return other, web


def external_filename(n, url, content_type):
    p = urllib.parse.urlparse(url)
    stem = ws.safe_name((p.netloc + p.path).replace("/", "_").strip("_"), 70)
    ct = (content_type or "").lower()
    if "pdf" in ct or url.lower().endswith(".pdf"):
        ext = ".pdf"
    elif "html" in ct or not ct:
        ext = ".html"
    else:
        ext = os.path.splitext(p.path)[1] or ".bin"
    return "%02d_%s%s" % (n, stem, "" if stem.endswith(ext) and ext != ".html" else ext)


def _fetch_web(url, last={}):
    """普通网页 / 文件：同一站点间隔 ≥3 秒；返回 (内容, content-type, 最终地址)。不处理登录、付费墙。"""
    host = urllib.parse.urlparse(url).netloc
    wait = 3 - (time.time() - last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    last[host] = time.time()
    req = urllib.request.Request(urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*[]~"), headers={"User-Agent": ws.UA})
    with urllib.request.urlopen(req, timeout=40) as resp:
        if int(resp.headers.get("Content-Length") or 0) > MAX_EXTERNAL_BYTES:
            raise ValueError("文件超过 200MB")
        return resp.read(), resp.headers.get("Content-Type", ""), resp.geturl()


def external(images=True, max_items=400):
    """对目标号的每篇文章：下载其中链到的其他公众号文章和网页 / 文件（只追一层）。遇微信验证即停。"""
    cfg = ws.load_config()
    bizset = {a["biz"] for a in cfg["accounts"] if a.get("biz")}
    fetcher, store = ws.Fetcher(), ws.Store()
    store.db.executescript(EXT_SCHEMA)
    done_n = 0
    for acc in cfg["accounts"]:
        account_dir = os.path.join(ws.RAW_DIR, acc["name"])
        if not os.path.isdir(account_dir):
            continue
        for sub in sorted(os.listdir(account_dir)):
            art_dir = os.path.join(account_dir, sub)
            src, meta_p = os.path.join(art_dir, "source.html"), os.path.join(art_dir, "metadata.json")
            if not (os.path.exists(src) and os.path.exists(meta_p)):
                continue
            with open(meta_p, encoding="utf-8") as f:
                key = __import__("json").load(f).get("key")
            if not key or store.one("SELECT 1 FROM ext_done WHERE key=?", (key,)):
                continue
            with open(src, encoding="utf-8", errors="ignore") as f:
                other, web = external_links(f.read(), bizset)
            index_p = os.path.join(art_dir, "external", "index.json")
            index = []
            if os.path.exists(index_p):
                with open(index_p, encoding="utf-8") as f:
                    index = __import__("json").load(f)
            for u in other:
                if store.one("SELECT 1 FROM externals WHERE url=?", (u,)):
                    continue
                try:
                    status, msg, _ = download(u, fetcher, store, images, source="external")
                except ws.WechatVerify:
                    print("⏸ 微信要求验证（访问太频繁），外链下载本轮停止", flush=True)
                    ws.write_index(store)
                    return "verify"
                except Exception as e:  # noqa: BLE001
                    status, msg = "fail", str(e)[:200]
                store.exec("INSERT OR REPLACE INTO externals VALUES (?,?,?,?,?,?,?)",
                           (u, key, "wechat", status, "", "", ws.now_iso()))
                print({"ok": "  🔗✅ ", "dup": "  🔗↩︎ 已有 ", "fail": "  🔗❌ "}[status] + msg, flush=True)
                done_n += status == "ok"
            for u in web:
                if store.one("SELECT 1 FROM externals WHERE url=?", (u,)):
                    continue
                rec = {"url": u, "from": sub}
                try:
                    data, ctype, final = _fetch_web(u)
                    text = data[:4000].decode("utf-8", errors="ignore")
                    if "<title>404" in text or "页面出错" in text or "页面不存在" in text:
                        raise ValueError("页面已失效")
                    os.makedirs(os.path.dirname(index_p), exist_ok=True)
                    fname = external_filename(len(index) + 1, final, ctype)
                    with open(os.path.join(art_dir, "external", fname), "wb") as f:
                        f.write(data)
                    title = re.search(r"<title[^>]*>(.*?)</title>", data[:200000].decode("utf-8", errors="ignore"),
                                      re.S | re.I)
                    rec.update(status="ok", file="external/" + fname, content_type=ctype, bytes=len(data),
                               title=unescape(title.group(1)).strip()[:200] if title else "")
                    print("  🌐✅ %s → %s" % (u[:90], fname), flush=True)
                    done_n += 1
                except Exception as e:  # noqa: BLE001 — 失效 / 拒绝访问只记录，不重试
                    rec.update(status="fail", error=str(e)[:200])
                    print("  🌐❌ %s：%s" % (u[:90], str(e)[:80]), flush=True)
                index.append(rec)
                store.exec("INSERT OR REPLACE INTO externals VALUES (?,?,?,?,?,?,?)",
                           (u, key, "web", rec["status"], rec.get("file", ""), rec.get("content_type", ""),
                            ws.now_iso()))
                os.makedirs(os.path.dirname(index_p), exist_ok=True)
                with open(index_p, "w", encoding="utf-8") as f:
                    __import__("json").dump(index, f, ensure_ascii=False, indent=2)
            store.exec("INSERT OR REPLACE INTO ext_done VALUES (?,?)", (key, ws.now_iso()))
            if done_n >= max_items:
                ws.write_index(store)
                return "limit"
    ws.write_index(store)
    return "done"


# ---------------------------------------------------------------- 全自动循环


def write_progress(store, last_status):
    import json  # noqa: F401
    lines = ["# 公众号采集进度", "", "更新时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
             "| 公众号 | 篇数 | 最早 | 最晚 |", "| --- | --- | --- | --- |"]
    for acc, n, lo, hi in store.db.execute(
            "SELECT account, count(*), min(ts), max(ts) FROM articles GROUP BY account ORDER BY count(*) DESC"):
        fmt = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else "?"
        lines.append("| %s | %d | %s | %s |" % (acc, n, fmt(lo), fmt(hi)))
    store.db.executescript(EXT_SCHEMA)
    ext = dict(store.db.execute("SELECT kind || ':' || status, count(*) FROM externals GROUP BY 1").fetchall())
    lines += ["", "外部链接：%s" % (ext or "暂无"), "", "最近一轮：%s" % last_status]
    with open(os.path.join(ws.RAW_DIR, "_进度.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def auto(interval=2700, images=True):
    """循环：合集 → 顺藤摸瓜 → 外部链接；遇到微信验证就跳过本轮剩余步骤，等下一轮。"""
    ws.setup_logging()
    while True:
        started = datetime.now().strftime("%H:%M")
        status = []
        for name, step in (("合集", lambda: albums(500, images)), ("顺藤摸瓜", lambda: expand(500, images)),
                           ("外部链接", lambda: external(images))):
            print("=== [%s] %s" % (started, name), flush=True)
            try:
                r = step()
            except Exception as e:  # noqa: BLE001 — 某一步出错不影响整个循环
                import traceback
                traceback.print_exc()
                r = "error:%s" % str(e)[:80]
            status.append("%s:%s" % (name, r))
            if r == "verify":
                break
        write_progress(ws.Store(), "%s 开始，%s" % (started, "，".join(status)))
        print("=== 本轮结束（%s），%d 分钟后下一轮" % ("，".join(status), interval // 60), flush=True)
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description="按链接下载公众号文章")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="监听剪贴板")
    w.add_argument("--no-images", action="store_true")
    sf = sub.add_parser("sniffed", help="消费 mitmproxy 旁听队列（见 scripts/wechat_sniff.py）")
    sf.add_argument("--no-images", action="store_true")
    f = sub.add_parser("file", help="批量下载文件里的链接")
    f.add_argument("path")
    f.add_argument("--no-images", action="store_true")
    sub.add_parser("external", help="下载目标号文章里链到的其他公众号文章和网页 / 文件（只追一层）")
    au = sub.add_parser("auto", help="全自动循环：合集 → 顺藤摸瓜 → 外部链接")
    au.add_argument("--interval", type=int, default=2700, help="每轮间隔秒数")
    al = sub.add_parser("albums", help="读取目标号的合集列表，下载缺的文章")
    al.add_argument("--max", type=int, default=500)
    al.add_argument("--no-images", action="store_true")
    e = sub.add_parser("expand", help="顺着已存文章里的同号链接继续下载")
    e.add_argument("--max", type=int, default=300, help="本次最多新增多少篇")
    e.add_argument("--no-images", action="store_true")
    args = ap.parse_args(argv)
    os.makedirs(ws.RAW_DIR, exist_ok=True)
    try:
        if args.cmd == "watch":
            watch(not args.no_images)
        elif args.cmd == "sniffed":
            sniffed(not args.no_images)
        elif args.cmd == "external":
            external()
        elif args.cmd == "auto":
            auto(args.interval)
        elif args.cmd == "albums":
            albums(args.max, not args.no_images)
        elif args.cmd == "expand":
            expand(args.max, not args.no_images)
        else:
            from_file(args.path, not args.no_images)
    except KeyboardInterrupt:
        print("\n已结束。")
        sys.exit(0)


if __name__ == "__main__":
    main()

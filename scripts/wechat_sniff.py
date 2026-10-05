"""旁听微信客户端自己发出的公众号历史消息请求，把文章链接写进队列文件。

不伪造任何请求、不改任何包：微信客户端打开公众号「全部消息」并往下滚时，自己会向
mp.weixin.qq.com 请求每页的文章列表，这里只是把响应里的链接记下来。

    # 终端 1：旁听（只解密 mp.weixin.qq.com，其它域名原样放行）
    mitmdump -p 8899 --allow-hosts 'mp\\.weixin\\.qq\\.com' -s scripts/wechat_sniff.py
    # 终端 2：把队列里的链接下载下来（遇微信验证自动暂停，稍后续）
    python3 scripts/wechat_links.py sniffed

前置（由用户本人操作）：信任 ~/.mitmproxy/mitmproxy-ca-cert.pem；系统网络代理指向 127.0.0.1:8899；重启微信。
队列文件 <归档目录>/_sniffed_links.txt（一行一个链接）；明细 <归档目录>/_sniffed_records.jsonl；
请求日志 <归档目录>/_logs/wechat_sniff.out（凭证参数一律打码）。

本文件在 mitmproxy 自带的 Python 里运行，只用标准库（和同目录的 config.py）。
"""

import json
import os
import re
import sys
import urllib.parse
from datetime import datetime
from html import unescape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ACCOUNTS_PATH, get_archive_dir  # noqa: E402

RAW_DIR = str(get_archive_dir())
ROOT = RAW_DIR
QUEUE = os.path.join(RAW_DIR, "_sniffed_links.txt")
RECORDS = os.path.join(RAW_DIR, "_sniffed_records.jsonl")
SPOOL = os.path.join(RAW_DIR, "_sniffed_pages")  # 客户端打开的文章正文页原样落盘，由下载器解析入库，不再二次下载
LOG = os.path.join(RAW_DIR, "_logs", "wechat_sniff.out")
HOSTS_LOG = os.path.join(RAW_DIR, "_logs", "wechat_sniff_hosts.out")  # 经过代理的所有主机名（含未解密直通的）

HOST = "mp.weixin.qq.com"
CONFIG = str(ACCOUNTS_PATH)  # 白名单：只收这里列出的公众号（按 __biz）
LINK_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s\?[^\s\"'<>\\]+")
MSGLIST_RE = re.compile(r"var\s+msgList\s*=\s*'(.*?)';", re.S)
SECRET_KEYS = {"key", "uin", "pass_ticket", "appmsg_token", "x5", "wxtoken", "devicetype", "exportkey"}


def _unescape_all(s):
    """JSON / HTML 里的链接常被转义多层（\\/、\\u0026、&amp;amp;、&quot;）：反复还原直到稳定。"""
    prev = None
    while s != prev:
        prev = s
        s = unescape(s.replace("\\/", "/").replace("\\u0026", "&").replace("\\x26", "&"))
    return s


def link_key(url):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    biz, mid, idx, sn = (q.get(k, [""])[0] for k in ("__biz", "mid", "idx", "sn"))
    return "%s_%s_%s" % (biz, mid, idx) if biz and mid and idx and sn else None


def _clean_url(url):
    if not url:
        return None
    url = _unescape_all(url).strip()
    if url.startswith("/"):
        url = "https://" + HOST + url
    url = url.replace("http://", "https://", 1).split("#", 1)[0]
    if "${" in url or "{" in url:  # 页面脚本里的模板占位符，不是真链接
        return None
    return url if link_key(url) else None


def parse_items(obj):
    """历史消息列表 JSON（{"list": [...]}）→ [{title, url, ts}]，含多图文的次条。"""
    out = []
    for item in obj.get("list") or []:
        ts = (item.get("comm_msg_info") or {}).get("datetime")
        ext = item.get("app_msg_ext_info") or {}
        if not ext:
            continue
        for e in [ext] + list(ext.get("multi_app_msg_item_list") or []):
            url = _clean_url(e.get("content_url"))
            if url:
                out.append({"title": e.get("title") or None, "url": url, "ts": ts})
    return out


def _dedupe(recs):
    seen, out = set(), []
    for r in recs:
        k = link_key(r["url"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def extract(path, body):
    """从一个 mp.weixin.qq.com 响应里找文章链接。已知格式精确解析，未知格式兜底扫全部完整链接。"""
    recs = []
    try:
        data = json.loads(body)
        gml = data.get("general_msg_list") if isinstance(data, dict) else None
        if gml:
            recs = parse_items(json.loads(gml) if isinstance(gml, str) else gml)
    except (ValueError, AttributeError):
        pass
    if not recs:
        m = MSGLIST_RE.search(body)
        if m:
            try:
                recs = parse_items(json.loads(_unescape_all(m.group(1))))
            except ValueError:
                recs = []
    if not recs:
        recs = [{"title": None, "url": u, "ts": None}
                for u in (_clean_url(x) for x in LINK_RE.findall(_unescape_all(body))) if u]
    return _dedupe(recs)


def is_article_page(path, body):
    """微信客户端打开的文章正文页（不是验证页、不是接口响应）。"""
    return (path.startswith("/s?") or path.startswith("/s/")) and 'id="js_content"' in body


def spool_page(spool_dir, path, body):
    """正文页按去重键落盘，返回文件路径；已有同一篇则返回 None。"""
    key = link_key("https://" + HOST + path)  # 客户端请求的 URL 自带 biz/mid/idx/sn，比页面里的变量可靠
    if not key:
        import hashlib
        key = "page_" + hashlib.sha1(body.encode("utf-8", "ignore")).hexdigest()[:16]
    os.makedirs(spool_dir, exist_ok=True)
    f = os.path.join(spool_dir, key + ".html")
    if os.path.exists(f) or os.path.exists(os.path.join(spool_dir, "done", key + ".html")):
        return None
    tmp = f + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, f)
    return f


def load_bizset(path=CONFIG):
    try:
        with open(path, encoding="utf-8") as f:
            return {a["biz"] for a in json.load(f).get("accounts", []) if a.get("biz")}
    except (OSError, ValueError):
        return set()


def url_biz(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("__biz", [""])[0]


def filter_biz(recs, bizset):
    """只留白名单公众号的链接；bizset 为空表示不过滤。"""
    return [r for r in recs if not bizset or url_biz(r["url"]) in bizset]


def redact(path):
    """日志里只留参数名，凭证值打码。"""
    if "?" not in path:
        return path
    base, qs = path.split("?", 1)
    parts = []
    for p in qs.split("&"):
        k, _, v = p.partition("=")
        parts.append(k + "=" + ("*" if k.lower() in SECRET_KEYS else v))
    return base + "?" + "&".join(parts)


def _queued(path):
    try:
        with open(path, encoding="utf-8") as f:
            return {link_key(l.strip()) for l in f if l.strip()}
    except FileNotFoundError:
        return set()


def append_queue(path, recs, records_path=None):
    """新链接追加到队列（每行一个），返回新增条数；明细另写 jsonl。"""
    have = _queued(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = 0
    with open(path, "a", encoding="utf-8") as q, \
            open(records_path or os.devnull, "a", encoding="utf-8") as rf:
        for r in recs:
            k = link_key(r["url"])
            if not k or k in have:
                continue
            have.add(k)
            q.write(r["url"] + "\n")
            rf.write(json.dumps(dict(r, captured_at=datetime.now().isoformat(timespec="seconds")), ensure_ascii=False) + "\n")
            n += 1
    return n


def read_queue(path, pos):
    """从 pos 起读新增行，返回 (链接列表, 新 pos)。"""
    try:
        with open(path, encoding="utf-8") as f:
            f.seek(pos)
            chunk = f.read()
            return [l.strip() for l in chunk.splitlines() if l.strip()], f.tell()
    except FileNotFoundError:
        return [], pos


def _log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = "%s %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, file=sys.stderr, flush=True)


class Sniffer:
    """mitmproxy addon：只看 mp.weixin.qq.com 的响应；另把所有经过的主机名记下来，便于发现新域名。"""

    def __init__(self):
        self.bizset = load_bizset()
        _log("白名单 biz：%s" % (", ".join(sorted(self.bizset)) or "（空，不过滤）"))

    def server_connect(self, data):
        try:
            host, port = data.server.address[0], data.server.address[1]
        except Exception:  # noqa: BLE001
            return
        with open(HOSTS_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s:%s\n" % (datetime.now().strftime("%H:%M:%S"), host, port))

    def response(self, flow):
        req = flow.request
        if req.host != HOST or flow.response is None:
            return
        ctype = flow.response.headers.get("content-type", "")
        if not any(t in ctype for t in ("json", "html", "text", "javascript")):
            return
        body = flow.response.get_text(strict=False) or ""
        if is_article_page(req.path, body):
            if self.bizset and url_biz("https://" + HOST + req.path) not in self.bizset:
                _log("📄 正文页不在白名单，忽略 %s" % redact(req.path)[:90])
            else:
                f = spool_page(SPOOL, req.path, body)
                _log("📄 正文页 %s → %s" % (redact(req.path)[:90], os.path.basename(f) if f else "已落盘过，跳过"))
        recs = filter_biz(extract(req.path, body), self.bizset)
        n = append_queue(QUEUE, recs, RECORDS) if recs else 0
        _log("%s  %dB  链接 %d  新入队 %d" % (redact(req.path)[:160], len(body), len(recs), n))
        if n:
            titles = [r["title"] or r["url"][-20:] for r in recs[:3]]
            _log("   ↳ %s%s" % ("；".join(titles), " …" if len(recs) > 3 else ""))


addons = [Sniffer()] if "mitmproxy" in sys.modules else []  # 只在 mitmproxy 加载本文件时启用，普通 import 不留痕

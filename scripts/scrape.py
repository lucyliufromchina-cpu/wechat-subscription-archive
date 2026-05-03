#!/usr/bin/env python3
"""抓订阅公众号 — Step 1: 登录自己的公众号后台，搜索目标号，拉文章列表。

流程：
    1. Playwright 启动 Chromium，加载持久化用户目录
    2. 打开 mp.weixin.qq.com → 检测/等待扫码登录 → 提取 token
    3. 调用 searchbiz API 搜目标公众号 → 拿 fakeid
    4. 分页调用 appmsg API 拉全量文章列表（带 --since 截断）
    5. 列表写入 ~/wechat-archive/<公众号名>/_list.json

依赖：playwright，安装见 SKILL.md
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

# 同级 import
sys.path.insert(0, str(Path(__file__).parent))
from config import get_archive_dir  # noqa: E402

USER_DATA_DIR = Path.home() / ".mp-data-browser"
LOGIN_URL = "https://mp.weixin.qq.com/"

SEARCHBIZ_URL = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
APPMSG_URL = "https://mp.weixin.qq.com/cgi-bin/appmsg"


def extract_token(url: str):
    m = re.search(r"token=(\d+)", url)
    return m.group(1) if m else None


def ensure_login(page):
    """打开后台首页，已登录直接拿 token；未登录等扫码（最长 3 分钟）。"""
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)

    token = extract_token(page.url)
    if token:
        return token

    print("未检测到登录态，请在浏览器中用微信扫码登录公众号后台...")
    for _ in range(60):  # 60 * 3s = 3min
        page.wait_for_timeout(3000)
        token = extract_token(page.url)
        if token:
            print("登录成功。")
            return token
    return None


def api_get(page, url: str, params: dict):
    """在已登录的浏览器 context 里发 GET 请求（自动带 cookie）。

    使用 page.evaluate + fetch 而不是 requests，避开 cookie 同步问题。
    """
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{qs}"
    js = (
        "async (u) => {"
        "  const r = await fetch(u, {credentials: 'include'});"
        "  return await r.text();"
        "}"
    )
    text = page.evaluate(js, full_url)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text[:500]}


def search_biz(page, token: str, query: str):
    """搜目标公众号，返回候选列表 [{fakeid, nickname, alias, ...}, ...]"""
    data = api_get(page, SEARCHBIZ_URL, {
        "action": "search_biz",
        "begin": 0,
        "count": 5,
        "query": query,
        "token": token,
        "lang": "zh_CN",
        "f": "json",
        "ajax": 1,
    })
    if "list" not in data:
        print(f"[!] searchbiz 异常返回: {data}")
        return []
    return data["list"]


def list_appmsg(page, token: str, fakeid: str, begin: int):
    """拉一页文章列表（5 篇）。"""
    return api_get(page, APPMSG_URL, {
        "action": "list_ex",
        "begin": begin,
        "count": 5,
        "fakeid": fakeid,
        "type": 9,
        "query": "",
        "token": token,
        "lang": "zh_CN",
        "f": "json",
        "ajax": 1,
    })


# 错误码说明（mp 后台 base_resp.ret）：
#   200013 = freq control（频控，等待后可重试）
#   200002 = invalid args / login expired（参数错或登录失效，重试无效）
#   200003 = invalid session（会话失效，重试无效）
# 频控等待时长（秒）：60 → 180 → 600 → 1200 → 3600，递增到 1 小时
FREQ_BACKOFF = [60, 180, 600, 1200, 3600]


def fetch_all_articles(page, token: str, fakeid: str, since_ts: int = 0):
    """分页拉全量文章。since_ts 之前的会停止。"""
    all_items = []
    begin = 0
    freq_attempt = 0  # 当前 begin 的累计频控重试次数
    while True:
        data = list_appmsg(page, token, fakeid, begin)
        base_resp = data.get("base_resp", {})
        ret = base_resp.get("ret")

        # 致命错误：登录失效 / 会话失效，无法重试
        if ret in (200002, 200003):
            print(f"[X] 登录/会话已失效 (ret={ret})，请删除 ~/.mp-data-browser 后重新扫码登录。")
            print(f"    已抓取 {len(all_items)} 篇，将这部分保存。")
            break

        # 频控：指数退避重试
        if ret == 200013:
            if freq_attempt >= len(FREQ_BACKOFF):
                print(f"[X] 频控重试 {freq_attempt} 次仍失败，今日额度已用尽。")
                print(f"    已抓取 {len(all_items)} 篇，将这部分保存。建议明天用 --since 增量补抓。")
                break
            wait = FREQ_BACKOFF[freq_attempt]
            freq_attempt += 1
            mins = wait // 60
            print(f"[!] 触发频控 (ret=200013)，等待 {mins} 分钟后重试 (第 {freq_attempt}/{len(FREQ_BACKOFF)} 次)...")
            time.sleep(wait)
            continue

        # 其他非 0 ret，告警但继续尝试推进
        if ret not in (0, None):
            print(f"[!] 未知错误 ret={ret}, errmsg={base_resp.get('err_msg', '')}")

        # 成功，重置频控计数
        freq_attempt = 0

        items = data.get("app_msg_list", [])
        total = data.get("app_msg_cnt", -1)
        if not items:
            print(f"[i] 已到末尾（begin={begin}），共 {len(all_items)} 篇。")
            break

        # 截断：since
        if since_ts > 0:
            items_kept = [a for a in items if a.get("create_time", 0) >= since_ts]
            all_items.extend(items_kept)
            if len(items_kept) < len(items):
                print(f"[i] 抵达 --since 截止时间，提前停止。共 {len(all_items)} 篇。")
                break
        else:
            all_items.extend(items)

        print(f"  ...已拉取 {len(all_items)}/{total if total > 0 else '?'} 篇")
        begin += 5
        time.sleep(2)  # 礼貌间隔
    return all_items


def main():
    ap = argparse.ArgumentParser(description="抓订阅公众号 — 搜索 + 拉列表")
    ap.add_argument("query", help="目标公众号名称（模糊匹配）")
    ap.add_argument("--since", help="只抓某日期之后的文章 (YYYY-MM-DD)")
    ap.add_argument("--auto-first", action="store_true",
                    help="多候选时自动选第 1 个，不交互（脚本化场景用）")
    args = ap.parse_args()

    since_ts = 0
    if args.since:
        from datetime import datetime
        since_ts = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp())
        print(f"[i] 仅抓取 {args.since} 之后的文章 (ts >= {since_ts})")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: 需要 playwright，运行: pip3 install playwright && playwright install chromium")
        sys.exit(1)

    with sync_playwright() as pw:
        print("启动浏览器...")
        ctx = pw.chromium.launch_persistent_context(
            str(USER_DATA_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        token = ensure_login(page)
        if not token:
            print("ERROR: 登录超时（3 分钟），退出。")
            ctx.close()
            sys.exit(1)

        print(f"\n搜索公众号: {args.query!r}")
        candidates = search_biz(page, token, args.query)
        if not candidates:
            print("ERROR: 未找到匹配的公众号。")
            ctx.close()
            sys.exit(1)

        print(f"找到 {len(candidates)} 个候选：")
        for i, c in enumerate(candidates, 1):
            verify = c.get("verify_name") or ""
            verify_tag = f", 主体={verify}" if verify else ""
            signature = (c.get("signature") or "")[:40]
            sig_tag = f"\n     {signature}" if signature else ""
            print(f"  {i}. {c.get('nickname')} (alias={c.get('alias','')}{verify_tag}){sig_tag}")

        # 单候选 / --auto-first：直接选第 1 个；多候选：交互让用户选
        if len(candidates) == 1 or args.auto_first:
            target = candidates[0]
            print(f"\n选中: {target['nickname']}")
        else:
            while True:
                try:
                    choice = input(f"\n请选择第几个 (1-{len(candidates)}, 回车=1, q=退出): ").strip()
                except EOFError:
                    choice = ""
                if choice.lower() == "q":
                    print("已取消。")
                    ctx.close()
                    sys.exit(0)
                if choice == "":
                    target = candidates[0]
                    break
                if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                    target = candidates[int(choice) - 1]
                    break
                print(f"无效输入: {choice!r}")
            print(f"已选: {target['nickname']}")
        fakeid = target["fakeid"]
        nickname = target["nickname"]

        print(f"\n开始拉取文章列表...")
        articles = fetch_all_articles(page, token, fakeid, since_ts)

        ctx.close()

    if not articles:
        print("[!] 未抓到任何文章。")
        sys.exit(1)

    archive_dir = get_archive_dir() / nickname
    archive_dir.mkdir(parents=True, exist_ok=True)
    list_path = archive_dir / "_list.json"
    list_path.write_text(
        json.dumps({"nickname": nickname, "fakeid": fakeid, "articles": articles},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[OK] 已保存 {len(articles)} 篇到 {list_path}")
    print(f"     下一步: python3 scripts/download_full.py \"{nickname}\"")


if __name__ == "__main__":
    main()

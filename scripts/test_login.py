#!/usr/bin/env python3
"""只测试登录是否通。不抓任何数据。

成功标准：
    1. 浏览器弹出
    2. 你扫码登录（或已有登录态自动通过）
    3. 终端打印出公众号名字 + token
    4. 按回车退出

如果这一步过不了，后续 scrape / download 都不用试。
"""

import re
import sys
from pathlib import Path

USER_DATA_DIR = Path.home() / ".mp-data-browser"
LOGIN_URL = "https://mp.weixin.qq.com/"


def extract_token(url):
    m = re.search(r"token=(\d+)", url)
    return m.group(1) if m else None


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: 需要 playwright，运行: pip3 install playwright && playwright install chromium")
        sys.exit(1)

    print("=== 公众号后台登录测试 ===\n")
    print(f"用户目录: {USER_DATA_DIR}")
    print("启动浏览器...\n")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(USER_DATA_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)

        token = extract_token(page.url)
        if token:
            print(f"[✓] 已有登录态，token = {token}")
        else:
            print("[ ] 未登录，请在浏览器中用微信扫码（最多等 3 分钟）...")
            for i in range(60):
                page.wait_for_timeout(3000)
                token = extract_token(page.url)
                if token:
                    print(f"\n[✓] 登录成功！token = {token}")
                    break
                if i % 5 == 4:
                    print(f"    等待中... ({(i+1)*3}s)")
            else:
                print("\n[✗] 登录超时（3 分钟），请重试。")
                ctx.close()
                sys.exit(1)

        # 登录后打印公众号名字
        try:
            nickname = page.evaluate(
                "() => {"
                "  var el = document.querySelector('.weui-desktop-account__nickname') ||"
                "           document.querySelector('.nickname') ||"
                "           document.querySelector('[class*=account] [class*=name]');"
                "  return el ? el.innerText.trim() : '';"
                "}"
            )
        except Exception as e:
            nickname = f"(读取失败: {e})"

        print(f"\n=== 登录信息 ===")
        print(f"公众号名: {nickname or '(未取到)'}")
        print(f"Token:    {token}")
        print(f"当前 URL: {page.url}")
        print(f"\n登录态已保存在 {USER_DATA_DIR}，下次自动复用。")

        input("\n按回车关闭浏览器并退出...")
        ctx.close()
        print("[OK] 测试结束。")


if __name__ == "__main__":
    main()

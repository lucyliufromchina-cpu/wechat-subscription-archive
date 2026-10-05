# 抓订阅公众号 — Claude Code Skill

把你**订阅的他人微信公众号**的历史文章（标题、链接、发布时间、全文、图片）归档为本地 Markdown，供研究、收藏、离线阅读、个人知识库使用。

> 抓的是**别人**运营的号；不是你自己号的后台数据。

## 现在还能怎么抓（2026-10）

| 路线 | 状态 |
| --- | --- |
| 公众号后台「引用文章」接口 | 2026-07-30 关闭，[wechat-article-exporter](https://github.com/wechat-article/wechat-article-exporter/issues/200) 因此归档 |
| 微信读书列表接口 | 只返回最新一篇 |
| 截客户端凭证回放 getmsg（Windows 工具的做法） | 9 月起多方报告返回空；且只给最近 100 页、只有群发文章 |
| 搜索引擎 `site:mp.weixin.qq.com` | 不收录 |
| 搜狗微信搜索 | 只能按关键词碰，有验证码 |
| **微信客户端打开的文章页** | **稳定** —— 本 skill 的主方法 |

微信 Mac 4.1.x 的公众号主页是内置浏览器页面，但文章**列表**由客户端经内部通道取回，HTTP 代理看不到；只有点开的**文章页**走 HTTP。所以：本机跑 mitmproxy 旁听，微信里逐篇点开（人点或 Computer Use 点），整页落盘入库，不再二次下载，也不会触发微信的"访问太频繁"验证。合集页、顺藤摸瓜、搜狗关键词作为补充自动跑。

**不做**：伪造请求、用凭证调接口、绕过验证码或风控、换 IP 清 cookie。

## 安装

```bash
git clone https://github.com/lucyliufromchina-cpu/wechat-subscription-archive.git
cd wechat-subscription-archive
ln -sf "$(pwd)" ~/.claude/skills/抓订阅公众号
brew install mitmproxy
cp scripts/accounts.example.json ~/.config/抓订阅公众号/accounts.json   # 填公众号名和 __biz
python3 -m unittest discover -s scripts/tests -t scripts                # 自检
```

脚本只用 Python 3.9+ 标准库；mitmproxy 自带运行时。

## 使用

完整步骤见 [SKILL.md](SKILL.md)；Computer Use 自动点击的规程见 [docs/codex-click-method.md](docs/codex-click-method.md)。

最短路径：

1. 信任 `~/.mitmproxy/mitmproxy-ca-cert.pem`，系统代理指向 `127.0.0.1:8899`，重启微信，关掉自动锁屏
2. `mitmdump -p 8899 --allow-hosts 'mp\.weixin\.qq\.com' -s scripts/wechat_sniff.py`
3. `python3 scripts/wechat_links.py sniffed`
4. 微信里进入公众号主页「文章」列表，逐篇点开、返回
5. 采完关代理

产出在 `~/wechat-archive/<公众号>/<日期>_<标题>/`，总清单 `INDEX.csv`，覆盖情况 `_进度.md`。

## 历史

- 2023-11 起：公众号后台「引用文章」接口拉列表（已失效，代码见 git 历史）
- 2026-08：改搜狗「公众号名 + 关键词」
- 2026-10：主方法改为客户端打开 + 代理旁听直存；合集 / 顺藤摸瓜 / 外链一层 / 搜狗并入 `auto` 循环；加白名单、验证页检测、Computer Use 规程

## 许可与边界

归档内容版权归原作者；本工具仅供个人研究与离线阅读，`.gitignore` 默认排除抓取内容与账号配置。

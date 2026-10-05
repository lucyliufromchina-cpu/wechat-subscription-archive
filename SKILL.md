---
name: 抓订阅公众号
description: 把你订阅的微信公众号的历史文章（标题/链接/发布时间/全文/图片）归档为本地 Markdown。2026-10 起主方法：本机 mitmproxy 旁听微信 Mac 客户端自己打开的文章页并直接落盘（不伪造请求、不绕过验证码），配合合集页、顺藤摸瓜、搜狗关键词三种补充手段。Python 标准库 + mitmproxy。
trigger: /抓订阅公众号
---

# 抓订阅公众号 Skill

> **2026-10 现状**：公众号后台「引用文章」接口（7-30 关闭）、微信读书列表接口、Windows 工具链依赖的凭证回放（9 月起返回空）都已不可用；
> 搜索引擎不收录公众号文章；搜狗只能按关键词碰、且有验证码。微信 Mac 4.1.x 的公众号主页虽是网页，但文章**列表**由客户端通过内部通道取回，HTTP 层看不到。
> **唯一还能稳定拿到的是"客户端打开的那篇文章"**——本 skill 就建立在这一点上。

适用：行业研究 / 内容收藏 / 离线归档。不适用：一次性拉某号全部列表（没有公开途径）、阅读量等互动数据。

## 方法总览

| 方法 | 原理 | 能拿到 | 限制 |
| --- | --- | --- | --- |
| **A 直存（主）** | 微信客户端打开文章 → 本机 mitmproxy 旁听响应 → 整页落盘入库 | 任何你点开的文章，全文 + 图片，不触发微信验证 | 每篇要点一次（人或 Codex） |
| B 合集 | 公众号「合集」页免登录可翻 | 作者归入合集的文章 | 只覆盖有合集的 |
| C 顺藤摸瓜 | 从已存文章里找同号链接继续下载 | 往期推荐、系列文 | 程序直连文章页，频繁会被微信要求验证 |
| D 搜狗关键词 | 「公众号名 + 主题词」检索 | 按主题碰老文章 | 每词 10 页；验证码即停 |

B / C / D 由 `auto` 循环自动跑；A 需要有人在微信里点。

## 边界（不做的事）

不伪造微信请求、不用截到的凭证调接口、不绕过验证码或账号风控、不换 IP 清 cookie；遇验证即停、冷却后再试。

## 前置

- macOS + 微信 Mac 客户端（实测 4.1.13；升级后请先用一篇文章复测）
- Python 3.9+（脚本只用标准库）；`brew install mitmproxy`
- 公众号清单：把 `scripts/accounts.example.json` 复制为 `~/.config/抓订阅公众号/accounts.json`，填 `name`（署名）和 `biz`（任意一篇文章链接里的 `__biz`）。它同时是**白名单**：不在清单里的公众号一律不收
- 归档目录默认 `~/wechat-archive`，可在 `~/.config/抓订阅公众号/config.json` 改 `archive_dir`

## 主流程 A：旁听直存

### 一次性设置（用户本人操作）

1. 启动一次代理生成 CA，然后信任它（登录钥匙串即可，不需要 sudo）：
   ```bash
   security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db ~/.mitmproxy/mitmproxy-ca-cert.pem
   ```
2. 系统代理指向本机（网络服务名用 `networksetup -listallnetworkservices` 查）：
   ```bash
   networksetup -setwebproxy Wi-Fi 127.0.0.1 8899 && networksetup -setsecurewebproxy Wi-Fi 127.0.0.1 8899
   ```
3. **完全退出微信再重开**
4. 系统设置 → 锁定屏幕：屏保"永不"、接电源时关闭显示器"永不"，插电。锁屏会让点击全部落空而脚本毫无察觉

### 每次采集

```bash
# 终端 1：旁听（只解密 mp.weixin.qq.com，其它域名原样直通；凭证参数在日志里打码）
mitmdump -p 8899 --allow-hosts 'mp\.weixin\.qq\.com' -s scripts/wechat_sniff.py
# 终端 2：消费（落盘页解析入库 + 取图；文章里带出的同号链接排队下载）
python3 scripts/wechat_links.py sniffed
```

然后在微信里：点开目标公众号任意一篇文章 → 点文章顶部的公众号名进入主页 → 「文章」标签，逐篇**点开、等 2 秒、返回**。每篇一行 `✅ …（客户端页面直存）` 即成功；已存的自动跳过。

- 交给 Codex / Computer Use 自动点：规程见 [docs/codex-click-method.md](docs/codex-click-method.md)（实测约 6 秒/篇，别压到 4 秒以内）
- 采完**关代理**：`networksetup -setwebproxystate Wi-Fi off && networksetup -setsecurewebproxystate Wi-Fi off`

### 成功的证据

只有 `<归档目录>/_logs/wechat_sniff.out` 里出现 `📄 正文页 …`、消费端出现 `✅`，才算抓到。鼠标脚本打印 completed、队列没动，就是代理或锁屏出了问题。

## 补充流程 B/C/D：自动循环

```bash
nohup caffeinate -i python3 scripts/wechat_links.py auto > ~/wechat-archive/_logs/auto.out 2>&1 &
```

每轮：合集 → 顺藤摸瓜 → 外部链接（其它号文章 / 网页 / PDF，只追一层）→ 搜狗补老文章；任一步遇微信验证就跳过本轮剩余，45 分钟后再来。进度写在 `<归档目录>/_进度.md`。

单独跑：`python3 scripts/wechat_links.py albums|expand|external`、`python3 scripts/wechat_sogou.py run [--account <公众号名>]`、`python3 scripts/wechat_links.py file <含链接的文本>`、`python3 scripts/wechat_links.py watch`（剪贴板监听，老方法，仍可用）。

## 输出结构

```
~/wechat-archive/
├── INDEX.csv                       # 总清单（公众号、标题、时间、永久链接、目录）
├── _进度.md                        # 按号 / 按年覆盖情况
├── wechat.sqlite                   # 去重库（__biz+mid+idx）与断点状态
├── _sniffed_links.txt / _sniffed_records.jsonl / _sniffed_pages/   # 旁听队列、明细、落盘页
├── _logs/
└── <公众号>/<日期>_<标题>/{article.md, metadata.json, source.html, images/}
```

## 故障排除

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 点了很多篇，日志没有 `正文页` | 代理没在跑 / 系统代理没指过来 / 证书未信任 / 改完没重启微信 | 逐项检查，用一篇文章做端到端验证 |
| `⏸ 微信要求验证` | 程序直连文章页太频繁 | 自动暂停 30 分钟重试；直存不受影响，继续点就行 |
| 一夜只抓了几十篇 | 屏幕锁定，点击全部落空 | 见"一次性设置"第 4 条 |
| 列表里有文章但程序拉不到 | 微信只给群发文章、客户端不走 HTTP 取列表 | 这就是为什么要点开 |
| 日志 `不在白名单，忽略` | 点开了清单外的号 | 想收就加进 accounts.json 后重启代理 |
| 想一次拿整个列表 | 本 skill 做不到 | 商业数据接口（按次计费）或向号主要授权导出 |

## 文件结构

```
mp-data/
├── SKILL.md / README.md
├── docs/codex-click-method.md      # Computer Use 点击规程（Codex 实跑记录）
└── scripts/
    ├── wechat_sniff.py             # mitmproxy addon：旁听、落盘、白名单
    ├── wechat_links.py             # sniffed / watch / file / albums / expand / external / auto
    ├── wechat_sogou.py             # 搜狗关键词采集 + 文章解析 + 存储（被前两者复用）
    ├── config.py                   # 归档目录 / 清单路径
    ├── accounts.example.json       # 公众号清单模板
    ├── build_index.py              # 旧索引生成器（INDEX.md），可选
    └── tests/                      # python3 -m unittest discover -s scripts/tests -t scripts
```

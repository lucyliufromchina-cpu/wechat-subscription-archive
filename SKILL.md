---
name: 抓订阅公众号
description: 抓取目标微信公众号的全量历史文章（标题/链接/发布时间/摘要/全文/图片），转存为本地 Markdown 归档。需登录自己的公众号后台（用其超链接接口搜索目标号）。
trigger: /抓订阅公众号
---

# 抓订阅公众号 Skill

通过登录自己的公众号后台 mp.weixin.qq.com，调用其内置的"超链接 → 引用其他公众号文章"接口，搜索目标公众号、拉取全量历史文章列表，再逐篇下载全文 HTML，解析为 Markdown 并下载图片到本地。

适用场景：行业研究 / 内容收藏 / 离线归档 (≤ 10 个目标号)。
不适用场景：抓取阅读量、点赞、在看（这些数据微信只对号主开放，需走逆向抓包或第三方付费 API）。

## 前置条件

```bash
pip3 install playwright requests beautifulsoup4 markdownify lxml
playwright install chromium
```

**你必须有自己的微信公众号**（订阅号免费注册即可），首次运行会弹出浏览器，用微信扫码登录到 mp.weixin.qq.com。登录态保存在 `~/.mp-data-browser/`。

## 使用方式

```bash
/抓订阅公众号 "极客公园"                       # 抓单个号的全量历史
/抓订阅公众号 "极客公园" --since 2025-01-01    # 只抓某日期之后的文章（增量）
/抓订阅公众号 --add "极客公园"                 # 添加目标号到配置（不抓取）
/抓订阅公众号 --list                          # 列出已配置的目标号
/抓订阅公众号 --all                           # 抓配置里所有号（自动跳过已抓的）
```

## 输出结构

```
~/wechat-archive/
├── <公众号A>/
│   ├── INDEX.md                              # 索引（按时间倒序，可点击跳转）
│   ├── 2025-04-20_文章标题/
│   │   ├── article.md                        # 正文 Markdown
│   │   ├── metadata.json                     # 元数据（链接、发布时间、摘要等）
│   │   └── images/
│   │       ├── 01.jpg
│   │       └── 02.jpg
│   └── 2025-04-15_另一篇标题/
│       └── ...
└── <公众号B>/...
```

## 执行流程

### Step 1: 解析参数

读取 `--add / --list / --all / --since` 等开关，决定执行路径。

### Step 2: 登录到自己的公众号后台

```bash
python3 ~/.claude/skills/mp-data/scripts/scrape.py "<目标公众号名>" [--since YYYY-MM-DD]
```

1. Playwright 启动 Chromium，加载 `~/.mp-data-browser/` 持久化用户目录
2. 打开 `https://mp.weixin.qq.com/`
3. 已登录 → 自动提取 token；未登录 → 等待扫码（最长 3 分钟）

### Step 3: 搜索目标公众号 → 拿 fakeid

调用接口：
```
GET https://mp.weixin.qq.com/cgi-bin/searchbiz
    ?action=search_biz&begin=0&count=5&query=<目标名>
    &token=<token>&lang=zh_CN&f=json&ajax=1
```

返回候选列表，自动取第一个匹配的 `fakeid`。如果有多个同名号，会列出让用户确认。

### Step 4: 分页拉取文章列表

```
GET https://mp.weixin.qq.com/cgi-bin/appmsg
    ?action=list_ex&begin=<begin>&count=5&fakeid=<fakeid>&type=9
    &query=&token=<token>&lang=zh_CN&f=json&ajax=1
```

每页返回 5 篇，循环直到 `app_msg_list` 为空或 `--since` 截止日期之前。

每篇包含：`aid` / `title` / `link` / `digest` / `cover` / `create_time` / `update_time`。

**频率限制**：mp 后台对 `appmsg` 接口有约 100 次/小时的限制，触发后脚本会等待 60 秒重试。

列表保存到 `~/wechat-archive/<公众号>/_list.json`。

### Step 5: 逐篇下载全文

```bash
python3 ~/.claude/skills/mp-data/scripts/download_full.py "<公众号名>"
```

对 `_list.json` 中每篇文章：
1. 检查目录是否已存在 → 已存在则跳过（增量友好）
2. `requests.get(link)` 拉公开 HTML（不需要登录）
3. BeautifulSoup 解析 `#js_content`
4. markdownify 转为 Markdown
5. 提取所有 `img[data-src]` 图片，下载到 `images/`
6. 替换 Markdown 里的图片引用为本地相对路径
7. 写 `article.md` + `metadata.json`

**频率限制**：每篇间隔 2 秒，避免触发反爬。

### Step 6: 生成索引

```bash
python3 ~/.claude/skills/mp-data/scripts/build_index.py "<公众号名>"
```

读取该公众号目录下所有 `metadata.json`，按发布时间倒序生成 `INDEX.md`，每条包含：
- 标题（点击跳转到 `article.md`）
- 发布日期
- 摘要前 100 字

## 故障排除

- **扫码超时**：重新运行命令
- **token 失效**：删除 `~/.mp-data-browser/` 重新登录
- **频率限制 (`freq control`)**：脚本自动等待重试，如频繁触发，建议第二天再跑
- **搜索不到目标号**：mp 后台的搜索是模糊匹配，尝试更精确的名字或带备案号关键词
- **某篇文章下载失败**：可能是该文章被作者删除或违规屏蔽，跳过即可
- **图片下载失败**：微信图床偶有限流，重新跑该号会跳过已下载的、补抓失败的

## 文件结构

```
mp-data/
├── SKILL.md
├── scripts/
│   ├── scrape.py          # 登录 + 搜索 + 拉文章列表
│   ├── download_full.py   # 下载全文 HTML → Markdown + 图片
│   ├── build_index.py     # 生成 INDEX.md
│   └── config.py          # 配置管理（目标号列表）
```

## 配置文件

`~/.config/抓订阅公众号/config.json`

```json
{
  "targets": ["极客公园", "晚点LatePost"],
  "archive_dir": "~/wechat-archive"
}
```

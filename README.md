# 抓订阅公众号 — Claude Code Skill

抓取你**订阅的他人微信公众号**的全量历史文章（标题、链接、发布时间、摘要、全文、图片），自动转存为本地 Markdown 归档。

适用场景：行业研究 / 内容收藏 / 离线阅读 / 个人知识库（≤ 10 个目标号）

> **不要混淆**：本工具抓的是 **别人** 运营的公众号，**不是** 你自己运营的公众号后台数据。原项目（fork 来源）是抓自己运营号的运营 KPI。

---

## 安装

```bash
# 1. clone
git clone https://github.com/lucyliufromchina-cpu/wechat-subscription-archive.git
cd wechat-subscription-archive

# 2. 装依赖
pip3 install playwright requests beautifulsoup4 markdownify lxml
playwright install chromium

# 3. 装到 Claude Code skills 目录（软链方式，方便后续改造）
ln -sf "$(pwd)" ~/.claude/skills/抓订阅公众号
```

或者直接复制粘贴到 Claude Code 里：

> 帮我安装抓订阅公众号 skill：从 https://github.com/lucyliufromchina-cpu/wechat-subscription-archive clone 下来，安装 playwright + chromium + requests + beautifulsoup4 + markdownify + lxml 依赖，然后软链到 `~/.claude/skills/抓订阅公众号`

---

## 前置条件

**你必须有自己的微信公众号**（订阅号免费注册即可）。这个工具会通过你后台的"超链接 → 引用其他公众号文章"接口去搜索目标公众号、拉取它的全量历史文章列表。

不需要订阅号也不要紧，注册一个空号也能用，全程不会用你的号发任何内容。

---

## 使用方式

```bash
# 在 Claude Code 中
/抓订阅公众号 "极客公园"                       # 抓单个号的全量历史
/抓订阅公众号 "极客公园" --since 2025-01-01    # 增量
/抓订阅公众号 --add "极客公园"                 # 添加到目标列表（不抓取）
/抓订阅公众号 --list                          # 列出已配置的目标号
/抓订阅公众号 --all                           # 抓配置里所有号

# 或直接命令行
python3 scripts/scrape.py "极客公园" --since 2025-04-01
python3 scripts/download_full.py "极客公园"
python3 scripts/build_index.py "极客公园"
```

首次运行会弹出浏览器，用微信扫码登录 mp.weixin.qq.com 后台。登录态保存在 `~/.mp-data-browser/`，后续无需重复扫码。

---

## 输出结构

```
~/wechat-archive/
├── <公众号A>/
│   ├── INDEX.md                              # 索引（按月分组、时间倒序、可点击）
│   ├── 2025-04-20_文章标题/
│   │   ├── article.md                        # 正文 Markdown（带 frontmatter）
│   │   ├── metadata.json                     # 元数据（链接、发布时间、摘要等）
│   │   └── images/
│   │       ├── 01.jpg
│   │       └── 02.jpg
│   └── 2025-04-15_另一篇标题/
│       └── ...
└── <公众号B>/...
```

推荐用 [Typora](https://typora.io) / [Obsidian](https://obsidian.md) / VSCode 打开 `~/wechat-archive/<公众号>/` 目录，体验最好（图片本地化、链接可跳转）。

---

## 工作原理

1. 通过 Playwright 启动 Chromium，登录 mp.weixin.qq.com 后台
2. 调用 `cgi-bin/searchbiz` 接口搜索目标公众号 → 拿 `fakeid`
3. 分页调用 `cgi-bin/appmsg` 接口拉文章列表（标题 / 链接 / 发布时间 / 摘要 / 封面）
4. 用 `requests` 拉每篇文章的公开 HTML 页（公众号文章是公开内容）
5. BeautifulSoup 解析 `#js_content`，markdownify 转 Markdown
6. 下载文章里所有图片到本地，替换正文里的图片路径

---

## 不能做什么 / 已知限制

- **拿不到阅读量、点赞、在看**：这些数据微信只对号主开放，需要走逆向抓包或第三方付费 API（新榜、清博等）
- **mp 后台接口有日额度**：约几百次/日，触发频控会自动指数退避（60s → 3min → 10min → 20min → 1h），最多 5 次
- **被作者删除/违规屏蔽的文章**：抓不到，会标记为 fail
- **不会抓你自己运营号的运营数据**：那是原项目（[Larkin0302/mp-data](https://github.com/Larkin0302/mp-data)）的功能

---

## 文件结构

```
.
├── SKILL.md                  # Claude Code skill 定义
├── scripts/
│   ├── test_login.py         # 登录测试（独立验证扫码登录）
│   ├── scrape.py             # 搜索公众号 + 拉文章列表
│   ├── download_full.py      # 下载全文 + 图片，转 Markdown
│   ├── build_index.py        # 生成 INDEX.md
│   └── config.py             # 配置管理（目标号列表）
└── README.md
```

---

## 致谢

本项目 fork 自 [Larkin0302/mp-data](https://github.com/Larkin0302/mp-data)。

原项目用于抓取**自己运营的公众号**后台运营数据（阅读量、互动率等 KPI），生成 HTML 数据看板。

本 fork 在原项目基础上重写了核心逻辑，转向另一个使用场景：**抓取自己订阅的他人公众号文章**，输出 Markdown 归档供个人研究和阅读。

复用了原项目的：
- Playwright + 持久化登录态架构
- mp.weixin.qq.com 扫码登录流程

重写的部分：
- 抓取目标：自己的发表记录页 → 别人的全量文章列表（searchbiz + appmsg API）
- 输出形式：HTML 数据看板 → 按月分组的 Markdown 归档 + 图片本地化
- 新增：增量抓取、目标号配置管理、INDEX 自动索引

感谢 [@Larkin0302](https://github.com/Larkin0302) 提供的优秀基础。

---

## License

MIT

# 抓订阅公众号 — Claude Code Skill

按主题抓取你**订阅的他人微信公众号**的历史文章（标题、链接、发布时间、全文、图片），转存为本地 Markdown 归档。

适用场景：行业研究 / 内容收藏 / 离线阅读 / 个人知识库

> **不要混淆**：本工具抓的是 **别人** 运营的公众号，**不是** 你自己运营的公众号后台数据。原项目（fork 来源）是抓自己运营号的运营 KPI。

---

## ⚠️ 2026-08 更新：方法已更换

**2026-07-30 起，微信关闭了公众号后台「超链接 → 引用其他公众号文章」背后的接口**：拉文章列表恒返回 `200013 freq control`，后台编辑器里的入口也已消失（订阅号、已认证服务号实测均如此）。同类最大的开源项目 [wechat-article-exporter](https://github.com/wechat-article/wechat-article-exporter/issues/200) 已因此停止维护。微信读书网页端的文章列表接口也已废弃，只能拿到最新一篇。

本 skill 现改用 **搜狗微信搜索「公众号名 + 主题关键词」**：

| 搜索方式 | 实测命中率 |
| --- | --- |
| 只搜公众号名 | 约 1%（搜的是正文提及，账号自己的文章被转载、引用淹没） |
| 公众号名 + 主题词 | 接近 100% |

代价：不能一次拉"全部"文章，要用一组关键词覆盖（每个查询最多 10 页 ≈ 100 条）；搜狗有防爬，需要慢速采集。

老脚本（`scrape.py` / `download_full.py` / `test_login.py`）保留作存档。

---

## 安装

```bash
git clone https://github.com/lucyliufromchina-cpu/wechat-subscription-archive.git
cd wechat-subscription-archive
ln -sf "$(pwd)" ~/.claude/skills/抓订阅公众号
```

**无第三方依赖**（Python 3.9+ 标准库），不需要公众号账号，不需要扫码。

---

## 使用方式

```bash
# 在 Claude Code 中
/抓订阅公众号 "菜花来了" --keywords 税,股权,社保

# 或直接命令行
python3 scripts/sogou_collect.py "菜花来了" --keywords 税,股权,社保 --max-requests 300
python3 scripts/build_index.py "菜花来了"
```

关键词建议：先放 1 个该号最常写的宽泛主题词，再加细分词；越靠前越优先。

常用参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--keywords` | 必填 | 逗号分隔的主题词 |
| `--max-requests` | 300 | 本次运行最多发多少次搜狗请求 |
| `--cooldown` | 7200 | 遇到验证码后暂停秒数 |
| `--max-cooldowns` | 3 | 本次运行最多暂停几次，超过即结束（重跑可续） |
| `--no-images` | 关 | 不下载图片 |

---

## 输出结构

```
~/wechat-archive/
└── <公众号>/
    ├── INDEX.md                      # 索引（按月分组、时间倒序、可点击）
    ├── _sogou_state.sqlite           # 断点状态（已完成的页、已下载的文章）
    └── 2025-04-20_文章标题/
        ├── article.md                # 正文 Markdown（带 frontmatter，图片本地引用）
        ├── metadata.json             # 标题、永久链接、发布时间、作者、命中关键词等
        └── images/01.jpg ...
```

推荐用 [Obsidian](https://obsidian.md) / [Typora](https://typora.io) / VSCode 打开 `~/wechat-archive/<公众号>/`。

---

## 工作原理

1. 搜狗微信文章搜索 `公众号名 关键词`，逐页解析结果，只保留署名与目标号完全一致的文章
2. 还原搜狗跳转页中用 JS 拼接的真实文章链接（临时链接，立即使用）
3. 下载公开的文章页，解析 `#js_content` 转 Markdown，图片下载到本地
4. 从文章页取 `__biz / mid / idx / sn`，生成永久链接并去重（同一篇被多个关键词搜到只下载一次；老方法抓过的也会识别）
5. 搜狗请求间隔 25–35 秒；遇到验证码暂停后重试，**不做任何验证码绕过**；可断点续跑

---

## 不能做什么 / 已知限制

- **拿不到某个号的完整文章列表**：只能按关键词覆盖
- **拿不到阅读量、点赞、在看**：仅号主可见
- **采集较慢**：一整套关键词通常要几小时，建议后台运行（`nohup caffeinate -i ...`）
- **被作者删除 / 违规屏蔽的文章**：抓不到

---

## 文件结构

```
.
├── SKILL.md                  # Claude Code skill 定义
├── scripts/
│   ├── sogou_collect.py      # 现行：搜狗「名字 + 关键词」采集
│   ├── build_index.py        # 生成 INDEX.md
│   ├── config.py             # 归档目录配置
│   ├── scrape.py             # 已失效（2026-07-30）：公众号后台引用接口
│   ├── download_full.py      # 已失效：依赖 scrape.py 的文章列表
│   └── test_login.py         # 已失效：后台扫码登录测试
└── README.md
```

---

## 致谢

本项目 fork 自 [Larkin0302/mp-data](https://github.com/Larkin0302/mp-data)。原项目用于抓取**自己运营的公众号**后台运营数据（阅读量、互动率等 KPI），生成 HTML 数据看板。本 fork 转向另一个使用场景：**抓取自己订阅的他人公众号文章**，输出 Markdown 归档供个人研究和阅读。

感谢 [@Larkin0302](https://github.com/Larkin0302) 提供的优秀基础。

---

## License

MIT

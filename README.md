# xszj-sh — 小说爬取 + 阅读一体化书架

针对 [xszj.org（小说之家）](https://xszj.org) 的前后端一体化小说爬取与阅读工具：
粘贴书籍链接即可后台爬取整本书，在浏览器里阅读并记忆进度，支持导出 TXT/EPUB、
站内搜索添加书籍、连载中书籍检查更新。

**技术栈**：Python 3.12 + FastAPI + SQLite（后端） / Vue3 本地单文件（前端，零构建）

> ⚠️ 仅供个人学习与备份自己阅读的内容，请控制爬取频率，尊重目标网站。

## 快速开始

```bash
# 1. 安装依赖（uv 会自动下载 Python 3.12）
uv sync

# 2. 启动
uv run main.py
#    → 浏览器打开 http://127.0.0.1:8000

# 3. 在页面顶部粘贴书籍链接添加，例如：
#    https://xszj.org/b/490683/
```

数据保存在 `data/novel.db`（SQLite 单文件，备份即拷贝）。

## 可选增强：Playwright 目录模式

网站的完整目录页 `/b/{id}/cs/N` 被 Cloudflare 人机验证保护。默认情况下程序使用
**顺序遍历模式**：从第一章沿"下一章"链接逐章爬取（纯 HTTP，已验证稳定，但进度
显示为"已入库 N 章"、串行较慢）。

安装可选的 Playwright 后，程序会**优先**用无头浏览器抓取完整目录：可知全书总章数
（百分比进度）、并发下载（默认 3 并发）、支持目录 diff 断点续爬。

```bash
uv sync --extra playwright
uv run playwright install chromium --no-shell
```

> ⚠️ 实测该站 Cloudflare 配置较严格，无头浏览器（含完整版 Chromium 新无头模式 +
> 反自动化指纹）仍可能被拦。此时自动降级为顺序遍历，不影响任何功能；且失败后
> `XSZJ_CATALOG_RETRY_HOURS`（默认 6 小时）冷却期内新任务直接跳过目录尝试，
> 不会每次白等几十秒。

## 站内搜索与验证码

搜索页 `/s/` 未被 Cloudflare 拦截，但有**站内图形验证码**：搜索时前端会显示验证码
图片（点击可刷新），输入后即可拿到结果并一键加入书架。无需任何 OCR 依赖。

## 开发/验证脚本

- `scripts/test_playwright.py` — 验证 Playwright 目录模式与搜索页可达性
- `scripts/test_frontend.py` — 无头浏览器加载各视图、捕获控制台错误、截图到 /tmp/xszj_ui/

## 功能一览

| 功能 | 说明 |
|---|---|
| 添加书籍 | 粘贴书籍页/章节页 URL，自动解析入库并开始后台爬取 |
| 站内搜索 | 顶栏"搜索添加"，按书名/作者/主角名搜索（搜索页若被 CF 拦截会自动尝试 Playwright） |
| 后台爬取 | 书架卡片实时进度（1s 轮询），可取消；服务重启后重新添加同书即断点续爬 |
| 在线阅读 | 章节列表 + 阅读页（字号可调），进度保存在服务端，书架"继续阅读"直达 |
| 检查更新 | 连载中书籍对比站内最新章节，增量爬取新章 |
| 导出 | 整本 TXT（UTF-8）或 EPUB3（含目录、封面页、内容简介） |

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `XSZJ_PORT` / `XSZJ_HOST` | 8000 / 127.0.0.1 | 服务监听 |
| `XSZJ_DELAY_MIN` / `XSZJ_DELAY_MAX` | 1.0 / 2.0 | 每次请求间的随机礼貌延时（秒） |
| `XSZJ_CONCURRENCY` | 3 | 目录模式并发下载章节数 |
| `XSZJ_CATALOG_RETRY_HOURS` | 6 | 目录模式失败冷却时长（小时内跳过目录尝试） |
| `XSZJ_MAX_RETRIES` | 3 | 网络错误重试次数（指数退避） |
| `XSZJ_DATA_DIR` | ./data | 数据库与导出目录 |
| `XSZJ_ORIGIN` | https://xszj.org | 目标站点（如换域名可改） |

## 项目结构

```
main.py                     # 入口
app/config.py               # 配置
app/db.py                   # SQLite schema + 查询
app/api/                    # FastAPI 路由：books / crawl / search / export
app/scraper/http.py         # httpx 封装（UA/重试/延时/CF 检测）
app/scraper/parser.py       # 页面解析（书籍页 og meta 优先，DOM 兜底）
app/scraper/catalog_playwright.py  # 可选：无头浏览器抓目录页
app/scraper/pipeline.py     # 爬取编排（catalog 并发 / sequential 降级）
app/scraper/tasks.py        # 任务状态机与注册表
app/exporter/               # TXT / EPUB3（zipfile 手写）
static/                     # Vue3 单页前端（本地 vendor，无 CDN 依赖）
```

## API 摘要

- `GET/POST /api/books` — 书架列表 / 添加书籍（`{url}`）
- `GET/DELETE /api/books/{id}` — 详情+目录 / 删除
- `GET /api/books/{id}/chapters/{cid}` — 章节正文（含前后章导航）
- `PUT /api/books/{id}/progress` — 更新阅读进度（`{cid}`）
- `POST /api/books/{id}/crawl` — 启动爬取（`{mode: auto|update}`）
- `GET /api/tasks/{id}` / `POST /api/tasks/{id}/cancel` — 任务进度 / 取消
- `GET /api/search?keyword=` — 站内搜索
- `GET /api/books/{id}/export?format=txt|epub` — 导出下载

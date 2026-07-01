# Daily News

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://github.com/CoderMrG/daily-news/actions/workflows/ci.yml/badge.svg)](https://github.com/CoderMrG/daily-news/actions/workflows/ci.yml)

Daily News 是一个个人技术社区情报工具。它通过 Agent-Reach / OpenCLI
采集 Reddit 和 X/Twitter 信号，筛选 AI 与开发工具相关内容，翻译并整理入选信息，
最终生成 Markdown 日报。

本项目不会自行实现 Reddit 或 X/Twitter 爬虫，所有采集能力均委托给
Agent-Reach 的上游工具。

## 功能

- 搜索 Reddit 主题、读取重点社区、帖子评论和讨论线程。
- 搜索 X/Twitter 主题、读取重点账号时间线和线程回复。
- 筛选 AI、LLM、Agent、开发工具、开源项目和 SaaS 相关信号。
- 通过 Anthropic-compatible API 调用 GLM 5.2 翻译。
- GLM 请求限速、限流退避和临时网络故障恢复。
- 生成每日技术社区情报日报。
- 生成高质量文章精选。
- 对 Reddit、X 和外部文章进行事件级及跨日去重。
- 控制内容时效性、来源多样性、讨论质量和输出长度。
- 翻译覆盖率门禁和 Markdown 原子写入。
- 使用 SQLite 保存运行记录、标准化来源、文章、翻译和报告历史。
- 可选同步到带有 Markdown frontmatter 的 Obsidian 仓库。
- 默认从 Git 中排除所有运行时数据。

## 环境要求

- Python 3.11+
- Agent-Reach
- OpenCLI，以及 Reddit 和 X/Twitter 的本地浏览器登录状态
- 使用 GLM 翻译时，需要 GLM/DashScope Anthropic-compatible API Key

检查 Agent-Reach 后端状态：

```bash
agent-reach doctor --json
```

## 安装配置

创建本地 Python 环境、配置文件和环境变量文件：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
cp config/daily_news.example.json config/daily_news.json
cp .env.example .env
```

在本机编辑 `.env` 和 `config/daily_news.json`。CLI 会自动读取 `.env`，
但不会覆盖 Shell 中已经导出的环境变量。不要提交这两个本地配置文件。

## 运行

```bash
python main.py
```

默认输出：

```text
data/reports/YYYY-MM-DD.md
data/articles/YYYY-MM-DD.md
data/db/daily_news.sqlite3
```

这些运行产物默认不会提交到 Git。

每次成功运行后，程序还会在以下目录创建 SQLite 备份：

```text
data/db/backups/YYYY-MM-DD.sqlite3
```

默认清理超过 14 天的备份。

所有运行路径均以项目目录为基准，不依赖启动命令时的工作目录。可以通过
`DAILY_NEWS_DATA_DIR` 指定其他绝对数据目录。原始采集归档默认保留 30 天，
计划任务日志达到 5 MB 后会自动轮转。

第一次启用 SQLite 时，程序会把已有 Markdown 日报和文章精选导入发布历史。
后续跨日去重优先读取 SQLite；仅当数据库不存在匹配历史时，才回退读取 Markdown。

使用某天已经归档的原始采集结果重新生成：

```bash
python main.py rerun --date 2026-06-26
```

常用运维命令：

```bash
python main.py status
python main.py health
python main.py health --date 2026-06-30
python main.py db stats
python main.py feedback --date 2026-06-29 --list
python main.py feedback \
  --date 2026-06-29 \
  --entry ENTRY_KEY \
  --type daily-report \
  --rating 有用 \
  --note "值得继续跟进"
```

## 每日自动运行

在 macOS 上安装每天 08:30 启动 Ghostty 的 LaunchAgent：

```bash
python main.py schedule install --hour 8 --minute 30
python main.py schedule status
python main.py schedule uninstall
```

用户必须保持登录状态，Agent-Reach 会继续使用本机保存的 Reddit 和 X 登录状态。
如果计划时间 Mac 正在睡眠，launchd 通常会在唤醒后执行任务。当天已有成功日报时，
程序会自动跳过重复运行。

日志位置：

```text
data/logs/scheduled.out.log
data/logs/scheduled.err.log
```

每次计划任务结束后，macOS 会显示简短的成功或失败通知。运行健康度包含采集、
Reddit、X、文章、翻译、渲染、发布和总耗时：

```bash
python main.py health
```

无人值守运行默认执行以下约束：

- 同一时间只允许一个 daily-news 进程运行。
- 整体运行时间预算为 30 分钟。
- 单个平台连续失败三次后触发熔断。
- Reddit 和 X 命令成功率均不得低于 50%。
- 至少解析出五条有效来源。
- 发布失败时恢复之前的 Markdown 文件。

可以通过 `.env.example` 中记录的环境变量调整这些参数。

## 完整测试

运行可重复的分层测试：

```bash
./scripts/full_test.sh offline
./scripts/full_test.sh cached
./scripts/full_test.sh live
./scripts/full_test.sh all
```

- `offline`：运行单元测试、编译检查、CLI 检查和 SQLite 检查。
- `cached`：在不访问网络的情况下，使用最近一次原始归档重新生成报告。
- `live`：使用有限样本验证 Agent-Reach、Reddit、X、GLM 5.2、SQLite 和
  Markdown 输出。
- `all`：依次执行所有测试配置。

测试生成过程在临时目录中完成，不会修改正式日报、正式数据库或 Obsidian。
测试报告保存到 `data/test-reports/`。

## 翻译

使用 Anthropic-compatible 路由时，默认模型为 `glm-5.2`。

示例：

```bash
DAILY_NEWS_TRANSLATION_PROVIDER=anthropic python main.py
```

相关环境变量：

```text
ANTHROPIC_AUTH_TOKEN
ANTHROPIC_BASE_URL
DAILY_NEWS_ANTHROPIC_MODEL
DAILY_NEWS_ANTHROPIC_DISABLE_THINKING
DAILY_NEWS_ANTHROPIC_MIN_REQUEST_INTERVAL_SECONDS
DAILY_NEWS_ANTHROPIC_REQUEST_RETRY_LIMIT
DAILY_NEWS_ANTHROPIC_RETRY_BASE_SECONDS
DAILY_NEWS_ANTHROPIC_RETRY_MAX_SECONDS
DAILY_NEWS_TRANSLATION_PROVIDER
```

默认情况下，GLM 请求至少间隔 5 秒。HTTP 429 会优先遵循服务端返回的
`Retry-After`，否则使用指数退避。持续限流时会停止后续批次，避免产生请求风暴。

## Obsidian

在 `config/daily_news.json` 中设置：

```json
{
  "obsidian_vault_dir": "/path/to/YourVault",
  "obsidian_subdir": "Daily News"
}
```

之后每次运行还会写入：

```text
YourVault/Daily News/reports/YYYY-MM-DD.md
YourVault/Daily News/articles/YYYY-MM-DD.md
YourVault/Daily News/reviews/YYYY-MM-DD.md
```

重新生成日报时，评价文档会更新，但会保留已有的结构化评价和备注。把
`评价：待评价` 修改为 `有用`、`一般`、`无用` 或 `跟进`，还可以填写
`备注`。下一次运行或执行 `python main.py status` 时，程序会把反馈同步到
SQLite。

## 七日质量观察

建议在最初七天观察期内保持筛选阈值不变。每次成功运行都会向 SQLite 写入质量快照。
查看观察进度：

```bash
python main.py status
```

状态信息包括连续成功天数、观察天数、反馈汇总、议题数量、X 信号数量、讨论深度、
文章读取成功率和去重数量。

## 数据策略

仓库只应包含代码、示例配置、文档和测试。

以下内容属于本机运行数据，默认被 Git 忽略：

- `data/raw/`
- `data/reports/`
- `data/articles/`
- `data/db/`
- `data/cache/`
- `.env`
- Cookie 和登录凭据

## 质量规则

默认筛选规则有意保持严格：

- Reddit 日报主题最多允许两天时效。
- X/Twitter 日报信号最多允许三天时效。
- 外部文章最多允许七天时效。
- 每期日报最多选择五条 X/Twitter 信号。
- 每期文章精选最多五篇，同一发布方最多两篇。
- 同一个社交平台发布事件只生成一个主条目，论文、博客和仓库作为关联链接附加。
- 只有成功读取正文的文章才能标记为“必读”。
- 低信息量回应、求助帖和账号问题不会进入代表性讨论。
- 当翻译失败率超过配置阈值时，不覆盖已有报告。

## 当前架构

CLI 入口调用一个小型 Python 包：

```text
daily_news/
  app.py       采集、筛选、翻译、渲染和主流程编排
  cli.py       命令行入口和运维命令
  full_test.py 隔离的 offline、cached 和 live 测试运行器
  models.py    公共数据模型
  observability.py 运行指标、健康摘要和系统通知
  reviews.py   Obsidian 评价文档和反馈同步
  runtime.py   运行锁、时间预算、熔断和发布回滚
  scheduler.py macOS LaunchAgent 集成
  settings.py  运行配置和本地配置
  storage.py   SQLite Schema 迁移和持久化
  utils.py     解析和标准化工具
```

SQLite 保存运行状态、标准化来源、文章正文、翻译、已发布 Markdown 版本、
报告条目、质量快照、读者反馈和每次运行的健康指标。

Markdown 仍然是面向阅读和 Obsidian 的最终输出。

建议在七日观察期内保持筛选阈值稳定。观察结束后的下一个结构优化方向，是把筛选、
翻译和渲染逻辑从 `app.py` 拆分出来。

## 开源安全

- 不要把 Cookie、Token、账号密码写入代码或提交到 Git。
- `.env`、本地配置、原始采集数据和数据库默认不提交。
- X/Twitter 建议使用独立小号，不要使用主账号。
- 本项目只读取公开内容，不执行发帖、评论、点赞或关注。

## License

MIT

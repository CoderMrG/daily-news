# AGENTS.md

## 项目目标

这是一个个人使用的每日技术社区情报工具。

本项目必须基于 Agent-Reach 实现采集能力，不要自己从零写 Reddit 或 X/Twitter 爬虫。

目标：
- 使用 Agent-Reach 安装和配置 Reddit、X/Twitter、Web、GitHub 等读取能力
- 每天抓取 AI、LLM、Agent、开发工具、开源项目、SaaS、独立开发、赚钱机会相关内容
- 抓取帖子/推文正文以及用户讨论、评论、回复
- 过滤娱乐、八卦、政治、低质量营销内容
- 生成 Markdown 日报

## 第一阶段范围

第一阶段只做：
1. 安装并验证 Agent-Reach
2. 配置 Reddit 和 X/Twitter 渠道
3. 通过 Agent-Reach 已安装的上游工具手动跑通一次搜索/读取
4. 写一个最小 daily_report 脚本，调用这些上游命令并保存结果
5. 生成 data/reports/YYYY-MM-DD.md

第一阶段不要做：
- 不要自己实现 Reddit API / PRAW 抓取
- 不要自己实现 Playwright X 爬虫
- 不要做 MCP
- 不要做 Web 后台
- 不要做数据库
- 不要做 Docker
- 不要做代理池
- 不要自动发帖、点赞、评论、关注

## 安全要求

- 不要把 Cookie、Token、账号密码写入代码
- Cookie 和登录凭据只保存在本机
- 不要打印敏感凭据
- 不要提交 .env、Cookie、data/raw 到 git
- X/Twitter 建议使用小号，不要使用主账号

## 输出

最终希望运行：

```bash
python main.py
```

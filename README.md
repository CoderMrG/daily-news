# Daily News

Daily News is a personal technical community intelligence tool. It collects Reddit and X/Twitter signals through Agent-Reach / OpenCLI, filters for AI and developer-tool relevance, translates and summarizes selected content, and writes Markdown reports.

The project intentionally does not implement its own Reddit or X/Twitter crawler. Collection is delegated to Agent-Reach upstream tools.

## Features

- Reddit topic search, subreddit reads, post comments, and discussion threads.
- X/Twitter topic search, account timeline reads, and thread replies.
- AI / LLM / Agent / developer-tool / open-source / SaaS signal filtering.
- GLM 5.2 translation through an Anthropic-compatible API.
- Daily report output.
- High-quality article digest output.
- Event-level and cross-day deduplication for Reddit, X, and linked articles.
- Freshness, source-diversity, discussion-quality, and output-length limits.
- Translation coverage checks and atomic Markdown writes.
- Optional Obsidian vault sync with Markdown frontmatter.
- Runtime data excluded from git by default.

## Requirements

- Python 3.11+
- Agent-Reach
- OpenCLI with browser login state for Reddit and X/Twitter
- A GLM/DashScope Anthropic-compatible API key if using GLM translation

Check Agent-Reach backend status:

```bash
agent-reach doctor --json
```

## Setup

Create local config and environment files:

```bash
cp config/daily_news.example.json config/daily_news.json
cp .env.example .env
```

Edit `.env` and `config/daily_news.json` locally. Do not commit either file.

## Run

```bash
python main.py
```

Outputs are written to:

```text
data/reports/YYYY-MM-DD.md
data/articles/YYYY-MM-DD.md
```

These runtime outputs are ignored by git.

To regenerate from cached raw command output for a date:

```bash
DAILY_NEWS_DATE=2026-06-26 DAILY_NEWS_FROM_RAW=1 python main.py
```

## Translation

The default model is `glm-5.2` when using the Anthropic-compatible route.

Example:

```bash
DAILY_NEWS_TRANSLATION_PROVIDER=anthropic python main.py
```

Relevant environment variables:

```text
ANTHROPIC_AUTH_TOKEN
ANTHROPIC_BASE_URL
DAILY_NEWS_ANTHROPIC_MODEL
DAILY_NEWS_ANTHROPIC_DISABLE_THINKING
DAILY_NEWS_TRANSLATION_PROVIDER
```

## Obsidian

Set these in `config/daily_news.json`:

```json
{
  "obsidian_vault_dir": "/path/to/YourVault",
  "obsidian_subdir": "Daily News"
}
```

Then every run also writes:

```text
YourVault/Daily News/reports/YYYY-MM-DD.md
YourVault/Daily News/articles/YYYY-MM-DD.md
```

## Data Policy

The repository should contain code, example configuration, documentation, and tests only.

The following are local runtime data and are ignored:

- `data/raw/`
- `data/reports/`
- `data/articles/`
- `data/db/`
- `data/cache/`
- `.env`
- cookies and login credentials

## Quality Rules

Default report selection is intentionally strict:

- Reddit daily topics: up to 2 days old.
- X/Twitter daily signals: up to 3 days old.
- Linked articles: up to 7 days old.
- X/Twitter signals: up to 5 per report.
- Article digest: up to 5 articles, with at most 2 from one publisher.
- One social release event produces one primary entry; papers, blogs, and repositories are attached as related links.
- "Must read" requires successfully fetched article content.
- Low-information reactions, support requests, and account issues are excluded from representative discussions.
- A report is not overwritten when translation failures exceed the configured threshold.

## Current Architecture

The CLI entrypoint delegates to a small package:

```text
daily_news/
  app.py       collection, filtering, translation, rendering, orchestration
  models.py    shared data models
  settings.py  runtime and local configuration
  utils.py     parsing and normalization helpers
```

The next engineering step is to split the remaining pipeline boundaries and move durable state to SQLite. Markdown remains the rendered output for reading and Obsidian.

## License

MIT

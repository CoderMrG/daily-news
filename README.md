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
- SQLite persistence for runs, normalized sources, articles, translations, and report history.
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
data/db/daily_news.sqlite3
```

These runtime outputs are ignored by git.

On the first SQLite-enabled run, existing Markdown reports and article digests are
imported as publication history. Future history deduplication reads SQLite first
and falls back to Markdown only when the database has no matching history.

To regenerate from cached raw command output for a date:

```bash
python main.py rerun --date 2026-06-26
```

Operational commands:

```bash
python main.py status
python main.py db stats
python main.py feedback --date 2026-06-29 --list
python main.py feedback \
  --date 2026-06-29 \
  --entry ENTRY_KEY \
  --type daily-report \
  --rating 有用 \
  --note "值得继续跟进"
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
YourVault/Daily News/reviews/YYYY-MM-DD.md
```

Review notes are refreshed on reruns while preserving existing structured
ratings and notes. Change `评价：待评价` to one of `有用`, `一般`, `无用`, or
`跟进`, and optionally fill in `备注`. The next run or
`python main.py status` imports the feedback into SQLite.

## Seven-day Quality Review

The current filtering thresholds should remain unchanged during the initial
seven-day observation period. Each successful run records a quality snapshot
in SQLite. Check progress with:

```bash
python main.py status
```

The status output includes the current streak, observation-day count, feedback
summary, topic count, X signal count, discussion depth, article fetch rate, and
deduplication count.

## Data Policy

The repository should contain code, example configuration, documentation, and tests only.

The following are local runtime data and are ignored:

- `data/raw/`
- `data/reports/`
- `data/articles/`
- `data/db/`
- `data/cache/`
- `data/db/`
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
  storage.py   SQLite schema migrations and persistence
  utils.py     parsing and normalization helpers
```

SQLite stores run status, normalized source items, article bodies, translations,
published Markdown versions, report entries, quality snapshots, and reader feedback.
Markdown remains the rendered output for reading and Obsidian.

The next engineering step is to split filtering, translation, and rendering out
of `app.py`, then add commands for database inspection and feedback capture.

## License

MIT

# Daily News

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://github.com/CoderMrG/daily-news/actions/workflows/ci.yml/badge.svg)](https://github.com/CoderMrG/daily-news/actions/workflows/ci.yml)

Daily News is a personal technical community intelligence tool. It collects Reddit and X/Twitter signals through Agent-Reach / OpenCLI, filters for AI and developer-tool relevance, translates and summarizes selected content, and writes Markdown reports.

The project intentionally does not implement its own Reddit or X/Twitter crawler. Collection is delegated to Agent-Reach upstream tools.

## Features

- Reddit topic search, subreddit reads, post comments, and discussion threads.
- X/Twitter topic search, account timeline reads, and thread replies.
- AI / LLM / Agent / developer-tool / open-source / SaaS signal filtering.
- GLM 5.2 translation through an Anthropic-compatible API.
- GLM request pacing, rate-limit backoff, and transient network recovery.
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
python3.11 -m venv .venv
source .venv/bin/activate
cp config/daily_news.example.json config/daily_news.json
cp .env.example .env
```

Edit `.env` and `config/daily_news.json` locally. The CLI loads `.env` without
overriding variables already exported by the shell. Do not commit either file.

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

Each successful run also creates a SQLite backup under
`data/db/backups/YYYY-MM-DD.sqlite3`. Backups older than 14 days are removed by
default.

Runtime paths are anchored to the project directory, independent of the shell
working directory. Set `DAILY_NEWS_DATA_DIR` to use another absolute data
directory. Raw archives are retained for 30 days by default, and scheduled logs
rotate at 5 MB.

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

## Daily Schedule

On macOS, install a LaunchAgent that starts Ghostty at 08:30 every day:

```bash
python main.py schedule install --hour 8 --minute 30
python main.py schedule status
python main.py schedule uninstall
```

The user must be logged in, and Agent-Reach keeps using the local Reddit and X
login state. If the Mac is asleep at the scheduled time, launchd runs the task
after wake. A successful report for the current day is skipped automatically.
Logs are written to `data/logs/scheduled.out.log` and
`data/logs/scheduled.err.log`.
After each scheduled run, macOS shows a concise success or failure notification.
Detailed runtime health includes collection, Reddit, X, article, translation,
rendering, publication, and total duration metrics:

```bash
python main.py health
```

Unattended runs use the project `.venv` directly and enforce:

- one active daily-news process at a time;
- a 30-minute whole-run budget;
- a circuit breaker after three consecutive failures per platform;
- at least 50% successful Reddit and X commands;
- at least five parsed source items;
- rollback to the previous Markdown files if publication fails.

These values can be adjusted through the environment variables documented in
`.env.example`.

## Full Test

Run the repeatable test profiles:

```bash
./scripts/full_test.sh offline
./scripts/full_test.sh cached
./scripts/full_test.sh live
./scripts/full_test.sh all
```

`offline` runs unit, compile, CLI, and SQLite checks. `cached` regenerates the
latest archived date without network access. `live` uses a limited sample to
verify Agent-Reach, Reddit, X, GLM 5.2 translation, SQLite, and Markdown output.
All generation happens in a temporary directory and does not update production
reports, the production database, or Obsidian. Test reports are written to
`data/test-reports/`.

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
DAILY_NEWS_ANTHROPIC_MIN_REQUEST_INTERVAL_SECONDS
DAILY_NEWS_ANTHROPIC_REQUEST_RETRY_LIMIT
DAILY_NEWS_ANTHROPIC_RETRY_BASE_SECONDS
DAILY_NEWS_ANTHROPIC_RETRY_MAX_SECONDS
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
  cli.py       command-line interface and operational commands
  full_test.py isolated offline, cached, and live test runner
  models.py    shared data models
  observability.py runtime metrics, health summaries, and notifications
  reviews.py   Obsidian feedback notes and feedback sync
  runtime.py   run lock, deadline, circuit breaker, and publication rollback
  scheduler.py macOS LaunchAgent integration
  settings.py  runtime and local configuration
  storage.py   SQLite schema migrations and persistence
  utils.py     parsing and normalization helpers
```

SQLite stores run status, normalized source items, article bodies, translations,
published Markdown versions, report entries, quality snapshots, reader feedback,
and per-run health metrics.
Markdown remains the rendered output for reading and Obsidian.

Filtering thresholds should remain stable during the seven-day observation
period. The next structural step after that review is to split filtering,
translation, and rendering out of `app.py`.

## License

MIT

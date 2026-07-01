"""Configuration and runtime settings for daily-news."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_env_file(path: Path | None = None) -> None:
    target = path or PROJECT_ROOT / ".env"
    if not target.exists():
        return
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError(f"Could not read environment file {target}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" not in stripped:
            raise RuntimeError(f"Invalid .env line {line_number}: expected KEY=VALUE")
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            raise RuntimeError(f"Invalid .env key on line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file()

DATA_DIR = project_path(os.environ.get("DAILY_NEWS_DATA_DIR", "data"))
REPORT_DIR = DATA_DIR / "reports"
ARTICLE_DIR = DATA_DIR / "articles"
REVIEW_DIR = DATA_DIR / "reviews"
RAW_DIR = DATA_DIR / "raw"
RUN_DIR = DATA_DIR / "run"
RUN_LOCK_PATH = RUN_DIR / "daily-news.lock"
CONFIG_PATH = project_path(
    os.environ.get("DAILY_NEWS_CONFIG", "config/daily_news.json")
)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Could not load config {CONFIG_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid config {CONFIG_PATH}: top-level value must be an object")
    return data


def config_list(name: str, default: Iterable[str]) -> list[str]:
    value = PROJECT_CONFIG.get(name)
    if value is None:
        return list(default)
    if not isinstance(value, list):
        raise RuntimeError(f"Invalid config {name}: expected a list")
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned or list(default)


def config_set(name: str, default: Iterable[str]) -> set[str]:
    return {item.lower() for item in config_list(name, default)}


def config_str(name: str, default: str = "") -> str:
    value = PROJECT_CONFIG.get(name)
    if value is None:
        return default
    if not isinstance(value, (str, int, float)):
        raise RuntimeError(f"Invalid config {name}: expected a scalar value")
    return str(value).strip()


def config_int(name: str, default: int) -> int:
    value = PROJECT_CONFIG.get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        raise RuntimeError(f"Invalid config {name}: expected an integer")
    if isinstance(value, int):
        if value < 1:
            raise RuntimeError(f"Invalid config {name}: expected a positive integer")
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise RuntimeError(f"Invalid config {name}: expected an integer") from exc
        if parsed < 1:
            raise RuntimeError(f"Invalid config {name}: expected a positive integer")
        return parsed
    raise RuntimeError(f"Invalid config {name}: expected an integer")

TOPICS = [
    "openai",
    "AI agents developer tools",
    "open source LLM",
    "local LLM inference",
    "AI coding agents",
    "AI devtools",
    "AI SaaS indie hackers",
    "LLM model release benchmark",
    "Claude Code",
    "Cursor AI",
    "Windsurf AI",
    "Codex CLI",
    "OpenAI Codex",
    "Gemini CLI",
    "Qwen coding",
    "DeepSeek",
    "vLLM",
    "Ollama",
    "llama.cpp",
    "MCP server",
    "AI agent workflow",
    "AI code review",
    "AI pull request",
    "LLM inference cost",
    "local AI assistant",
    "agent tool use",
    "developer productivity AI",
]

X_TOPICS = [
    "Claude Code",
    "AI agents developer tools",
    "OpenAI Codex",
    "Cursor AI",
    "Windsurf AI",
    "Qwen coding",
    "vLLM",
    "Ollama",
    "MCP server",
    "AI pull request",
]

X_ACCOUNTS = [
    "OpenAI",
    "AnthropicAI",
    "GoogleDeepMind",
    "Alibaba_Qwen",
    "cursor_ai",
    "windsurf_ai",
    "huggingface",
    "ollama",
    "vllm_project",
    "MistralAI",
]

X_QUALITY_AUTHORS = {
    "openai",
    "anthropicai",
    "googledeepmind",
    "googleai",
    "metaai",
    "mistralai",
    "alibaba_qwen",
    "cursor_ai",
    "windsurf_ai",
    "huggingface",
    "ollama",
    "vllm_project",
    "simonw",
    "karpathy",
    "swyx",
    "hardmaru",
}

REDDIT_SUBREDDITS = [
    "LocalLLaMA",
    "OpenAI",
    "ClaudeAI",
    "SaaS",
    "indiehackers",
]

INCLUDE_TERMS = [
    "ai",
    "llm",
    "agent",
    "agents",
    "openai",
    "anthropic",
    "claude",
    "codex",
    "gpt",
    "sora",
    "api",
    "coding",
    "model",
    "inference",
    "tts",
    "developer",
    "tool",
    "open source",
    "local",
    "huggingface",
    "saas",
    "indie",
    "startup",
    "devday",
    "gemini",
    "deepseek",
    "qwen",
    "cursor",
    "windsurf",
    "claude code",
    "mcp",
    "vllm",
    "ollama",
    "llama.cpp",
    "gemma",
    "mistral",
    "perplexity",
    "langchain",
    "aider",
    "opencode",
    "litellm",
]

ENTITY_TERMS = [
    "anthropic",
    "claude",
    "claude code",
    "codex",
    "cursor",
    "windsurf",
    "gemini",
    "deepseek",
    "qwen",
    "llama.cpp",
    "vllm",
    "ollama",
    "mcp",
    "gemma",
    "mistral",
    "perplexity",
    "huggingface",
    "langchain",
    "aider",
    "opencode",
    "litellm",
]

EXCLUDE_TERMS = [
    "celebrity",
    "gossip",
    "election",
    "politics",
    "meme",
    "nsfw",
    "giveaway",
    "airdrop",
]

EXCLUDE_SUBREDDITS = {
    "r/meirl",
    "r/peterexplainsthejoke",
    "r/blackpeopletwitter",
    "r/funny",
    "r/memes",
    "r/pics",
    "r/chatgptcomplaints",
}

HIGH_RELEVANCE_SUBREDDITS = {
    "r/localllama",
    "r/openai",
    "r/claudeai",
    "r/chatgptcoding",
    "r/cursor",
    "r/saas",
    "r/indiehackers",
}

ARTICLE_QUALITY_DOMAINS = {
    "anthropic.com",
    "openai.com",
    "ai.google.dev",
    "deepmind.google",
    "qwenlm.github.io",
    "huggingface.co",
    "github.com",
    "arxiv.org",
    "semianalysis.com",
    "simonwillison.net",
    "www.latent.space",
    "www.wheresyoured.at",
    "arstechnica.com",
}

ARTICLE_BLOCKED_DOMAINS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "x.com",
    "twitter.com",
    "mobile.twitter.com",
    "i.redd.it",
    "preview.redd.it",
    "pbs.twimg.com",
    "video.twimg.com",
    "platform.twitter.com",
    "claude.md",
}

PROJECT_CONFIG = load_config()
_configured_database_path = (
    os.environ.get("DAILY_NEWS_DB_PATH")
    or config_str("database_path")
)
DB_PATH = (
    project_path(_configured_database_path)
    if _configured_database_path
    else DATA_DIR / "db" / "daily_news.sqlite3"
)
TOPICS = config_list("topics", TOPICS)
X_TOPICS = config_list("x_topics", X_TOPICS)
X_ACCOUNTS = config_list("x_accounts", X_ACCOUNTS)
X_QUALITY_AUTHORS = config_set("x_quality_authors", X_QUALITY_AUTHORS)
REDDIT_SUBREDDITS = config_list("reddit_subreddits", REDDIT_SUBREDDITS)
INCLUDE_TERMS = config_list("include_terms", INCLUDE_TERMS)
ENTITY_TERMS = config_list("entity_terms", ENTITY_TERMS)
EXCLUDE_TERMS = config_list("exclude_terms", EXCLUDE_TERMS)
EXCLUDE_SUBREDDITS = config_set("exclude_subreddits", EXCLUDE_SUBREDDITS)
HIGH_RELEVANCE_SUBREDDITS = config_set("high_relevance_subreddits", HIGH_RELEVANCE_SUBREDDITS)
ARTICLE_QUALITY_DOMAINS = config_set("article_quality_domains", ARTICLE_QUALITY_DOMAINS)
ARTICLE_BLOCKED_DOMAINS = config_set("article_blocked_domains", ARTICLE_BLOCKED_DOMAINS)
OBSIDIAN_VAULT_DIR = os.environ.get("DAILY_NEWS_OBSIDIAN_VAULT_DIR") or config_str("obsidian_vault_dir")
OBSIDIAN_SUBDIR = os.environ.get("DAILY_NEWS_OBSIDIAN_SUBDIR") or config_str("obsidian_subdir", "Daily News")


def env_int(
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {name}: expected an integer") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        expected = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise RuntimeError(f"Invalid {name}: expected {expected}")
    return parsed


def env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid {name}: expected a boolean value")


LIMIT = env_int("DAILY_NEWS_LIMIT", 8)
READ_LIMIT = env_int("DAILY_NEWS_READ_LIMIT", 20)
TOPIC_COUNT = env_int("DAILY_NEWS_TOPIC_COUNT", min(len(TOPICS), 18))
SUBREDDIT_COUNT = env_int("DAILY_NEWS_SUBREDDIT_COUNT", len(REDDIT_SUBREDDITS))
X_LIMIT = env_int("DAILY_NEWS_X_LIMIT", 5)
X_TOPIC_COUNT = env_int("DAILY_NEWS_X_TOPIC_COUNT", min(len(X_TOPICS), 8))
X_ACCOUNT_COUNT = env_int("DAILY_NEWS_X_ACCOUNT_COUNT", min(len(X_ACCOUNTS), 8))
X_THREAD_READ_LIMIT = env_int("DAILY_NEWS_X_THREAD_READ_LIMIT", 8)
X_THREAD_REPLY_LIMIT = env_int("DAILY_NEWS_X_THREAD_REPLY_LIMIT", 2)
X_SIGNAL_LIMIT = env_int("DAILY_NEWS_X_SIGNAL_LIMIT", config_int("x_signal_limit", 5))
ARTICLE_LIMIT = env_int("DAILY_NEWS_ARTICLE_LIMIT", config_int("article_limit", 5))
ARTICLE_FETCH_LIMIT = env_int("DAILY_NEWS_ARTICLE_FETCH_LIMIT", config_int("article_fetch_limit", 8))
ARTICLE_CANDIDATE_LIMIT = env_int(
    "DAILY_NEWS_ARTICLE_CANDIDATE_LIMIT",
    config_int("article_candidate_limit", 20),
)
ARTICLE_MIN_SCORE = env_int("DAILY_NEWS_ARTICLE_MIN_SCORE", config_int("article_min_score", 80))
ARTICLE_AUTHOR_LIMIT = env_int(
    "DAILY_NEWS_ARTICLE_AUTHOR_LIMIT",
    config_int("article_author_limit", 2),
)
ARTICLE_FRESHNESS_DAYS = env_int(
    "DAILY_NEWS_ARTICLE_FRESHNESS_DAYS",
    config_int("article_freshness_days", 7),
)
ARTICLE_FETCH_ENABLED = env_flag("DAILY_NEWS_ARTICLE_FETCH", True)
REDDIT_FRESHNESS_DAYS = env_int("DAILY_NEWS_REDDIT_FRESHNESS_DAYS", 2)
X_FRESHNESS_DAYS = env_int("DAILY_NEWS_X_FRESHNESS_DAYS", 3)
FRESHNESS_FUTURE_GRACE_DAYS = env_int("DAILY_NEWS_FRESHNESS_FUTURE_GRACE_DAYS", 3)
TIMEOUT_SECONDS = env_int("DAILY_NEWS_TIMEOUT", 120)
GOOGLE_TIMEOUT_SECONDS = env_int("DAILY_NEWS_GOOGLE_TIMEOUT", 20)
REPORT_ITEM_LIMIT = env_int("DAILY_NEWS_REPORT_ITEM_LIMIT", 12)
FOCUS_GROUP_LIMIT = env_int("DAILY_NEWS_FOCUS_GROUP_LIMIT", 4)
SHORT_ITEM_LIMIT = env_int("DAILY_NEWS_SHORT_ITEM_LIMIT", 5)
REQUIRE_HOT_DISCUSSION = env_flag("DAILY_NEWS_REQUIRE_HOT_DISCUSSION", True)
HOT_ITEM_SCORE_MIN = env_int("DAILY_NEWS_HOT_ITEM_SCORE_MIN", 500)
HOT_ITEM_COMMENT_MIN = env_int("DAILY_NEWS_HOT_ITEM_COMMENT_MIN", 150)
HIGH_RELEVANCE_SUBREDDIT_COMMENT_MIN = env_int("DAILY_NEWS_HIGH_RELEVANCE_SUBREDDIT_COMMENT_MIN", 50)
HISTORY_DEDUP_DAYS = env_int("DAILY_NEWS_HISTORY_DEDUP_DAYS", 7)
HISTORY_CONTINUATION_LIMIT = env_int("DAILY_NEWS_HISTORY_CONTINUATION_LIMIT", 3)
HISTORY_CONTINUATION_COMMENT_MIN = env_int("DAILY_NEWS_HISTORY_CONTINUATION_COMMENT_MIN", 100)
HISTORY_CONTINUATION_SCORE_MIN = env_int("DAILY_NEWS_HISTORY_CONTINUATION_SCORE_MIN", 500)
DISCUSSION_TEXT_WIDTH = env_int("DAILY_NEWS_DISCUSSION_TEXT_WIDTH", 280)
REPLY_TEXT_WIDTH = env_int("DAILY_NEWS_REPLY_TEXT_WIDTH", 220)
SOURCE_SUMMARY_WIDTH = env_int("DAILY_NEWS_SOURCE_SUMMARY_WIDTH", 280)
DISCUSSION_RAW_WIDTH = env_int("DAILY_NEWS_DISCUSSION_RAW_WIDTH", 5000)
DISCUSSION_TRANSLATION_WIDTH = env_int("DAILY_NEWS_DISCUSSION_TRANSLATION_WIDTH", 5000)
ANTHROPIC_BATCH_SIZE = env_int("DAILY_NEWS_ANTHROPIC_BATCH_SIZE", 3)
ANTHROPIC_RETRY_LIMIT = env_int("DAILY_NEWS_ANTHROPIC_RETRY_LIMIT", 2)
HYBRID_GLM_ITEM_LIMIT = env_int("DAILY_NEWS_HYBRID_GLM_ITEM_LIMIT", 8)
ANTHROPIC_CACHE_VERSION = "v3"
THREAD_LIMIT = env_int("DAILY_NEWS_THREAD_LIMIT", 3)
THREAD_REPLY_LIMIT = env_int("DAILY_NEWS_THREAD_REPLY_LIMIT", 2)
THREAD_MIN_SCORE = env_int("DAILY_NEWS_THREAD_MIN_SCORE", 5)
LOW_SCORE_THREAD_MIN_LENGTH = env_int("DAILY_NEWS_LOW_SCORE_THREAD_MIN_LENGTH", 700)
LOW_SCORE_THREAD_LIMIT = env_int(
    "DAILY_NEWS_LOW_SCORE_THREAD_LIMIT",
    0,
    minimum=0,
)
INCLUDE_TWITTER = env_flag("DAILY_NEWS_INCLUDE_TWITTER", True)
READ_FETCH_LIMIT = env_int("DAILY_NEWS_READ_FETCH_LIMIT", 10)
READ_REPLIES = env_int("DAILY_NEWS_READ_REPLIES", 5)
USE_LLM = env_flag("DAILY_NEWS_USE_LLM", bool(os.environ.get("OPENAI_API_KEY")))
OPENAI_MODEL = os.environ.get("DAILY_NEWS_OPENAI_MODEL", "gpt-4.1-mini")
ANTHROPIC_MODEL = os.environ.get(
    "DAILY_NEWS_ANTHROPIC_MODEL",
    os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", os.environ.get("MODEL", "glm-5.2")),
)
ANTHROPIC_BASE_URL = (os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("BASE_URL", "")).rstrip("/")
ANTHROPIC_DISABLE_THINKING = env_flag("DAILY_NEWS_ANTHROPIC_DISABLE_THINKING", True)
FROM_RAW = env_flag("DAILY_NEWS_FROM_RAW", False)
TRANSLATION_PROVIDER = os.environ.get("DAILY_NEWS_TRANSLATION_PROVIDER", "auto").strip().lower()
_translation_providers = {
    "auto",
    "openai",
    "google",
    "anthropic",
    "glm",
    "none",
    "hybrid",
    "glm-hybrid",
    "anthropic-hybrid",
}
if TRANSLATION_PROVIDER not in _translation_providers:
    raise RuntimeError(
        "Invalid DAILY_NEWS_TRANSLATION_PROVIDER: "
        f"expected one of {', '.join(sorted(_translation_providers))}"
    )
TRANSLATION_MAX_FAILURE_PERCENT = env_int(
    "DAILY_NEWS_TRANSLATION_MAX_FAILURE_PERCENT",
    20,
    maximum=100,
)
RUN_TIMEOUT_SECONDS = env_int("DAILY_NEWS_RUN_TIMEOUT", 1800)
COLLECTION_FAILURE_LIMIT = env_int("DAILY_NEWS_COLLECTION_FAILURE_LIMIT", 3)
COLLECTION_MIN_SUCCESS_PERCENT = env_int(
    "DAILY_NEWS_COLLECTION_MIN_SUCCESS_PERCENT",
    50,
    maximum=100,
)
COLLECTION_MIN_SOURCE_ITEMS = env_int(
    "DAILY_NEWS_COLLECTION_MIN_SOURCE_ITEMS",
    5,
)
COLLECTION_MIN_TIMESTAMP_PERCENT = env_int(
    "DAILY_NEWS_COLLECTION_MIN_TIMESTAMP_PERCENT",
    80,
    maximum=100,
)
STALE_RUN_MINUTES = env_int("DAILY_NEWS_STALE_RUN_MINUTES", 180)
BACKUP_RETENTION_DAYS = env_int("DAILY_NEWS_BACKUP_RETENTION_DAYS", 14)
RAW_RETENTION_DAYS = env_int("DAILY_NEWS_RAW_RETENTION_DAYS", 30)
MAX_HTTP_RESPONSE_BYTES = env_int(
    "DAILY_NEWS_MAX_HTTP_RESPONSE_BYTES",
    5_000_000,
    minimum=1024,
)
MAX_COMMAND_OUTPUT_BYTES = env_int(
    "DAILY_NEWS_MAX_COMMAND_OUTPUT_BYTES",
    10_000_000,
    minimum=1024,
)

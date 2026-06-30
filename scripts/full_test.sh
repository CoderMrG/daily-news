#!/bin/zsh

PROJECT_ROOT=${0:A:h:h}
cd "$PROJECT_ROOT" || exit 1

PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi

exec "$PYTHON" -m daily_news.full_test "$@"

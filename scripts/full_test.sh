#!/bin/zsh

PROJECT_ROOT=${0:A:h:h}
cd "$PROJECT_ROOT" || exit 1

exec python3 -m daily_news.full_test "$@"

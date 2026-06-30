#!/bin/zsh

PROJECT_ROOT=${0:A:h:h}
cd "$PROJECT_ROOT" || exit 1

mkdir -p data/logs
exec >>data/logs/scheduled.out.log 2>>data/logs/scheduled.err.log
print "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] scheduled run started"

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
unset http_proxy https_proxy all_proxy
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export LC_ALL=C

PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  /usr/bin/osascript -e 'display notification "缺少项目 .venv，请先运行 Python 3.11 初始化" with title "Daily News 自动运行失败"' >/dev/null 2>&1
  print "Missing Python environment: $PYTHON"
  exit 127
fi

"$PYTHON" main.py run --skip-existing
exit_code=$?

"$PYTHON" main.py health --date "$(/bin/date '+%Y-%m-%d')" --notify
health_exit=$?

if (( health_exit != 0 )); then
  /usr/bin/osascript -e 'display notification "健康检查失败，请查看 data/logs/scheduled.err.log" with title "Daily News 自动运行异常"' >/dev/null 2>&1
fi

if (( exit_code == 0 )); then
  print "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] scheduled run finished"
fi

exit "$exit_code"

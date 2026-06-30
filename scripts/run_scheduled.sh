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

python3 main.py run --skip-existing
exit_code=$?

if (( exit_code != 0 )); then
  /usr/bin/osascript -e 'display notification "请查看 data/logs/scheduled.err.log" with title "Daily News 自动运行失败"' >/dev/null 2>&1
else
  print "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] scheduled run finished"
fi

exit "$exit_code"

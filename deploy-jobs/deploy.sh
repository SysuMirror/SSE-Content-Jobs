#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${CONTENT_JOBS_ROOT:-/home/cloud/content-jobs}
mkdir -p "$ROOT/venv" "$ROOT/state" "$ROOT/logs"
if [ ! -x "$ROOT/venv/bin/python" ]; then
  python3 -m venv "$ROOT/venv"
  "$ROOT/venv/bin/pip" install -q -i https://mirrors.aliyun.com/pypi/simple requests beautifulsoup4 pymysql croniter
fi
[ -f "$ROOT/env.sh" ] || { echo "missing $ROOT/env.sh"; exit 1; }
. "$ROOT/env.sh"
exec "$ROOT/venv/bin/python" "$HERE/scheduler.py"

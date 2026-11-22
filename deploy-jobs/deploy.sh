#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${CONTENT_JOBS_ROOT:-/home/cloud/content-jobs}
mkdir -p "$ROOT/vendor" "$ROOT/state" "$ROOT/logs"
pip3 install -q --upgrade --target "$ROOT/vendor" -i https://mirrors.aliyun.com/pypi/simple requests beautifulsoup4 pymysql croniter
[ -f "$ROOT/env.sh" ] || { echo "missing $ROOT/env.sh"; exit 1; }
. "$ROOT/env.sh"
export PYTHONPATH="$ROOT/vendor${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$HERE/scheduler.py"

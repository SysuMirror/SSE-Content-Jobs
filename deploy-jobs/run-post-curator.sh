#!/bin/bash
set -euo pipefail
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC=$(CDPATH= cd -- "$HERE/.." && pwd)
ROOT=${CONTENT_JOBS_ROOT:-/home/cloud/content-jobs}
. "$ROOT/env.sh"
mkdir -p "$ROOT/state/post-curator-data"
ln -sfn "$ROOT/state/post-curator-data" "$SRC/post-curator/data"
cd "$SRC/post-curator"
exec "$ROOT/venv/bin/python" main.py scan "$@"

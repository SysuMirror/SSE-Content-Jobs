#!/bin/bash
set -euo pipefail
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC=$(CDPATH= cd -- "$HERE/.." && pwd)
ROOT=${CONTENT_JOBS_ROOT:-/home/cloud/content-jobs}
. "$ROOT/env.sh"
export PYTHONPATH="$ROOT/vendor${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$ROOT/state/post-curator-data"
ln -sfn "$ROOT/state/post-curator-data" "$SRC/post-curator/data"
cd "$SRC/post-curator"
exec python3 main.py scan "$@"

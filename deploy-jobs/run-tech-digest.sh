#!/bin/bash
set -euo pipefail
TASK="${1:?usage: run-tech-digest.sh <ingest|daily|star|weekly>}"
shift || true
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC=$(CDPATH= cd -- "$HERE/.." && pwd)
ROOT=${CONTENT_JOBS_ROOT:-/home/cloud/content-jobs}
. "$ROOT/env.sh"
mkdir -p "$ROOT/state/tech-digest-data"
ln -sfn "$ROOT/state/tech-digest-data" "$SRC/tech-digest/data"
cd "$SRC/tech-digest"
if [ "$TASK" = "star" ] || [ "$TASK" = "weekly" ]; then
  sed -i 's/"reasoning_effort": "none"/"reasoning_effort": "low"/' app/llm.py || true
  trap 'git -C "$SRC" checkout -- tech-digest/app/llm.py 2>/dev/null || true' EXIT
fi
exec "$ROOT/venv/bin/python" main.py "$TASK" "$@"

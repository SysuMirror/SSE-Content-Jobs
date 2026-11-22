#!/usr/bin/env python3
import json
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from croniter import croniter

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROOT = Path(os.environ.get("CONTENT_JOBS_ROOT", "/home/cloud/content-jobs"))
LOG_DIR = ROOT / "logs"
STATE = ROOT / "scheduler-state.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)
RUNNING = {}


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, STATE)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def due(expr, now):
    try:
        return croniter(expr, now - timedelta(seconds=1)).get_next(datetime) == now
    except Exception:
        return False


def main():
    jobs = json.loads((HERE / "jobs.json").read_text())
    schedules = jobs.get("schedules") or []
    commands = jobs.get("commands") or {}
    log(f"content scheduler started repo={REPO} jobs={len(schedules)}")
    while True:
        now = datetime.now().replace(second=0, microsecond=0)
        state = load_state()
        changed = False
        for item in schedules:
            name = str(item.get("job") or "")
            expr = str(item.get("cron") or "")
            if not name or not due(expr, now):
                continue
            key = now.strftime("%Y-%m-%dT%H:%M")
            if state.get(name) == key:
                continue
            command = commands.get(name)
            if not command:
                continue
            pid = RUNNING.get(name)
            if pid and alive(pid):
                log(f"skip {name}: previous run still alive pid={pid}")
            else:
                logf = open(LOG_DIR / f"{name}.log", "ab")
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=str(REPO),
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=os.environ.copy(),
                )
                RUNNING[name] = proc.pid
                log(f"start {name}: {command} pid={proc.pid}")
            state[name] = key
            changed = True
        if changed:
            save_state(state)
        time.sleep(20)


if __name__ == "__main__":
    main()

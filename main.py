"""One short Railway cron invocation; expensive reports run in bounded children."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

from schedule import GRACE, JOB_TIMEOUT_SECONDS, Occurrence, candidates
from state import State, state_path


def execute(occurrence, path):
    from delivery import Delivery
    import tasks
    functions = {"fear": tasks.run_fear_greed_task, "reddit": tasks.run_reddit_task}
    if occurrence.job not in functions:
        raise ValueError(f"Unsupported report job: {occurrence.job}")
    state = State(path)
    try:
        if not state.claim(occurrence.key):
            print(f"SKIP already handled: {occurrence.key}", flush=True)
            return 0
        sender = Delivery(state, occurrence.key)
        kwargs = {"previous_value": state.get("previous_fear_value")} if occurrence.job == "fear" else {}
        functions[occurrence.job](sender, **kwargs)
        if state.run(occurrence.key)["status"] != "sent":
            raise RuntimeError("Task finished without confirmed delivery")
        return 0
    except Exception as exc:
        state.fail(occurrence.key, type(exc).__name__)
        print(f"FAILED {occurrence.key}: {type(exc).__name__}", flush=True)
        return 1
    finally:
        state.close()


def run_scheduled(path, *, clock=None, sleep=time.sleep, launch=subprocess.run):
    clock = clock or (lambda: datetime.now(timezone.utc))
    state = State(path)
    failed = False
    try:
        now = clock()
        slots = candidates(now)
        print(f"CRON_START {now.isoformat()} candidates={len(slots)} state={path}", flush=True)
        for slot in slots:
            if not state.can_run(slot.key):
                print(f"SKIP already handled: {slot.key}", flush=True)
                continue
            delay = (slot.at - clock()).total_seconds()
            if delay > 0:
                print(f"WAIT {int(delay)}s for {slot.key}", flush=True)
                sleep(delay)
            if clock() - slot.at > GRACE:
                print(f"SKIP expired: {slot.key}", flush=True)
                continue
            command = [sys.executable, "-u", str(Path(__file__).resolve()),
                       "--execute", slot.job, "--at", slot.at.isoformat(), "--state", str(path)]
            try:
                result = launch(command, timeout=JOB_TIMEOUT_SECONDS, check=False)
                failed |= result.returncode != 0
            except subprocess.TimeoutExpired:
                state.fail(slot.key, "Task exceeded 480 seconds")
                print(f"TIMEOUT {slot.key}; child terminated", flush=True)
                failed = True
        print(f"CRON_FINISHED failed={failed}; exiting and releasing memory", flush=True)
        return int(failed)
    finally:
        state.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Read data without posting or writing state")
    parser.add_argument("--job", choices=("fear", "reddit"), help="Preview a report; requires --dry-run")
    parser.add_argument("--execute", choices=("fear", "reddit"), help=argparse.SUPPRESS)
    parser.add_argument("--at", help=argparse.SUPPRESS)
    parser.add_argument("--state", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.job and not args.dry_run:
        parser.error("--job requires --dry-run")
    if args.dry_run:
        if args.job:
            from delivery import preview
            import tasks
            {"fear": tasks.run_fear_greed_task, "reddit": tasks.run_reddit_task}[args.job](preview)
        else:
            for slot in candidates(datetime.now(timezone.utc)):
                print(f"DRY_RUN {slot.key}")
        return 0
    if not os.environ.get("WEBHOOK_URL"):
        raise RuntimeError("WEBHOOK_URL is missing")
    path = args.state or state_path()
    if args.execute:
        if not args.at:
            parser.error("--execute requires --at")
        return execute(Occurrence(args.execute, datetime.fromisoformat(args.at)), path)
    return run_scheduled(path)


if __name__ == "__main__":
    sys.exit(main())

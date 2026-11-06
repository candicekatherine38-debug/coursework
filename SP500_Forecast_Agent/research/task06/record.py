"""Run a real command and keep its argv, clocks, exit status and stdout/stderr."""
import argparse
from pathlib import Path
import subprocess
import time

from .contracts import now, write


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args()
    cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not cmd or args.log.exists():
        p.error("Provide a command and a new log path")
    start = now()
    begin = time.monotonic()
    r = subprocess.run(cmd, text=True, capture_output=True)
    write(args.log, {"argv": cmd, "started_at_utc": start, "finished_at_utc": now(),
                     "elapsed_seconds": time.monotonic()-begin, "exit_code": r.returncode,
                     "stdout": r.stdout, "stderr": r.stderr})
    print(r.stdout, end="")
    if r.stderr:
        import sys
        print(r.stderr, end="", file=sys.stderr)
    raise SystemExit(r.returncode)


if __name__ == "__main__":
    main()

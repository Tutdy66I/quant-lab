"""Capture the complete regression or frozen report run under a wall limit."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def main():
    operations = {
        "regression": ("round35-regression.log", ["run_synthetic.py"], 120),
        "report": ("round35-report-execution.log", ["study35.py", "report"], 240),
    }
    if len(sys.argv) != 2 or sys.argv[1] not in operations:
        raise SystemExit("Use run_quality35.py regression|report")
    name, args, timeout = operations[sys.argv[1]]
    argv = [sys.executable, "-I", "-S", "-B", *args]
    started = time.monotonic()
    with (ROOT/name).open("x", encoding="utf8") as log:
        log.write(f"start_utc={datetime.now(timezone.utc).isoformat()}\nargv={argv!r}\n")
        log.flush()
        try:
            code = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = 124
        log.write(f"\nexit_code={code}\nelapsed_seconds={time.monotonic()-started:.4f}\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())


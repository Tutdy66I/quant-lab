"""Capture local evidence with exclusive logs and a wall-time limit."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent


def main():
    entries={
      "development":("round39-development-run.log",["study39.py","development"],120),
      "regression_dev":("round39-regression-dev.log",["run_synthetic.py"],120),
      "regression":("round39-regression.log",["run_synthetic.py"],120),
      "report":("round39-execution.log",["study39.py","report"],240),
    }
    if len(sys.argv)!=2 or sys.argv[1] not in entries:
        raise SystemExit("Use run_study39.py development|regression_dev|regression|report")
    name,script,limit=entries[sys.argv[1]]
    command=[sys.executable,"-I","-S","-B",*script]
    start=time.monotonic()
    with (ROOT/name).open("x",encoding="utf8") as stream:
        stream.write(f"utc={datetime.now(timezone.utc).isoformat()}\nargv={command!r}\n")
        stream.flush()
        try:
            code=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                                timeout=limit).returncode
        except subprocess.TimeoutExpired:
            code=124
        stream.write(f"\nexit_code={code}\nelapsed_seconds={time.monotonic()-start:.4f}\n")
    return code


if __name__=="__main__":
    raise SystemExit(main())


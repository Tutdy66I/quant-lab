"""Capture exclusive lightweight regression and feature-report logs."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent


def main():
    choices={"development":("round38-development-regression.log",["run_synthetic.py"],120),
             "regression":("round38-regression.log",["run_synthetic.py"],120),
             "report":("round38-execution.log",["study38.py","report"],240)}
    if len(sys.argv)!=2 or sys.argv[1] not in choices:
        raise SystemExit("Use run_features38.py development|regression|report")
    name,args,timeout=choices[sys.argv[1]]
    command=[sys.executable,"-I","-S","-B",*args]
    start=time.monotonic()
    with (ROOT/name).open("x",encoding="utf8") as log:
        log.write(f"utc={datetime.now(timezone.utc).isoformat()}\nargv={command!r}\n")
        log.flush()
        try:
            code=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code=124
        log.write(f"\nexit_code={code}\nelapsed_seconds={time.monotonic()-start:.4f}\n")
    return code


if __name__=="__main__":
    raise SystemExit(main())


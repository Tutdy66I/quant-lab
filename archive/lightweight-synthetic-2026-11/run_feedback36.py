"""Write exclusive execution logs with a bounded subprocess."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def main():
    choices={"development":("round36-development.log",["run_synthetic.py"],120),
             "regression":("round36-regression.log",["run_synthetic.py"],120),
             "report":("round36-execution.log",["study36.py","report"],240)}
    if len(sys.argv)!=2 or sys.argv[1] not in choices:
        raise SystemExit("Use run_feedback36.py development|regression|report")
    name,args,timeout=choices[sys.argv[1]]
    argv=[sys.executable,"-I","-S","-B",*args]
    start=time.monotonic()
    with (ROOT/name).open("x",encoding="utf8") as out:
        out.write(f"utc={datetime.now(timezone.utc).isoformat()}\nargv={argv!r}\n")
        out.flush()
        try:
            code=subprocess.run(argv,cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,
                                timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code=124
        out.write(f"\nexit_code={code}\nelapsed_seconds={time.monotonic()-start:.4f}\n")
    return code


if __name__=="__main__":
    raise SystemExit(main())


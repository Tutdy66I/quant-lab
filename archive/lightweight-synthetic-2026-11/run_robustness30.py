"""Emit reproducible exploratory model comparisons, synthetic only."""
import hashlib
import json
from pathlib import Path
import sys
import time

if len(sys.argv)!=1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    print('Use python -I -S -B run_robustness30.py',file=sys.stderr); sys.exit(2)
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root))
import robustness_synthetic30 as robust


def main():
    started=time.perf_counter()
    result=robust.run_matrix()
    result['runtime_seconds']=round(time.perf_counter()-started,6)
    result['python']=sys.version
    result['source_sha256']={p:hashlib.sha256((root/p).read_bytes()).hexdigest()
        for p in ('robustness_synthetic30.py','ridge_synthetic.py','run_robustness30.py')}
    print(json.dumps(result,sort_keys=True,allow_nan=False))


if __name__=='__main__': main()

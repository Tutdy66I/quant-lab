"""Repeat the fixed synthetic calibration; print JSON to stdout."""
import hashlib
import json
from pathlib import Path
import sys
import time

if len(sys.argv)!=1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    print('Use python -I -S -B run_calibration31.py',file=sys.stderr);sys.exit(2)
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root))
import calibration_synthetic31 as c


def main():
    path=root/'round31-calibration-preconfig.json'; raw=path.read_bytes(); frozen=json.loads(raw)
    if frozen['config']!=c.CONFIG or any(hashlib.sha256((root/n).read_bytes()).hexdigest()!=h for n,h in frozen['source_sha256'].items()):
        raise ValueError('pre-experiment configuration or source differs')
    t=time.perf_counter();result=c.run_calibration()
    print(json.dumps({'preconfig_sha256':hashlib.sha256(raw).hexdigest(),
                      'elapsed_seconds':round(time.perf_counter()-t,6),'result':result},
                     sort_keys=True,separators=(',',':'),allow_nan=False))


if __name__=='__main__':main()

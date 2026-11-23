"""Bounded, fully synthetic AR(1) coverage matrix; never reads holdout."""
import json
from pathlib import Path
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from bootstrap_robustness27 import execute_matrix

def main():
    root=Path(__file__).resolve().parent
    config=json.loads((root/'bootstrap_matrix_config27.json').read_text(encoding='utf-8-sig'))
    started=time.monotonic(); result=execute_matrix(config)
    result['seconds']=round(time.monotonic()-started,6)
    print(json.dumps(result,sort_keys=True,allow_nan=False))

if __name__=='__main__': main()

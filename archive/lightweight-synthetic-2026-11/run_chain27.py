"""Run only fixed development scenario against a prior local population anchor."""
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys

if len(sys.argv) != 1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    print('Use Python -I -S -B run_chain27.py; no overrides', file=sys.stderr)
    sys.exit(2)
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
from research_chain27 import run_verified, verify_receipt
from ridge_synthetic import generate


def encode(value):
    if is_dataclass(value): return asdict(value)
    raise TypeError('unsupported record')


def main():
    manifest = json.loads((root / 'round27-development-manifest.json').read_text(encoding='utf-8'))
    anchor = json.loads((root / 'round27-development-anchor.json').read_text(encoding='utf-8'))
    if anchor['custody'] != 'same-local-user-not-independent':
        raise ValueError('unexpected custody description')
    run, payload, receipt = run_verified('signal', manifest,
        expected_manifest_sha256=anchor['expected_manifest_sha256'],
        anchor_source='local-precommitted-digest-not-independent', replicates=200)
    verify_receipt(payload, receipt, expected_manifest_sha256=anchor['expected_manifest_sha256'],
                   rows=generate('signal', 20261113), run=run)
    print(json.dumps({'run':run,'payload':payload,'receipt':receipt,
                      'independent_origin_authenticated':False}, default=encode,
                     sort_keys=True,allow_nan=False))


if __name__ == '__main__':
    main()

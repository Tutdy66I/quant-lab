"""Record the reviewed local study configuration before any report seed use."""
from datetime import datetime, timezone
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study34 as study


def main():
    root = study.ROOT
    for name in ('round34-lock.json', 'round34-independent-freeze.json',
                 'round34-runs', 'round34-seed-reservations', 'round34-results.json'):
        if (root / name).exists():
            raise FileExistsError(name)
    protocol = study.read(root / 'protocol34.json')
    lock = {
        'frozen_utc': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(),
        'protocol': protocol,
        'engine_config': study.json.loads(study.json.dumps(study.engine.CONFIG)),
        'source_sha256': study.source_hashes(),
        'alarm_thresholds': study.read(root / 'round32-alarm-dev-results.json')['thresholds'],
    }
    study.verify_lock(protocol, lock)
    study.write(root / 'round34-lock.json', lock)
    print(study.sha(root / 'round34-lock.json'))


if __name__ == '__main__':
    main()

"""Inspect Hummingbot prerequisites without installing software or starting bots."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def probe(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return {"ok": result.returncode == 0, "output": (result.stdout or result.stderr).strip()[:1000]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "output": str(error)}


def inspect():
    docker = shutil.which("docker")
    checks = {"docker_cli": {"ok": bool(docker), "output": docker or "not installed/on PATH"}}
    if docker:
        checks["linux_engine"] = probe([docker, "info", "--format", "{{.OSType}}"])
        checks["linux_engine"]["ok"] &= checks["linux_engine"]["output"] == "linux"
        checks["compose"] = probe([docker, "compose", "version", "--short"])
    else:
        checks["linux_engine"] = {"ok": False, "output": "Docker unavailable"}
        checks["compose"] = {"ok": False, "output": "Docker unavailable"}
    administrator = None
    if os.name == "nt":
        import ctypes
        administrator = bool(ctypes.windll.shell32.IsUserAnAdmin())
        checks["wsl_cli"] = {"ok": bool(shutil.which("wsl")), "output": shutil.which("wsl") or "not installed/on PATH"}
    return {"observed_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(), "administrator": administrator, "ready": all(c["ok"] for c in checks.values()), "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/deployment_preflight.json")
    args = parser.parse_args()
    report = inspect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

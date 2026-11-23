"""Round 40 local acceptance probe for three existing R39 development arms."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if len(sys.argv) != 1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise SystemExit("Use python -I -S -B smoke40.py")
sys.path.insert(0, str(ROOT))

from arrival39 import run
from design39 import contract, fixture, transport
from evaluation39 import evaluate
from evidence_store28 import create_run, finalize_run, save_artifact, verify_run
from publication37 import canonical_sha

SEED = 69100
CASES = (
    ("timing", "short", 120, "population_supported_under_assumptions"),
    ("timing_mnar", "short", 55, "sensitivity_only"),
    ("timing_mnar", "short_abstain", 27, "insufficient_evidence"),
)


def raw(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    protocol = ROOT / "protocol39.json"
    inputs = ("smoke40.py", "arrival39.py", "design39.py", "evaluation39.py",
              "evidence_store28.py", "publication37.py")
    hashes = {n: sha(ROOT / n) for n in inputs}
    config = json.loads(protocol.read_bytes())
    if SEED not in config["development_seeds"] or SEED in config["report_seeds"]:
        raise ValueError("probe seed outside the recorded development cohort")
    previous = json.loads((ROOT / "round39-development.json").read_bytes())
    if previous["protocol_sha256"] != sha(protocol):
        raise ValueError("R39 development digest mismatch")
    archived = {(r["case"], r["arm"]): r for r in previous["rows"] if r["seed"] == SEED}
    results = []
    for case, arm, expected_count, expected_state in CASES:
        data = fixture(case, SEED, sha(protocol))
        columns, ttl, packets = contract(data, case, arm)
        commitment, events = transport(data, packets)
        trace = run(data["dataset"], arm, columns, ttl, events)
        outcome = evaluate(trace, data, commitment, sha(protocol), case, arm)
        target = outcome["evidence"]["target"]
        count, state = len(outcome["common"]), outcome["publication"]["status"]
        mse = {name: entry["oracle"]["mse"] for name, entry in outcome["models"].items()}
        old = archived[(case, arm)]
        if (target["planned"] != 120 or target["common"]["denominator"] != 120
                or target["common"]["numerator"] != count
                or (count, state) != (expected_count, expected_state)
                or (count, state, mse) != (old["cover"], old["state"], old["models"])):
            raise ValueError(f"archived data or release status differs: {case}/{arm}")
        results.append(dict(case=case, arm=arm, planned=120, observed=count, state=state,
                            scope=outcome["publication"]["target"],
                            trace_sha256=canonical_sha(trace), source_commitment=commitment))
    if any(sha(ROOT / name) != value for name, value in hashes.items()):
        raise ValueError("probe inputs changed during execution")

    base = ROOT / "round40-smoke-runs"
    base.mkdir(exist_ok=True)
    event = create_run(base, "lead40-probe")
    directory, run_id, reviewer = event["directory"], event["run_id"], event["reviewer"]
    artifacts = {"inputs.json": ("source", raw(hashes)),
                 "configuration.json": ("configuration", raw(dict(seed=SEED, protocol_sha256=sha(protocol)))),
                 "execution.json": ("log", raw(dict(argv=sys.argv, interpreter=sys.version,
                                                      formal_22="not_executed"))),
                 "outcomes.json": ("result", raw(results))}
    for name, (_, data) in artifacts.items():
        save_artifact(directory, run_id, reviewer, name, data)
    roles = {name: kind for name, (kind, _) in artifacts.items()}
    digest = finalize_run(directory, run_id, reviewer, roles)
    receipt = verify_run(directory, run_id, reviewer, roles, digest)
    print(raw(dict(directory=Path(directory).relative_to(ROOT).as_posix(), receipt=receipt,
                   archived_development_sha256=sha(ROOT / "round39-development.json"),
                   cases=results)).decode("utf-8").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


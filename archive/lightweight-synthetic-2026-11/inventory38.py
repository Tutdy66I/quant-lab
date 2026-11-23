"""Inventory the retained artificial research capability before adding report data."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent

CAPABILITIES=(
 ("lightweight_entry","stable_retained","24","run_synthetic.py","main",
  ("test_primitives.py","test_synthetic_integration.py"),"Only statically enumerated lightweight synthetic tests are authorized."),
 ("ridge_and_baselines","stable_retained","25,30,34","ridge_synthetic.py","fit_ridge; predict_ridge",
  ("test_ridge_synthetic.py","test_robustness_synthetic30.py","test_engine_five34.py"),"Frozen grid, fit/tune time order and data-only scope."),
 ("sample_time_identity","stable_retained","25-26","sample_lineage.py","sample_id_for; validate_samples",
  ("test_sample_lineage.py","test_leakage_diagnostics.py"),"Integrity relies on trusted frozen provenance, not a signature."),
 ("prequential_moving_windows","stable_retained","26","rolling_synthetic.py","rolling comparison",
  ("test_rolling_synthetic.py","test_rolling_integration.py"),"Score coverage and label timing constrain pairing."),
 ("population_manifest","stable_retained","27","synthetic_manifest27.py","build_manifest; verify_manifest",
  ("test_synthetic_manifest27.py",),"Local digest alone cannot prove independent custody."),
 ("integrated_research_chain","stable_retained","27","research_chain27.py","research chain validation",
  ("test_research_chain27.py",),"Offline oracle data is confined to artificial evaluation."),
 ("bootstrap_robustness","diagnostic_only","25-27","bootstrap_robustness27.py","coverage simulation",
  ("test_paired_bootstrap.py","test_bootstrap_robustness27.py"),"Coverage depends on generator and dependence."),
 ("holdout_protocol","research_required","27-28","holdout_audit28.py","prepare; local ledger validation",
  ("test_holdout_audit28.py","test_evidence_store28.py"),"A locally unopened marker cannot prove historical first use."),
 ("synthetic_audited_entry","historical_reference","29","audited_evaluation29.py","audited artificial evaluation",
  ("test_audited_evaluation29.py","test_fault29.py"),"Not ready for the original 60-row holdout; external authorization absent."),
 ("original_holdout_scoring","currently_prohibited","27-29","holdout_synthetic.py","score path restricted",
  ("test_holdout_synthetic.py",),"Original holdout unopened this round; prior first use remains unknown."),
 ("drift_calibration","diagnostic_only","30-32","calibration_synthetic31.py","alarm simulations",
  ("test_calibration_synthetic31.py","test_drift_robust32.py"),"Alarm performance is mechanism-dependent."),
 ("adaptive_strategy_history","historical_reference","31-34","engine_cross33.py","historical run_online",
  ("test_engine_cross33.py","test_study34.py"),"Original adaptive is historical; robust alarm is for anomaly diagnostics."),
 ("quality_and_feedback","stable_retained","35","quality35.py","feature_status; check_label; check_population",
  ("test_quality35.py",),"A recorded timestamp cannot authenticate a forged real source."),
 ("missingness_diagnostic","research_required","36","missing36.py","fit_response; weighted; sensitivity",
  ("test_feedback36.py",),"Weighting is assumption-specific and MNAR rank may be unidentified."),
 ("publication_contract","stable_retained","37","publication37.py","coverage; publication; choose_winner",
  ("test_publication37.py",),"Observed coverage is separate from target support and rank certainty."),
)
DEPENDENCIES={
 "lightweight_entry":"unittest and source audit",
 "ridge_and_baselines":"standard library Cholesky and chronological training",
 "sample_time_identity":"leakage_diagnostics; fixed manifests",
 "prequential_moving_windows":"sample_lineage; ridge_synthetic",
 "population_manifest":"sample_lineage",
 "integrated_research_chain":"synthetic_manifest27; rolling_synthetic",
 "bootstrap_robustness":"coverage_synthetic; paired_bootstrap",
 "holdout_protocol":"holdout_synthetic; evidence_store28",
 "synthetic_audited_entry":"holdout_audit28; evidence_store28",
 "original_holdout_scoring":"ridge_synthetic; external authorization required",
 "drift_calibration":"drift_synthetic30; prequential_synthetic31",
 "adaptive_strategy_history":"prequential_synthetic31; drift_robust32",
 "quality_and_feedback":"synthetic_manifest27; feedback35",
 "missingness_diagnostic":"quality35; qualification36",
 "publication_contract":"qualification36; missing36; qualification policy",
}
EVIDENCE=("evidence-manifest.json",*(f"round{i}-evidence-manifest.json" for i in
          (25,26,27,31,32,33,34,35,36,37)),"round28-report.md","round29-report.md","round30-report.md")


def sha(path):
    return hashlib.sha256((ROOT/path).read_bytes()).hexdigest()


def main():
    entries=[]
    for key,status,rounds,source,interface,tests,limits in CAPABILITIES:
        entries.append(dict(capability=key,status=status,rounds=rounds,source=source,interface=interface,
                            dependencies=DEPENDENCIES[key],limits=limits,source_sha256=sha(source),
                            test_sources={t:sha(t) for t in tests}))
    record=dict(version="lightweight-synthetic-stage-v1",scope="synthetic development; no formal admission",
                prior_test_count=297,prior_runner_sha256=sha("round38-prior-runner37.txt"),
                historical_evidence={name:sha(name) for name in EVIDENCE},capabilities=entries,
                prohibited=["60-row holdout scoring","formal 22 tests","real historical evaluation",
                            "Qlib","production","trading"])
    with (ROOT/"round38-capability-inventory.json").open("x",encoding="utf8") as out:
        json.dump(record,out,indent=2,sort_keys=True,allow_nan=False)
        out.write("\n")


if __name__=="__main__":
    main()


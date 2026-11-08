# AI Research: first-stage review and synthetic baseline comparison

## First-stage review

This is an author-performed code review, not independent reviewer sign-off. The first-stage tests cover selected time-order, cost, fingerprint and error cases; 209 passing tests do not establish production readiness or predictive value. The implementation does not prove that supplied predictions came from the claimed inputs or model, or that caller-supplied timestamps are true. `visible_price_features` has no automatic freshness threshold: a caller must not silently treat an old but visible feature as current. The first-stage per-sample round trips are not a continuous MA20/60 strategy comparison.

One first-stage temporal-isolation issue was fixed in this development copy: future-event fields were parsed before the event-time cutoff, and future training-row fields were parsed before the decision-time cutoff. A malformed future-only field could therefore make an earlier computation fail. Future-only records are now skipped before their unavailable fields are parsed; malformed fields on relevant records still fail closed. Regression tests perturb those future-only fields. This changes only the unmerged AI research groundwork, not the frozen historical research code.

The shared `strategies.execution` applies a 0.1% fee and 0.05% adverse slippage on each side. The existing MA20/60 backtest uses previous daily close signals, next daily open fills, a flat initial position and end-of-window liquidation. Those are idealized research assumptions; they are not proof of executable historical prices. The existing code and frozen research protocols remain unchanged.

## Synthetic-only comparison

`research.ai_baselines.compare_synthetic` accepts canonical, in-memory, caller-declared synthetic daily candles. It checks the input fingerprint, complete contiguous UTC dates, event/availability times and that all outcomes are available by the evaluation cutoff. It produces separate zero and previous-close-return predictions for the same open-to-close target. Future labels are separate from predictions.

The strategy portion compares flat, a predeclared positive-previous-return long/flat rule, the existing `research.Config()` MA20/60 implementation, and the existing buy-and-hold curve. All use the same comparison dates, initial capital, modeled execution costs, start-flat and terminal liquidation rules. These simulated strategy equities and forecast MSE are different outputs and cannot be substituted for one another. No model is trained; there is no training sample and its hash is null.

The daily-bar comparison deliberately adopts the repository's existing same-boundary previous-close/next-open **counterfactual**. It must not be interpreted as evidence that a real continuous crypto market allowed computation and execution at that same instant. Input timestamps and the assertion that a dataset is synthetic are caller claims, not a security boundary. Real historical data, future labels and retained market snapshots are not used by these tests.

## Provenance and limitations

The in-memory result records SHA-256 fingerprints for the source bytes, fixed feature config, computed features, built-in model description and implementation, predictions, labels, metrics and report payload. Python and existing execution/backtest code versions are included. The seed is null because the baseline contains no randomness. The chain permits deterministic consistency checks; it does not independently prove execution, source authenticity, historical point-in-time availability or model training.

`verify_synthetic_report` replays the complete synthetic calculation from the supplied input bytes and checks the entire report, including the individual fingerprints. It rejects edited predictions even when the report's outer hash was recalculated. It verifies consistency in the current code and Python environment; it cannot prove what happened in another execution or that a caller's synthetic-data claim is true.

`external_prediction_record` records a digest and marks any supplied prediction bytes `unverified_external`, `execution_proven=false` and `eligible_for_formal_research=false`. It does not score, execute or trust an external model. All outputs continue to state `synthetic_engineering_only`, `formal_statistics_enabled=false` and `research_admission=insufficient_evidence`.

Future formal work still requires independent code review, immutable source and feature-to-model provenance, an explicitly preregistered training/validation protocol, point-in-time data, independent samples, realistic execution timing and separate authorization. Do not integrate Qlib, TradingAgents, paper ledgers, source admission or production tasks on the strength of this synthetic check.
# Single-decision prefix path

`predict_synthetic_at` is a separate, synthetic-declared, single-decision
interface. It classifies every candle by its UTC daily timestamp and checks
availability of ended candles before detecting visible conflicts and validating
the OHLC values of the three most recent visible candles. Unclassifiable time
metadata is rejected. Unavailable contents do not participate.
It produces forecasts only when the caller-specified freshness rule reports
`fresh`; it is not a complete comparison, strategy simulation, or evaluation.
`compare_synthetic` still validates all candles used in its complete report
and rejects malformed inputs instead of returning a partial report. These
interfaces do not prove input provenance or model execution and do not grant
formal research admission. The close/next-open same-instant assumption remains
an idealized daily-bar convention, not evidence of an executable live fill.

For a fixed decision, changes to records demonstrably unavailable at that time
must not alter its valid historical prediction or execution status when visible
used data are unchanged. Ambiguous time metadata still fails closed. Feature
returns and evaluation metrics reject nonfinite intermediate or final results.

Report schema v2 fingerprints `research.ai_offline` and `strategies.market_data`
in addition to the prior direct code dependencies. A v1 report must be replayed
with its original evaluator; v2 explicitly rejects it rather than treating a
changed implementation as identical. No report or historical evidence is
rewritten. These fingerprints do not prove execution or source authenticity.

# AI Research: offline engineering groundwork

This module is limited to a synthetic-input engineering interface. It does not train a model, run a strategy, start an observer, make network requests, read a production directory or write reports. It does not authorize any research protocol, data admission or trading. All evaluation results are marked `synthetic_engineering_only`, `formal_statistics_enabled=false` and `research_admission=insufficient_evidence`.

`research.ai_offline.adapt_ohlc_bytes` accepts caller-supplied CSV bytes in the existing five-column daily OHLC schema and verifies the expected SHA-256, UTC epoch seconds, contiguous dates, price bounds and completed days. It works entirely in memory. It records the supplied snapshot collection time as a lower bound for all rows' availability; unknown publication times remain null and rows are labelled retrospective. In particular, a historical date is not evidence that the row was available on that date. The adapter does not supply volume. Collection time and expected hash are caller claims and must be checked against a trusted manifest before any future market-data work.

`visible_price_features` uses only complete records whose event end and stated availability precede the decision, and rejects missing days in its feature window. `eligible_training` checks that features were available by their decisions and labels were both complete and available by the training cutoff. `evaluate_synthetic` checks supplied feature, model, training-label, entry and outcome times, compares model MSE against zero, and exercises long/flat modeled fills using the existing fee and slippage functions. It rejects overlapping positions. Its separate per-sample modeled returns are not a compounded strategy backtest.

The returned hashes record the supplied bytes, input rows, evaluator and execution code. **They do not prove that rows were derived from those bytes, that the provided model was trained as claimed, or that timestamps represent independent point-in-time evidence.** `model_id` and `dataset_kind` are self-declared; the latter is not a security boundary. The output cannot be used to claim statistical or trading performance. Future work needs a provenance-linked dataset format, training and prediction implementation, independent MA20/60 and buy-and-hold comparisons with identical execution semantics, protocol preregistration and independently available samples.

The existing daily-close to next-daily-open model is an idealized comparison: crypto trades continuously and the actual close can become available after the next bar opens. This module requires a synthetic entry strictly after the decision; daily OHLC alone cannot verify realistic intraday execution latency. Do not infer historical first publication or real fill prices.

Tests use constructed synthetic data and an explicitly blocked socket. They check snapshot hash and timing, future perturbations, training-label availability, modeled fee/slippage, input fingerprints and error handling. These tests do not demonstrate a predictive edge. Frozen historical protocols, existing 7/28-day starts, paper ledgers, archived evidence and production service states are unchanged.
# Time isolation repair: explicit daily freshness

`visible_price_features` requires the caller to declare a nonnegative integer
`max_age_seconds`; there is no implicit research default. Times are converted to
UTC. Daily candles end at UTC midnight; the expected latest completed candle ends
at the most recent UTC midnight at or before the decision. Age is the decision
time minus the newest available completed candle end. Age equal to the limit is
within the limit; a greater age is `stale`. A missing expected completed candle
is `missing_latest` when the age limit is not exceeded; the result also exposes
`missing_latest` when it is stale. Fewer than three available candles are
`insufficient_data`. Only `fresh` includes returns. An unavailable or absent
latest candle must not silently become a current feature. Missing or malformed
timestamps cannot be classified as future and are rejected.

`eligible_training` first checks decision and label end. An unfinished label is
excluded without reading its future fields. For an ended label, feature times
and label availability are validated before inclusion. Once available by the
cutoff, the label must contain a finite numeric `label_value`; unavailable
future contents are not parsed. The caller must validate any further
model-specific label semantics. This does not train a model or establish provenance.

`evaluate_synthetic` evaluates caller-supplied predictions and checks timing
order only; `eligible_training` filters training rows. Neither accepts a
freshness policy or checks feature age. Their results must not be used as proof
that `visible_price_features` was run or returned `fresh`.

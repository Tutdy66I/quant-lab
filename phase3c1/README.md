# Quant Lab Phase 3C-1: synthetic offline implementation

This isolated directory exercises frozen protocol SHA-256
`4c9a904d12c469a23cec928e195bf2c91a111cb7f47d4cbb1f52177fef5bc82e`.
It is not part of the production checkout and has no collector, scheduler,
broker, network client or external framework. Tests generate all OHLC bars in
memory and read only the local frozen protocol and a synthetic reference vector.
Do not supply real historical observations. `dataset_kind=synthetic` and
collection time are caller claims, not an authentication or access-control
boundary; this code cannot prove input provenance.

`build_samples` records event and simulated timestamps separately and leaves
actual historical availability unknown. It requires a complete synthetic daily
calendar over the frozen interval; missing, duplicate, malformed, invalid or
nonfinite inputs reject the entire run. `fit_ridge` uses training-only
standardization and the frozen centered Cholesky formula. Selection uses tuning
MSE and does not refit. Bootstrap requires CPython 3.12.14 exactly, as frozen.

`run_synthetic` accepts in-memory input and the protocol bytes; it returns a
complete or failed envelope with configuration, input where hashable, source
code, sample, prediction and result fingerprints. No incomplete results are
reported as complete. `verify_synthetic_run` recomputes the complete envelope.
These in-memory envelopes do not provide durable begin/end receipts, atomic
storage, reviewer identity, execution authentication or source authenticity.
Independent numerical fixtures and fractions test the solver; self-replay alone
would not establish numerical correctness. Results are synthetic engineering
evidence only, `insufficient_evidence`, and cannot authorize real research.

Ordinary `Exception` failures across snapshot, computation and receipt sealing
return a minimal failed envelope, with the known input hash or an explicit
unavailable status. The failure path does not reread caller input or stringify
the exception, and does not claim a receipt hash or full metrics. Control
signals such as `KeyboardInterrupt` and `SystemExit` propagate. This does not
guarantee a receipt after forced process termination, power loss, or resource
exhaustion; no durable execution record exists.

At entry, `capture_synthetic_input` serializes the caller's rows once into
immutable canonical JSON bytes; hashing and sample construction both use those
same bytes, with private decoded objects. The input hash remains SHA-256 of the
same canonical JSON format used before this change. Callers may retain these
bytes for replay; replaying later-mutated original rows must fail. Capturing
does not establish that caller-owned data remained unchanged during the initial
serialization, nor does it defend against arbitrary code replacing the verifier.

Every run and replay checks the complete method configuration against the
separately constructed v1 specification and rejects changed runtime partitions,
alpha grid or caller configuration. Successful envelopes include that full
validated method configuration in the configuration hash. The verifier does
not accept a report's self-consistent hash as proof of protocol compliance.
This is an ordinary configuration-drift check in a cooperative Python process,
not an operating-system security boundary or protection against code that can
replace the verifier or alter the executing interpreter.

Run only the isolated synthetic suite with CPython 3.12.14:
`python -B -m unittest discover -s tests -v`. No real-history test is authorized.
Further independent review, real-data admission, Qlib, tasks and deployment are
separate decisions. No immutable audit storage or physical power-loss claim is
made by this prototype.

## Isolated CI validation

The isolated CI validation branch uses separate Windows/Python 3.10 and
Ubuntu/Python 3.13 runtime-rejection jobs plus an exact CPython 3.12.14 job for
the full synthetic suite. The frozen patch version is checked at runtime; no
substitute is accepted. These jobs use sparse checkout for the synthetic inputs.
The legacy full test job remains unchanged for other refs, but is intentionally
skipped for pushes to this validation branch because its history tests replay
real market data. Consequently, a passing synthetic validation run is not proof
that the legacy full suite passed on that commit. No production deployment or
real-history research is authorized by this branch.

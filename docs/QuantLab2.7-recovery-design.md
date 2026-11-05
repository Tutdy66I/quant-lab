# Quant Lab 2.7: offline recovery design

The ordinary `review` command acknowledges an operations error after investigation; it does not authorize a collection when the collector ledger still has an exhausted transient retry budget. Do not change or delete historical receipts, run records, locks or health projections to manufacture authorization. `health` reports the last successful collection, age, block reason and manual action. Failure diagnostics distinguish TLS, timeout, other network errors, access restrictions and source/integrity stops.

## Recovery remains disabled

`recover-once` fails closed before writing authorization or sending requests: no supported durable publication barrier has been established for the current Windows/NTFS environment. Tests that exercise the rest of the state machine explicitly mock this barrier; they do not demonstrate disk persistence. Do not replace this guard or deploy recovery until a supported persistence protocol has been designed, reviewed and validated for the deployment environment and a separate production authorization is granted.

The latest verified operations snapshot must anchor the current collector head. A missing authorization or result cannot make a subsequently committed collector run look absent: an unanchored head blocks normal execution and ordinary review, with manual investigation required. Hash chains detect internal changes and missing intermediate records, but cannot detect a coherent rollback of all ledger and snapshot files to an earlier valid state. Snapshot and receipt publication, filesystem caches, storage firmware, directory entry ordering, OS crash and physical power loss remain distinct failure domains. No test here simulates a physical power cut or proves device durability. Original 7/28-day observation start remains unchanged.

The current standalone `collect/watch` entry points reject writes to a service-managed root, even with `--resume-after-review`. A management directory (including a dangling symlink) is treated conservatively as managed; read-only `verify` and `snapshot` remain available. Service writes use an internal cooperation marker while holding the service lease. This is an application boundary, not an OS permission boundary: old code copies and local code that deliberately imports the marker can bypass it. Deployment must prevent simultaneous use of older executables and protect local filesystem permissions.

Historical observer snapshots are accepted only when the running observer has the exact guarded source hash and the stored fingerprint is either the exact 2.6 baseline or that guarded version. Replay still verifies snapshot bytes and semantic registry. Any other source version is rejected and requires an explicit, reviewed compatibility update. Existing snapshots are never rewritten.

## Scheduler linkage design

Current receipts contain no scheduler instance identity. Historical time overlaps remain candidates only. A later deployment design should record a fresh random invocation ID, wrapper PID and creation time in an append-only wrapper event and pass that ID to the child receipt; join the wrapper process to Windows Scheduler event 129 by PID plus creation time and event instance ID, recording provenance and ambiguity. Validate PID reuse, duplicate/overlapping triggers, spoofed identifiers, missing events, task restarts and retention gaps before treating any association as proven. No task or wrapper changes are deployed by this PR, and no historical association is reconstructed.

## Paper provenance

New decision rows retain their first observed UTC time and source fingerprint in a separate SQLite table. Dates earlier than the UTC observation date are marked `historical_backfill`; a same-day row is `same_utc_day`, not a claim of punctual execution. Existing daily `observed_at` remains the latest mark time and can advance. Legacy rows with no independently retained decision provenance are reported as `unknown_legacy`, never guessed. Existing account values, model and history remain untouched by the code change; the additional table and view columns are created only if a user later runs the new code against an account.

Recovery guarantees apply to cooperative callers through the current service and observer code; older code copies cannot be stopped by this application guard. File fsync and rename do not establish directory-entry durability under physical power loss on all filesystems; do not claim tested power-cut durability. The observation monitor does not yet export first-decision provenance; use the paper CSV or SQLite provenance table for new decisions.

The separate 2.7B design and fault-injection plan is in [2.7B durability plan](QuantLab2.7B-durability-plan.md).

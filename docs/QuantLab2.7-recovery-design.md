# Quant Lab 2.7: offline recovery design

The ordinary `review` command acknowledges an operations error after investigation; it does not authorize a collection when the collector ledger still has an exhausted transient retry budget. Do not change or delete historical receipts, run records, locks or health projections to manufacture authorization. `health` reports the last successful collection, age, block reason and manual action. Failure diagnostics distinguish TLS, timeout, other network errors, access restrictions and source/integrity stops.

For a future, separately authorized production recovery, first inspect the ledger, exact error evidence, TLS trust and connectivity, source restrictions, current locks and task activity without modifying them. A controlled attempt requires an explicit operator decision and reason:

```
python -B -m deployment.archive_service recover-once --root <observer-root> --authorize-recovery --reason "<specific investigation and authorization>"
```

This command is not executed by this change. It checks integrity, the existing cooldown and a budget exhausted by consecutive transient failures. It writes a durable authorization linked to the ledger head before a single normal-budget collection and records the outcome in the receipt chain. Access restrictions, changed terms, redirects and integrity failures are not overridden. Failed recovery remains blocked. An authorization without an outcome blocks subsequent attempts, even after `review`; investigate it manually rather than replaying it. This command does not approve certificate installation, network changes, or a change to scheduler configuration. Existing collector TLS validation, source limits and one-hour cooldown remain in force. Long-run observation start is unchanged.

## Scheduler linkage design

Current receipts contain no scheduler instance identity. Historical time overlaps remain candidates only. A later deployment design should record a fresh random invocation ID, wrapper PID and creation time in an append-only wrapper event and pass that ID to the child receipt; join the wrapper process to Windows Scheduler event 129 by PID plus creation time and event instance ID, recording provenance and ambiguity. Validate PID reuse, duplicate/overlapping triggers, spoofed identifiers, missing events, task restarts and retention gaps before treating any association as proven. No task or wrapper changes are deployed by this PR, and no historical association is reconstructed.

## Paper provenance

New decision rows retain their first observed UTC time and source fingerprint in a separate SQLite table. Dates earlier than the UTC observation date are marked `historical_backfill`; a same-day row is `same_utc_day`, not a claim of punctual execution. Existing daily `observed_at` remains the latest mark time and can advance. Legacy rows with no independently retained decision provenance are reported as `unknown_legacy`, never guessed. Existing account values, model and history remain untouched by the code change; the additional table and view columns are created only if a user later runs the new code against an account.

Recovery guarantees apply to cooperative callers through the service; the older standalone collector CLI has its own explicit resume switch and is not governed by this service lease. File fsync and rename do not establish directory-entry durability under physical power loss on all filesystems; do not claim tested power-cut durability. The observation monitor does not yet export first-decision provenance; use the paper CSV or SQLite provenance table for new decisions.

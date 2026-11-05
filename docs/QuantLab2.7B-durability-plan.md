# Quant Lab 2.7B: recovery durability plan (not implemented)

Recovery remains disabled on all platforms. Implementing or using it requires a separate review and production authorization. The known deployment observation is Windows, C: NTFS, and Python 3.13.14 in a saved service receipt; recheck these facts before any deployment. Do not infer controller cache behavior, power-loss protection, firmware flush correctness or filesystem namespace persistence from NTFS alone.

## Candidate protocol and assumptions

Choose a documented Windows-supported persistent journal with a pre-provisioned, verified durable identity; record an immutable authorization ID, root identity, expected collector head and reason before requests. Use synchronous durable commits and explicitly validate errors. After collection, bind the committed collector run to the authorization, validate its snapshot, then persist the single result. On restart reconcile every transition and the latest anchored collector head. Missing, duplicated, ambiguous or mismatched records require manual investigation and cannot be cleared by ordinary review. The design must state which file or database and directory entries need persistence, how initial creation and rename are made durable, and what happens if the storage device ignores flushes. A hash chain alone cannot detect a coherent rollback of the entire local evidence directory; independent checkpoints are needed if that failure is in scope.

Establish the actual assumptions for local NTFS, filesystem filters, hardware write cache, device power-loss protection and platform API behavior. Restrict supported configurations if a verified guarantee is unavailable. Process termination, OS crash and physical power loss are distinct: process-kill tests cannot prove either storage failure mode. Physical power-cut testing is not authorized or assumed here.

## Test matrix for a disposable environment

| Injection boundary | Required observation |
|---|---|
| Before, during and after authorization commit | No network before successful durable commit; incomplete or ambiguous authorization blocks restart and review. |
| Before first request and immediately after request | No automatic replay or assumption that a request did not occur; one authorization permits at most one collector attempt. |
| Before and after collector ledger commit | Detect missing, partial, duplicate, reordered or unanchored runs; no automatic retry. |
| Before and after snapshot creation and verification | Missing, corrupt or mismatched snapshot blocks outcome completion and ordinary review. |
| Before, during and after result receipt commit | Missing outcome is uncertain; valid unique outcome matches authorization, run and verified snapshot; duplicate outcome fails closed. |
| Concurrent service and legacy processes | Only the managed service path can collect; test lease loss and a newer restriction/cooldown. |
| OS crash and physical power loss | Validate only in a separately authorized representative environment; report actual hardware and untested cases. |

For each boundary, use actual child-process termination as well as injected I/O failures and simulated missing records; test restart with fresh processes and unchanged original evidence. Keep `recover-once` disabled until these acceptance conditions and supported platform assumptions are met. This plan does not reset the observation clock or authorize real collection.

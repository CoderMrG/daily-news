# Runtime Reliability Hardening

This stage covers the existing source-checkout workflow on macOS. It does not
change report ranking thresholds, package installation, or module boundaries.

## Invariants

| Failure | Required outcome |
| --- | --- |
| Top-level source has no valid timestamp | Exclude it from freshness-sensitive output |
| Timestamp parsing degrades across a platform | Fail collection validation |
| External command times out | Terminate the complete process group |
| Run receives an interrupt or SIGTERM | Persist an `interrupted` terminal state |
| Article fetch has a transient failure | Retry on a later run |
| Article fetch returns a durable 404/410 | Use a time-limited negative cache |
| Report publishes but backup or cleanup fails | Preserve output as `degraded` |
| Short URL redirects to a private address | Reject the redirect |
| Database backup is created | Verify that it can be opened and queried |

## Stage Gate

The stage closes when all regression and fault-injection tests pass, offline and
cached full tests pass, the limited live test passes, and a final review finds
no remaining P1/P2 issue within this scope.

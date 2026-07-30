# WCA Incident And Recovery Runbook

## Crash Recovery

Recover after a crash by starting the WCA background runtime, keeping new entries blocked, loading authoritative state, reconciling broker and local state, rebuilding inventory projections from the ledger if needed, and verifying the latest event checkpoint before processing new finalized candles.

Do not resubmit unknown orders until reconciliation has searched by WCA client-order ID and determined whether the broker accepted the original request.

## Blocked Entries

Diagnose a blocked entry from persisted reason codes first. Common blockers are stale finalized bars, stale quote, stale broker snapshot, stale reconciliation, open circuit breaker, daily loss, daily trade limit, cooldown, conflicting WCA position, pending WCA entry, buying-power failure, spread failure, and missing rollout evidence.

Protective exits remain operational while entries are blocked. Entry gates must not cancel, delay, or weaken protective-order management.

## End Of Session

Verify end-of-session flatness by checking the broker SPY position, WCA inventory projection, WCA entry orders, protective orders, unreleased risk reservations, reconciliation result, and daily-state finalisation evidence. If flatness cannot be verified, keep the circuit breaker open and continue reconciliation.

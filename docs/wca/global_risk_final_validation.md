# WCA Global Risk And Final Validation

Step 9 routes WCA entries through `run_wca_execution_pipeline` using a WCA-owned adapter over the neutral shared account-risk boundary.

## Neutral Proposal

`backend.app.algorithms.wca.global_risk.WcaGlobalRiskProposal` is the only proposal contract emitted by the WCA pipeline. It contains:

- `algorithm_id`
- `account_id`
- `symbol`
- `side`
- `requested_quantity`
- `requested_risk`
- `stop_distance`
- `expected_holding_period_seconds`
- `current_wca_attributed_exposure`
- `total_account_exposure_snapshot`
- `configuration_version`
- `configuration_hash`
- `decision_id`
- `idempotency_key`

The shared risk response may approve, reduce quantity, reduce risk, reject an entry, or block new entries. The WCA adapter has no fields that can rewrite WCA strategy outputs, confidence, weights, dynamic profile, side, stop method, target method, or strategy settings.

## Final Validation

Final order validation runs after WCA sizing, manual overrides, global-risk caps, broker rounding, and limit-price construction. The validation context keeps new-entry permission separate from risk-reducing-exit permission so protective exits can remain available while entries fail closed.

The final validator checks paper-only mode, WCA ownership, fresh quote, positive quantity, buying power, position limits, WCA daily loss and trade limits, aggregate account-risk limits, spread and participation limits, stop/target geometry, entry cutoff, expected net edge, idempotency, prohibited position increases, and cross-algorithm mutation flags.

## Concurrency

`SharedGlobalRiskReservationEngine` provides the local neutral reservation primitive used by Step 9 tests. It reserves risk under a lock by account and symbol, deduplicates by idempotency key, and preserves algorithm attribution. Production deployments may replace this object with the shared account-risk engine if the same neutral proposal and decision contract is preserved.

# WCA Rollout And Rollback Runbook

## Promotion

Promotion order is strict: `DISABLED`, `HISTORICAL_REPLAY`, `SHADOW`, `PAPER_RECOMMENDATION`, `MANUAL_PAPER`, `LIMITED_AUTOMATIC_PAPER`, `AUTOMATIC_PAPER`.

A user request or API call cannot bypass evidence. Code completion alone does not promote WCA. Feature flags allow a stage only after the required persisted evidence has been accepted.

Required automatic-paper evidence includes deterministic replay parity, zero unexplained decision mismatches, zero duplicate broker orders, zero cross-algorithm inventory mutations, successful restart recovery, accepted reconciliation, no unprotected positions, accepted event and decision latency, accepted broker latency, recorded slippage, opening-session evidence, midday evidence, closing-session evidence, high-volatility evidence, economic-event-session evidence, minimum paper observation duration, sufficient paper trade count, and tested rollback.

## Mode Operations

Run replay before shadow promotion and preserve the replay fixture, settings version, inventory version, weights, calibration, and decision output.

Run shadow mode until comparison evidence shows zero unexplained mismatches.

Run manual paper only after recommendation evidence is accepted.

Enable limited automatic paper only after the limited-paper evidence gate passes. Conservative limits remain configuration: SPY, max quantity 10, max daily trades 3, max daily loss 100 USD, windows 10:00-11:30 and 13:30-15:30 America/New_York, and initially permitted strategies C1, C4, and C7.

## Rollback

Perform rollback from `MANUAL_PAPER`, `LIMITED_AUTOMATIC_PAPER`, or `AUTOMATIC_PAPER` to `SHADOW` or `DISABLED` when automatic-paper safety is not accepted.

Rollback must stop new entries, cancel WCA entry orders, preserve protective exits, reconcile broker and local state, preserve WCA inventory, preserve evidence, verify a safe state, and require explicit re-promotion.

After rollback, inspect inventory, inspect orders and fills, inspect reconciliation, and verify end-of-session flatness if the incident happened near the close.

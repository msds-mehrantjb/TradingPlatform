# Voting Ensemble V1 Baseline

This document captures the current V1 behavior before any Voting Ensemble V2 logic is enabled. The platform remains for backtesting and paper trading only; no live trading path is introduced here.

## Versioned Configuration

V2 feature flags are introduced under the backend application configuration object, defaulting to off:

- `strategyEngineV2Enabled = false`
- `familyEnsembleV2Enabled = false`
- `metaModelV2Enabled = false`
- `dynamicTradingPolicyEnabled = false`
- `globalGateEngineEnabled = false`

The current V1 code paths do not read these flags yet, so behavior remains unchanged with all flags off.

## Current Strategy-Vote Catalog

The V1 voting catalog is duplicated in backend replay logic and frontend live/paper-trading logic:

1. Multi-Timeframe Trend Alignment
2. First Pullback After Open
3. Failed Breakout Strategy
4. Liquidity Sweep Reversal
5. Bollinger Band Reversion
6. ATR Overextension Reversion
7. Relative Strength vs QQQ/IWM
8. Market Breadth Momentum
9. Economic Event Reaction Strategy
10. Ensemble Strategy Voting

Backend replay catalog: `backend/app/main.py::VOTING_STRATEGY_NAMES`.
Frontend catalog: `frontend/src/main.ts::strategyVoteCatalog`.

## Current Strategy Proxy Mappings

The frontend maps several strategies to context direction proxies instead of computing each strategy from required strategy-specific inputs:

- Multi-Timeframe Trend Alignment: `directionalSignal(context.session.directionBias, context.event.directionBias)`.
- First Pullback After Open: same session/event directional proxy.
- Failed Breakout Strategy: `oppositeSignal(context.event.directionBias)`.
- Liquidity Sweep Reversal: `oppositeSignal(context.session.directionBias)`.
- Bollinger Band Reversion: `oppositeSignal(context.session.directionBias)`.
- ATR Overextension Reversion: `oppositeSignal(context.session.directionBias)`.
- Relative Strength vs QQQ/IWM: session/event directional proxy.
- Market Breadth Momentum: session/event directional proxy.
- Economic Event Reaction Strategy: event/session directional proxy.
- Ensemble Strategy Voting: session/event directional proxy.

The backend replay path computes V1 tags from candle-derived approximations: trend/VWAP, opening range, gap percent, volume expansion, failed breakout, liquidity sweep, Bollinger overextension, ATR overextension, recent return, range compression, and cash-filter tags.

## Current Voting Calculation

Backend V1 replay:

- `historical_strategy_fits()` scores every catalog entry from tags.
- Strategies with `Allowed` or `Strong Fit` are eligible.
- The first nine strategies produce raw Buy/Sell/Hold signals.
- The tenth strategy, `Ensemble Strategy Voting`, then creates an additional self-vote:
  - Buy when eligible raw Buy votes are at least 3 and greater than Sell votes.
  - Sell when eligible raw Sell votes are at least 3 and greater than Buy votes.
  - Hold otherwise.
- `historical_vote_summary()` counts Buy/Sell/Hold after this self-vote is appended.
- Intraday timeframes select Buy/Sell only when that side has more votes than both other buckets.
- `1Hour`, `1Day`, and `1Week` use `directionalWinnerMinVotesByTimeframe`.

Frontend V1 live/paper path:

- `strategyEnsembleSignals()` builds votes from `strategyVoteCatalog`.
- `isEligibleStrategyVote()` excludes votes below eligibility thresholds.
- `votingEnsembleScoreSummary()` counts eligible votes and uses `winningVoteSignal()`, returning Hold on ties.

Known explicit defect: V1 includes an ensemble self-vote. The ensemble votes on itself as the tenth strategy, which can amplify the same underlying proxy signal and distort the final vote count.

Known explicit defect: vote logic is duplicated. Backend replay and frontend live/paper paths do not share a single authoritative implementation, and they use different inputs and proxy mappings.

## Current Eligibility Thresholds

Backend strategy fit scoring:

- Base score: `44 + 11 * matched_tags - 18 * blocked_tags`.
- Confidence bonus: `min(18, max(4, len(tags) * 1.8))`.
- Blocked strategies are capped at 64.
- Strong Fit: score >= 78 and no blockers.
- Allowed: score >= 62 and no blockers.
- Watch: score >= 45 otherwise.
- Avoid: below Watch.
- Only Allowed and Strong Fit votes are eligible.

Frontend live strategy voting:

- Missing strategy or `Avoid` or score < 45: inactive Hold.
- `Watch` or score < 62: Hold needing confirmation.
- Eligible votes are then counted by the frontend helper.

## Current Quantity-Sizing Calculation

Backend dynamic allocation mode:

- `order_limit = startingCapital * orderAllocationPercent / 100`.
- `risk_budget = order_limit * riskBudgetPercentOfOrder / 100`.
- Initial shares: `floor(min(order_limit, equity) / entry_price)`.
- If planned risk exceeds risk budget, shares become `floor(risk_budget / stop_distance)`.
- Risk mode uses `equity * riskPerTradePercent / 100`, capped by available capital.

Frontend V1 has additional local sizing for target-order templates, including account balance, order allocation, daily allocation, risk budget, default sizing settings, ATR/fixed stop distance, max position, participation, and max-share caps.

## Current Trade Gates

Backend replay gates:

- Warmup bars by timeframe.
- Daily loss lock.
- Max trades per day.
- Session start, new-trades-until, and force-close times.
- Signal is not Hold.
- Signal confirmation across configured confirmation bars.
- Allowed entry hours by timeframe.
- Position size must be at least one share.
- Stop/target/time exits; signal-fade exit is currently disabled.

Frontend live/paper gates:

- Event mode gate.
- Execution gate using 1m/VWAP and short-cycle 5m momentum.
- ML quality gate.
- Late-session and forecast safety gates for Buy.
- Sell blocked when there are no held shares.
- Quantity, planned risk, target order, and manual override gates.

## Current ML-Quality Behavior

The frontend `mlQualityGate()` treats missing or still-running matching dynamic artifacts as caution, not a hard failure. If an artifact exists, it inspects relevant rows from `dynamicArtifact.mlComparison.bestByTimeframe`; Improved passes, Mixed or profitable cautions, and other poor rows fail.

Backend dynamic artifacts are produced as `dynamic_trading_artifact_v1` and compare base replay with ML-filtered variants. Training policy notes use earlier years for walk-forward probabilities. V1 replay and dynamic artifact paths can therefore influence frontend display/gating without sharing one backend order-decision implementation.

## Current Backtesting and Snapshot Paths

Backtest and artifact paths:

- `/api/backtest-data/prepare`
- `/api/backtest-data/daily-refresh`
- `/api/backtest-data/latest`
- `/api/backtest-data/candles`
- `/api/backtest-data/artifacts/regenerate`
- `/api/backtest-data/artifacts/jobs/{job_id}`
- `/api/backtest-data/artifacts/latest`
- `/api/voting-ensemble/backtest`
- `/api/voting-ensemble/dynamic-artifact`
- `/api/voting-ensemble/dynamic-artifact/jobs`
- `/api/voting-ensemble/dynamic-artifact/latest`

Snapshot paths:

- Frontend recorder builds V1 snapshots in `buildDecisionRecorderSnapshot()`.
- Backend saves snapshots through `/api/decision-snapshots`.
- Labels and meta-strategy training use `/api/decision-snapshots/label`, `/api/meta-strategy/backfill-snapshots`, and `/api/meta-strategy/train`.

## Current Settings Fields

Backend default settings:

- `startingCapital`
- `orderAllocationPercent`
- `dailyAllocationPercent`
- `riskBudgetPercentOfOrder`
- `maxTradesPerDay`
- `stopLossPercent`
- `fixedStopDistanceDollars`
- `takeProfitR`
- `slippagePerShare`
- `positionSizingMode`

Frontend trading settings add:

- `useDefaultSizingSettings`
- `minimumBuyScore`
- `minimumSignalEdge`
- `baseRiskPercent`
- `maxPositionPercent`
- `atrStopMultiplier`
- `minimumStopDistancePercent`
- `maxParticipationPercent`
- `maxAllowedShares`
- `maxDailyLossPercent`
- `minimumActiveStrategies`
- `minimumBuyStrategyCount`
- `maxSpreadPercent`
- `minimumOneMinuteVolume`
- `pyramidingEnabled`

Separate local storage keys exist for voting, weighted, confidence, regime, and meta trading settings.

## Known Defects and Limitations

- The ensemble self-vote problem is present: `Ensemble Strategy Voting` can add a vote derived from the other votes.
- The duplicated vote problem is present: backend replay and frontend live/paper logic are independent implementations.
- Frontend strategy proxies can substitute session or event direction for strategy-specific inputs.
- Missing live context can fall back to local RAG/order-template behavior rather than a single backend authoritative Hold/no-trade result.
- ML-quality gating is split between backend artifact generation and frontend interpretation.
- Thresholds are scattered across backend constants and frontend constants/settings.
- Decision snapshots are V1 and must not be mixed with V2 snapshots for future training/evaluation.
- Current order templates are paper-trading UI aids only; they are not live trading orders.

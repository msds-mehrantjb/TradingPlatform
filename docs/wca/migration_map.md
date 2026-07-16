# WCA Migration Map

This map identifies the current WCA implementation surface and where each responsibility should move in later WCA V2 work. No migration is performed in Step 0.

## Migration Principles

- Keep WCA separate from Weighted Voting.
- Do not add ML to WCA.
- Preserve current behavior behind feature flags until parity tests pass.
- Move authority from frontend to backend gradually.
- Preserve user settings through explicit migration.
- Keep protective exits available even when new entries are blocked.

## Current-To-Future Mapping

| Current frontend responsibility | Current symbol/storage | Future backend responsibility |
| --- | --- | --- |
| Strategy catalog | `confidenceAggregationStrategies`, `confidenceBaseWeights` | `backend/app/algorithms/wca/catalog.py` |
| Strategy signal rules | `confidenceMovingAverageTrend`, `confidenceVwapPosition`, etc. | Pure strategy modules under `backend/app/algorithms/wca/strategies/` |
| Market snapshot assembly | `confidenceMarketSnapshot`, `confidenceMarketSnapshotFromCandles` | `market_snapshot.py` using neutral raw candles/quotes |
| Weight multipliers | `confidenceSystemWeightMultiplier` and ADX/ATR/volume/time helpers | `weight_engine.py` / `condition_adjustments.py` |
| Aggregation | `calculateConfidenceAggregationFromMarket` | `aggregation.py` |
| Local gates | `confidenceHardFilters`, `confidenceTargetOrderFailedGates` | `decision_gates.py` |
| Position sizing | `confidencePositionSizing` | `position_sizing.py` |
| Target order proposal | `confidenceTargetOrderRecommendation` | `order_proposal.py`, `entry_policy.py` |
| Target-order overrides | `weighted-confidence-target-order-overrides-v1` | Versioned backend config/order-intent draft storage or display-only UI state |
| Automatic submission | `maybeAutoSubmitConfidenceTargetOrder` | backend paper-order gateway through shared execution path |
| Trade history | `trading-dashboard.confidence-trade-history.v1` | backend WCA-owned trade ledger |
| Position state | `confidenceOpenOrderLots`, `confidencePositionSummary` | backend WCA position/ownership ledger |
| Daily WCA backtest | `runConfidenceDailyBacktestFromPreparedCandles`, `backtestConfidenceAggregation` | backend WCA backtest engine and scheduler |
| Backtest cache | `trading-dashboard.confidence-backtest-result.v1` | backend artifact storage |
| Decision settings | `weighted-confidence-decision-settings-v1` | versioned backend WCA config |
| Trading settings | `weighted-confidence-trading-settings-v1` | versioned backend WCA risk/config |
| Forecast safety | forecast helpers accepting `confidence` mode | remove from WCA decision authority or replace with neutral/global safety policy |

## Suggested Backend Package Shape

Future package:

```text
backend/app/algorithms/wca/
  __init__.py
  api.py
  service.py
  models.py
  config.py
  catalog.py
  market_snapshot.py
  signal_engine.py
  weight_engine.py
  aggregation.py
  decision_gates.py
  position_sizing.py
  entry_policy.py
  exit_policy.py
  order_proposal.py
  persistence.py
  scheduler.py
  backtest/
    data_validation.py
    execution_simulator.py
    engine.py
    walk_forward.py
  strategies/
    base.py
    moving_average_trend.py
    vwap_position.py
    trend_pullback.py
    rsi_mean_reversion.py
    bollinger_band_mean_reversion.py
    opening_range_breakout.py
    intraday_breakout.py
    macd_momentum.py
    market_structure.py
    gap_continuation_fade.py
    volume_confirmation.py
```

## Fixture And Parity Plan

Step 0 fixtures:

- `backend/tests/fixtures/wca/golden_snapshots.json`
- `backend/tests/test_wca_step0_characterization.py`

Future parity stages:

1. Backend WCA contracts reproduce Step 0 fixture schema.
2. Backend WCA aggregation reproduces all 100 golden snapshots exactly or within documented rounding tolerance.
3. Backend WCA strategy modules reproduce frontend strategy outputs from saved candle snapshots.
4. Backend order proposal reproduces current target-order output under a compatibility flag.
5. Backend paper-order path replaces frontend automatic append only after parity and paper validation.

## Settings Migration Map

| Browser storage key | Future handling |
| --- | --- |
| `weighted-confidence-decision-settings-v1` | migrate to backend WCA decision config |
| `weighted-confidence-trading-settings-v1` | migrate to backend WCA trading/risk config |
| `weighted-confidence-target-order-overrides-v1` | migrate only if still needed as draft/display preference; not authoritative |
| `trading-dashboard.confidence-trade-history.v1` | archive and optionally import into backend WCA ledger as untrusted historical UI trades |
| `trading-dashboard.confidence-order-control-modes.v1` | migrate to display/order-control preferences only |
| `trading-dashboard.confidence-order-control-overrides.v1` | migrate to display/order-control preferences only |
| `trading-dashboard.confidence-backtest-result.v1` | archive; do not seed backend performance weights from it without provenance |

## Risk Items To Address Later

- WCA currently shares target-order and automatic order plumbing with other frontend modes.
- WCA current forecast-safety checks are not WCA-internal deterministic rules.
- WCA backtest is frontend-owned and not production parity.
- WCA automatic mode appends frontend trade history instead of submitting through backend paper gateway.
- WCA spread uses slippage-derived synthetic spread in some paths instead of actual quote data.
- WCA settings and trade history are browser-local and can be reset by clearing storage.

## Step 0 Non-Changes

Step 0 intentionally does not:

- change runtime WCA calculations
- remove forecast dependencies
- move WCA to backend
- alter settings defaults
- alter automatic submission
- alter backtest behavior
- alter trade history persistence

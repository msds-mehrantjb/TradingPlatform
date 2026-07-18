# WCA Current Implementation Inventory

Scope: Step 0 baseline only. This document inventories the current Weighted Confidence Aggregation implementation before any behavior-changing refactor.

## Identity

- WCA means Weighted Confidence Aggregation.
- Current UI/mode identifier: `confidence`.
- Current primary implementation file: `frontend/src/main.ts`.
- Current implementation is frontend-authoritative for WCA calculation, settings, target-order generation, automatic submission, trade history, and WCA short-cycle backtest.
- WCA is separate from the eight-strategy Weighted Voting algorithm. This inventory does not treat Weighted Voting as a WCA dependency.

## Primary Frontend Code

| Area | Current symbols / storage | Location |
| --- | --- | --- |
| Strategy catalog | `confidenceAggregationStrategies`, `confidenceBaseWeights` | `frontend/src/main.ts` |
| Main calculation | `calculateConfidenceAggregation`, `calculateConfidenceAggregationFromMarket` | `frontend/src/main.ts` |
| Market snapshot | `confidenceMarketSnapshot`, `confidenceMarketSnapshotFromCandles` | `frontend/src/main.ts` |
| Weight multipliers | `confidenceSystemWeightMultiplier`, ADX/ATR/volume/time-of-day multiplier helpers | `frontend/src/main.ts` |
| Hard filters | `confidenceHardFilters` | `frontend/src/main.ts` |
| Position sizing | `confidencePositionSizing`, `confidenceSizeMultiplier`, `confidenceDefaultSizingSettings` | `frontend/src/main.ts` |
| Decision settings | `confidenceDecisionSettings`, `defaultConfidenceDecisionSettings`, `sanitizeConfidenceDecisionSettings` | `frontend/src/main.ts` |
| Trading settings | `confidenceTradingSettings`, `loadConfidenceTradingSettings`, `saveConfidenceTradingSettings` | `frontend/src/main.ts` |
| Target order | `confidenceTargetOrderRecommendation`, `confidenceTargetOrderFailedGates`, `applyConfidenceTargetOrderOverrides`, render/input helpers | `frontend/src/main.ts` |
| Automatic submission | `maybeAutoSubmitConfidenceTargetOrder` | `frontend/src/main.ts` |
| Trade history | `confidenceTradeHistory`, `confidenceOpenOrderLots`, `confidencePositionSummary`, `confidenceTodayPnl` | `frontend/src/main.ts` |
| Backtest | `runConfidenceDailyBacktestFromPreparedCandles`, `backtestConfidenceAggregation`, `confidenceBacktest*` helpers | `frontend/src/main.ts` |
| Forecast safety | `forecastBuySafetyBlockers`, `forecastBuySafetyGates`, `forecastStopOverrideKeepReason`, `forecastStopCloseReason`, `openingGraceKeepReason` include `confidence` mode | `frontend/src/main.ts` |
| Shared entry/exit checks | late-session buy guard, target-order consistency, automatic order rejection, open-order lot exits | `frontend/src/main.ts` |

## Backend Primary Voter List And Base Weights

| Key | Slug | Name | Family | Base weight |
| --- | --- | --- | --- | --- |
| C1 | `moving_average_trend` | Moving Average Trend | Trend | 0.10 |
| C2 | `trend_pullback` | Trend Pullback | Trend | 0.09 |
| C3 | `vwap_trend_continuation` | VWAP Trend Continuation | Trend | 0.09 |
| C4 | `vwap_mean_reversion` | VWAP Mean Reversion | Mean reversion | 0.08 |
| C5 | `rsi_mean_reversion` | RSI Mean Reversion | Mean reversion | 0.08 |
| C6 | `bollinger_atr_reversion` | Bollinger/ATR Reversion | Mean reversion | 0.08 |
| C7 | `opening_range_breakout` | Opening Range Breakout | Breakout | 0.10 |
| C8 | `intraday_volatility_breakout` | Intraday/Volatility Breakout | Breakout | 0.10 |
| C9 | `failed_breakout_reversal` | Failed Breakout Reversal | Reversal | 0.09 |
| C10 | `liquidity_sweep_reversal` | Liquidity Sweep Reversal | Reversal | 0.09 |
| C11 | `gap_continuation_fade` | Gap Continuation/Fade | Event | 0.10 |

Total base weight: 1.00.

## Current Default Decision Settings

`defaultConfidenceDecisionSettings`:

- `strongBuyThreshold`: 0.65
- `buyThreshold`: 0.35
- `sellThreshold`: -0.35
- `strongSellThreshold`: -0.65
- `minimumActiveStrategies`: 3
- `minimumDirectionalAgreement`: 0.50
- `minimumAverageConfidence`: 0.45

Legacy defaults are recognized and replaced by current defaults:

- `strongBuyThreshold`: 0.70
- `buyThreshold`: 0.50
- `sellThreshold`: -0.50
- `strongSellThreshold`: -0.70
- `minimumActiveStrategies`: 4
- `minimumDirectionalAgreement`: 0.60
- `minimumAverageConfidence`: 0.55

## Current Trading/Sizing Defaults

WCA uses `defaultTradingSettings()` through `loadConfidenceTradingSettings()` and then maps WCA default sizing via `confidenceDefaultSizingSettings()` / `defaultSizingSettingsFromTradingSettings()`.

Important WCA sizing defaults when default sizing is enabled:

- `minimumBuyScore`: `confidenceTradingSettings.minimumBuyScore`
- `minimumSignalEdge`: `confidenceTradingSettings.minimumSignalEdge`
- `baseRiskPercent`: `confidenceTradingSettings.baseRiskPercent`
- `maxPositionPercent`: `confidenceTradingSettings.maxPositionPercent`
- `maxDailyLossPercent`: `confidenceTradingSettings.maxDailyLossPercent`
- `maxDailyTrades`: `confidenceTradingSettings.maxTradesPerDay`
- `fixedStopDistanceDollars`: `confidenceTradingSettings.fixedStopDistanceDollars`
- `atrStopMultiplier`: `confidenceTradingSettings.atrStopMultiplier`
- `minimumStopDistancePercent`: `confidenceTradingSettings.minimumStopDistancePercent`
- `maxSpreadPercent`: `confidenceTradingSettings.maxSpreadPercent`
- `minimumOneMinuteVolume`: `confidenceTradingSettings.minimumOneMinuteVolume`
- `maxParticipationPercent`: `confidenceTradingSettings.maxParticipationPercent`
- `maxAllowedShares`: `confidenceTradingSettings.maxAllowedShares`
- `pyramidingEnabled`: `confidenceTradingSettings.pyramidingEnabled`

When default sizing is disabled, WCA falls back to allocation-derived values from generic trading settings.

## Current Decision Formula

For every configured WCA strategy:

1. Build `ConfidenceMarket` from current regular-session candles.
2. Strategy returns `{ signal, confidence, reason }`.
3. Signal is normalized to `buy`, `sell`, or `hold`.
4. Direction is `+1`, `-1`, or `0`.
5. Effective weight is `baseWeight * confidenceSystemWeightMultiplier(...)`, clamped nonnegative and rounded.
6. Contribution is `direction * effectiveWeight * confidence`.

Aggregate scores:

- `buyScore = sum(effectiveWeight * confidence)` for Buy strategy rows.
- `sellScore = sum(effectiveWeight * confidence)` for Sell strategy rows.
- `activeWeight = sum(effectiveWeight)` for non-Hold strategy rows.
- `netScore = buyScore - sellScore`.
- `normalizedNetScore = netScore / activeWeight` when active weight is positive.
- `buyAgreement = buyWeight / activeWeight`.
- `sellAgreement = sellWeight / activeWeight`.
- `buyAverageConfidence = buyScore / buyWeight`.
- `sellAverageConfidence = sellScore / sellWeight`.

Raw decision:

- Strong Buy when normalized score is at least `strongBuyThreshold` and Buy requirements pass.
- Buy when normalized score is at least `buyThreshold` and Buy requirements pass.
- Strong Sell when normalized score is at most `strongSellThreshold` and Sell requirements pass.
- Sell when normalized score is at most `sellThreshold` and Sell requirements pass.
- Otherwise Hold.

Hard filters force final decision to Hold when any filter fails.

## Current Filters

`confidenceHardFilters` returns:

- Spread: fail when `spreadTooWide`.
- Liquidity: fail when `volumeTooLow`.
- ATR: fail on `extreme`; info on `high`; pass otherwise.
- Time: fail when new trades are blocked and raw signal is directional.
- Max Trades: fail when current effective WCA trade count reaches default max daily trades.
- Daily Loss: fail when WCA daily loss limit is reached.

Additional target-order gates:

- WCA late-session buy guard.
- WCA forecast safety blockers.
- Automatic short-cycle market availability.
- Automatic short-cycle VWAP requirement: close must be above VWAP.
- Sizing blocked reason.

## Current Quantity Formula

`confidencePositionSizing` calculates:

- `signalStrength = abs(normalizedNetScore)`.
- `sizeMultiplier` ladder:
  - `>= 0.80`: 1.00
  - `>= 0.70`: 0.75
  - `>= 0.60`: 0.50
  - `>= 0.50`: 0.25
  - otherwise 0
- Automatic WCA Buy can add short-cycle context boost before clamping to `[0.25, 1]`.
- `riskDollars = accountEquity * baseRiskPercent * sizeMultiplier`.
- Stop distance uses fixed stop when configured; otherwise max of ATR multiplier and minimum stop percent.
- Quantity caps:
  - shares by risk
  - shares by order allocation
  - shares by max position
  - shares by available buying power
  - shares by liquidity participation
  - max allowed shares
- Final quantity is floor of the smallest cap, or zero when signal is Hold, size multiplier is zero, or stop distance is unavailable.

## Current Entry Flow

1. `updateConfidenceAggregationPanel()` calculates WCA.
2. `renderConfidenceTradingSettingsPanel()` builds a target-order recommendation.
3. `confidenceTargetOrderRecommendation()` determines side, pricing, stop, target, quantity, and failed gates.
4. Manual order button uses shared `recordTradeHistory()` and shared order checks.
5. Automatic mode calls `maybeAutoSubmitConfidenceTargetOrder()`.
6. Automatic WCA submission only submits Buy orders, requires no duplicate candle/order key, applies shared automatic rejection checks, appends frontend trade history, and updates UI.

## Current Exit Flow

Frontend open-lot exits use shared open-order lot logic:

- selected/manual sell setup
- automatic sell setup
- protective stop and target values from `lotOrderTemplate`
- forecast shock-stop keep/close logic includes confidence mode
- opening grace logic includes confidence mode
- end-of-session behavior is implemented in WCA backtest, not as a dedicated backend WCA lifecycle

## Current Backtesting Flow

WCA daily backtest is frontend-owned:

- `maybeRunDailyAlgorithmBacktests()` coordinates daily algorithm backtest refresh.
- `runConfidenceDailyBacktestFromPreparedCandles()` runs WCA once the refreshed dataset is available.
- `backtestConfidenceAggregation()` replays recent regular sessions.
- Warmup is 60 bars.
- It calls `calculateConfidenceAggregationFromMarket()` with backtest-specific hard filters and sizing.
- Entries are long-only WCA short-cycle Buy entries.
- Exits are protective stop, target, short-cycle sell exit, WCA Sell signal, or end-of-session.
- Result is cached in `confidenceBacktestCache` and persisted to browser storage.

## ML Or Forecast Dependencies

WCA itself is deterministic and rule-based, but current surrounding entry/exit safety logic can read market forecast state:

- `forecastBuySafetyBlockers("confidence", ...)` can block WCA Buy orders.
- `forecastBuySafetyGates("confidence", ...)` can add failed target-order gates.
- `forecastStopOverrideKeepReason("confidence", ...)` can keep a stopped lot when forecast predicts upside.
- `forecastStopCloseReason("confidence", ...)` can close a stopped lot when forecast predicts downside.
- `openingGraceKeepReason("confidence", ...)` checks forecast when deciding whether to keep a previous-session stopped position during opening grace.
- Forecast safety decisions are logged to `market-forecast-safety-decision-log-v1`.

This is a baseline dependency to remove or neutralize in later WCA V2 steps, not a Step 0 behavior change.

## Global-Gate Dependencies

Current WCA does not use the backend neutral global-gate service as its authoritative decision boundary. It uses shared frontend controls and checks:

- `canSubmitTrades()`
- `automaticOrderRejectionReason()`
- `lateSessionAboveAverageBuyBlocker()`
- `forecastBuySafetyBlockers()`
- shared open-order controls
- local frontend trade history and duplicate-order keys

## Persistence Locations

Browser local storage:

- `weighted-confidence-decision-settings-v1`
- `weighted-confidence-trading-settings-v1`
- `weighted-confidence-target-order-overrides-v1`
- `trading-dashboard.confidence-trade-history.v1`
- `trading-dashboard.confidence-order-control-modes.v1`
- `trading-dashboard.confidence-order-control-overrides.v1`
- `trading-dashboard.confidence-backtest-result.v1`
- `market-forecast-safety-decision-log-v1` for shared forecast-safety records

Backend/browser snapshots:

- Generic browser storage snapshot service stores local storage snapshots when enabled.
- Decision snapshot recorder can include WCA decisions as part of broader dashboard state.

## Characterization Fixtures

Golden fixtures for this Step 0 baseline:

- `backend/tests/fixtures/wca/golden_snapshots.json`
- Contains 100 WCA market/aggregation snapshots.
- Each snapshot stores strategy signal outputs, hard filters, sizing inputs, decision settings, trading settings, and expected WCA result.
- `backend/tests/test_wca_step0_characterization.py` recomputes current WCA aggregation and quantity behavior from the fixture.

## Implementation Boundaries For Future Refactor

- Do not reuse the eight-strategy Weighted Voting package for WCA.
- Extract WCA into its own backend package in later steps.
- Keep current behavior behind feature flags until parity passes.
- Preserve browser settings through a migration, but do not keep browser local storage as authoritative once backend WCA exists.

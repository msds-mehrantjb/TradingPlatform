# Regime Current-State Audit

Phase 0 audit date: 2026-07-15.

## Repository Baseline

- Current branch: `feature/ensemble-v2`
- Current commit: `4560309271663e85ec3be13566bd0e426b36584e`
- Pre-edit worktree state: existing uncommitted changes were present before Phase 0. Tracked modified files included `.gitignore`, `backend/app/config.py`, `backend/app/main.py`, `backend/app/meta_strategy_training.py`, `frontend/package.json`, `frontend/src/main.ts`, and `frontend/src/styles.css`. Many backend, frontend, docs, scripts, and test paths were untracked and treated as existing user work.
- Baseline frontend build: `npm run build` in `frontend` passed before Phase 0 edits.
- Baseline frontend unit tests: `npm test` in `frontend` passed before Phase 0 edits: 3 passed.
- Baseline backend tests: `.\backend\.venv\Scripts\python -m pytest backend\tests` passed before Phase 0 edits: 693 passed, 381 warnings.
- Baseline coverage: `pytest --cov=backend.app` could not run because `pytest-cov` is not installed in the active backend virtualenv. No coverage percentage is available from the current checked-out environment.

## Current Regime Surfaces

### Frontend Regime Selection

The active frontend Regime Selection algorithm is implemented in `frontend/src/main.ts` and is not isolated yet.

- Regime-specific types: `RegimePrimaryTrend`, `RegimeVolatilityState`, `RegimeOpportunityState`, `RegimeDecisionSignal`, `RegimeSelectionFeature`, `RegimeSelectedStrategy`, `RegimeSelectionScores`, `RegimeConditionSnapshot`, and `RegimeSelectionResult`.
- Strategy catalog: `regimeSelectionStrategies` contains all WCA/confidence aggregation strategies plus 8 extra Regime entries:
  - `vwap_trend_continuation`
  - `vwap_mean_reversion`
  - `failed_breakout_reversal`
  - `liquidity_sweep_reversal`
  - `adx_trend_strength`
  - `atr_volatility_regime`
  - `volatility_breakout`
  - `cash_avoid_filter`
- Shared WCA/confidence strategies pulled into Regime are:
  - `moving_average_trend`
  - `vwap_position`
  - `trend_pullback`
  - `rsi_mean_reversion`
  - `bollinger_band_mean_reversion`
  - `opening_range_breakout`
  - `intraday_breakout`
  - `macd_momentum`
  - `market_structure`
  - `gap_continuation_fade`
  - `volume_confirmation`
- Current strategy roles:
  - Trend-following / continuation: moving averages, trend pullback, VWAP trend continuation, MACD momentum, ADX trend strength.
  - Breakout: opening range breakout, intraday breakout, volatility breakout.
  - Mean reversion / reversal: VWAP mean reversion, RSI mean reversion, Bollinger mean reversion, failed breakout reversal, liquidity sweep reversal.
  - Context / filters: VWAP position, market structure, gap continuation/fade, volume confirmation, ATR volatility regime, cash/avoid trading.
- Main Regime functions:
  - `calculateRegimeSelection`
  - `regimeSelectionFeatures`
  - `rawRegimeConditionFromMarket`
  - `confirmedRegimeCondition`
  - `recentRegimeConditionKeys`
  - `aggregateRegimeStrategyScores`
  - `regimeMarketConditionConfidence`
  - `regimeTradeBlockers`
  - `classifyRegimePrimaryTrend`
  - `classifyRegimeVolatility`
  - `classifyRegimeOpportunity`
  - `regimeNoTradeReasons`
  - `regimeSelectedStrategySlugs`
  - `regimeStrategySelectorReason`
  - `regimeStrategyAvoidReason`
  - `regimeSelectionAsConfidenceResult`
  - `renderRegimeTradingSettingsPanel`
  - `renderRegimeDefaultSizingSection`
- Current Regime UI elements:
  - `algoRegimeSelectionTabButton`
  - `algoRegimeSelectionPanel`
  - `regimeFinalSignal`
  - `regimeScoreGrid`
  - `regimeSummary`
  - `regimeTradingSettingsMount`
  - `regimeIndicatorsToggle`
  - `regimeIndicatorsToggleMeta`
  - `regimeFeatureGrid`
  - `regimeStrategiesToggle`
  - `regimeStrategiesToggleMeta`
  - `regimeStrategiesList`
- Current Regime local-storage keys:
  - `regime-selection-trading-settings-v1`
  - `regime-selection-target-order-overrides-v1`
  - `trading-dashboard.regime-trade-history.v1`
  - `trading-dashboard.regime-order-control-modes.v1`
  - `trading-dashboard.regime-order-control-overrides.v1`
  - UI expansion state lives inside `trading-dashboard-ui-state-v1` via `regimeTradingSettingsExpanded`, `regimeDefaultSizingExpanded`, `regimeIndicatorsExpanded`, and `regimeStrategiesExpanded`.

### Backend Canonical Regime Classifier

The backend canonical regime classifier is `AdxAtrRegimeClassifier` in `backend/app/strategies/regime/adx_atr_regime.py`.

- Backend registry entries are in `backend/app/strategies/registry.py`:
  - `adx_trend_strength_regime`
  - `atr_volatility_regime`
- Existing backend regime labels:
  - `strong_trend`
  - `weak_trend`
  - `range`
  - `low_volatility`
  - `high_volatility`
  - `event_shock`
  - `unknown`
- Existing backend volatility states:
  - `LOW`
  - `NORMAL`
  - `HIGH`
  - `EXTREME`
- Backend regime features include `trendStrengthAdx`, `atr`, `atrPercentile`, `realizedVolatilityPercentile`, `rangeTrendClassification`, `volatilityExpansionContraction`, `directionalBias`, `trendFit`, `breakoutFit`, `reversalFit`, `meanReversionFit`, `gapSessionFit`, and `reasonCodes`.
- The backend classifier records that directional bias is context-only and must not substitute for a strategy signal.

## Existing Regime States and Hysteresis

Frontend Regime Selection states:

- Primary trend:
  - `Strong uptrend`
  - `Weak uptrend`
  - `Strong downtrend`
  - `Weak downtrend`
  - `Sideways / range-bound`
- Volatility:
  - `Low volatility`
  - `Normal volatility`
  - `High volatility`
- Opportunity:
  - `Trend continuation`
  - `Bullish breakout`
  - `Bearish breakout`
  - `Bullish reversal risk`
  - `Bearish reversal risk`
  - `Mean reversion`
  - `No-trade`

Confirmation and hysteresis:

- `lastConfirmedRegimeCondition` stores one confirmed condition in module state.
- `confirmedRegimeCondition` resets the stored condition when the symbol/session context key changes.
- A condition switch is accepted if the raw key is unchanged, or at least 2 of the last 3 recent condition keys match, or raw confidence is at least 65%.
- Otherwise the previous condition is held and `conditionHeld` blocks trading.
- There is no durable Regime hysteresis history beyond the current in-memory `lastConfirmedRegimeCondition`.

## Existing Regime Settings and Sizing

- Regime trading settings are loaded through `loadRegimeTradingSettings` and saved through `saveRegimeTradingSettings`.
- Regime settings reuse the generic `TradingSettings` shape and `sanitizeTradingSettings`.
- Regime-specific defaults are derived from `state.regimeTradingSettings` through `regimeDefaultSizingSettings`.
- Position sizing is shared with WCA/confidence via `confidencePositionSizing(..., { mode: "regime" })`.
- `confidencePositionSizing` switches to `state.regimeTradingSettings`, `regimeDefaultSizingSettings`, and `summarizePositionFromTradeHistory(..., "regime")` when `mode === "regime"`.
- Regime order intent is built by `confidenceTargetOrderRecommendation(adapted, "regime")`.
- Regime target overrides are stored separately in `state.regimeTargetOrderOverrides`.

## Current Regime Order Flow

1. `updateRegimeSelectionPanel` calls `calculateRegimeSelection`.
2. `renderRegimeTradingSettingsPanel` adapts the result with `regimeSelectionAsConfidenceResult`.
3. The adapted result is passed to `confidenceTargetOrderRecommendation(..., "regime")`.
4. The recommendation is stored as `state.currentRegimeTargetOrder`.
5. `maybeAutoSubmitRegimeTargetOrder` can use `state.currentRegimeTargetOrder`.
6. Global UI submission gates are `canSubmitTrades`, `tradingEnabled`, and `marketStatus`.
7. Order evidence and history are recorded through shared trade-history helpers with mode `regime`.
8. Regime trade history is stored separately in `state.regimeTradeHistory` and `trading-dashboard.regime-trade-history.v1`.

Live-trading posture:

- `state.tradingEnabled` defaults to `false`.
- `canSubmitTrades` returns true only when `tradingEnabled` is true and `marketStatus === "open"`.
- `AUTO_DAILY_ALGORITHM_BACKTESTS` is currently `false`.
- The frontend order submit mode default is currently `"Automatic"`, but automatic submission still requires the global `canSubmitTrades` gate.

## Shared Global Gates

Current shared global gates used by Regime are frontend-local:

- `tradingEnabled`
- `marketStatus`
- `isMarketOpenForOrders`
- `canSubmitTrades`
- `autoSubmittedOrderKeys`
- per-mode order-control settings and duplicate prevention

Backend global gates exist separately in:

- `backend/app/gates/engine.py`
- `backend/app/gates/account_risk.py`
- `backend/app/risk/global_gate_engine.py`
- `backend/app/trading_policy/engine.py`

The current frontend Regime Selection path does not call a dedicated shared global risk service.

## Existing Backtesting Paths

- Regime Selection does not have a dedicated frontend or backend backtest result object today.
- The Regime tab displays the shared WCA backtest panel through `confidenceBacktestResult`, `renderConfidenceBacktestState`, and `renderConfidenceBacktestSummary`.
- `maybeRunDailyAlgorithmBacktests` currently runs Voting Ensemble, Weighted Voting, and WCA refreshes when enabled; it does not run a dedicated Regime backtest.
- Backend replay paths do run the canonical `AdxAtrRegimeClassifier` as part of the V2 event replay engine.
- Backend `/api/v2/backtests/run` stores replay snapshots and includes `regimeState` in snapshot payloads, but this is not a dedicated Regime algorithm backtest path.

## Storage, Archive, Rendering, Backtest, and Submission Map

- Stored:
  - frontend Regime settings, target overrides, order-control modes/overrides, and trade history in local storage.
  - backend canonical `regime_states` rows in `DecisionSnapshotStore`.
- Archived:
  - `DecisionSnapshotStore` normalizes `snapshot.regimeState` into `regime_states`.
  - frontend browser storage snapshots include Regime settings and trade-history data.
- Rendered:
  - frontend Regime tab renders final signal, scores, summary, settings, indicators, selected/skipped strategy rows, and target order settings.
  - backend V2 decision panel renders canonical `RegimeState` under “Regime and safety”.
- Backtested:
  - frontend Regime currently piggybacks on WCA backtest UI/result naming.
  - backend canonical regime state is generated inside V2 replay/backtest snapshots.
- Submitted:
  - frontend Regime target orders flow through shared target-order and trade-ledger helpers using mode `regime`.
  - No backend Regime-specific paper execution service exists in this baseline.

## Known Coupling With WCA, Confidence, and Other Algorithms

- `regimeSelectionStrategies` directly spreads `confidenceAggregationStrategies`.
- Regime strategy entries call `confidence*` strategy functions, including WCA/confidence strategy logic.
- Regime uses `confidenceMarketSnapshot` and `confidenceMarketSnapshotFromCandles`.
- Regime uses `confidenceSystemWeightMultiplier`, `confidenceSignalDirection`, and `confidenceContractSignal`.
- Regime adapts output into `ConfidenceAggregationResult` through `regimeSelectionAsConfidenceResult`.
- Regime sizing and order construction use `confidencePositionSizing`, `confidenceEmptyPositionSizing`, `confidenceTargetOrderRecommendation`, and `confidenceTargetOrderFailedGates`.
- Regime rendering reuses `renderConfidenceDefaultSizingSection`, `renderConfidenceTargetOrderSettings`, and related confidence target-setting controls.
- Regime backtest display uses `confidenceBacktestResult`, which is a WCA backend backtest result converted through `backendWcaBacktestToFrontendResult`.
- The Meta-Strategy catalog also references confidence and ensemble strategy names, but Phase 0 made no behavior changes there.

## Existing Buy/Sell Asymmetry

- Regime trade permission is Buy-centered:
  - `regimeTradeBlockers` blocks whenever the aggregate final signal is not `buy`.
  - It requires buy score at least 60%.
  - It requires buy edge at least 20%.
- `calculateRegimeSelection` can return `Sell`, but `tradeAllowed` only unlocks Buy.
- `regimeSelectionAsConfidenceResult` converts non-trade-allowed results to `Hold`, except it preserves a displayed `Sell` signal when the Regime signal is `Sell`.
- `confidenceTargetOrderRecommendation(..., "regime")` can price Sell orders, but the Regime gates usually prevent Sell target orders from becoming eligible.

## Current ML Dependencies

- Frontend Regime Selection has no dedicated ML artifact.
- Backend canonical `RegimeState` is consumed by ML feature generation in `backend/app/ml/features.py` as `regime_category`, `adx`, `atr_percentile`, `realized_volatility_percentile`, and family-fit features.
- Meta-strategy training reads `regimeState.label` and can select trend/reversion families based on regime-derived context.
- Dynamic policy shadow paths can reconstruct a fallback unknown `RegimeState` if replay snapshots lack one.

## Phase 0 Characterization Coverage

Phase 0 added a deterministic characterization test in `backend/tests/test_adx_atr_regime.py` covering five current canonical regime outputs:

- strong uptrend
- strong downtrend
- range
- low volatility
- event shock

The test captures label, direction, volatility, confidence, family-fit values, range/trend classification, volatility expansion/contraction, and reason codes.

## Missing Tests

- No frontend unit test directly exercises `calculateRegimeSelection`.
- No test confirms the frontend Regime strategy catalog stays separate from WCA/confidence once isolation starts.
- No test confirms Regime local-storage keys never overlap WCA, Weighted Voting, Meta, Voting Ensemble, or Future Market Prediction keys.
- No test confirms Regime never reads or mutates WCA weights, WCA thresholds, Meta models, other algorithms' learned state, other algorithms' trade histories, or other position-sizing profiles.
- No dedicated Regime order-intent builder test exists; current coverage is indirect through shared confidence order helpers.
- No dedicated Regime trade-history persistence test exists.
- No dedicated Regime backtesting path or Regime backtest-result persistence test exists.
- No frontend test asserts the Buy/Sell asymmetry documented above.
- No test enforces that live trading remains disabled for new Regime functionality.
- No coverage metric is currently available because `pytest-cov` is absent from the backend virtualenv.

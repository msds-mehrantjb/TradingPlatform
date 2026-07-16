# WCA Current Behavior

This file freezes the current WCA behavior before refactoring. It is descriptive only and does not change runtime behavior.

## Current Owner

Current authoritative owner: frontend `confidence` mode in `frontend/src/main.ts`.

Current behavior is not yet a backend WCA engine. The frontend calculates the decision, target order, local gates, automatic paper-trade append, trade history, and WCA short-cycle backtest.

## Data Readiness

When no current regular-session candles are available:

- final signal is Hold
- scores are zero
- active strategy count is zero
- every strategy row is Hold with zero confidence
- sizing quantity is zero
- hard filter indicates waiting for market data

## Strategy Behavior

WCA evaluates 11 confidence strategies. Each returns:

- Buy, Sell, or Hold
- confidence value
- human-readable reason

Strategy output is then weighted. WCA does not currently use trained model probabilities for strategy confidence.

## Weight Behavior

Base weights sum to 1.00. Effective weights are dynamic but rule-based:

- ADX regime can favor trend, breakout, mean-reversion, or VWAP-like strategies.
- ATR regime can reduce or increase influence by family.
- Volume context can reduce weak breakout/trend setups or lightly boost active volume setups.
- Time of day adjusts weights, especially opening drive and midday.

Hold signals keep a multiplier of 1 but do not contribute to active directional weight.

## Score Formula

Current aggregation:

```text
buyScore = sum(effectiveWeight * confidence) for Buy rows
sellScore = sum(effectiveWeight * confidence) for Sell rows
activeWeight = sum(effectiveWeight) for Buy/Sell rows
netScore = buyScore - sellScore
normalizedNetScore = netScore / activeWeight
```

Directional agreement:

```text
buyAgreement = buyWeight / activeWeight
sellAgreement = sellWeight / activeWeight
buyAverageConfidence = buyScore / buyWeight
sellAverageConfidence = sellScore / sellWeight
```

Decision thresholds:

- Strong Buy: normalized score >= 0.65 plus Buy requirements.
- Buy: normalized score >= 0.35 plus Buy requirements.
- Strong Sell: normalized score <= -0.65 plus Sell requirements.
- Sell: normalized score <= -0.35 plus Sell requirements.
- Hold: otherwise.

Requirement gates:

- at least 3 active strategies
- at least 50% directional agreement
- at least 45% average confidence on the winning side

Hard-filter failure changes final decision to Hold.

## Hard Filters

Current hard filters are:

- Spread
- Liquidity
- ATR
- Time
- Max Trades
- Daily Loss

Failure of any hard filter forces Hold. High ATR may be informational; extreme ATR fails.

## Position Sizing

Current size ladder:

| Normalized signal strength | Size multiplier |
| --- | --- |
| >= 0.80 | 1.00 |
| >= 0.70 | 0.75 |
| >= 0.60 | 0.50 |
| >= 0.50 | 0.25 |
| < 0.50 | 0.00 |

Risk dollars:

```text
riskDollars = accountEquity * baseRiskPercent * sizeMultiplier
```

Stop distance:

```text
stopDistance = fixed stop if set
otherwise max(ATR * atrStopMultiplier, price * minimumStopDistancePercent)
```

Quantity caps:

- risk budget
- order limit
- max position
- buying power
- liquidity participation
- max allowed shares

Final quantity is the floor of the smallest cap. Quantity is zero when final signal is Hold, size multiplier is zero, or stop distance is invalid.

## Target Order Behavior

Target orders are created by `confidenceTargetOrderRecommendation()`.

Current behavior:

- Manual mode can produce Buy, Sell, or Hold based on WCA final signal.
- Automatic WCA mode forces candidate side to Buy.
- Automatic WCA mode applies a short-cycle sizing boost.
- Automatic WCA mode requires close above VWAP.
- Trigger and limit prices are derived from latest execution candle plus or minus configured slippage.
- Stop uses WCA stop distance.
- Target uses `takeProfitR` and target minimum-profit helpers.
- Target-order overrides are stored in local storage when default sizing is off.

## Automatic Submission Behavior

`maybeAutoSubmitConfidenceTargetOrder()`:

- only runs when global frontend trade submission is enabled
- requires current confidence target order
- requires submit mode Automatic
- submits only Buy orders
- blocks duplicate candle submissions
- uses shared automatic quantity and rejection checks
- appends frontend WCA trade history on success
- stores duplicate-order key in browser local storage

This is current behavior only. Later WCA V2 steps should move authority to backend paper-order execution.

## Trade History And Position Behavior

Current WCA trade history is browser-local:

- `trading-dashboard.confidence-trade-history.v1`
- max persisted rows: latest 50 rows

Position calculation:

- rebuild open lots from Buy/Sell rows
- Sell rows with `closedLotId` reduce matching lot quantity
- realized P/L is calculated from same-day closed WCA lots
- open P/L is calculated against current latest price

## Backtest Behavior

Current WCA backtest is a frontend replay:

- uses recent regular-session one-minute candles
- requires at least 60 candles in a session
- warmup starts at bar 60
- uses `calculateConfidenceAggregationFromMarket()` with backtest filters and sizing
- entries are long-only WCA Buy entries
- entries happen at candle close plus slippage
- exits use stop, target, short-cycle sell exit, WCA Sell signal, or end of session
- results are cached and stored in `trading-dashboard.confidence-backtest-result.v1`

The current WCA backtest is not yet production-parity backend execution.

## Forecast And ML-Adjacent Dependencies

WCA strategy aggregation is rule-based, but current shared safety logic can use market forecast output around WCA entries/exits:

- WCA Buy can be blocked by forecast conflict or overextension guard.
- A WCA stopped lot can be kept when forecast predicts upside.
- A WCA stopped lot can be closed when forecast predicts downside.
- Opening grace can consider forecast state.

These are documented as existing dependencies for future removal or redesign. Step 0 does not alter them.

## Golden Behavior Fixtures

Current characterization fixtures:

- `backend/tests/fixtures/wca/golden_snapshots.json`
- 100 snapshots
- includes Buy, Sell, Hold, spread failure, liquidity failure, ATR failure, and time failure cases

The characterization test recomputes:

- scores
- normalized score
- agreement
- raw decision
- hard-filtered final decision
- sizing quantity and caps

This fixture is the baseline parity target for later WCA backend extraction.

# Regime Dynamic Profiles

## Settings Layers

Regime uses three explicit layers:

| Layer | Mutability | Purpose |
| --- | --- | --- |
| Base settings | User-edited and saved only | Immutable baseline for risk, allocation, thresholds, stops, and limits. |
| Dynamic modifiers | Derived per decision | Conservative temporary tightening based on regime and context. |
| Effective settings | Read-only output | Values used for sizing/order planning for the current confirmed market condition. |

Dynamic settings may tighten baseline limits. They do not rewrite the saved baseline and do not loosen global hard limits.

## Base Defaults

| Setting | Default |
| --- | ---: |
| `startingCapital` | 25000 |
| `orderAllocationPercent` | 10 |
| `dailyAllocationPercent` | 50 |
| `baseRiskPercent` | 0.25 |
| `maxPositionPercent` | 50 |
| `maxTradesPerDay` | 10 |
| `minimumWinningScore` | 0.60 |
| `minimumDirectionalEdge` | 0.20 |
| `minimumRegimeConfidence` | 0.65 |
| `minimumActiveStrategies` | 3 |
| `minimumIndependentFamilies` | 2 |
| `maximumAbstentionRate` | 0.60 |
| `fixedStopDistanceDollars` | 1 |
| `atrStopMultiplier` | 2 |
| `minimumStopDistancePercent` | 0.05 |
| `takeProfitR` | 1.5 |
| `maximumHoldingMinutes` | 120 |
| `maximumVolumeParticipationPercent` | 0.3 |
| `minimumOneMinuteVolume` | 0 |
| `maximumAllowedShares` | 0, meaning no local share cap |
| `algorithmDailyLossPercent` | 1 |
| `pyramidingEnabled` | true |
| `shortEntriesEnabled` | false |
| `slippagePerShare` | 0.02 |
| `mlMode` | shadow |

## Profile Matrix

| Regime | Risk | Allocation | Position cap | ATR stop | Target R | Entry behavior |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `strong_uptrend`, `strong_downtrend` | 1.00x | 1.00x | 1.00x | +0.10 | 1.10x | Normal. |
| `weak_uptrend`, `weak_downtrend` | 0.65x | 0.70x | 0.70x | +0.00 | 1.00x | Selective entries; threshold tightened. |
| `range_bound`, `sideways_range` | 0.70x | 0.75x | 0.75x | -0.10 | 0.90x | Mean reversion only. |
| `opening_breakout`, `intraday_expansion` | 0.60x | 0.70x | 0.70x | +0.25 | 1.20x | Requires confirmation; threshold tightened. |
| `high_volatility_trend` | 0.50x | 0.60x | 0.60x | +0.35 | 1.15x | Selective entries; threshold tightened. |
| `low_volatility_quiet` | 0.40x | 0.50x | 0.50x | -0.15 | 0.80x | No breakout chasing. |
| `choppy_mixed` | 0.25x | 0.35x | 0.35x | +0.00 | 0.80x | Maximum one selective trade. |
| `failed_breakout_reversal` | 0.60x | 0.70x | 0.70x | +0.05 | 1.00x | Selective reversal entries. |
| `gap_session` | 0.60x | 0.70x | 0.70x | +0.15 | 1.05x | Confirmation required. |
| `event_risk`, `liquidity_stress`, `extreme_volatility_no_trade`, `no_trade` | 0 | 0 | 0 | +0.00 | 1.00x | No new entries. |

All risk, allocation, position, liquidity participation, and signal-size multipliers are clamped to no more than 1.0 in the current deployment.

## Additional Modifiers

| Modifier | Behavior |
| --- | --- |
| Time of day | Blocks when new trades are disallowed; midday tightens risk to 0.85x and allocation to 0.90x; closing window tightens to 0.50x and max one trade. |
| Event proximity | Currently neutral when unavailable; no loosening. |
| Spread | Blocks new entries when spread is too wide. |
| Quote freshness | Currently neutral when unavailable; no loosening. |
| Relative volume | Blocks below 0.55x; tightens risk/allocation/participation below 0.80x. |
| Account drawdown | Blocks at 2%; tightens at 1%. |
| Consecutive losses | Blocks at 3; tightens and max one trade at 2. |
| Account exposure | Blocks at 80%; tightens at 50%. |
| Regime stability | Tightens during transitions or unstable candidate regimes. |
| ML disagreement | In confirm-only mode, disagreement tightens risk, allocation, signal size, and score threshold. |

## Effective Formulas

```text
effectiveRiskPercent = baseRiskPercent * riskMultiplier * signalSizeMultiplier
effectiveOrderAllocationPercent = baseOrderAllocationPercent * allocationMultiplier
effectiveMaxPositionPercent = baseMaxPositionPercent * positionMultiplier
effectiveAtrStopMultiplier = max(0, baseAtrStopMultiplier + atrStopMultiplierAdjustment)
effectiveTakeProfitR = baseTakeProfitR * targetRMultiplier
effectiveMaximumParticipationPercent = baseMaximumParticipationPercent * liquidityParticipationMultiplier
effectiveMinimumWinningScore = clamp(baseMinimumWinningScore + winningScoreAdjustment, 0, 1)
effectiveMinimumDirectionalEdge = clamp(baseMinimumDirectionalEdge + directionalEdgeAdjustment, 0, 1)
effectiveMinimumRegimeConfidence = clamp(baseMinimumRegimeConfidence + regimeConfidenceAdjustment, 0, 1)
effectiveMaximumTrades = min(baseMaxTradesPerDay, maximumTradesOverride when present)
```

When the confirmed regime changes, Regime recalculates effective settings. It does not widen existing stops, move stops farther from market, or automatically increase an existing position. A no-trade profile blocks new entries while preserving protective exits.

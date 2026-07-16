# Family-Aware Deterministic Ensemble V2

Step 21 replaces V2 simple vote counting with `FamilyAwareDeterministicEnsemble`.

V1 `strategyVoteCatalog`, `strategyVote()`, and simple frontend vote counting remain available only as the existing baseline path. The V2 path is backend authoritative and consumes canonical `StrategySignal`, `ContextSignal`, `RegimeState`, and `GlobalGateDecision` objects.

## Sequence

1. Run the ten registered directional strategies exactly once.
2. Reject aggregator, context, regime, or safety modules as directional inputs.
3. Filter inactive, ineligible, data-unready, and Hold strategy outputs.
4. Compute each strategy value:

   `direction * confidence * reliability * regimeFit`

5. Aggregate values by independent strategy family using a weighted mean.
6. Apply the family-level regime fit from `RegimeState` when present.
7. Average independent family scores with equal initial family weights.
8. Apply bounded context effects. Context modules can confirm, weaken, or conflict with an existing candidate side; they cannot create a Buy or Sell candidate.
9. Apply hard safety. A blocking safety result forces Hold.
10. Return Buy, Sell, or Hold with diagnostics.

## Families

- Trend: Multi-Timeframe Trend Alignment, First Pullback After Open, VWAP Trend Continuation
- Breakout: Opening Range Breakout, Volatility Breakout
- Reversal: Failed Breakout Reversal, Liquidity Sweep Reversal
- Mean Reversion: VWAP Mean Reversion, Bollinger/ATR Reversion
- Gap/Session: Gap Continuation / Gap Fade

Strategies inside a family are averaged. Three trend strategies do not receive triple influence, and duplicating one strategy inside a family does not materially increase family influence.

## Configurable Thresholds

`FamilyAwareEnsembleConfig` is versioned and hashed. It includes:

- minimum final score
- minimum independent supporting families
- minimum family agreement
- maximum context conflict
- minimum eligible directional strategies
- maximum context adjustment per signal
- family weights

## Hold Conditions

The ensemble returns Hold for:

- ties
- weak raw or final score
- insufficient eligible directional strategies
- insufficient independent-family support
- conflicting families
- excessive context conflict
- safety block

## Returned Diagnostics

`EnsembleDecision` includes:

- `rawScore`
- `finalScore`
- `buyConfidence`
- `sellConfidence`
- `holdConfidence`
- `supportingFamilies`
- `opposingFamilies`
- `eligibleStrategyCount`
- `familyScores`
- `contextAdjustments`
- `safetyStatus`
- explanation, version, configuration hash, and decision timestamp

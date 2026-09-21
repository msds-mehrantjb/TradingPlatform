# Market Forecast Label Barriers V2

The market forecast predicts which of three things happens first within a horizon (5, 10
or 15 minutes): price travels `targetDistance` up, price travels `stopDistance` down, or
neither ("timeout"). Those two distances define the question the model is asked. If they
are not symmetric, the answer is decided before the model sees a single feature.

## What was wrong

`DEFAULT_MIN_STOP_PCT` was 0.25% of price while `DEFAULT_MIN_TARGET_PCT` was 0. The
barriers are floors combined with ATR:

```
targetDistance = max($0.25, price × 0.0,    ATR)   ≈ $0.30
stopDistance   = max($0.25, price × 0.0025, ATR)   ≈ $1.90   (SPY at 760)
```

The down barrier sat about six times farther from the entry than the up barrier, so at 15
minutes "up first" was the label **65%** of the time and "down first" **4%**.

A model trained on that learns to answer "up" and stops there. Measured on the live
prediction ledger over 34 sessions (~8,500 predictions per horizon), the deployed model
predicted "up" on **96%** of 10-minute and **99%** of 15-minute forecasts, and was
directionally correct 51% and 50% of the time. Its reported accuracy (0.593 at 10 minutes,
0.655 at 15) equalled the base rates exactly, which is the signature of a model that always
answers the majority class.

The serving path had the mirror-image skew: it forced a $1.00 target floor
(`DEFAULT_PROFIT_TARGET_DOLLARS`) while the stop floor came from the artifact's own label
config ($0.25). Probabilities trained against one pair of barriers were then interpreted
against another.

## What changed

- `DEFAULT_MIN_STOP_PCT` is 0.0, symmetric with the target floor.
- `volatility_adjusted_barriers` serves each model the barriers recorded in its own
  artifact, including the fixed target, and falls back to defaults only when the artifact
  does not specify them.
- A configured **0.0** is honoured. `numeric(...) or DEFAULT` treated zero as missing, so a
  model trained on symmetric barriers would have been served the asymmetric default.
- Models trained before this keep the barriers they learned, because their artifacts carry
  an explicit `minStopPct` of 0.0025.

Covered by `backend/tests/test_market_forecast_label_barriers.py`.

## What a fair question revealed

Retraining on symmetric barriers (SPY, SIP one-minute bars, 2022-01 to 2026-09, 17,221
training rows, untouched holdout of 2,871):

| | Asymmetric labels | Symmetric labels |
|---|---|---|
| 15m class balance (up / down) | 65% / 4% | 50% / 47% |
| 15m AUC | 0.561 | 0.533 |
| 10m AUC | 0.573 | 0.536 |
| 5m AUC | 0.586 | 0.573 |
| Says "buy" (15m holdout) | 99.6% | 64% |
| 15m precision when it commits | 0.646 (base rate 0.645) | 0.515 (base rate 0.498) |

The classes balance, and the edge disappears: AUC around 0.53 at 10 and 15 minutes is a
coin flip, and precision beats the base rate by 1-2 points. The earlier "65% accuracy" was
never skill; it was the base rate of a rigged question.

**No candidate was promoted.** The live artifact is still the 2026-09-05 model. Replacing it
would trade a model with flattering metrics for one with honest near-zero ones, with no
measured gain.

## How much the forecast is worth today

Measured from the live ledger, at moments resembling a stop-out (a 3-minute fall of 0.05%
or worse, 396 cases): price was lower 15 minutes later 52% of the time, the average move
was **-$0.27** a share, and filtering on the forecast saying "up" did not help (**-$0.24**).

So the forecast should stay advisory, as it is: it can nudge a directional candidate at
entry, it cannot authorise an entry, and it has no say in exits. Do not let it hold a
losing position open, and do not wire it into the backtest engine (which omits it today, a
parity gap with live) until a retrained model shows an edge on an untouched holdout.

## If you pick this up again

The features are price and volume summaries of the trailing 60 minutes. At 0.53 AUC on a
fair question, they do not carry a 5-15 minute barrier outcome. More rows will not fix that:
tripling the training data (5,798 to 17,221) and adding the 2022 decline moved AUC by less
than 0.02. New information (order-flow imbalance, book depth, index or sector lead-lag)
is the thing to try, not more of the same.

Note that microstructure features are trained-on but never supplied at inference; measured
effect on predictions is about 0.01 in probability, and the direction never flipped across
35 samples, so `--no-require-microstructure` unlocks years of training data at negligible
cost.

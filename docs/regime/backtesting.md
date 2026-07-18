# Regime Backtesting

## Dedicated Path

Regime has a dedicated backtest engine in `frontend/src/algorithms/regime/backtest/engine.ts`. It imports the same Regime decision core used for paper decisions and does not reuse WCA backtest results.

Daily refresh uses independent Regime state, cache key, storage key, result object, failure message, API path, artifact path, and UI panel.

The dedicated inventory is:

| File | Responsibility |
| --- | --- |
| `engine.ts` | Authoritative Regime replay loop and result assembly. |
| `execution-simulator.ts` | Entry, exit, cost, slippage, volume participation, global-cap, position-ledger, and trade-ledger simulation helpers. |
| `metrics.ts` | Regime metrics, reports, and strategy-family attribution summaries. |
| `diagnostics.ts` | Backtest diagnostics and executable inventory status. |
| `walk-forward.ts` | Walk-forward runner wrapper. |
| `runner.ts` | Node runner adapter for Regime backtests. |
| `types.ts` | Backtest inputs, decisions, trades, metrics, reports, comparisons, folds, and inventory contracts. |

The inventory explicitly owns Regime replay, warm-up handling, point-in-time classification, hysteresis replay, strategy routing, dynamic-profile reconstruction, family aggregation, entry and exit simulation, costs and slippage, position and trade ledgers, Regime-segmented performance, strategy-family attribution, walk-forward validation, untouched holdout testing, and daily independent backtests.

## Replay Flow

For each historical timestamp:

```text
slice candles through t
-> calculate Regime decision from point-in-time data
-> update hysteresis
-> route strategies
-> evaluate context and safety gates
-> aggregate family scores
-> derive dynamic profile
-> calculate requested quantity
-> simulate global gate quantity
-> create order intent
-> fill no earlier than t+1
-> manage open position
-> record decision/trade/report metrics
```

## Anti-Lookahead

- A signal calculated from candle `t` cannot fill on candle `t`.
- Default execution starts after `orderDelayBars`, currently 1 bar.
- Historical candles are sliced through the decision timestamp.
- Stop/target ambiguity is conservative: if both are touched intrabar, stop evaluation happens before target evaluation.
- Gap-through-stop exits fill at the next candle open when the open crosses the stop.
- External event, quote, QQQ, IWM, VIX, ES, and breadth feeds must be supplied with publication timestamps and freshness thresholds before use.
- ML artifacts used in historical simulation must have training end before the simulated decision timestamp.
- Final test-period thresholds must not be optimized on that same period.

## Fill Model

The default cost and fill model is:

| Field | Default |
| --- | ---: |
| Spread percent | 0.0002 |
| Slippage per share | 0.01 |
| Fee per share | 0.0002 |
| Maximum volume participation | 0.03 |
| Order delay bars | 1 |
| Reject zero participation quantity | true |

Entry price is next-bar open plus half-spread and slippage in the order direction. Quantity is capped by simulated global gate capacity and volume participation. Partial fills are recorded when participation caps reduce fills.

## Trade Management

The backtest records protective stops, profit targets, gap-through-stop fills, end-of-backtest exits, MAE, MFE, fees, slippage, P/L, R multiple, holding time, exit reason, dynamic profile, strategy IDs, family scores, limiting quantity cap, and global approved quantity.

## Reports And Metrics

Reports cover confirmed regime, raw regime, transition versus stable regime, strategy, family, long versus short, time of day, volatility, liquidity, event period, dynamic profile, signal-strength bucket, winning-score bucket, edge bucket, regime-confidence bucket, month/year, exit reason, and limiting cap.

Metrics include net return, net profit, trade count, win rate, profit factor, expectancy, average R, Sharpe, Sortino, maximum drawdown, drawdown duration, Calmar, exposure, turnover, average holding time, long/short performance, regime coverage, no-trade percentage, regime-switch frequency, average confirmation delay, false-transition rate, blocked-trade counterfactual result, and static-versus-dynamic profile difference.

## Required Comparisons

The engine reports these comparison variants:

- Rule classifier with static settings.
- Rule classifier with dynamic profiles.
- Rule classifier plus ML in shadow mode.
- Rule classifier plus ML in confirm-only mode.
- Regime routing without context modifiers.
- Regime routing with context modifiers.
- Regime routing without family caps.
- Regime routing with family caps.
- Long-only behavior.
- Long-and-short behavior when supported.

Walk-forward summaries include training, validation, and test periods and reject insufficient trade counts.

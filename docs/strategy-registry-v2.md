# Strategy Registry V2

The canonical V2 strategy registry lives in `backend/app/strategies/registry.py`.

The directional collection contains exactly ten initial strategies:

1. Multi-Timeframe Trend Alignment
2. First Pullback After Open
3. VWAP Trend Continuation
4. Opening Range Breakout
5. Volatility Breakout
6. Failed Breakout Reversal
7. Liquidity Sweep Reversal
8. VWAP Mean Reversion
9. Bollinger/ATR Reversion
10. Gap Continuation / Gap Fade

Non-directional modules are registered separately as context, regime, safety, or aggregator modules. `Ensemble Strategy Voting` is registered only as an aggregator and is rejected if inserted into a directional-voter list.

Old V1 names resolve through `STRATEGY_ALIAS_MAP`. Alias resolution returns canonical strategy IDs and de-duplicates repeated aliases before voting, so legacy names cannot create multiple strategy instances.


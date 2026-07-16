# Global Account Risk V2

Global account risk is aggregated from broker-authoritative state before automatic new-entry gates run. Local UI trade history is not considered authoritative for exposure, reconciliation, or daily-loss decisions.

## Included Exposure

- Voting Ensemble positions
- Weighted Voting positions
- Confidence Aggregation positions
- Regime Selector positions
- Meta-Strategy positions
- pending orders
- partially filled orders

## Calculated Fields

- global open risk
- global SPY notional
- global same-direction exposure
- global daily realized P&L
- global daily unrealized P&L
- conservative estimated exit costs
- daily net P&L after exit costs
- drawdown from intraday equity high

Daily-loss enforcement uses realized P&L plus unrealized P&L minus conservative exit costs. This prevents an account with large open losses from continuing to open new trades just because those losses have not been realized.

## Portfolio Netting

Portfolio netting is not enabled. Until a future explicit design exists, duplicate same-direction SPY exposure and conflicting SPY exposure from another algorithm block automatic new entries. Protective exits, risk-reducing orders, end-of-day liquidation, and reconciliation actions remain permitted through the global gate engine's non-entry intent handling.

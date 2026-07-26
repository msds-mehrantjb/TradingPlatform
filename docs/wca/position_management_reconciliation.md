# WCA Position Management and Reconciliation

Step 11 adds WCA-owned continuous paper position management on top of the durable inventory from the execution outbox.

## Authoritative State

WCA rebuilds paper positions from `wca_owned_lots`, `wca_attributed_fills`, `wca_virtual_positions`, `wca_trade_ledger`, `wca_exit_state`, and `wca_broker_reconciliations`.

Each mutable record remains scoped with `algorithm_id = "wca"` plus account, symbol, decision, configuration, event, and run identifiers where the table supports them. WCA closes only WCA-owned lots through `close_wca_attributed_position_quantity`; broker net SPY quantity is never used as ownership proof.

## Runtime Management

The background runtime enqueues a position/protective-exit command after each finalized-bar decision. The worker uses the completed bar close as the mark price and calls the WCA position manager. The manager maintains:

- open quantity and average entry from durable WCA lots;
- realized P&L from the WCA trade ledger;
- unrealized P&L from the current mark;
- persisted stop, target, trailing, time-exit, signal-exit, emergency-exit and end-of-day state;
- pending risk-reducing exit orders when a stop, target, end-of-day flatten, time exit, signal exit, emergency reduction, or unprotected-position circuit breaker is due.

Protective position management remains separate from new-entry permission. A reconciliation block or circuit breaker pauses new entries while position management continues to produce risk-reducing exit state.

## Market Calendar

End-of-day flattening uses the WCA-owned `WcaMarketCalendar`, including regular NYSE/Nasdaq holidays and early closes such as the day after Thanksgiving. The calendar module is isolated from sibling algorithm packages.

## Reconciliation

WCA broker reconciliation compares:

- WCA internal order intents;
- WCA broker-attributed open orders and positions;
- WCA fills;
- WCA-owned open lots;
- broker net position across algorithms;
- the shared global attribution ledger when supplied.

Discrepancies are persisted to `wca_broker_reconciliations`. Hard discrepancies block new WCA entries, but protective exits remain operational. If broker netting combines algorithms, WCA compares the broker net against the sum of attributed algorithm inventories and never absorbs another algorithm's quantity to make totals match.

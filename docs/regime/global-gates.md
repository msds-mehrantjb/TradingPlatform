# Global Portfolio Gates

## Contract

Backend global risk evaluates an algorithm order intent and returns:

```text
status: approved | resized | denied
approvedQuantity
approvedRiskDollars
passedGates
failedGates
warningGates
accountSnapshotId
reservationId
evaluatedAt
```

The global manager can approve, resize, or deny. It must not change Regime strategy signals, classifier state, strategy weights, dynamic settings, or ML artifacts.

## Global Defaults

| Setting | Default |
| --- | ---: |
| `masterNewEntryEnabled` | true |
| `emergencyKillSwitch` | false |
| `cancelPendingEntriesOnEmergency` | true |
| `flattenOnEmergency` | false |
| `tradingEnabled` | true |
| `globalMaximumDailyLossPercent` | 0, disabled |
| `globalMaximumGrossExposurePercent` | 0, disabled |
| `globalMaximumNetExposurePercent` | 0, disabled |
| `globalMaximumSymbolExposurePercent` | 0, disabled |
| `globalMaximumSectorExposurePercent` | 0, disabled |
| `globalMaximumOpenRiskPercent` | 0, disabled |
| `globalMaximumConcurrentPositions` | 0, disabled |
| `globalMaximumPendingOrders` | 0, disabled |
| `globalMaximumTradesPerDay` | 0, disabled |
| `globalMaximumOrdersPerMinute` | 0, disabled |
| `globalMaximumSpreadPercent` | 0, disabled |
| `globalMaximumEstimatedSlippagePercent` | 0, disabled |
| `globalQuoteStaleSeconds` | 15 |
| `globalCandleStaleSeconds` | 120 |
| `globalNewEntryCutoff` | 15:30 |
| `minimumOneMinuteVolume` | 0 |
| `maximumShareCap` | 0, disabled |
| `maximumNotionalCap` | 0, disabled |
| `requireSettledCash` | false |
| `shortSalesEnabled` | false |
| `eventBlackoutPolicy` | block_new_entries |

## System And Broker Gates

- Manager version.
- Master new-entry switch.
- Normal trading-enabled switch.
- Emergency kill switch.
- Broker API connectivity.
- Broker account active status.
- Trading permission.
- Clock synchronization.
- Current account snapshot freshness.
- Local/broker order reconciliation.
- Local/broker position reconciliation.
- Unresolved submission failure.
- Broker rate-limit protection.

The normal trading switch can block new entries without blocking protective exits. Emergency kill switch can block exits when configured as an emergency state.

## Market Gates

- Regular-session permission.
- Premarket/after-hours permission.
- Market holiday.
- Early close.
- New-entry cutoff.
- Trading halt.
- LULD.
- Market-wide circuit breaker.
- Stale candle.
- Stale quote.
- Excessive spread.
- Insufficient liquidity.
- Excessive estimated slippage.
- Event blackout.
- Unsupported order type.

## Portfolio Gates

- Available buying power.
- Settled cash, when required.
- Account-wide realized daily loss.
- Account-wide unrealized daily loss.
- Account-wide drawdown.
- Total gross exposure.
- Total net exposure.
- Per-symbol aggregate exposure.
- Per-sector exposure.
- Total risk to protective stops.
- Risk from pending orders.
- Maximum concurrent positions.
- Maximum pending orders.
- Maximum account-wide trades per day.
- Maximum algorithm-specific trades per day.
- Maximum orders per minute.
- Remaining daily risk capacity.

Quantity can be resized by buying power, gross exposure, symbol exposure, and remaining open-risk capacity.

## Order Integrity Gates

- Duplicate decision ID.
- Duplicate client-order ID.
- Existing pending order for same intent.
- Conflicting simultaneous orders.
- Invalid quantity.
- Invalid price.
- Invalid stop relationship.
- Expired intent.
- Intent based on stale market data.
- Insufficient shortability.
- Unsupported fractional quantity.
- Maximum share cap.
- Maximum notional cap.

Short entries require global short sales to be enabled, asset shortability, and borrow availability when known.

## Reservations

Before submission, global risk can reserve buying power and risk for an approved quantity. Active reservations reduce available buying power and appear as pending risk for later algorithms, preventing two algorithms from consuming the same capacity. Reservations are committed with broker order ID or released on failure.

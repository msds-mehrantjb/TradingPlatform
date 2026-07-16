from __future__ import annotations

from datetime import datetime

from backend.app.risk.settings import GlobalRiskSettings
from backend.app.risk.types import GateResult, GlobalOrderIntent, MarketSnapshot


def evaluate_market_gates(intent: GlobalOrderIntent, market: MarketSnapshot, settings: GlobalRiskSettings, *, evaluated_at: datetime) -> tuple[GateResult, ...]:
    gates: list[GateResult] = []
    new_entry = intent.is_new_entry

    def add(gate_id: str, passed: bool, reason: str, *, warning: bool = False, blocks_exits: bool = False) -> None:
        gates.append(
            GateResult(
                gateId=gate_id,
                gateName=gate_id.replace("_", " ").title(),
                status="pass" if passed else "warning" if warning else "fail",
                reason=reason,
                blocksNewEntries=not passed and new_entry and not warning,
                blocksProtectiveExits=not passed and blocks_exits,
                evaluatedAt=evaluated_at,
            )
        )

    extended = market.session in {"premarket", "after_hours"}
    add("regular_session_permission", market.session != "regular" or market.regularSessionAllowed, "Regular-session permission evaluated.")
    add("extended_hours_permission", not extended or market.extendedHoursAllowed, "Premarket/after-hours permission evaluated.")
    add("market_holiday", not market.marketHoliday, "Market holiday gate evaluated.")
    add("early_close", not market.earlyClose or not new_entry, "Early-close gate evaluated.", warning=market.earlyClose and not new_entry)
    add("new_entry_cutoff", not market.entryCutoffReached or not new_entry, "New-entry cutoff evaluated.", warning=market.entryCutoffReached and not new_entry)
    add("trading_halt", not market.tradingHalt, "Trading halt gate evaluated.", blocks_exits=True)
    add("luld", not market.luld, "LULD gate evaluated.", blocks_exits=True)
    add("market_wide_circuit_breaker", not market.marketWideCircuitBreaker, "Market-wide circuit breaker evaluated.", blocks_exits=True)
    add("stale_candle", (market.evaluatedAt - market.candleTimestamp).total_seconds() <= settings.globalCandleStaleSeconds, "Stale candle gate evaluated.")
    add("stale_quote", (market.evaluatedAt - market.quoteTimestamp).total_seconds() <= settings.globalQuoteStaleSeconds, "Stale quote gate evaluated.")
    add("excessive_spread", market.spreadPercent is not None and (settings.globalMaximumSpreadPercent <= 0 or market.spreadPercent <= settings.globalMaximumSpreadPercent), "Spread gate evaluated.")
    add("insufficient_liquidity", market.oneMinuteVolume is not None and market.oneMinuteVolume >= settings.minimumOneMinuteVolume, "Liquidity gate evaluated.")
    add(
        "excessive_estimated_slippage",
        market.estimatedSlippagePercent is not None and (settings.globalMaximumEstimatedSlippagePercent <= 0 or market.estimatedSlippagePercent <= settings.globalMaximumEstimatedSlippagePercent),
        "Estimated slippage gate evaluated.",
    )
    add("event_blackout", not (market.eventBlackout and settings.eventBlackoutPolicy == "block_new_entries" and new_entry), "Event blackout policy evaluated.")
    add("unsupported_order_type", not market.unsupportedOrderType, "Unsupported order type gate evaluated.")
    return tuple(gates)


__all__ = ["evaluate_market_gates"]

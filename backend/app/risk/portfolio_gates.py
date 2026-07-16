from __future__ import annotations

from datetime import datetime
from math import floor

from backend.app.risk.settings import GlobalRiskSettings
from backend.app.risk.types import AccountSnapshot, GateResult, GlobalOrderIntent, PortfolioSnapshot


def evaluate_portfolio_gates(intent: GlobalOrderIntent, account: AccountSnapshot, portfolio: PortfolioSnapshot, settings: GlobalRiskSettings, *, evaluated_at: datetime) -> tuple[GateResult, ...]:
    new_entry = intent.is_new_entry
    metrics = portfolio_metrics(portfolio)
    symbol_exposure = metrics["symbolExposure"].get(intent.symbol.upper(), 0.0)
    sector_exposure = max(metrics["sectorExposure"].values(), default=0.0)
    pending_risk = sum(order.riskDollars for order in portfolio.pendingOrders)
    daily_loss = max(0.0, -(account.realizedDailyPnl + account.unrealizedDailyPnl))
    unrealized_loss = max(0.0, -account.unrealizedDailyPnl)
    drawdown = max(0.0, account.highWaterEquity - account.equity)

    return tuple(
        _gate(gate_id, passed, reason, new_entry, evaluated_at, warning=gate_id in RESIZABLE_GATES and not passed)
        for gate_id, passed, reason in (
            ("available_buying_power", account.availableBuyingPower >= min(intent.requested_notional, account.availableBuyingPower) and account.availableBuyingPower > 0, "Available buying power evaluated."),
            ("settled_cash", not settings.requireSettledCash or (account.settledCash or 0.0) >= intent.requested_notional, "Settled cash evaluated."),
            ("account_wide_realized_daily_loss", _under_percent(settings.globalMaximumDailyLossPercent, max(0.0, -account.realizedDailyPnl), account.equity), "Realized daily loss evaluated."),
            ("account_wide_unrealized_daily_loss", _under_percent(settings.globalMaximumDailyLossPercent, unrealized_loss, account.equity), "Unrealized daily loss evaluated."),
            ("account_wide_drawdown", _under_percent(settings.globalMaximumDailyLossPercent, drawdown, account.highWaterEquity), "Account drawdown evaluated."),
            ("total_gross_exposure", _under_percent(settings.globalMaximumGrossExposurePercent, metrics["grossExposure"] + intent.requested_notional, account.equity), "Gross exposure evaluated."),
            ("total_net_exposure", _under_percent(settings.globalMaximumNetExposurePercent, abs(metrics["netExposure"] + _signed_notional(intent)), account.equity), "Net exposure evaluated."),
            ("per_symbol_aggregate_exposure", _under_percent(settings.globalMaximumSymbolExposurePercent, symbol_exposure + intent.requested_notional, account.equity), "Symbol exposure evaluated."),
            ("per_sector_exposure", _under_percent(settings.globalMaximumSectorExposurePercent, sector_exposure, account.equity), "Sector exposure evaluated."),
            ("total_risk_to_protective_stops", _under_percent(settings.globalMaximumOpenRiskPercent, metrics["openRisk"] + intent.requestedRiskDollars, account.equity), "Open stop risk evaluated."),
            ("risk_from_pending_orders", _under_percent(settings.globalMaximumOpenRiskPercent, pending_risk + intent.requestedRiskDollars, account.equity), "Pending order risk evaluated."),
            ("maximum_concurrent_positions", settings.globalMaximumConcurrentPositions <= 0 or len(portfolio.positions) < settings.globalMaximumConcurrentPositions, "Concurrent positions evaluated."),
            ("maximum_pending_orders", settings.globalMaximumPendingOrders <= 0 or len(portfolio.pendingOrders) < settings.globalMaximumPendingOrders, "Pending orders evaluated."),
            ("maximum_account_wide_trades_per_day", settings.globalMaximumTradesPerDay <= 0 or portfolio.tradesToday < settings.globalMaximumTradesPerDay, "Trades per day evaluated."),
            ("maximum_algorithm_specific_trades_per_day", settings.globalMaximumTradesPerDay <= 0 or portfolio.algorithmTradesToday.get(intent.algorithmId, 0) < settings.globalMaximumTradesPerDay, "Algorithm trades per day evaluated."),
            ("maximum_orders_per_minute", settings.globalMaximumOrdersPerMinute <= 0 or portfolio.ordersSubmittedInLastMinute < settings.globalMaximumOrdersPerMinute, "Orders per minute evaluated."),
            ("remaining_daily_risk_capacity", settings.globalMaximumDailyLossPercent <= 0 or daily_loss + intent.requestedRiskDollars <= account.equity * settings.globalMaximumDailyLossPercent / 100.0, "Remaining daily risk capacity evaluated."),
        )
    )


def approved_quantity_cap(intent: GlobalOrderIntent, account: AccountSnapshot, portfolio: PortfolioSnapshot, settings: GlobalRiskSettings) -> int:
    if intent.is_protective_exit:
        return intent.requestedQuantity
    caps = [intent.requestedQuantity]
    _cap(caps, account.availableBuyingPower, intent.expectedEntryPrice)
    _cap(caps, _remaining_percent(settings.globalMaximumGrossExposurePercent, account.equity, portfolio_metrics(portfolio)["grossExposure"]), intent.expectedEntryPrice)
    _cap(caps, _remaining_percent(settings.globalMaximumSymbolExposurePercent, account.equity, portfolio_metrics(portfolio)["symbolExposure"].get(intent.symbol.upper(), 0.0)), intent.expectedEntryPrice)
    remaining_risk = _remaining_percent(settings.globalMaximumOpenRiskPercent, account.equity, portfolio_metrics(portfolio)["openRisk"] + sum(order.riskDollars for order in portfolio.pendingOrders))
    if remaining_risk is not None and intent.requestedRiskDollars > 0:
        caps.append(floor(intent.requestedQuantity * max(0.0, remaining_risk) / intent.requestedRiskDollars))
    return max(0, min(caps))


def portfolio_metrics(portfolio: PortfolioSnapshot) -> dict:
    symbol_exposure: dict[str, float] = {}
    sector_exposure: dict[str, float] = {}
    for position in portfolio.positions:
        symbol = position.symbol.upper()
        symbol_exposure[symbol] = symbol_exposure.get(symbol, 0.0) + position.marketValue
        if position.sector:
            sector_exposure[position.sector] = sector_exposure.get(position.sector, 0.0) + position.marketValue
    return {
        "grossExposure": sum(position.marketValue for position in portfolio.positions),
        "netExposure": sum(position.signed_value for position in portfolio.positions),
        "symbolExposure": symbol_exposure,
        "sectorExposure": sector_exposure,
        "openRisk": sum(position.openRiskDollars for position in portfolio.positions),
    }


def _gate(gate_id: str, passed: bool, reason: str, new_entry: bool, evaluated_at: datetime, *, warning: bool = False) -> GateResult:
    return GateResult(
        gateId=gate_id,
        gateName=gate_id.replace("_", " ").title(),
        status="pass" if passed else "warning" if warning else "fail",
        reason=reason,
        blocksNewEntries=not passed and new_entry and not warning,
        blocksProtectiveExits=False,
        evaluatedAt=evaluated_at,
    )


RESIZABLE_GATES = frozenset(
    {
        "available_buying_power",
        "settled_cash",
        "total_gross_exposure",
        "total_net_exposure",
        "per_symbol_aggregate_exposure",
        "per_sector_exposure",
        "total_risk_to_protective_stops",
        "risk_from_pending_orders",
        "remaining_daily_risk_capacity",
    }
)


def _under_percent(limit_percent: float, value: float, basis: float) -> bool:
    return limit_percent <= 0 or value <= basis * limit_percent / 100.0


def _remaining_percent(limit_percent: float, basis: float, used: float) -> float | None:
    if limit_percent <= 0:
        return None
    return max(0.0, basis * limit_percent / 100.0 - used)


def _cap(caps: list[int], remaining_dollars: float | None, price: float) -> None:
    if remaining_dollars is not None:
        caps.append(floor(max(0.0, remaining_dollars) / price))


def _signed_notional(intent: GlobalOrderIntent) -> float:
    return intent.requested_notional if intent.side == "Buy" else -intent.requested_notional


__all__ = ["approved_quantity_cap", "evaluate_portfolio_gates", "portfolio_metrics"]

"""Backtest ledger construction for Meta-Strategy simulations."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.meta_strategy.execution_pipeline import MetaStrategyExecutionPipelineResult
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


@dataclass(frozen=True)
class MetaStrategyBacktestTrade:
    algorithm_id: str
    decision_id: str
    symbol: str
    side: str
    requested_quantity: int
    filled_quantity: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float
    net_pnl: float
    partial_fill: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class MetaStrategyBacktestNoTrade:
    algorithm_id: str
    decision_id: str
    symbol: str
    status: str
    strategy_ids: tuple[str, ...]
    families: tuple[str, ...]
    regime: str
    session: str
    market_condition: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class MetaStrategyBacktestLedger:
    algorithm_id: str
    trades: tuple[MetaStrategyBacktestTrade, ...]
    no_trades: tuple[MetaStrategyBacktestNoTrade, ...]
    rejected_trade_count: int
    total_fees: float
    net_pnl: float


def ledger_from_pipeline_results(
    results: tuple[MetaStrategyExecutionPipelineResult, ...],
    *,
    fee_per_share: float,
    regulatory_fee_per_share: float = 0.0,
) -> MetaStrategyBacktestLedger:
    trades = tuple(
        _trade_from_result(result, fee_per_share=fee_per_share, regulatory_fee_per_share=regulatory_fee_per_share)
        for result in results
        if result.order_intent is not None
    )
    no_trades = tuple(_no_trade_from_result(result) for result in results if result.order_intent is None or not result.final_valid)
    return MetaStrategyBacktestLedger(
        algorithm_id=ALGORITHM_ID,
        trades=trades,
        no_trades=no_trades,
        rejected_trade_count=sum(1 for item in no_trades if item.status in {"REJECTED", "BLOCKED", "HOLD", "NO_TRADE"}),
        total_fees=sum(trade.fees for trade in trades),
        net_pnl=sum(trade.net_pnl for trade in trades),
    )


def _trade_from_result(
    result: MetaStrategyExecutionPipelineResult,
    *,
    fee_per_share: float,
    regulatory_fee_per_share: float,
) -> MetaStrategyBacktestTrade:
    order = result.order_intent
    if order is None:
        raise ValueError("cannot create ledger trade without order intent")
    requested = int(order.quantity)
    filled_value = result.broker_result.get("filledQuantity")
    filled = max(0, int(filled_value) if filled_value is not None else 0)
    entry = float(result.geometry.entry_reference or result.snapshot.last_price)
    if order.side == "BUY":
        exit_price = float(result.geometry.geometry.target_price or entry)
        gross = (exit_price - entry) * filled
    else:
        exit_price = float(result.geometry.geometry.target_price or entry)
        gross = (entry - exit_price) * filled
    fees = filled * (float(fee_per_share) + float(regulatory_fee_per_share))
    return MetaStrategyBacktestTrade(
        algorithm_id=ALGORITHM_ID,
        decision_id=result.snapshot.decision_id,
        symbol=order.symbol,
        side=order.side,
        requested_quantity=requested,
        filled_quantity=filled,
        entry_price=entry,
        exit_price=exit_price,
        gross_pnl=gross,
        fees=fees,
        net_pnl=gross - fees,
        partial_fill=0 < filled < requested,
        reason_codes=tuple(dict.fromkeys((*result.reason_codes, "meta_strategy.backtest.ledger_recorded"))),
    )


def _no_trade_from_result(result: MetaStrategyExecutionPipelineResult) -> MetaStrategyBacktestNoTrade:
    candidate = result.deterministic_candidate
    evidence = candidate.evidence if isinstance(candidate.evidence, dict) else {}
    directional = evidence.get("directionalOutputs") if isinstance(evidence.get("directionalOutputs"), dict) else {}
    regime = evidence.get("regimeCompatibility") if isinstance(evidence.get("regimeCompatibility"), dict) else {}
    market_condition = str((result.snapshot.features or {}).get("marketCondition") or (result.snapshot.liquidity or {}).get("condition") or "UNKNOWN")
    status = "REJECTED" if not result.final_valid else "HOLD" if result.order_intent is None else "NO_TRADE"
    return MetaStrategyBacktestNoTrade(
        algorithm_id=ALGORITHM_ID,
        decision_id=result.snapshot.decision_id,
        symbol=result.snapshot.symbol,
        status=status,
        strategy_ids=tuple(str(strategy_id) for strategy_id in directional.keys()),
        families=tuple(str(family) for family in candidate.supporting_families),
        regime="|".join(str(item) for item in regime.get("labels", ())) if regime else "UNKNOWN",
        session=result.snapshot.session_phase,
        market_condition=market_condition,
        reason_codes=tuple(dict.fromkeys((*result.reason_codes, "meta_strategy.backtest.no_trade_recorded"))),
    )


__all__ = [
    "MetaStrategyBacktestLedger",
    "MetaStrategyBacktestNoTrade",
    "MetaStrategyBacktestTrade",
    "ledger_from_pipeline_results",
]

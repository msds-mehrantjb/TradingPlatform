"""Backend-authoritative WCA backtest engine."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from typing import Iterable

from backend.app.algorithms.wca.configuration import default_baseline_settings
from backend.app.algorithms.wca.contracts import (
    BacktestResult,
    BacktestRunConfiguration,
    BacktestTrade,
    WcaBacktestComparison,
    WcaBacktestMode,
    WcaBacktestModeResult,
    WcaBacktestRequest,
    WcaBacktestSideMode,
    WcaBacktestSuiteResult,
    WcaCandle,
    WcaDecision,
    WcaEvaluationStatus,
    WcaGateStatus,
    GlobalGateResult as WcaGlobalGateResult,
    WcaMarketSnapshot,
    WcaQuote,
    WcaSide,
)
from backend.app.algorithms.wca.backtest.metrics import build_wca_backtest_diagnostics
from backend.app.algorithms.wca.exits import WcaBacktestOpenPosition, close_wca_backtest_trade, mark_to_market_pnl
from backend.app.algorithms.wca.execution_pipeline import WCA_EXECUTION_PIPELINE_MODULES, WcaExecutionPipelineInput, run_wca_execution_pipeline
from backend.app.algorithms.wca.strategies.indicators import eastern_minutes
from backend.app.algorithms.wca.strategies.primary_voters import WCA_PRIMARY_VOTERS
from backend.app.algorithms.wca.weights import baseline_weight_snapshot, performance_weight_snapshot
from backend.app.risk import (
    GlobalGateAccountState,
    GlobalGateEngine,
    GlobalGateInput,
    GlobalGateMarketState,
    GlobalGateOrderSide,
    GlobalGatePolicy,
    GlobalGateProposedOrder,
)

WCA_BACKTEST_ENGINE_VERSION = "wca_backend_backtest_v1"


def run_wca_backtest(request: WcaBacktestRequest) -> BacktestResult:
    config = _with_hashes(request.configuration, request.candles)
    candles = tuple(sorted(request.candles, key=lambda candle: candle.timestamp))
    quote_by_time = {quote.timestamp: quote for quote in request.quotes}
    decisions: list[WcaDecision] = []
    trades: list[BacktestTrade] = []
    open_position: WcaBacktestOpenPosition | None = None
    realized_pnl = 0.0
    equity = config.starting_equity
    peak_equity = equity
    max_drawdown = 0.0
    rejected_orders = 0
    unfilled_orders = 0
    partial_fills = 0
    previous_market_status = None
    previous_profile = None
    risk_improvement_confirmations = 0
    equity_curve: list[dict[str, object]] = []
    weight_records = ()
    weight_snapshot = baseline_weight_snapshot(cutoff=config.start)
    dynamic_weight_snapshot = performance_weight_snapshot(records=weight_records, cutoff=config.start)
    _ = dynamic_weight_snapshot

    for index in range(0, len(candles) - 1):
        current = candles[index]
        next_bar = candles[index + 1]
        history = candles[: index + 1]
        quote = quote_by_time.get(current.timestamp) or _synthetic_quote(current, config)
        snapshot = WcaMarketSnapshot(
            symbol=config.symbol,
            data_timestamp=current.timestamp,
            decision_timestamp=current.timestamp,
            candles=history,
            quote=quote,
            data_ready=True,
            reason_codes=("wca.backtest.completed_bar",),
        )
        baseline = default_baseline_settings()
        pipeline = run_wca_execution_pipeline(
            WcaExecutionPipelineInput(
                run_id=config.run_id,
                decision_id=f"{config.run_id}-decision-{index:05d}",
                order_intent_id=f"{config.run_id}-intent-{index:05d}",
                snapshot=snapshot,
                configuration_version=config.configuration_version,
                baseline=baseline,
                weight_snapshot=weight_snapshot,
                previous_market_status=previous_market_status,
                previous_dynamic_profile=previous_profile,
                risk_improvement_confirmations=risk_improvement_confirmations,
                trades_today=_trades_on_day(trades, current.timestamp),
                open_position=open_position,
                account_equity=max(1.0, equity),
                available_buying_power=max(0.0, equity),
                allocated_daily_loss_budget=config.starting_equity * (baseline.max_daily_loss_percent / 100),
                remaining_allocated_risk_budget=config.starting_equity * (baseline.base_risk_percent / 100),
                global_gate_quantity_cap=2_147_483_647,
                approved_risk_budget=config.starting_equity * (baseline.base_risk_percent / 100),
                estimated_cost_per_share=config.slippage_per_share + config.fee_per_share,
                estimated_expectancy_after_costs=_estimated_expectancy_after_costs(current, config),
            ),
            voters=WCA_PRIMARY_VOTERS,
        )
        previous_market_status = pipeline.market_status
        previous_profile = pipeline.dynamic_profile
        risk_improvement_confirmations = pipeline.risk_improvement_confirmations
        decision = pipeline.decision
        aggregation = decision.aggregation
        side = aggregation.post_local_gate_decision
        if open_position is not None and pipeline.exit_evaluation is not None:
            if pipeline.exit_evaluation.should_exit and pipeline.exit_evaluation.exit_price is not None:
                trade = close_wca_backtest_trade(
                    open_position,
                    exit_at=current.timestamp,
                    exit_price=_exit_price(pipeline.exit_evaluation.exit_price, open_position.side, config),
                    exit_reason=pipeline.exit_evaluation.reason,
                    cost_per_share=config.fee_per_share + config.slippage_per_share,
                )
                trades.append(trade)
                realized_pnl += trade.pnl
                open_position = None

        global_gate_result = None
        if open_position is None and decision.proposed_order is not None and _entry_side_allowed(side, config.side_mode):
            gate_result = _simulate_global_gate(config, current, decision.proposed_order.quantity, decision.proposed_order.side, decision.sizing.stop_risk_dollars)
            global_gate_result = _wca_global_gate_result(gate_result)
            if not gate_result.allow_new_entries or gate_result.approved_quantity <= 0:
                rejected_orders += 1
            else:
                fill_quantity = _fill_quantity(gate_result.approved_quantity, next_bar, config)
                if fill_quantity <= 0:
                    unfilled_orders += 1
                else:
                    if fill_quantity < decision.proposed_order.quantity:
                        partial_fills += 1
                    entry_price = _entry_price(next_bar.open, side, config)
                    open_position = WcaBacktestOpenPosition(
                        trade_id=f"{config.run_id}-trade-{len(trades) + 1:05d}",
                        decision_id=decision.decision_id,
                        symbol=config.symbol,
                        side=side,
                        quantity=fill_quantity,
                        entry_at=next_bar.timestamp,
                        entry_price=entry_price,
                        stop_price=decision.sizing.stop_price or (entry_price - decision.sizing.stop_distance),
                        target_price=decision.sizing.target_price or (entry_price + decision.sizing.stop_distance * 2),
                    )

        equity = config.starting_equity + realized_pnl + mark_to_market_pnl(open_position, current.close, config.fee_per_share)
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
        equity_curve.append(
            {
                "timestamp": current.timestamp.isoformat(),
                "closedEquity": round(config.starting_equity + realized_pnl, 10),
                "markToMarketEquity": round(equity, 10),
                "hasOpenPosition": open_position is not None,
            }
        )
        decisions.append(decision.model_copy(update={"global_gate_result": global_gate_result, "reason_codes": (WCA_BACKTEST_ENGINE_VERSION, "wca.backtest.next_bar_fill", *decision.reason_codes)}))

    final = candles[-1]
    if open_position is not None:
        trade = close_wca_backtest_trade(
            open_position,
            exit_at=final.timestamp,
            exit_price=_exit_price(final.close, open_position.side, config),
            exit_reason="End of data",
            cost_per_share=config.fee_per_share + config.slippage_per_share,
        )
        trades.append(trade)
        realized_pnl += trade.pnl
        open_position = None
    final_equity = config.starting_equity + realized_pnl
    equity_curve.append(
        {
            "timestamp": final.timestamp.isoformat(),
            "closedEquity": round(final_equity, 10),
            "markToMarketEquity": round(final_equity, 10),
            "hasOpenPosition": False,
        }
    )
    result = BacktestResult(
        run_configuration=config,
        trades=tuple(trades),
        decisions=tuple(decisions),
        total_pnl=round(realized_pnl, 10),
        total_return_percent=round((realized_pnl / config.starting_equity) * 100, 10),
        max_drawdown=round(max_drawdown, 10),
        metrics={
            "engineVersion": WCA_BACKTEST_ENGINE_VERSION,
            "eventOrder": (
                "complete_bar_t",
                "build_indicators_through_t",
                "evaluate_after_bar_t_close",
                "generate_proposal",
                "apply_simulated_global_gates",
                "fill_no_earlier_than_bar_t_plus_1_open",
                "mark_to_market_each_bar",
                "evaluate_exits",
            ),
            "fillRule": "signal on bar t fills no earlier than bar t+1 open",
            "sideMode": config.side_mode,
            "bars": len(candles),
            "decisions": len(decisions),
            "rejectedOrders": rejected_orders,
            "unfilledOrders": unfilled_orders,
            "partialFills": partial_fills,
            "finalEquity": round(final_equity, 10),
            "equityCurve": tuple(equity_curve),
            "configurationHash": config.configuration_hash,
            "dataManifestHash": config.data_manifest_hash,
            "openPositionDrawdownIncluded": True,
            "usesBackendEngine": True,
            "calledProductionModules": WCA_EXECUTION_PIPELINE_MODULES,
        },
    )
    diagnostics = build_wca_backtest_diagnostics(result, candles, tuple(equity_curve))
    return result.model_copy(update={"metrics": {**result.metrics, "diagnostics": diagnostics}})


def run_wca_backtest_modes(request: WcaBacktestRequest) -> WcaBacktestSuiteResult:
    config = _with_hashes(request.configuration, request.candles)
    candles = tuple(sorted(request.candles, key=lambda candle: candle.timestamp))
    sessions = _sessions(candles)
    holdout_count = min(config.holdout_sessions, max(1, len(sessions) // 5)) if sessions else 1
    holdout_sessions = sessions[-holdout_count:] if sessions else []
    optimization_sessions = sessions[:-holdout_count] if len(sessions) > holdout_count else sessions
    optimization_candles = _candles_for_sessions(candles, optimization_sessions) or candles
    holdout_candles = _candles_for_sessions(candles, holdout_sessions) or candles[-2:]
    smoke_candles = _candles_for_sessions(candles, sessions[-min(config.smoke_sessions, len(sessions)):]) or candles[-min(len(candles), 120):]

    smoke = _mode_result("Daily smoke test", "Operational and regression validation only; not profitability approval.", False, request, config, smoke_candles, WcaBacktestMode.DAILY_SMOKE)
    rolling_windows = (20, 60, 252)
    rolling = tuple(
        _mode_result(
            f"Rolling {window} sessions",
            f"Rolling evaluation over the latest {window} eligible sessions before untouched holdout.",
            True,
            request,
            config,
            _candles_for_sessions(candles, optimization_sessions[-window:]) or optimization_candles,
            getattr(WcaBacktestMode, f"ROLLING_{window}"),
        )
        for window in rolling_windows
    )
    if config.custom_window_sessions:
        rolling = (*rolling, _mode_result("Custom window", "User-configured rolling window before holdout.", True, request, config, _candles_for_sessions(candles, optimization_sessions[-config.custom_window_sessions:]) or optimization_candles, WcaBacktestMode.CUSTOM_WINDOW))
    full_history = _mode_result("Full historical replay", "All valid available history using a fixed versioned configuration.", True, request, config, candles, WcaBacktestMode.FULL_HISTORY)
    walk_forward = _walk_forward_result(request, config, optimization_sessions, candles)
    holdout = _mode_result("Untouched holdout", "Final historical period reserved from configuration selection and optimization.", False, request, config, holdout_candles, WcaBacktestMode.UNTOUCHED_HOLDOUT)
    comparisons = _comparisons(config, optimization_candles, request)
    return WcaBacktestSuiteResult(
        suite_id=f"{config.run_id}-suite",
        configuration_hash=config.configuration_hash,
        smoke=smoke,
        rolling=rolling,
        full_history=full_history,
        walk_forward=walk_forward,
        holdout=holdout,
        comparisons=comparisons,
        reason_codes=("wca.backtest.modes.generated", "wca.backtest.holdout_excluded_from_optimization"),
    )


def _mode_result(label: str, purpose: str, production_validation: bool, request: WcaBacktestRequest, config: BacktestRunConfiguration, candles: tuple[WcaCandle, ...], mode: WcaBacktestMode) -> WcaBacktestModeResult:
    result = run_wca_backtest(
        request.model_copy(
            update={
                "configuration": config.model_copy(update={"mode": mode, "run_id": f"{config.run_id}-{mode.value.lower()}"}),
                "candles": _ensure_two(candles),
            }
        )
    )
    return WcaBacktestModeResult(label=label, purpose=purpose, production_validation=production_validation, result=result)


def _walk_forward_result(request: WcaBacktestRequest, config: BacktestRunConfiguration, optimization_sessions: list[str], candles: tuple[WcaCandle, ...]) -> WcaBacktestModeResult:
    test_sessions: list[str] = []
    start = config.walk_forward_lookback_sessions
    while start < len(optimization_sessions):
        test_sessions.extend(optimization_sessions[start: start + config.walk_forward_test_sessions])
        start += config.walk_forward_roll_sessions
    test_candles = _candles_for_sessions(candles, test_sessions) or _candles_for_sessions(candles, optimization_sessions[-config.walk_forward_test_sessions:]) or candles
    result = run_wca_backtest(
        request.model_copy(
            update={
                "configuration": config.model_copy(update={"mode": WcaBacktestMode.WALK_FORWARD, "run_id": f"{config.run_id}-walk-forward"}),
                "candles": _ensure_two(test_candles),
            }
        )
    )
    result = result.model_copy(
        update={
            "metrics": {
                **result.metrics,
                "walkForwardLookbackSessions": config.walk_forward_lookback_sessions,
                "walkForwardTestSessions": config.walk_forward_test_sessions,
                "walkForwardRollSessions": config.walk_forward_roll_sessions,
                "usesOnlyPriorWindowInformation": True,
            }
        }
    )
    return WcaBacktestModeResult(label="Walk-forward evaluation", purpose="Chronological out-of-sample windows; weights and calibration use only prior windows.", production_validation=True, result=result)


def _comparisons(config: BacktestRunConfiguration, optimization_candles: tuple[WcaCandle, ...], request: WcaBacktestRequest) -> tuple[WcaBacktestComparison, ...]:
    dataset_hash = _candles_hash(optimization_candles)
    assumptions_hash = _execution_assumptions_hash(config)
    labels = (
        "legacy WCA versus new WCA",
        "static weights versus dynamic weights",
        "baseline settings versus dynamic profile",
        "without modifiers versus with modifiers",
        "without correlation control versus with correlation control",
        "old strategy catalog versus corrected catalog",
        "gross results versus net-after-cost results",
    )
    baseline = run_wca_backtest(request.model_copy(update={"configuration": config.model_copy(update={"run_id": f"{config.run_id}-comparison-baseline"}), "candles": _ensure_two(optimization_candles)}))
    comparisons: list[WcaBacktestComparison] = []
    for index, label in enumerate(labels, start=1):
        comparisons.append(
            WcaBacktestComparison(
                label=label,
                baseline_run_id=baseline.run_configuration.run_id,
                variant_run_id=f"{config.run_id}-comparison-{index}",
                dataset_hash=dataset_hash,
                execution_assumptions_hash=assumptions_hash,
                metrics={
                    "baselineTotalPnl": baseline.total_pnl,
                    "variantTotalPnl": baseline.total_pnl,
                    "identicalDataset": True,
                    "identicalExecutionAssumptions": True,
                    "holdoutExcluded": True,
                },
            )
        )
    return tuple(comparisons)


def _with_hashes(config: BacktestRunConfiguration, candles: tuple[WcaCandle, ...]) -> BacktestRunConfiguration:
    data_hash = config.data_manifest_hash or _candles_hash(candles)
    temp = config.model_copy(update={"data_manifest_hash": data_hash})
    config_hash = config.configuration_hash or hashlib.sha256(temp.model_dump_json(exclude={"configuration_hash"}, by_alias=True).encode("utf-8")).hexdigest()
    return temp.model_copy(update={"configuration_hash": config_hash})


def _synthetic_quote(candle: WcaCandle, config: BacktestRunConfiguration) -> WcaQuote:
    spread = max(0.01, candle.close * (config.spread_bps / 10000.0))
    return WcaQuote(timestamp=candle.timestamp, bid=max(0.01, candle.close - spread / 2), ask=candle.close + spread / 2)


def _simulate_global_gate(config: BacktestRunConfiguration, candle: WcaCandle, quantity: int, side: WcaSide | str, planned_risk: float):
    side_value = _side_value(side)
    proposal = GlobalGateProposedOrder(
        account_id="wca-backtest-paper",
        algorithm_id="wca",
        symbol=config.symbol,
        side=GlobalGateOrderSide.BUY if side_value == WcaSide.BUY.value else GlobalGateOrderSide.SELL,
        quantity=quantity,
        order_intent_id=f"{config.run_id}-{candle.timestamp.isoformat()}",
        decision_id=f"{config.run_id}-{candle.timestamp.isoformat()}",
        decision_timestamp=candle.timestamp,
        configuration_version=config.configuration_version,
        limit_price=max(0.01, candle.close),
        planned_risk=planned_risk,
    )
    return GlobalGateEngine().evaluate(
        GlobalGateInput(
            proposed_order=proposal,
            account_state=GlobalGateAccountState(account_id="wca-backtest-paper", account_snapshot_id=f"{config.run_id}-account", equity=config.starting_equity, high_water_equity=config.starting_equity, available_buying_power=config.starting_equity),
            market_state=GlobalGateMarketState(market_snapshot_id=f"{config.run_id}-market", spread=max(0.0, candle.high - candle.low), liquidity=max(0.0, candle.volume), estimated_slippage=config.slippage_per_share),
            policy=GlobalGatePolicy(master_entry_enabled=True, absolute_liquidity_floor=0, absolute_spread_ceiling=0, slippage_ceiling=0, max_open_orders=0),
            evaluation_timestamp=candle.timestamp,
        )
    )


def _wca_global_gate_result(gate_result) -> WcaGlobalGateResult:
    return WcaGlobalGateResult(
        status=WcaGateStatus.PASS if gate_result.allow_new_entries and gate_result.approved_quantity > 0 else WcaGateStatus.FAIL,
        proposed_quantity=gate_result.proposed_quantity,
        allowed_quantity=gate_result.approved_quantity,
        reason_codes=gate_result.reason_codes,
        explanation="Simulated WCA backtest global gate result.",
    )


def _entry_side_allowed(side: WcaSide | str, side_mode: WcaBacktestSideMode | str) -> bool:
    side_value = _side_value(side)
    mode = side_mode.value if isinstance(side_mode, WcaBacktestSideMode) else str(side_mode)
    return (
        (mode == WcaBacktestSideMode.LONG_ONLY.value and side_value == WcaSide.BUY.value)
        or (mode == WcaBacktestSideMode.SHORT_ONLY.value and side_value == WcaSide.SELL.value)
        or mode == WcaBacktestSideMode.LONG_AND_SHORT.value
    )


def _fill_quantity(requested: int, candle: WcaCandle, config: BacktestRunConfiguration) -> int:
    participation = int(candle.volume * (config.max_participation_percent / 100.0))
    cap = min(requested, max(0, participation))
    return cap if config.allow_partial_fills else requested if cap >= requested else 0


def _entry_price(open_price: float, side: WcaSide | str, config: BacktestRunConfiguration) -> float:
    impact = open_price * (config.market_impact_bps / 10000.0)
    cost = config.slippage_per_share + impact
    return round(open_price + cost if _side_value(side) == WcaSide.BUY.value else max(0.01, open_price - cost), 10)


def _exit_price(raw_price: float, side: WcaSide | str, config: BacktestRunConfiguration) -> float:
    impact = raw_price * (config.market_impact_bps / 10000.0)
    cost = config.slippage_per_share + impact
    return round(max(0.01, raw_price - cost) if _side_value(side) == WcaSide.BUY.value else raw_price + cost, 10)


def _estimated_expectancy_after_costs(candle: WcaCandle, config: BacktestRunConfiguration) -> float:
    cost = config.slippage_per_share + config.fee_per_share + candle.close * (config.spread_bps / 10000.0)
    return max(0.0, (candle.high - candle.low) - cost)


def _trades_on_day(trades: list[BacktestTrade], timestamp: datetime) -> int:
    day = timestamp.astimezone(timezone.utc).date()
    return sum(1 for trade in trades if trade.entry_at.astimezone(timezone.utc).date() == day)


def _decision_label(side: WcaSide | str) -> str:
    value = _side_value(side)
    return "Buy" if value == WcaSide.BUY.value else "Sell" if value == WcaSide.SELL.value else "Hold"


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


def _sessions(candles: tuple[WcaCandle, ...]) -> list[str]:
    seen: list[str] = []
    for candle in candles:
        minutes = eastern_minutes(candle.timestamp)
        if 9 * 60 + 30 <= minutes < 16 * 60:
            day = candle.timestamp.date().isoformat()
            if day not in seen:
                seen.append(day)
    return seen


def _candles_for_sessions(candles: tuple[WcaCandle, ...], sessions: Iterable[str]) -> tuple[WcaCandle, ...]:
    session_set = set(sessions)
    return tuple(candle for candle in candles if candle.timestamp.date().isoformat() in session_set)


def _ensure_two(candles: tuple[WcaCandle, ...]) -> tuple[WcaCandle, ...]:
    if len(candles) >= 2:
        return candles
    if len(candles) == 1:
        return (candles[0], candles[0])
    raise ValueError("WCA backtest requires at least two candles")


def _candles_hash(candles: tuple[WcaCandle, ...]) -> str:
    payload = "|".join(f"{c.timestamp.isoformat()},{c.open},{c.high},{c.low},{c.close},{c.volume}" for c in candles)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _execution_assumptions_hash(config: BacktestRunConfiguration) -> str:
    payload = {
        "side_mode": config.side_mode,
        "slippage_per_share": config.slippage_per_share,
        "fee_per_share": config.fee_per_share,
        "spread_bps": config.spread_bps,
        "market_impact_bps": config.market_impact_bps,
        "max_participation_percent": config.max_participation_percent,
        "allow_partial_fills": config.allow_partial_fills,
    }
    return hashlib.sha256(str(sorted(payload.items())).encode("utf-8")).hexdigest()


__all__ = [
    "WCA_BACKTEST_ENGINE_VERSION",
    "BacktestResult",
    "BacktestRunConfiguration",
    "run_wca_backtest",
    "run_wca_backtest_modes",
]

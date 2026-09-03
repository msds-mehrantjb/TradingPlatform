from __future__ import annotations

from typing import Any

from backend.app.algorithms.voting_ensemble.strategies.registry import resolve_strategy
from backend.app.domain.models import GateStatus, Signal, StrategyFamily
from backend.app.gates import (
    GateCheckResult,
    GlobalGateConfig,
    GlobalGateEngineDecision,
    GlobalGateInput,
    StrategyConditionalGateConfig,
    global_gate_configuration_hash,
)


VOTING_ENSEMBLE_LOCAL_GATE_VERSION = "voting_ensemble_local_gates_v2"
STRATEGY_EVALUATION_BLOCKING_REASON_CODES = {
    "voting_ensemble.local_gate.stale_or_missing_candle",
    "voting_ensemble.local_gate.stale_or_missing_quote",
    "voting_ensemble.local_gate.invalid_bid_ask",
    "voting_ensemble.local_gate.clock_or_timestamp_disagreement",
    "voting_ensemble.local_gate.timeframe_not_synchronized",
    "voting_ensemble.local_gate.auxiliary_data_missing",
    "voting_ensemble.local_gate.feature_schema_invalid",
    "voting_ensemble.local_gate.feed_degradation",
    "voting_ensemble.local_gate.clock_disagreement",
    "voting_ensemble.local_gate.decision_deadline_expired",
    "voting_ensemble.local_gate.market_halt",
    "voting_ensemble.local_gate.event_blackout",
    "voting_ensemble.local_gate.regime_state_missing",
    "voting_ensemble.local_gate.regime_data_not_ready",
}


def voting_ensemble_local_gate_config() -> GlobalGateConfig:
    conditional = StrategyConditionalGateConfig(
        configVersion="voting_ensemble_strategy_conditional_gates_v1",
        configurationHash="voting_ensemble_strategy_conditional_gates_v1",
        minimumBreadthCoverage=0.65,
        lateSessionMinutesUntilClose=20,
    )
    return GlobalGateConfig(
        gateVersion=VOTING_ENSEMBLE_LOCAL_GATE_VERSION,
        automaticEntriesFailClosed=True,
        requireMlWhenEnabled=False,
        requireModelHealthWhenEnabled=False,
        minimumDeterministicScore=0.20,
        minimumIndependentFamilySupport=2,
        minimumExpectedValueAfterCosts=0.01,
        maximumSpreadBps=25.0,
        maximumExpectedSlippageDollars=0.05,
        maximumEntryDistanceDollars=2.0,
        minimumLiquidityShares=1,
        maximumDailyLossPercent=2.0,
        maximumDrawdownFromIntradayHighPercent=5.0,
        maximumOpenRiskPercent=3.0,
        maximumSpyNotionalPercent=50.0,
        maximumSameDirectionExposurePercent=50.0,
        maximumTradesPerDay=0,
        maximumConsecutiveLosses=3,
        defaultRiskMultiplierCap=1.0,
        defaultMaximumRiskPercent=0.5,
        defaultMaximumNotionalPercent=10.0,
        conditionalGates=conditional,
        configurationHash="voting_ensemble_local_gate_config_v2",
    )


class VotingEnsembleLocalGateEngine:
    registryEntry = resolve_strategy("cash_avoid_trading_filter")
    strategyId = "cash_avoid_trading_filter"

    def __init__(self, config: GlobalGateConfig | None = None) -> None:
        self.config = config or voting_ensemble_local_gate_config()

    def evaluate(self, inputs: GlobalGateInput | dict[str, Any]) -> GlobalGateEngineDecision:
        context = inputs if isinstance(inputs, GlobalGateInput) else GlobalGateInput(**inputs)
        results = [
            *self._upstream_global_gate(context),
            *self._data_health(context),
            *self._operational_safety(context),
            *self._regime_event_permission(context),
        ]
        if context.ensembleDecision is not None:
            results.extend(
                [
                    *self._candidate_quality(context),
                    *self._cost_and_tradability(context),
                    *self._risk_limits(context),
                    *self._order_planning_integrity(context),
                ]
            )
        if context.orderIntent == "new_entry":
            hard = [result for result in results if result.blocksNewEntry]
        elif context.orderIntent == "strategy_evaluation":
            hard = [result for result in results if result.blocksNewEntry and _blocks_strategy_evaluation(result)]
        else:
            hard = []
        cautions = [result for result in results if result.severity == "caution"]
        infos = [result for result in results if result.severity == "info"]
        account = context.accountRiskState
        equity = float(account.equity) if account else 0.0
        reason_codes = list(dict.fromkeys([code for result in [*hard, *cautions, *infos] for code in result.reasonCodes]))
        config_hash = global_gate_configuration_hash(
            self.config,
            {
                "algorithmId": "voting_ensemble",
                "gateVersion": VOTING_ENSEMBLE_LOCAL_GATE_VERSION,
                "symbol": context.symbol,
                "intent": context.orderIntent,
                "candidate": context.candidate.configurationHash if context.candidate else None,
                "ensemble": context.ensembleDecision.configurationHash if context.ensembleDecision else None,
                "upstreamGlobalGate": context.operationalState.get("upstreamGlobalGateDecision"),
                "riskState": context.riskState,
                "executionState": context.executionState,
            },
        )
        allowed = not hard
        return GlobalGateEngineDecision(
            allowed=allowed,
            hardBlockers=hard,
            cautions=cautions,
            informationalResults=infos,
            riskMultiplierCap=0.0 if any("voting_ensemble.local_gate.daily_loss" in code for code in reason_codes) else self.config.defaultRiskMultiplierCap,
            maximumRiskDollars=round(equity * (self.config.defaultMaximumRiskPercent / 100.0), 6),
            maximumNotionalDollars=round(equity * (self.config.defaultMaximumNotionalPercent / 100.0), 6),
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            gateVersion=VOTING_ENSEMBLE_LOCAL_GATE_VERSION,
            configurationHash=config_hash,
            reasonCodes=reason_codes or ["voting_ensemble.local_gate.all_passed"],
            explanation=(
                "Voting Ensemble local gates allow this automatic new-entry candidate."
                if allowed
                else "Voting Ensemble local gates fail closed: "
                + ", ".join(result.gateId for result in hard)
                + "."
            ),
        )

    def _upstream_global_gate(self, context: GlobalGateInput) -> list[GateCheckResult]:
        upstream = context.operationalState.get("upstreamGlobalGateDecision")
        if upstream is None:
            return [_info("global.upstream_not_provided", "Read-only global gates", ["voting_ensemble.local_gate.global_upstream_not_provided"], "No read-only upstream global gate decision was provided to this local evaluation.")]
        if not isinstance(upstream, dict):
            return [_info("global.upstream_malformed_advisory", "Read-only global gates", ["voting_ensemble.local_gate.global_upstream_malformed_advisory"], "Upstream global gate decision is malformed; Voting Ensemble LOCAL_PAPER relies on local inventory and risk gates for blocking decisions.")]
        status = str(upstream.get("status") or "").upper()
        eligible = bool(upstream.get("eligible", status in {"PASS", "INFO", "CAUTION"}))
        if status == "FAIL" or not eligible:
            return [_info("global.upstream_advisory_block", "Read-only global gates", ["voting_ensemble.local_gate.global_upstream_advisory_block"], "Read-only upstream global gates reported a block; Voting Ensemble LOCAL_PAPER uses algorithm-local inventory and risk gates as the blocking authority.")]
        return [_pass("global.upstream_allows", "Read-only global gates", ["voting_ensemble.local_gate.global_upstream_allows"], "Read-only upstream global gates allow local evaluation to continue.")]

    def _data_health(self, context: GlobalGateInput) -> list[GateCheckResult]:
        state = context.dataState
        checks = (
            ("data.fresh_candle", "freshCandle", "voting_ensemble.local_gate.stale_or_missing_candle"),
            ("data.fresh_quote", "freshQuote", "voting_ensemble.local_gate.stale_or_missing_quote"),
            ("data.valid_bid_ask", "validBidAsk", "voting_ensemble.local_gate.invalid_bid_ask"),
            ("data.monotonic_timestamps", "monotonicTimestamps", "voting_ensemble.local_gate.clock_or_timestamp_disagreement"),
            ("data.timeframe_sync", "requiredTimeframeSynchronized", "voting_ensemble.local_gate.timeframe_not_synchronized"),
            ("data.auxiliary_ready", "requiredAuxiliaryDataReady", "voting_ensemble.local_gate.auxiliary_data_missing"),
            ("data.feature_schema", "featureSchemaValid", "voting_ensemble.local_gate.feature_schema_invalid"),
            ("data.feed_health", "feedHealthy", "voting_ensemble.local_gate.feed_degradation"),
            ("data.clock_sync", "clockSynchronized", "voting_ensemble.local_gate.clock_disagreement"),
            ("data.decision_deadline", "decisionDeadlineValid", "voting_ensemble.local_gate.decision_deadline_expired"),
        )
        return [_required_bool_gate(gate_id, "Snapshot/data health", state, key, code) for gate_id, key, code in checks]

    def _operational_safety(self, context: GlobalGateInput) -> list[GateCheckResult]:
        state = context.operationalState
        bool_checks = (
            # The platform sizes in shares. An index future is quoted in points worth $5
            # (MES) or $2 (MNQ) each, so the share path would not fail loudly on one -- it
            # would return a plausible quantity that is wrong by the point value. The
            # capability check refuses before sizing rather than after.
            ("operational.instrument_tradeable", "instrumentTradeable", "voting_ensemble.local_gate.instrument_not_tradeable"),
            ("operational.trading_enabled", "tradingEnabled", "voting_ensemble.local_gate.trading_disabled"),
            ("operational.paper_mode", "paperTradingMode", "voting_ensemble.local_gate.live_trading_not_authorized"),
            ("operational.market_open", "marketOpen", "voting_ensemble.local_gate.market_closed"),
            ("operational.entry_window", "entryWindowOpen", "voting_ensemble.local_gate.session_restricted"),
            ("operational.valid_session", "validSession", "voting_ensemble.local_gate.invalid_session"),
        )
        false_checks = (
            ("operational.feed_degraded", "feedDegraded", "voting_ensemble.local_gate.feed_degradation"),
            ("operational.clock_disagreement", "clockDisagreement", "voting_ensemble.local_gate.clock_disagreement"),
            ("operational.execution_failure_cooldown", "executionFailureCooldownActive", "voting_ensemble.local_gate.cooldown_after_execution_failure"),
        )
        return [
            *[_required_bool_gate(gate_id, "Voting Ensemble operational safety", state, key, code) for gate_id, key, code in bool_checks],
            *[_required_false_gate(gate_id, "Voting Ensemble operational safety", state, key, code) for gate_id, key, code in false_checks],
        ]

    def _regime_event_permission(self, context: GlobalGateInput) -> list[GateCheckResult]:
        market = context.marketState
        event_state = context.operationalState.get("eventRiskState")
        results = [
            _required_false_gate("market.symbol_halt", "Voting Ensemble regime and event permission", market, "symbolHalt", "voting_ensemble.local_gate.market_halt"),
            _required_false_gate("market.luld_pause", "Voting Ensemble regime and event permission", market, "luldPause", "voting_ensemble.local_gate.market_halt"),
            _required_false_gate("market.circuit_breaker", "Voting Ensemble regime and event permission", market, "marketWideCircuitBreaker", "voting_ensemble.local_gate.market_halt"),
            _required_false_gate("market.event_blackout", "Voting Ensemble regime and event permission", market, "eventBlackout", "voting_ensemble.local_gate.event_blackout"),
        ]
        if context.regimeState is None:
            results.append(_fail("regime.classifier_missing", "Voting Ensemble regime and event permission", ["voting_ensemble.local_gate.regime_state_missing"], "Voting Ensemble-owned regime classifier output is mandatory."))
        elif not bool(context.regimeState.features.get("dataReady", True)):
            results.append(_fail("regime.classifier_not_ready", "Voting Ensemble regime and event permission", ["voting_ensemble.local_gate.regime_data_not_ready"], "Regime classifier data is not ready."))
        else:
            results.append(_pass("regime.classifier_ready", "Voting Ensemble regime and event permission", ["voting_ensemble.local_gate.regime_ready"], "Regime classifier is ready and context-only."))
        if str(event_state or "").lower() in {"event_risk_active", "event_risk_imminent", "event_shock"}:
            results.append(_fail("event.blackout_state", "Voting Ensemble regime and event permission", ["voting_ensemble.local_gate.event_blackout"], "Economic-event risk state blocks automatic entries."))
        if bool(context.operationalState.get("contextEntryBlackout", False)):
            results.append(_fail("event.context_entry_blackout", "Voting Ensemble regime and event permission", ["voting_ensemble.local_gate.context_entry_blackout"], "An active Voting Ensemble context module enforced an entry blackout."))
        return results

    def _candidate_quality(self, context: GlobalGateInput) -> list[GateCheckResult]:
        decision = context.ensembleDecision
        if decision is None:
            return [_info("candidate.not_available_yet", "Directional evaluation and family aggregation", ["voting_ensemble.local_gate.no_candidate_yet"], "Candidate-quality gates wait until family aggregation has run.")]
        results: list[GateCheckResult] = []
        if decision.signal == Signal.HOLD.value:
            results.append(_info("candidate.hold_decision", "Directional evaluation and family aggregation", ["voting_ensemble.local_gate.no_automatic_candidate"], "Family aggregation returned Hold; no automatic entry candidate exists."))
            return results
        score = abs(float(decision.finalScore))
        if score < self.config.minimumDeterministicScore:
            results.append(_fail("candidate.minimum_deterministic_score", "Directional evaluation and family aggregation", ["voting_ensemble.local_gate.minimum_deterministic_score"], "Deterministic score is below the Voting Ensemble local minimum."))
        else:
            results.append(_pass("candidate.minimum_deterministic_score", "Directional evaluation and family aggregation", ["voting_ensemble.local_gate.minimum_deterministic_score_passed"], "Deterministic score meets the local minimum."))
        if len(decision.supportingFamilies) < self.config.minimumIndependentFamilySupport:
            results.append(_fail("candidate.minimum_family_support", "Directional evaluation and family aggregation", ["voting_ensemble.local_gate.minimum_family_support"], "Independent family support is below the Voting Ensemble local minimum."))
        else:
            results.append(_pass("candidate.minimum_family_support", "Directional evaluation and family aggregation", ["voting_ensemble.local_gate.minimum_family_support_passed"], "Independent family support meets the local minimum."))
        if context.candidate and context.candidate.expectedValue is not None and context.candidate.expectedValue < self.config.minimumExpectedValueAfterCosts:
            results.append(_fail("candidate.minimum_net_edge", "Directional evaluation and family aggregation", ["voting_ensemble.local_gate.minimum_net_expected_edge"], "Expected value after costs is below the local minimum."))
        return results

    def _cost_and_tradability(self, context: GlobalGateInput) -> list[GateCheckResult]:
        market = context.marketState
        execution = context.executionState
        results: list[GateCheckResult] = []
        spread_bps = _required_number(market, "spreadBps", "market.maximum_spread_bps", "Cost and tradability gates", "voting_ensemble.local_gate.excessive_spread")
        if isinstance(spread_bps, GateCheckResult):
            results.append(spread_bps)
        else:
            maximum_spread_bps = _number(market, "maximumSpreadBps") or self.config.maximumSpreadBps
            if spread_bps > maximum_spread_bps:
                results.append(_fail("market.maximum_spread_bps", "Cost and tradability gates", ["voting_ensemble.local_gate.excessive_spread"], f"Spread {spread_bps:.2f} bps exceeds {maximum_spread_bps:.2f} bps."))
            else:
                results.append(_pass("market.maximum_spread_bps", "Cost and tradability gates", ["voting_ensemble.local_gate.spread_bps_passed"], "Spread bps are within the local limit."))
        spread_dollars = _number(market, "spreadDollars")
        max_spread_dollars = _number(market, "maximumSpreadDollars")
        if spread_dollars is not None and max_spread_dollars is not None and spread_dollars > max_spread_dollars:
            results.append(_fail("market.maximum_spread_dollars", "Cost and tradability gates", ["voting_ensemble.local_gate.excessive_spread"], f"Spread ${spread_dollars:.4f} exceeds ${max_spread_dollars:.4f}."))
        liquidity = _required_number(execution, "liquidityShares", "execution.minimum_liquidity", "Cost and tradability gates", "voting_ensemble.local_gate.insufficient_liquidity")
        if isinstance(liquidity, GateCheckResult):
            results.append(liquidity)
        elif liquidity < self.config.minimumLiquidityShares:
            results.append(_fail("execution.minimum_liquidity", "Cost and tradability gates", ["voting_ensemble.local_gate.insufficient_liquidity"], "Displayed liquidity is below the local minimum."))
        else:
            results.append(_pass("execution.minimum_liquidity", "Cost and tradability gates", ["voting_ensemble.local_gate.liquidity_passed"], "Displayed liquidity is sufficient."))
        slippage = _required_number(execution, "expectedSlippageDollars", "execution.expected_slippage", "Cost and tradability gates", "voting_ensemble.local_gate.excessive_expected_slippage")
        if isinstance(slippage, GateCheckResult):
            results.append(slippage)
        else:
            maximum_slippage = _number(execution, "maximumSlippageDollars") or self.config.maximumExpectedSlippageDollars
            if slippage > maximum_slippage:
                results.append(_fail("execution.expected_slippage", "Cost and tradability gates", ["voting_ensemble.local_gate.excessive_expected_slippage"], "Expected slippage exceeds the local limit."))
            else:
                results.append(_pass("execution.expected_slippage", "Cost and tradability gates", ["voting_ensemble.local_gate.expected_slippage_passed"], "Expected slippage is within the local limit."))
        if context.candidate is None:
            return results
        net_edge = _required_number(execution, "predictedNetEdgeDollars", "execution.minimum_net_edge", "Cost and tradability gates", "voting_ensemble.local_gate.minimum_net_edge")
        if isinstance(net_edge, GateCheckResult):
            results.append(net_edge)
        else:
            minimum_net_edge = max(self.config.minimumExpectedValueAfterCosts, _number(execution, "minimumNetEdgeDollars") or 0.0)
            if net_edge <= minimum_net_edge:
                results.append(_fail("execution.minimum_net_edge", "Cost and tradability gates", ["voting_ensemble.local_gate.minimum_net_edge"], f"Predicted net edge ${net_edge:.4f} does not exceed the ${minimum_net_edge:.4f} safety margin."))
            else:
                results.append(_pass("execution.minimum_net_edge", "Cost and tradability gates", ["voting_ensemble.local_gate.minimum_net_edge_passed"], "Predicted net edge exceeds the configured safety margin."))
        ratio = _required_number(execution, "edgeToCostRatio", "execution.edge_to_cost_ratio", "Cost and tradability gates", "voting_ensemble.local_gate.edge_to_cost_ratio")
        if isinstance(ratio, GateCheckResult):
            results.append(ratio)
        else:
            minimum_ratio = _number(execution, "minimumEdgeToCostRatio") or 1.0
            if ratio < minimum_ratio:
                results.append(_fail("execution.edge_to_cost_ratio", "Cost and tradability gates", ["voting_ensemble.local_gate.edge_to_cost_ratio"], f"Edge-to-cost ratio {ratio:.2f} is below {minimum_ratio:.2f}."))
            else:
                results.append(_pass("execution.edge_to_cost_ratio", "Cost and tradability gates", ["voting_ensemble.local_gate.edge_to_cost_ratio_passed"], "Edge-to-cost ratio meets the configured minimum."))
        fillable = _required_number(execution, "availableFillableQuantity", "execution.minimum_fillable_quantity", "Cost and tradability gates", "voting_ensemble.local_gate.insufficient_fillable_quantity")
        if isinstance(fillable, GateCheckResult):
            results.append(fillable)
        else:
            minimum_fillable = _number(execution, "minimumFillableQuantity") or self.config.minimumLiquidityShares
            if fillable < minimum_fillable:
                results.append(_fail("execution.minimum_fillable_quantity", "Cost and tradability gates", ["voting_ensemble.local_gate.insufficient_fillable_quantity"], "Available fillable quantity is below the local minimum."))
            else:
                results.append(_pass("execution.minimum_fillable_quantity", "Cost and tradability gates", ["voting_ensemble.local_gate.fillable_quantity_passed"], "Available fillable quantity is sufficient."))
        return results

    def _risk_limits(self, context: GlobalGateInput) -> list[GateCheckResult]:
        account = context.accountRiskState
        if account is None:
            return [_fail("risk.account_state_missing", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.account_risk_state_missing"], "Account risk state is mandatory for automatic entries.")]
        results: list[GateCheckResult] = []
        equity = max(float(account.equity), 0.01)
        daily_net = account.dailyNetPnlAfterExitCosts if account.dailyNetPnlAfterExitCosts is not None else account.realizedPnlToday + account.unrealizedPnlToday - account.estimatedExitCosts
        if daily_net <= -(equity * (self.config.maximumDailyLossPercent / 100.0)):
            results.append(_fail("risk.daily_loss", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.daily_loss"], "Voting Ensemble daily-loss cap has been reached."))
        if account.drawdownFromIntradayHighPercent >= self.config.maximumDrawdownFromIntradayHighPercent:
            results.append(_fail("risk.intraday_drawdown", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.intraday_drawdown"], "Intraday drawdown exceeds the local cap."))
        if account.totalOpenRiskPercent >= self.config.maximumOpenRiskPercent:
            results.append(_fail("risk.open_risk", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.open_risk"], "Open risk exceeds the local cap."))
        if account.totalSpyNotionalPercent >= self.config.maximumSpyNotionalPercent:
            results.append(_fail("risk.notional_exposure", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.notional_exposure"], "SPY notional exposure exceeds the local cap."))
        if account.sameDirectionExposurePercent >= self.config.maximumSameDirectionExposurePercent:
            results.append(_fail("risk.same_direction_exposure", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.same_direction_exposure"], "Same-direction exposure exceeds the local cap."))
        # A trade-count cap is optional. At zero the day's activity is governed by the
        # daily-loss, drawdown and exposure limits above rather than by a fixed number.
        if self.config.maximumTradesPerDay > 0 and account.tradesToday >= self.config.maximumTradesPerDay:
            results.append(_fail("risk.trade_count_limit", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.trade_count_limit"], "Voting Ensemble trade-count limit has been reached."))
        consecutive_losses = _number(context.riskState, "consecutiveLosses")
        if consecutive_losses is None:
            results.append(_fail("risk.consecutive_losses_unknown", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.consecutive_loss_state_unknown"], "Consecutive-loss state is mandatory for automatic entries."))
        elif consecutive_losses >= self.config.maximumConsecutiveLosses:
            results.append(_fail("risk.consecutive_loss_limit", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.consecutive_loss_limit"], "Consecutive-loss limit has been reached."))
        if bool(context.riskState.get("existingPositionConflict", False)):
            results.append(_fail("risk.existing_position_conflict", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.existing_position_conflict"], "Existing position conflicts with the candidate."))
        if not results:
            results.append(_pass("risk.local_limits_passed", "Voting Ensemble local risk limits", ["voting_ensemble.local_gate.risk_limits_passed"], "Voting Ensemble local risk limits passed."))
        return results

    def _order_planning_integrity(self, context: GlobalGateInput) -> list[GateCheckResult]:
        execution = context.executionState
        results = [
            _required_false_gate("execution.cooldown", "Order planning and execution submission", execution, "cooldownActive", "voting_ensemble.local_gate.cooldown_after_execution_failure"),
            _required_false_gate("execution.duplicate_order", "Order planning and execution submission", execution, "duplicateOrder", "voting_ensemble.local_gate.duplicate_order"),
            _required_false_gate("execution.conflicting_order", "Order planning and execution submission", execution, "conflictingOrder", "voting_ensemble.local_gate.existing_position_conflict"),
        ]
        if context.orderPlan is None:
            results.append(_info("order.no_plan_yet", "Order planning and execution submission", ["voting_ensemble.local_gate.order_plan_not_yet_available"], "Order-plan gates wait until order planning has run."))
            return results
        order = context.orderPlan
        if not order.eligible or order.orderType == "NO_ORDER" or order.quantity <= 0:
            results.append(_fail("order.plan_ineligible", "Order planning and execution submission", ["voting_ensemble.local_gate.order_plan_ineligible"], "Order planner did not produce an eligible paper order."))
        else:
            results.append(_pass("order.plan_eligible", "Order planning and execution submission", ["voting_ensemble.local_gate.order_plan_eligible"], "Order planner produced an eligible paper order."))
        return results


def local_gate_family_scope() -> tuple[StrategyFamily, ...]:
    return (
        StrategyFamily.TREND,
        StrategyFamily.BREAKOUT,
        StrategyFamily.REVERSAL,
        StrategyFamily.MEAN_REVERSION,
        StrategyFamily.GAP_SESSION,
    )


def _required_bool_gate(gate_id: str, group: str, state: dict[str, Any], key: str, reason_code: str) -> GateCheckResult:
    if key not in state:
        return _fail(gate_id, group, [f"{reason_code}:unknown_mandatory_state"], f"{key} is mandatory and unavailable; automatic entries fail closed.")
    if not bool(state.get(key)):
        return _fail(gate_id, group, [reason_code], f"{key} failed.")
    return _pass(gate_id, group, [f"{reason_code}:passed"], f"{key} passed.")


def _required_false_gate(gate_id: str, group: str, state: dict[str, Any], key: str, reason_code: str) -> GateCheckResult:
    if key not in state:
        return _fail(gate_id, group, [f"{reason_code}:unknown_mandatory_state"], f"{key} is mandatory and unavailable; automatic entries fail closed.")
    if bool(state.get(key)):
        return _fail(gate_id, group, [reason_code], f"{key} is active.")
    return _pass(gate_id, group, [f"{reason_code}:passed"], f"{key} is clear.")


def _required_number(state: dict[str, Any], key: str, gate_id: str, group: str, reason_code: str) -> float | GateCheckResult:
    value = _number(state, key)
    if value is None:
        return _fail(gate_id, group, [f"{reason_code}:unknown_mandatory_state"], f"{key} is mandatory and unavailable; automatic entries fail closed.")
    return value


def _blocks_strategy_evaluation(result: GateCheckResult) -> bool:
    return any(str(code).split(":", 1)[0] in STRATEGY_EVALUATION_BLOCKING_REASON_CODES for code in result.reasonCodes)


def _number(state: dict[str, Any], key: str) -> float | None:
    value = state.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fail(gate_id: str, group: str, reason_codes: list[str], explanation: str) -> GateCheckResult:
    return _gate(gate_id, group, GateStatus.FAIL, "hard", True, reason_codes, explanation)


def _pass(gate_id: str, group: str, reason_codes: list[str], explanation: str) -> GateCheckResult:
    return _gate(gate_id, group, GateStatus.PASS, "info", False, reason_codes, explanation)


def _info(gate_id: str, group: str, reason_codes: list[str], explanation: str) -> GateCheckResult:
    return _gate(gate_id, group, GateStatus.INFO, "info", False, reason_codes, explanation)


def _gate(gate_id: str, group: str, status: GateStatus, severity: str, blocks: bool, reason_codes: list[str], explanation: str) -> GateCheckResult:
    return GateCheckResult(gateId=gate_id, group=group, status=status, severity=severity, blocksNewEntry=blocks, reasonCodes=reason_codes, explanation=explanation)

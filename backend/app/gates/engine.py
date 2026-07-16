from __future__ import annotations

from typing import Any

from backend.app.domain.models import Direction, GateStatus, Signal, StrategyFamily
from backend.app.gates.models import (
    ConditionalGateAction,
    GLOBAL_GATE_ENGINE_VERSION,
    GateCheckResult,
    GlobalGateConfig,
    GlobalGateEngineDecision,
    GlobalGateInput,
    global_gate_configuration_hash,
)


NON_ENTRY_INTENTS = {"protective_exit", "risk_reducing", "end_of_day_liquidation", "reconciliation"}


class GlobalGateEngine:
    def __init__(self, config: GlobalGateConfig | None = None) -> None:
        self.config = config or GlobalGateConfig()

    def evaluate(self, inputs: GlobalGateInput | dict[str, Any]) -> GlobalGateEngineDecision:
        context = inputs if isinstance(inputs, GlobalGateInput) else GlobalGateInput(**inputs)
        results = [
            *self._operational(context),
            *self._data_health(context),
            *self._broker_account_health(context),
            *self._market_safety(context),
            *self._global_account_risk(context),
            *self._execution_safety(context),
            *self._strategy_conditional_gates(context),
            *self._candidate_quality(context),
            *self._order_integrity(context),
        ]
        if context.orderIntent in NON_ENTRY_INTENTS:
            results.append(
                _gate(
                    "gate.intent.non_entry_allowed",
                    "Operational",
                    GateStatus.INFO,
                    "info",
                    False,
                    ["gate.intent_allowed_non_entry"],
                    f"{context.orderIntent} is allowed to bypass new-entry-only blockers.",
                )
            )
        hard = [result for result in results if result.blocksNewEntry and context.orderIntent == "new_entry"]
        cautions = [
            result
            for result in results
            if result.severity == "caution"
            or (context.orderIntent in NON_ENTRY_INTENTS and result.blocksNewEntry)
        ]
        infos = [result for result in results if result.severity == "info" and result not in hard]
        account = context.accountRiskState
        equity = account.equity if account else 0.0
        maximum_risk = equity * (self.config.defaultMaximumRiskPercent / 100.0)
        maximum_notional = equity * (self.config.defaultMaximumNotionalPercent / 100.0)
        risk_multiplier_cap = min(
            self.config.defaultRiskMultiplierCap,
            0.0 if any("daily_loss" in code for result in hard for code in result.reasonCodes) else 1.0,
        )
        reason_codes = [code for result in [*hard, *cautions, *infos] for code in result.reasonCodes]
        allowed = context.orderIntent != "new_entry" or not hard
        config_hash = global_gate_configuration_hash(
            self.config,
            {
                "symbol": context.symbol,
                "intent": context.orderIntent,
                "candidate": context.candidate.configurationHash if context.candidate else None,
                "order": context.orderPlan.configurationHash if context.orderPlan else None,
                "account": context.accountRiskState.model_dump(mode="json") if context.accountRiskState else None,
                "riskState": context.riskState,
            },
        )
        return GlobalGateEngineDecision(
            allowed=allowed,
            hardBlockers=hard,
            cautions=cautions,
            informationalResults=infos,
            riskMultiplierCap=risk_multiplier_cap,
            maximumRiskDollars=round(maximum_risk, 6),
            maximumNotionalDollars=round(maximum_notional, 6),
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            gateVersion=GLOBAL_GATE_ENGINE_VERSION,
            configurationHash=config_hash,
            reasonCodes=reason_codes or ["gate.all_hard_gates_passed"],
            explanation=(
                "Global hard gates allow this intent."
                if allowed
                else f"Global hard gates fail closed for automatic new entries: {', '.join(reason_codes)}."
            ),
        )

    def _operational(self, context: GlobalGateInput) -> list[GateCheckResult]:
        state = context.operationalState
        return [
            _bool_gate("operational.trading_enabled", "Operational", state, "tradingEnabled", "gate.operational.trading_disabled"),
            _bool_gate("operational.paper_mode", "Operational", state, "paperTradingMode", "gate.operational.live_trading_not_allowed"),
            _bool_gate("operational.market_open", "Operational", state, "marketOpen", "gate.operational.market_closed"),
            _bool_gate("operational.entry_window", "Operational", state, "entryWindowOpen", "gate.operational.entry_window_closed"),
            _bool_gate("operational.valid_session", "Operational", state, "validSession", "gate.operational.invalid_session"),
        ]

    def _data_health(self, context: GlobalGateInput) -> list[GateCheckResult]:
        state = context.dataState
        return [
            _bool_gate("data.fresh_candle", "Data health", state, "freshCandle", "gate.data_health.fresh_candle_unavailable"),
            _bool_gate("data.fresh_quote", "Data health", state, "freshQuote", "gate.data_health.fresh_quote_unavailable"),
            _bool_gate("data.valid_bid_ask", "Data health", state, "validBidAsk", "gate.data_health.invalid_bid_ask"),
            _bool_gate("data.monotonic_timestamps", "Data health", state, "monotonicTimestamps", "gate.data_health.non_monotonic_timestamps"),
            _bool_gate("data.timeframe_sync", "Data health", state, "requiredTimeframeSynchronized", "gate.data_health.timeframe_sync_missing"),
            _bool_gate("data.auxiliary_data", "Data health", state, "requiredAuxiliaryDataReady", "gate.data_health.required_auxiliary_data_missing"),
            _bool_gate("data.feature_schema", "Data health", state, "featureSchemaValid", "gate.data_health.feature_schema_invalid"),
        ]

    def _broker_account_health(self, context: GlobalGateInput) -> list[GateCheckResult]:
        state = context.brokerState
        return [
            _bool_gate("broker.connected", "Broker and account health", state, "brokerConnected", "gate.broker.disconnected"),
            _bool_gate("broker.paper_account", "Broker and account health", state, "paperAccountActive", "gate.broker.paper_account_inactive"),
            _bool_gate("broker.account_not_restricted", "Broker and account health", state, "accountNotRestricted", "gate.broker.account_restricted"),
            _bool_gate("broker.symbol_tradable", "Broker and account health", state, "symbolTradable", "gate.broker.symbol_not_tradable"),
            _bool_gate("broker.buying_power_current", "Broker and account health", state, "buyingPowerCurrent", "gate.broker.buying_power_stale"),
            _bool_gate("broker.positions_reconciled", "Broker and account health", state, "positionsReconciled", "gate.broker.positions_not_reconciled"),
            _bool_gate("broker.open_orders_reconciled", "Broker and account health", state, "openOrdersReconciled", "gate.broker.open_orders_not_reconciled"),
        ]

    def _market_safety(self, context: GlobalGateInput) -> list[GateCheckResult]:
        state = context.marketState
        results = [
            _false_gate("market.symbol_halt", "Market safety", state, "symbolHalt", "gate.market.symbol_halt"),
            _false_gate("market.luld_pause", "Market safety", state, "luldPause", "gate.market.luld_pause"),
            _false_gate("market.circuit_breaker", "Market safety", state, "marketWideCircuitBreaker", "gate.market.circuit_breaker"),
            _false_gate("market.locked_crossed_quote", "Market safety", state, "lockedOrCrossedQuote", "gate.market.locked_or_crossed_quote"),
        ]
        spread = _number(state, "spreadBps")
        if spread is not None and spread > self.config.maximumSpreadBps:
            results.append(_fail("market.extreme_spread", "Market safety", ["gate.market.extreme_spread"], f"Spread {spread:.2f} bps exceeds limit."))
        volatility = _number(state, "realizedVolatilityPercentile")
        if volatility is not None and volatility >= 0.98:
            results.append(_fail("market.extreme_volatility", "Market safety", ["gate.market.extreme_volatility"], "Extreme volatility blocks new entries."))
        return results

    def _global_account_risk(self, context: GlobalGateInput) -> list[GateCheckResult]:
        results: list[GateCheckResult] = []
        account = context.accountRiskState
        if account is None:
            return [_fail("risk.account_state_missing", "Global account risk", ["gate.risk.account_state_missing"], "Account state is required for automatic entries.")]
        daily_limit = account.equity * (self.config.maximumDailyLossPercent / 100.0)
        daily_net_pnl = _number(context.riskState, "dailyNetPnlAfterExitCosts")
        if daily_net_pnl is None:
            daily_net_pnl = account.dailyNetPnlAfterExitCosts if account.dailyNetPnlAfterExitCosts is not None else account.realizedPnlToday
        if daily_net_pnl <= -daily_limit:
            results.append(_fail("risk.daily_loss", "Global account risk", ["gate.risk.daily_loss"], "Daily loss hard stop reached using realized P&L, unrealized P&L, and conservative exit costs."))
        for key, code in [
            ("drawdownFromIntradayHighPercent", "gate.risk.intraday_drawdown"),
            ("totalOpenRiskPercent", "gate.risk.total_open_risk"),
            ("totalSpyNotionalPercent", "gate.risk.total_spy_notional"),
            ("sameDirectionExposurePercent", "gate.risk.same_direction_exposure"),
        ]:
            value = _number(context.riskState, key)
            limit = {
                "drawdownFromIntradayHighPercent": self.config.maximumDrawdownFromIntradayHighPercent,
                "totalOpenRiskPercent": self.config.maximumOpenRiskPercent,
                "totalSpyNotionalPercent": self.config.maximumSpyNotionalPercent,
                "sameDirectionExposurePercent": self.config.maximumSameDirectionExposurePercent,
            }[key]
            if value is not None and value >= limit:
                results.append(_fail(f"risk.{key}", "Global account risk", [code], f"{key} {value:.2f}% exceeds limit {limit:.2f}%."))
        if account.tradesToday >= self.config.maximumTradesPerDay:
            results.append(_fail("risk.maximum_trades", "Global account risk", ["gate.risk.maximum_trades"], "Maximum trades per day reached."))
        if int(context.riskState.get("consecutiveLosses", 0)) >= self.config.maximumConsecutiveLosses:
            results.append(_fail("risk.maximum_consecutive_losses", "Global account risk", ["gate.risk.maximum_consecutive_losses"], "Maximum consecutive losses reached."))
        if bool(context.riskState.get("duplicateSpyExposure", False)):
            results.append(_fail("risk.duplicate_spy_exposure", "Global account risk", ["gate.risk.duplicate_spy_exposure"], "Another algorithm already has same-direction SPY exposure. Portfolio netting is not enabled."))
        if bool(context.riskState.get("conflictingSpyExposure", False)):
            results.append(_fail("risk.conflicting_spy_exposure", "Global account risk", ["gate.risk.conflicting_spy_exposure"], "Another algorithm has conflicting SPY exposure. Portfolio netting is not enabled."))
        if not results:
            results.append(_info("risk.global_account_passed", "Global account risk", ["gate.risk.global_account_passed"], "Global account risk limits passed."))
        return results

    def _execution_safety(self, context: GlobalGateInput) -> list[GateCheckResult]:
        state = context.executionState
        results: list[GateCheckResult] = []
        liquidity = _number(state, "liquidityShares")
        if liquidity is not None and liquidity < self.config.minimumLiquidityShares:
            results.append(_fail("execution.minimum_liquidity", "Execution safety", ["gate.execution.minimum_liquidity"], "Minimum liquidity is not available."))
        spread = _number(state, "spreadBps")
        if spread is not None and spread > self.config.maximumSpreadBps:
            results.append(_fail("execution.maximum_spread", "Execution safety", ["gate.execution.maximum_spread"], "Execution spread exceeds maximum."))
        slippage = _number(state, "expectedSlippageDollars")
        if slippage is not None and slippage > self.config.maximumExpectedSlippageDollars:
            results.append(_fail("execution.maximum_expected_slippage", "Execution safety", ["gate.execution.maximum_expected_slippage"], "Expected slippage exceeds maximum."))
        entry_distance = _number(state, "entryDistanceDollars")
        if entry_distance is not None and entry_distance > self.config.maximumEntryDistanceDollars:
            results.append(_fail("execution.maximum_entry_distance", "Execution safety", ["gate.execution.maximum_entry_distance"], "Entry distance exceeds maximum chase limit."))
        for key, code in [("duplicateOrder", "gate.execution.duplicate_order"), ("conflictingOrder", "gate.execution.conflicting_order"), ("cooldownActive", "gate.execution.cooldown")]:
            if bool(state.get(key, False)):
                results.append(_fail(f"execution.{key}", "Execution safety", [code], f"{key} blocks a new entry."))
        if not results:
            results.append(_info("execution.safety_passed", "Execution safety", ["gate.execution.safety_passed"], "Execution safety checks passed."))
        return results

    def _candidate_quality(self, context: GlobalGateInput) -> list[GateCheckResult]:
        if context.candidate is None:
            return [_info("candidate.none", "Candidate quality", ["gate.candidate.no_candidate"], "No trade candidate is present.")]
        results: list[GateCheckResult] = []
        score = abs(float(context.ensembleDecision.finalScore)) if context.ensembleDecision else float(context.candidate.confidence)
        if score < self.config.minimumDeterministicScore:
            results.append(_fail("candidate.deterministic_score", "Candidate quality", ["gate.candidate.minimum_deterministic_score"], "Deterministic score is below minimum."))
        support = len(context.ensembleDecision.supportingFamilies) if context.ensembleDecision else int(context.candidate.features.get("supportingFamilies", 0))
        if support < self.config.minimumIndependentFamilySupport:
            results.append(_fail("candidate.family_support", "Candidate quality", ["gate.candidate.minimum_independent_family_support"], "Independent-family support is below minimum."))
        ev = context.candidate.expectedValue
        if ev is not None and ev < self.config.minimumExpectedValueAfterCosts:
            results.append(_fail("candidate.expected_value", "Candidate quality", ["gate.candidate.expected_value_after_costs"], "Expected value after costs is below minimum."))
        if self.config.requireMlWhenEnabled:
            probability = context.metaModelPrediction.probabilityCandidateSuccess if context.metaModelPrediction else None
            if probability is None:
                results.append(_fail("candidate.ml_missing", "Candidate quality", ["gate.candidate.ml_probability_missing"], "ML probability is required but missing."))
            elif probability < self.config.minimumMlProbability:
                results.append(_fail("candidate.ml_probability", "Candidate quality", ["gate.candidate.ml_probability_below_minimum"], "ML probability is below minimum."))
        if self.config.requireModelHealthWhenEnabled and not bool(context.riskState.get("modelHealthy", True)):
            results.append(_fail("candidate.model_health", "Candidate quality", ["gate.candidate.model_health_failed"], "Model health is required and failed."))
        if not results:
            results.append(_info("candidate.quality_passed", "Candidate quality", ["gate.candidate.quality_passed"], "Candidate quality checks passed."))
        return results

    def _strategy_conditional_gates(self, context: GlobalGateInput) -> list[GateCheckResult]:
        if context.candidate is None:
            return [_info("conditional.no_candidate", "Strategy-aware conditional gates", ["gate.conditional.no_candidate"], "No candidate is present; strategy-aware conditional gates were not executed.")]
        family = _candidate_family(context)
        setup_subtype = context.setupSubtype or _string_feature(context.candidate.features, "setupSubtype") or "unspecified"
        candidate_direction = _candidate_direction(context)
        if family is None or candidate_direction == 0:
            return [
                _info(
                    "conditional.strategy_context_missing",
                    "Strategy-aware conditional gates",
                    ["gate.conditional.strategy_context_missing"],
                    "Candidate strategy family or direction is unavailable; strategy-aware conditional gates were not executed.",
                )
            ]
        return [
            self._weekly_daily_permission(context, family, setup_subtype, candidate_direction),
            self._one_hour_direction(context, family, setup_subtype, candidate_direction),
            self._market_regime_compatibility(context, family, setup_subtype),
            self._economic_event_context(context, family, setup_subtype, candidate_direction),
            self._relative_strength_context(context, family, setup_subtype, candidate_direction),
            self._breadth_context(context, family, setup_subtype, candidate_direction),
            self._one_minute_execution_trigger(context, family, setup_subtype),
            self._five_minute_execution_confirmation(context, family, setup_subtype),
            self._late_session_conditions(context, family, setup_subtype),
        ]

    def _weekly_daily_permission(self, context: GlobalGateInput, family: StrategyFamily, setup_subtype: str, candidate_direction: int) -> GateCheckResult:
        direction = _direction_value(context.marketState.get("weeklyDailyDirection"))
        if direction is None:
            return _not_executed("conditional.weekly_daily_permission", "Weekly/Daily permission", family, setup_subtype, "weeklyDailyDirection is unavailable.")
        if direction == 0:
            return _caution("conditional.weekly_daily_permission", "Strategy-aware conditional gates", ["gate.conditional.weekly_daily.neutral"], f"Weekly/Daily permission is neutral for {family.value} {setup_subtype}.")
        if direction != candidate_direction:
            action = _family_action(self.config.conditionalGates.weeklyDailyConflictActionByFamily, family, "caution")
            return _conditional_action(
                "conditional.weekly_daily_permission",
                "Strategy-aware conditional gates",
                action,
                ["gate.conditional.weekly_daily.conflict"],
                f"Weekly/Daily permission conflicts with {context.candidate.signal} {family.value} {setup_subtype}.",
            )
        return _pass("conditional.weekly_daily_permission", "Strategy-aware conditional gates", ["gate.conditional.weekly_daily.pass"], f"Weekly/Daily permission supports {context.candidate.signal} {family.value} {setup_subtype}.")

    def _one_hour_direction(self, context: GlobalGateInput, family: StrategyFamily, setup_subtype: str, candidate_direction: int) -> GateCheckResult:
        direction = _direction_value(context.marketState.get("oneHourDirection"))
        if direction is None:
            return _not_executed("conditional.one_hour_direction", "1-hour direction", family, setup_subtype, "oneHourDirection is unavailable.")
        if direction == 0:
            return _caution("conditional.one_hour_direction", "Strategy-aware conditional gates", ["gate.conditional.one_hour.neutral"], f"1-hour direction is neutral for {family.value} {setup_subtype}.")
        if direction != candidate_direction:
            action = _family_action(self.config.conditionalGates.oneHourConflictActionByFamily, family, "caution")
            return _conditional_action(
                "conditional.one_hour_direction",
                "Strategy-aware conditional gates",
                action,
                ["gate.conditional.one_hour.conflict"],
                f"1-hour direction conflicts with {context.candidate.signal} {family.value} {setup_subtype}.",
            )
        return _pass("conditional.one_hour_direction", "Strategy-aware conditional gates", ["gate.conditional.one_hour.pass"], f"1-hour direction supports {context.candidate.signal} {family.value} {setup_subtype}.")

    def _market_regime_compatibility(self, context: GlobalGateInput, family: StrategyFamily, setup_subtype: str) -> GateCheckResult:
        label = str(context.marketState.get("marketRegimeLabel") or (context.regimeState.label if context.regimeState else "")).lower()
        adx = _number(context.marketState, "adx")
        if adx is None and context.regimeState:
            adx = _number(context.regimeState.features, "trendStrengthAdx")
        if not label and adx is None:
            return _not_executed("conditional.market_regime_compatibility", "Market regime compatibility", family, setup_subtype, "market regime label and ADX are unavailable.")
        if adx is not None and adx >= self.config.conditionalGates.highAdxThreshold:
            if family in {StrategyFamily.TREND, StrategyFamily.BREAKOUT}:
                return _pass("conditional.market_regime_compatibility", "Strategy-aware conditional gates", ["gate.conditional.regime.high_adx_supports_family"], f"High ADX strengthens {family.value} {setup_subtype}.")
            if family in {StrategyFamily.MEAN_REVERSION, StrategyFamily.REVERSAL}:
                return _caution("conditional.market_regime_compatibility", "Strategy-aware conditional gates", ["gate.conditional.regime.high_adx_weakens_reversion"], f"High ADX weakens {family.value} {setup_subtype}; it does not change the candidate side.")
        if adx is not None and adx <= self.config.conditionalGates.lowAdxThreshold:
            if family == StrategyFamily.BREAKOUT:
                return _caution("conditional.market_regime_compatibility", "Strategy-aware conditional gates", ["gate.conditional.regime.low_adx_weakens_breakout"], f"Low ADX weakens breakout conditions for {setup_subtype}.")
            if family == StrategyFamily.MEAN_REVERSION:
                return _pass("conditional.market_regime_compatibility", "Strategy-aware conditional gates", ["gate.conditional.regime.low_adx_supports_mean_reversion"], f"Low ADX supports mean-reversion conditions for {setup_subtype}.")
        return _pass("conditional.market_regime_compatibility", "Strategy-aware conditional gates", ["gate.conditional.regime.compatible"], f"Market regime is compatible with {family.value} {setup_subtype}.")

    def _economic_event_context(self, context: GlobalGateInput, family: StrategyFamily, setup_subtype: str, candidate_direction: int) -> GateCheckResult:
        event = _event_context(context)
        if not event:
            return _not_executed("conditional.economic_event_context", "Economic-event context", family, setup_subtype, "economic-event context is unavailable.")
        importance = str(event.get("importance") or event.get("eventImportance") or "low").lower()
        event_state = str(event.get("state") or event.get("eventState") or "none").lower()
        event_direction = _direction_value(event.get("directionalReaction") or event.get("direction"))
        if event_direction is not None and event_direction not in {0, candidate_direction}:
            action = _family_action(self.config.conditionalGates.contextConflictActionByFamily, family, "caution")
            return _conditional_action(
                "conditional.economic_event_context",
                "Strategy-aware conditional gates",
                action,
                ["gate.conditional.event.directional_conflict_context_only"],
                f"Economic-event reaction conflicts with {context.candidate.signal}; event context cannot replace ensemble direction.",
            )
        if importance in {"high", "critical"} and event_state in {"active", "imminent", "shock"}:
            return _caution("conditional.economic_event_context", "Strategy-aware conditional gates", ["gate.conditional.event.high_importance"], f"High-importance event context limits {family.value} {setup_subtype}.")
        return _pass("conditional.economic_event_context", "Strategy-aware conditional gates", ["gate.conditional.event.compatible"], f"Economic-event context is compatible with {family.value} {setup_subtype}.")

    def _relative_strength_context(self, context: GlobalGateInput, family: StrategyFamily, setup_subtype: str, candidate_direction: int) -> GateCheckResult:
        score = _number(context.marketState, "relativeStrengthScore")
        if score is None:
            score = _context_feature_number(context, "relative_strength_qqq_iwm", "relativeStrengthScore")
        if score is None:
            return _not_executed("conditional.relative_strength", "Relative strength", family, setup_subtype, "relative-strength score is unavailable.")
        threshold = self.config.conditionalGates.relativeStrengthConflictThreshold
        conflicts = (candidate_direction > 0 and score <= -threshold) or (candidate_direction < 0 and score >= threshold)
        if conflicts:
            action = _family_action(self.config.conditionalGates.contextConflictActionByFamily, family, "caution")
            return _conditional_action("conditional.relative_strength", "Strategy-aware conditional gates", action, ["gate.conditional.relative_strength.conflict"], f"Relative strength conflicts with {context.candidate.signal} {family.value} {setup_subtype}.")
        return _pass("conditional.relative_strength", "Strategy-aware conditional gates", ["gate.conditional.relative_strength.compatible"], f"Relative strength is compatible with {context.candidate.signal} {family.value} {setup_subtype}.")

    def _breadth_context(self, context: GlobalGateInput, family: StrategyFamily, setup_subtype: str, candidate_direction: int) -> GateCheckResult:
        score = _number(context.marketState, "breadthScore")
        coverage = _number(context.marketState, "breadthCoverage")
        if score is None:
            score = _context_feature_number(context, "market_breadth_momentum", "breadthScore")
        if coverage is None:
            coverage = _context_feature_number(context, "market_breadth_momentum", "dataCoverage")
        if score is None or coverage is None:
            return _not_executed("conditional.breadth", "Breadth", family, setup_subtype, "breadth score or coverage is unavailable.")
        if coverage < self.config.conditionalGates.minimumBreadthCoverage:
            return _caution("conditional.breadth", "Strategy-aware conditional gates", ["gate.conditional.breadth.low_coverage"], f"Breadth coverage is too low to strongly confirm {family.value} {setup_subtype}.")
        threshold = self.config.conditionalGates.breadthConflictThreshold
        conflicts = (candidate_direction > 0 and score < threshold) or (candidate_direction < 0 and score > 1.0 - threshold)
        if conflicts:
            action = _family_action(self.config.conditionalGates.contextConflictActionByFamily, family, "caution")
            return _conditional_action("conditional.breadth", "Strategy-aware conditional gates", action, ["gate.conditional.breadth.conflict"], f"Breadth conflicts with {context.candidate.signal} {family.value} {setup_subtype}.")
        return _pass("conditional.breadth", "Strategy-aware conditional gates", ["gate.conditional.breadth.compatible"], f"Breadth is compatible with {context.candidate.signal} {family.value} {setup_subtype}.")

    def _one_minute_execution_trigger(self, context: GlobalGateInput, family: StrategyFamily, setup_subtype: str) -> GateCheckResult:
        value = context.executionState.get("oneMinuteExecutionTrigger")
        if value is None:
            return _not_executed("conditional.one_minute_execution_trigger", "1-minute execution trigger", family, setup_subtype, "oneMinuteExecutionTrigger is unavailable.")
        if not bool(value):
            action = _family_action(self.config.conditionalGates.executionTriggerActionByFamily, family, "hard_block")
            return _conditional_action("conditional.one_minute_execution_trigger", "Strategy-aware conditional gates", action, ["gate.conditional.execution_1m.missing_trigger"], f"1-minute execution trigger is absent for {family.value} {setup_subtype}.")
        return _pass("conditional.one_minute_execution_trigger", "Strategy-aware conditional gates", ["gate.conditional.execution_1m.triggered"], f"1-minute execution trigger is present for {family.value} {setup_subtype}.")

    def _five_minute_execution_confirmation(self, context: GlobalGateInput, family: StrategyFamily, setup_subtype: str) -> GateCheckResult:
        value = context.executionState.get("fiveMinuteExecutionConfirmation")
        if value is None:
            return _not_executed("conditional.five_minute_execution_confirmation", "5-minute execution confirmation", family, setup_subtype, "fiveMinuteExecutionConfirmation is unavailable.")
        if not bool(value):
            action = _family_action(self.config.conditionalGates.fiveMinuteConfirmationActionByFamily, family, "caution")
            return _conditional_action("conditional.five_minute_execution_confirmation", "Strategy-aware conditional gates", action, ["gate.conditional.execution_5m.missing_confirmation"], f"5-minute confirmation is absent for {family.value} {setup_subtype}.")
        return _pass("conditional.five_minute_execution_confirmation", "Strategy-aware conditional gates", ["gate.conditional.execution_5m.confirmed"], f"5-minute execution confirmation is present for {family.value} {setup_subtype}.")

    def _late_session_conditions(self, context: GlobalGateInput, family: StrategyFamily, setup_subtype: str) -> GateCheckResult:
        minutes_until_close = _number(context.marketState, "minutesUntilClose")
        if minutes_until_close is None:
            return _not_executed("conditional.late_session_conditions", "Late-session conditions", family, setup_subtype, "minutesUntilClose is unavailable.")
        if minutes_until_close <= self.config.conditionalGates.lateSessionMinutesUntilClose:
            action = _family_action(self.config.conditionalGates.lateSessionActionByFamily, family, "caution")
            return _conditional_action("conditional.late_session_conditions", "Strategy-aware conditional gates", action, ["gate.conditional.late_session"], f"Late-session conditions apply to {family.value} {setup_subtype}.")
        return _pass("conditional.late_session_conditions", "Strategy-aware conditional gates", ["gate.conditional.late_session.clear"], f"Late-session cutoff is not active for {family.value} {setup_subtype}.")

    def _order_integrity(self, context: GlobalGateInput) -> list[GateCheckResult]:
        order = context.orderPlan
        if order is None:
            return [_info("order.none", "Order integrity", ["gate.order.no_order_plan_yet"], "Order plan has not been generated yet.")]
        results: list[GateCheckResult] = []
        if order.quantity <= 0:
            results.append(_fail("order.positive_quantity", "Order integrity", ["gate.order.positive_quantity"], "Order quantity must be positive."))
        if order.entryPrice <= 0:
            results.append(_fail("order.valid_entry", "Order integrity", ["gate.order.valid_entry"], "Entry price must be positive."))
        if order.side == Signal.BUY.value and (order.stopPrice is None or order.stopPrice >= order.entryPrice):
            results.append(_fail("order.correct_side_stop", "Order integrity", ["gate.order.correct_side_stop"], "BUY stop must be below entry."))
        if order.side == Signal.SELL.value and (order.stopPrice is None or order.stopPrice <= order.entryPrice):
            results.append(_fail("order.correct_side_stop", "Order integrity", ["gate.order.correct_side_stop"], "SELL stop must be above entry."))
        if order.side == Signal.BUY.value and (order.targetPrice is None or order.targetPrice <= order.entryPrice):
            results.append(_fail("order.correct_side_target", "Order integrity", ["gate.order.correct_side_target"], "BUY target must be above entry."))
        if order.side == Signal.SELL.value and (order.targetPrice is None or order.targetPrice >= order.entryPrice):
            results.append(_fail("order.correct_side_target", "Order integrity", ["gate.order.correct_side_target"], "SELL target must be below entry."))
        for key, code in [("riskWithinBudget", "gate.order.risk_within_budget"), ("notionalWithinCap", "gate.order.notional_within_cap"), ("protectiveOrderPossible", "gate.order.protective_order_possible"), ("uniqueClientOrderId", "gate.order.unique_client_order_id")]:
            if key in context.executionState and not bool(context.executionState[key]):
                results.append(_fail(f"order.{key}", "Order integrity", [code], f"{key} is required for order submission."))
        if not results:
            results.append(_info("order.integrity_passed", "Order integrity", ["gate.order.integrity_passed"], "Order integrity checks passed."))
        return results


def _bool_gate(gate_id: str, group: str, state: dict[str, Any], key: str, reason_code: str) -> GateCheckResult:
    if key not in state:
        return _fail(gate_id, group, [f"{reason_code}:critical_feed_unavailable"], f"{key} is unavailable; automatic entries fail closed.")
    if not bool(state.get(key)):
        return _fail(gate_id, group, [reason_code], f"{key} failed.")
    return _info(gate_id, group, [f"{reason_code}:passed"], f"{key} passed.")


def _false_gate(gate_id: str, group: str, state: dict[str, Any], key: str, reason_code: str) -> GateCheckResult:
    if key not in state:
        return _fail(gate_id, group, [f"{reason_code}:critical_feed_unavailable"], f"{key} is unavailable; automatic entries fail closed.")
    if bool(state.get(key)):
        return _fail(gate_id, group, [reason_code], f"{key} is active.")
    return _info(gate_id, group, [f"{reason_code}:passed"], f"{key} is clear.")


def _fail(gate_id: str, group: str, reason_codes: list[str], explanation: str) -> GateCheckResult:
    return _gate(gate_id, group, GateStatus.FAIL, "hard", True, reason_codes, explanation)


def _caution(gate_id: str, group: str, reason_codes: list[str], explanation: str) -> GateCheckResult:
    return _gate(gate_id, group, GateStatus.CAUTION, "caution", False, reason_codes, explanation)


def _pass(gate_id: str, group: str, reason_codes: list[str], explanation: str) -> GateCheckResult:
    return _gate(gate_id, group, GateStatus.PASS, "info", False, reason_codes, explanation)


def _info(gate_id: str, group: str, reason_codes: list[str], explanation: str) -> GateCheckResult:
    return _gate(gate_id, group, GateStatus.INFO, "info", False, reason_codes, explanation)


def _not_executed(gate_id: str, gate_label: str, family: StrategyFamily, setup_subtype: str, reason: str) -> GateCheckResult:
    return _info(
        gate_id,
        "Strategy-aware conditional gates",
        [f"gate.conditional.{gate_id.split('.')[-1]}.not_executed"],
        f"{gate_label} was not executed for {family.value} {setup_subtype}: {reason}",
    )


def _conditional_action(gate_id: str, group: str, action: ConditionalGateAction, reason_codes: list[str], explanation: str) -> GateCheckResult:
    if action == "hard_block":
        return _fail(gate_id, group, reason_codes, explanation)
    if action == "caution":
        return _caution(gate_id, group, reason_codes, explanation)
    return _info(gate_id, group, reason_codes, explanation)


def _family_action(mapping: dict[Any, ConditionalGateAction], family: StrategyFamily, default: ConditionalGateAction) -> ConditionalGateAction:
    return mapping.get(family) or mapping.get(family.value) or default


def _gate(gate_id: str, group: str, status: GateStatus, severity: str, blocks: bool, reason_codes: list[str], explanation: str) -> GateCheckResult:
    return GateCheckResult(gateId=gate_id, group=group, status=status, severity=severity, blocksNewEntry=blocks, reasonCodes=reason_codes, explanation=explanation)


def _number(state: dict[str, Any], key: str) -> float | None:
    value = state.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candidate_family(context: GlobalGateInput) -> StrategyFamily | None:
    if context.candidateStrategyFamily:
        return StrategyFamily(context.candidateStrategyFamily)
    if context.candidate:
        value = (
            context.candidate.features.get("strategyFamily")
            or context.candidate.features.get("family")
            or context.candidate.features.get("candidateStrategyFamily")
        )
        if value:
            try:
                return StrategyFamily(str(value))
            except ValueError:
                return None
    if context.ensembleDecision and len(context.ensembleDecision.supportingFamilies) == 1:
        return StrategyFamily(context.ensembleDecision.supportingFamilies[0])
    return None


def _candidate_direction(context: GlobalGateInput) -> int:
    if context.candidate:
        return int(context.candidate.direction)
    if context.ensembleDecision:
        return int(context.ensembleDecision.direction)
    return 0


def _direction_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, Direction):
        return int(value)
    if isinstance(value, (int, float)):
        numeric = int(value)
        if numeric in {-1, 0, 1}:
            return numeric
    normalized = str(value).strip().lower()
    if normalized in {"buy", "bullish", "long", "up", "1"}:
        return 1
    if normalized in {"sell", "bearish", "short", "down", "-1"}:
        return -1
    if normalized in {"hold", "neutral", "flat", "mixed", "0", "none"}:
        return 0
    return None


def _string_feature(features: dict[str, Any], key: str) -> str | None:
    value = features.get(key)
    return str(value) if value is not None and str(value) else None


def _event_context(context: GlobalGateInput) -> dict[str, Any]:
    market_event = context.marketState.get("economicEventState")
    if isinstance(market_event, dict):
        return market_event
    for signal in context.contextSignals:
        if signal.contextId == "economic_event_context":
            return signal.features
    return {}


def _context_feature_number(context: GlobalGateInput, context_id: str, feature_name: str) -> float | None:
    for signal in context.contextSignals:
        if signal.contextId == context_id:
            return _number(signal.features, feature_name)
    return None

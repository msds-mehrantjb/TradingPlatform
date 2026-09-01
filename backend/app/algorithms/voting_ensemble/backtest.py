"""Dedicated backtest runner for the backend-authoritative Voting Ensemble."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from backend.app.algorithms.voting_ensemble.event_calendar import (
    event_calendar_from_payload,
    resolve_event_veto,
)
from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig, backtest_config_reason_codes
from backend.app.algorithms.voting_ensemble.models import AlgoSignal, VotingCandle
from backend.app.algorithms.voting_ensemble.exit_policy import VotingEnsembleExecutionSimulator, exit_policy_reason_codes
from backend.app.algorithms.voting_ensemble.pipeline import VotingEnsemblePipeline
from backend.app.algorithms.voting_ensemble.profit_target_policy import profit_target_reason_codes
from backend.app.algorithms.voting_ensemble.snapshot.builder import build_backtest_snapshot
from backend.app.algorithms.voting_ensemble.stop_loss_policy import stop_loss_reason_codes
from backend.app.algorithms.voting_ensemble.strategies.registry import (
    VOTING_ENSEMBLE_ACTIVE_CONTEXT_STRATEGIES,
    VOTING_ENSEMBLE_ACTIVE_DIRECTIONAL_STRATEGIES,
    VOTING_ENSEMBLE_MODULE_INVENTORY,
)
from backend.app.domain.feature_engine import MarketCandle
from backend.app.domain.models import OrderPlan, Signal


VOTING_ENSEMBLE_BACKTEST_VERSION = "voting_ensemble_dedicated_backtest_v1"
VOTING_ENSEMBLE_DIRECTIONAL_CATALOG = tuple(entry.strategyName for entry in VOTING_ENSEMBLE_ACTIVE_DIRECTIONAL_STRATEGIES)
VOTING_ENSEMBLE_CONTEXT_CATALOG = tuple(entry.strategyName for entry in VOTING_ENSEMBLE_ACTIVE_CONTEXT_STRATEGIES)


class VotingBacktestService(Protocol):
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class VotingEnsembleBacktestRunner:
    service: VotingBacktestService = field(default_factory=VotingEnsemblePipeline)
    config: VotingEnsembleBacktestConfig = field(default_factory=VotingEnsembleBacktestConfig)

    def run(
        self,
        *,
        symbol: str,
        spy_1m_candles: list[dict[str, Any] | VotingCandle],
        spy_5m_candles: list[dict[str, Any] | VotingCandle] | None = None,
        spy_15m_candles: list[dict[str, Any] | VotingCandle] | None = None,
        qqq_candles: list[dict[str, Any] | VotingCandle] | None = None,
        iwm_candles: list[dict[str, Any] | VotingCandle] | None = None,
        breadth_components: dict[str, list[dict[str, Any] | VotingCandle]] | None = None,
        external_breadth_feed: dict[str, Any] | None = None,
        timeframe: str = "1Min",
    ) -> dict[str, Any]:
        one_minute = _sort_voting_candles(spy_1m_candles)
        five_minute = _sort_voting_candles(spy_5m_candles or [])
        fifteen_minute = _sort_voting_candles(spy_15m_candles or []) or _aggregate_voting_candles(one_minute, 15)
        qqq = _sort_voting_candles(qqq_candles or [])
        iwm = _sort_voting_candles(iwm_candles or [])
        breadth = {
            symbol.upper(): _sort_voting_candles(component_candles)
            for symbol, component_candles in (breadth_components or {}).items()
        }
        if not one_minute:
            return self._empty_result(symbol=symbol, timeframe=timeframe, data_quality=self._data_quality(five_minute, fifteen_minute, qqq, iwm, breadth, False))

        trades: list[dict[str, Any]] = []
        stage_results: list[dict[str, Any]] = []
        stress_results: list[dict[str, Any]] = []
        decision_count = 0
        active_until: datetime | None = None
        simulator = VotingEnsembleExecutionSimulator(self.config.execution)
        sessions = _group_by_session(one_minute)
        for session_date, session_candles in sorted(sessions.items()):
            for index, candle in enumerate(session_candles):
                if index + 1 < self.config.warmupCandles:
                    continue
                prefix = tuple(session_candles[: index + 1])
                evaluation = self._evaluate_at(
                    symbol=symbol,
                    timestamp=candle.timestamp,
                    candles=prefix,
                    five_minute=five_minute,
                    fifteen_minute=fifteen_minute,
                    qqq=qqq,
                    iwm=iwm,
                    breadth=breadth,
                    external_breadth_feed=external_breadth_feed,
                )
                position_active = bool(active_until and candle.timestamp <= active_until)
                order_plan = None if position_active else self._order_plan(symbol, evaluation, candle, session_date)
                future_candles = [_market_candle_from_voting(item, symbol=symbol, timeframe="1Min") for item in session_candles[index + 1 :]]
                execution = simulator.simulate(order_plan, future_candles, candle.timestamp) if order_plan else None
                stress_results.extend(
                    self._stress_results(
                        symbol=symbol,
                        timestamp=candle.timestamp,
                        evaluation=evaluation,
                        order_plan=order_plan,
                        future_candles=future_candles,
                    )
                )
                record = self._stage_result(
                    symbol=symbol,
                    timestamp=candle.timestamp,
                    evaluation=evaluation,
                    order_plan=order_plan,
                    execution=execution,
                    position_active=position_active,
                    input_stage=self._input_stage(
                        timestamp=candle.timestamp,
                        candles=prefix,
                        five_minute=five_minute,
                        fifteen_minute=fifteen_minute,
                        qqq=qqq,
                        iwm=iwm,
                        breadth=breadth,
                    ),
                )
                decision_count += 1
                if self.config.includeDecisionRecords and (
                    self.config.maximumDecisionRecords is None or len(stage_results) < self.config.maximumDecisionRecords
                ):
                    stage_results.append(record)
                if execution and order_plan and execution.fill.filledQuantity > 0:
                    trade = self._trade_record(record, order_plan, execution)
                    trades.append(trade)
                    if execution.exit and execution.exit.exitAt:
                        active_until = execution.exit.exitAt
                    else:
                        active_until = execution.fill.filledAt

        return {
            **self._metrics(
                trades=trades,
                bars=len(one_minute),
                sessions=len(sessions),
                timeframe=timeframe,
                date_label=f"{one_minute[0].timestamp.date()} to {one_minute[-1].timestamp.date()}",
            ),
            "engineVersion": "voting_ensemble_v2",
            "backtestVersion": VOTING_ENSEMBLE_BACKTEST_VERSION,
            "backtestConfigVersion": self.config.configVersion,
            "backtestConfigReasonCodes": list(backtest_config_reason_codes()),
            "algorithmVersion": "voting_ensemble_backend_v2",
            "strategyCatalog": {
                "directional": list(VOTING_ENSEMBLE_DIRECTIONAL_CATALOG),
                "context": list(VOTING_ENSEMBLE_CONTEXT_CATALOG),
                "moduleInventory": VOTING_ENSEMBLE_MODULE_INVENTORY.model_dump(mode="json"),
                "removedVoters": ["Ensemble Strategy Voting"],
            },
            "dataQuality": self._data_quality(five_minute, fifteen_minute, qqq, iwm, breadth, bool(spy_15m_candles)),
            "costStress": _stress_summary(stress_results),
            "decisionCount": decision_count,
            "stageResultCount": decision_count,
            "stageResults": stage_results,
            "decisionRecords": stage_results,
            "explanation": "Dedicated Voting Ensemble backtest used the unified Voting Ensemble pipeline for pre-execution decisions and limited backtest work to point-in-time event delivery, deterministic synthetic quotes, simulated fills, and reporting.",
        }

    def _evaluate_at(
        self,
        *,
        symbol: str,
        timestamp: datetime,
        candles: tuple[VotingCandle, ...],
        five_minute: tuple[VotingCandle, ...],
        fifteen_minute: tuple[VotingCandle, ...],
        qqq: tuple[VotingCandle, ...],
        iwm: tuple[VotingCandle, ...],
        breadth: dict[str, tuple[VotingCandle, ...]],
        external_breadth_feed: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = {
            "symbol": symbol.upper(),
            "data_timestamp": timestamp.isoformat(),
            "candles": [candle.model_dump(mode="json") for candle in candles],
            "spy_5m_candles": [candle.model_dump(mode="json") for candle in _prefix(five_minute, timestamp)],
            "spy_15m_candles": [candle.model_dump(mode="json") for candle in _prefix(fifteen_minute, timestamp)],
            "qqq_candles": [candle.model_dump(mode="json") for candle in _prefix(qqq, timestamp)],
            "iwm_candles": [candle.model_dump(mode="json") for candle in _prefix(iwm, timestamp)],
            "breadth_components": {
                name: [candle.model_dump(mode="json") for candle in _prefix(component, timestamp)]
                for name, component in breadth.items()
            },
            "external_breadth_feed": external_breadth_feed,
            "nbbo": _synthetic_backtest_nbbo(candles[-1], timestamp),
            # The event veto has to ride the snapshot, because the gates read it from
            # snapshot.economicEventState rather than from the evaluate payload. Without
            # this a replay of a gated live run would silently skip every blackout the
            # live run honoured, and the two would not be comparable.
            "market_context": {"event": self._event_state_at(timestamp)},
        }
        snapshot = build_backtest_snapshot(payload)
        evaluate_payload = snapshot.to_evaluate_payload()
        session_policy = getattr(self.config, "sessionPolicy", None)
        if isinstance(session_policy, dict):
            # Read by the service above the voting layer, so it attaches to the evaluate
            # payload rather than the snapshot.
            evaluate_payload["session_policy"] = session_policy
        if hasattr(self.service, "run"):
            envelope = self.service.run(evaluate_payload, mode="backtest")
            decision = dict(envelope["decision"])
            decision["pipeline_envelope"] = {key: value for key, value in envelope.items() if key != "decision"}
            return decision
        return self.service.evaluate(evaluate_payload)

    def _event_state_at(self, timestamp: datetime) -> dict[str, Any]:
        """Resolve the configured calendar for this bar, exactly as the live path does.

        Same module and same bar-end input as the producer, so a replay and the live run
        it reproduces reach the same verdict for the same bar instead of two
        implementations that merely look alike.
        """
        calendar = event_calendar_from_payload(getattr(self.config, "eventCalendar", None))
        return resolve_event_veto(bar_end=timestamp, settings=calendar).as_event_state()

    def _order_plan(self, symbol: str, evaluation: dict[str, Any], candle: VotingCandle, session_date: date) -> OrderPlan | None:
        order_payload = evaluation.get("order_plan")
        if not isinstance(order_payload, dict):
            return None
        order_plan = OrderPlan.model_validate(order_payload)
        if not order_plan.eligible or order_plan.orderType == "NO_ORDER" or order_plan.quantity <= 0:
            return None
        return order_plan

    def _input_stage(
        self,
        *,
        timestamp: datetime,
        candles: tuple[VotingCandle, ...],
        five_minute: tuple[VotingCandle, ...],
        fifteen_minute: tuple[VotingCandle, ...],
        qqq: tuple[VotingCandle, ...],
        iwm: tuple[VotingCandle, ...],
        breadth: dict[str, tuple[VotingCandle, ...]],
    ) -> dict[str, Any]:
        five_prefix = _prefix(five_minute, timestamp)
        fifteen_prefix = _prefix(fifteen_minute, timestamp)
        qqq_prefix = _prefix(qqq, timestamp)
        iwm_prefix = _prefix(iwm, timestamp)
        breadth_prefix = {name: _prefix(component, timestamp) for name, component in breadth.items()}
        return {
            "timestampUtc": timestamp.isoformat().replace("+00:00", "Z"),
            "pointInTime": True,
            "spy1m": _stream_summary(candles),
            "spy5m": _stream_summary(five_prefix),
            "spy15m": _stream_summary(fifteen_prefix),
            "qqq1m": _stream_summary(qqq_prefix),
            "iwm1m": _stream_summary(iwm_prefix),
            "breadthComponents": {name: _stream_summary(component) for name, component in breadth_prefix.items()},
        }

    def _stage_result(
        self,
        *,
        symbol: str,
        timestamp: datetime,
        evaluation: dict[str, Any],
        order_plan: OrderPlan | None,
        execution: Any,
        position_active: bool,
        input_stage: dict[str, Any],
    ) -> dict[str, Any]:
        final_signal = evaluation.get("final_signal")
        safety_reason = "Existing backtest position is active; no new entry order was created." if position_active else "No active backtest position blocked this timestamp."
        return {
            "schemaVersion": "voting_ensemble_stage_result_v1",
            "symbol": symbol.upper(),
            "decisionTimestampUtc": timestamp.isoformat().replace("+00:00", "Z"),
            "stages": {
                "inputData": input_stage,
                "directionalStrategies": evaluation.get("votes", []),
                "contextSignals": evaluation.get("context_signals", []),
                "familyAwareEnsemble": {
                    "candidateSignal": final_signal,
                    "baseScore": evaluation.get("base_score"),
                    "familyScores": evaluation.get("family_scores"),
                    "familySupport": evaluation.get("family_support"),
                    "safetyGateFailed": evaluation.get("safety_gate_failed"),
                    "reasonCodes": evaluation.get("reason_codes", []),
                },
                "contextAdjustment": {
                    "finalSignal": final_signal,
                    "contextAdjustedScore": evaluation.get("context_adjusted_score"),
                    "agreements": evaluation.get("context_agreements"),
                    "conflicts": evaluation.get("context_conflicts"),
                    "reason": evaluation.get("context_adjustment_reason"),
                    "confirmation": evaluation.get("context_confirmation"),
                },
                "safetyAndPosition": {
                    "positionActive": position_active,
                    "eligibleForNewEntry": bool(order_plan and order_plan.eligible),
                    "reason": safety_reason,
                },
                "candidateOrder": order_plan.model_dump(mode="json") if order_plan else None,
                "riskBudget": evaluation.get("risk_budget"),
                "execution": {
                    "fill": execution.fill.model_dump(mode="json") if execution else None,
                    "exit": execution.exit.model_dump(mode="json") if execution and execution.exit else None,
                    "reasonCodes": execution.reasonCodes if execution else [],
                },
            },
            "finalSignal": evaluation.get("final_signal"),
            "baseScore": evaluation.get("base_score"),
            "contextAdjustedScore": evaluation.get("context_adjusted_score"),
            "familyScores": evaluation.get("family_scores"),
            "familySupport": evaluation.get("family_support"),
            "strategyOutputs": evaluation.get("votes", []),
            "contextSignals": evaluation.get("context_signals", []),
            "candidate": order_plan.model_dump(mode="json") if order_plan else None,
            "riskBudget": evaluation.get("risk_budget"),
            "fill": execution.fill.model_dump(mode="json") if execution else None,
            "exit": execution.exit.model_dump(mode="json") if execution and execution.exit else None,
            "reasonCodes": evaluation.get("reason_codes", []),
        }

    def _trade_record(self, record: dict[str, Any], order_plan: OrderPlan, execution: Any) -> dict[str, Any]:
        side = "Long" if order_plan.side == Signal.BUY.value else "Short"
        exit_result = execution.exit
        gross_pnl = float(exit_result.grossPnl if exit_result else 0.0)
        net_pnl = float(exit_result.pnl if exit_result else 0.0)
        return {
            "side": side,
            "decisionTimestampUtc": record["decisionTimestampUtc"],
            "entryAt": execution.fill.filledAt.isoformat().replace("+00:00", "Z") if execution.fill.filledAt else None,
            "exitAt": exit_result.exitAt.isoformat().replace("+00:00", "Z") if exit_result and exit_result.exitAt else None,
            "entryPrice": execution.fill.averagePrice,
            "exitPrice": exit_result.exitPrice if exit_result else None,
            "quantity": execution.fill.filledQuantity,
            "grossPnl": round(gross_pnl, 2),
            "netPnl": round(net_pnl, 2),
            "pnl": round(net_pnl, 2),
            "expenses": round(execution.fill.costs.get("total", 0.0) + (exit_result.costs.get("total", 0.0) if exit_result else 0.0), 2),
            "exitReason": exit_result.exitReason if exit_result else "open",
            "strategy": "Voting Ensemble V2",
            "family": _record_family(record),
            "regime": _record_regime(record),
            "session": record["decisionTimestampUtc"][:10],
            "reasonCodes": execution.reasonCodes,
        }

    def _stress_results(
        self,
        *,
        symbol: str,
        timestamp: datetime,
        evaluation: dict[str, Any],
        order_plan: OrderPlan | None,
        future_candles: list[MarketCandle],
    ) -> list[dict[str, Any]]:
        if order_plan is None:
            return []
        rows: list[dict[str, Any]] = []
        for scenario in self.config.executionStressScenarios:
            execution = VotingEnsembleExecutionSimulator(scenario).simulate(order_plan, future_candles, timestamp)
            exit_result = execution.exit
            gross = float(exit_result.grossPnl if exit_result else 0.0)
            net = float(exit_result.pnl if exit_result else 0.0)
            rows.append(
                {
                    "scenario": scenario.scenarioName,
                    "symbol": symbol.upper(),
                    "timestampUtc": timestamp.isoformat().replace("+00:00", "Z"),
                    "strategy": _evaluation_strategy(evaluation),
                    "family": _evaluation_family(evaluation),
                    "regime": _evaluation_regime(evaluation),
                    "session": timestamp.date().isoformat(),
                    "fillStatus": execution.fill.status,
                    "exitStatus": exit_result.status if exit_result else None,
                    "grossPnl": round(gross, 6),
                    "netPnl": round(net, 6),
                    "costs": {
                        "entry": execution.fill.costs,
                        "exit": exit_result.costs if exit_result else {},
                        "total": round(float(execution.fill.costs.get("total", 0.0)) + float((exit_result.costs if exit_result else {}).get("total", 0.0)), 6),
                    },
                    "promotionBlocked": net <= 0.0 or execution.fill.status in {"UNFILLED", "EXPIRED"},
                    "reasonCodes": execution.reasonCodes,
                }
            )
        return rows

    def _metrics(self, *, trades: list[dict[str, Any]], bars: int, sessions: int, timeframe: str, date_label: str) -> dict[str, Any]:
        net_total_pnl = round(sum(float(trade.get("netPnl") if trade.get("netPnl") is not None else trade.get("pnl") or 0.0) for trade in trades), 2)
        gross_total_pnl = round(sum(float(trade.get("grossPnl") or 0.0) for trade in trades), 2)
        gross_profit = round(sum(float(trade.get("netPnl") if trade.get("netPnl") is not None else trade.get("pnl") or 0.0) for trade in trades if float(trade.get("netPnl") if trade.get("netPnl") is not None else trade.get("pnl") or 0.0) > 0), 2)
        gross_loss = round(abs(sum(float(trade.get("netPnl") if trade.get("netPnl") is not None else trade.get("pnl") or 0.0) for trade in trades if float(trade.get("netPnl") if trade.get("netPnl") is not None else trade.get("pnl") or 0.0) < 0)), 2)
        winners = sum(1 for trade in trades if float(trade.get("netPnl") if trade.get("netPnl") is not None else trade.get("pnl") or 0.0) > 0)
        losers = sum(1 for trade in trades if float(trade.get("netPnl") if trade.get("netPnl") is not None else trade.get("pnl") or 0.0) < 0)
        final_equity = round(self.config.startingCapital + net_total_pnl, 2)
        max_drawdown = abs(min(0.0, net_total_pnl))
        return {
            "dateLabel": date_label,
            "trades": trades,
            "grossTotalPnl": gross_total_pnl,
            "netTotalPnl": net_total_pnl,
            "totalPnl": net_total_pnl,
            "totalReturnPercent": round(((final_equity - self.config.startingCapital) / self.config.startingCapital) * 100, 2),
            "startingCapital": self.config.startingCapital,
            "finalEquity": final_equity,
            "maxDrawdown": round(max_drawdown, 2),
            "maxDrawdownPercent": round((max_drawdown / self.config.startingCapital) * 100, 2),
            "grossProfit": gross_profit,
            "grossLoss": gross_loss,
            "totalExpenses": round(sum(float(trade.get("expenses") or 0.0) for trade in trades), 2),
            "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
            "averageWin": round(gross_profit / winners, 2) if winners else 0,
            "averageLoss": round(gross_loss / losers, 2) if losers else 0,
            "expectancy": round(net_total_pnl / len(trades), 2) if trades else 0,
            "winners": winners,
            "losers": losers,
            "bars": bars,
            "sessions": sessions,
            "riskConfig": self.config.model_dump(mode="json"),
            "timeframe": timeframe,
            "strategyDescription": "Dedicated Voting Ensemble backend backtest",
            "totalTrades": len(trades),
            "netPerformanceByStrategy": _aggregate_trade_performance(trades, "strategy"),
            "netPerformanceByFamily": _aggregate_trade_performance(trades, "family"),
            "netPerformanceByRegime": _aggregate_trade_performance(trades, "regime"),
            "netPerformanceBySession": _aggregate_trade_performance(trades, "session"),
        }

    def _empty_result(self, *, symbol: str, timeframe: str, data_quality: dict[str, Any]) -> dict[str, Any]:
        return {
            **self._metrics(trades=[], bars=0, sessions=0, timeframe=timeframe, date_label="No candles"),
            "engineVersion": "voting_ensemble_v2",
            "backtestVersion": VOTING_ENSEMBLE_BACKTEST_VERSION,
            "backtestConfigVersion": self.config.configVersion,
            "backtestConfigReasonCodes": list(backtest_config_reason_codes()),
            "algorithmVersion": "voting_ensemble_backend_v2",
            "symbol": symbol.upper(),
            "strategyCatalog": {
                "directional": list(VOTING_ENSEMBLE_DIRECTIONAL_CATALOG),
                "context": list(VOTING_ENSEMBLE_CONTEXT_CATALOG),
                "moduleInventory": VOTING_ENSEMBLE_MODULE_INVENTORY.model_dump(mode="json"),
                "removedVoters": ["Ensemble Strategy Voting"],
            },
            "dataQuality": data_quality,
            "costStress": _stress_summary([]),
            "decisionCount": 0,
            "stageResultCount": 0,
            "stageResults": [],
            "decisionRecords": [],
            "explanation": "Dedicated Voting Ensemble backtest had no SPY 1m candles to evaluate.",
        }

    def _data_quality(
        self,
        five_minute: tuple[VotingCandle, ...],
        fifteen_minute: tuple[VotingCandle, ...],
        qqq: tuple[VotingCandle, ...],
        iwm: tuple[VotingCandle, ...],
        breadth: dict[str, tuple[VotingCandle, ...]],
        native_fifteen_minute: bool,
    ) -> dict[str, Any]:
        missing = []
        if not five_minute:
            missing.append("spy_5m_candles")
        if not qqq:
            missing.append("qqq_candles")
        if not iwm:
            missing.append("iwm_candles")
        if not breadth:
            missing.append("breadth_components")
        return {
            "usesActual5m": bool(five_minute),
            "usesActual15m": native_fifteen_minute,
            "usesDerived15m": bool(fifteen_minute) and not native_fifteen_minute,
            "usesActualQqqIwm": bool(qqq and iwm),
            "usesSyntheticQqqIwm": False,
            "breadthComponentCount": len(breadth),
            "missingInputs": missing,
            "policy": "Missing auxiliary data is reported as unavailable; the runner never substitutes SPY for QQQ/IWM or breadth.",
        }


def _sort_voting_candles(rows: list[dict[str, Any] | VotingCandle]) -> tuple[VotingCandle, ...]:
    return tuple(sorted((_voting_candle(row) for row in rows), key=lambda candle: candle.timestamp))


def _voting_candle(row: dict[str, Any] | VotingCandle) -> VotingCandle:
    if isinstance(row, VotingCandle):
        return row
    return VotingCandle(
        timestamp=_timestamp(row["timestamp"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume", 0.0)),
    )


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _group_by_session(candles: tuple[VotingCandle, ...]) -> dict[date, list[VotingCandle]]:
    sessions: dict[date, list[VotingCandle]] = {}
    for candle in candles:
        sessions.setdefault(candle.timestamp.date(), []).append(candle)
    return sessions


def _prefix(candles: tuple[VotingCandle, ...], timestamp: datetime) -> tuple[VotingCandle, ...]:
    return tuple(candle for candle in candles if candle.timestamp <= timestamp)


def _stream_summary(candles: tuple[VotingCandle, ...]) -> dict[str, Any]:
    if not candles:
        return {
            "count": 0,
            "firstTimestampUtc": None,
            "lastTimestampUtc": None,
            "dataReady": False,
        }
    return {
        "count": len(candles),
        "firstTimestampUtc": candles[0].timestamp.isoformat().replace("+00:00", "Z"),
        "lastTimestampUtc": candles[-1].timestamp.isoformat().replace("+00:00", "Z"),
        "dataReady": True,
    }


def _aggregate_voting_candles(candles: tuple[VotingCandle, ...], size: int) -> tuple[VotingCandle, ...]:
    groups = [candles[index : index + size] for index in range(0, len(candles), size)]
    return tuple(
        VotingCandle(
            timestamp=group[-1].timestamp,
            open=group[0].open,
            high=max(candle.high for candle in group),
            low=min(candle.low for candle in group),
            close=group[-1].close,
            volume=sum(candle.volume for candle in group),
        )
        for group in groups
        if len(group) == size
    )


def _synthetic_backtest_nbbo(candle: VotingCandle, timestamp: datetime) -> dict[str, Any]:
    midpoint = candle.close
    half_spread = 0.01
    return {
        "bid": round(max(0.01, midpoint - half_spread), 6),
        "ask": round(midpoint + half_spread, 6),
        "bidSize": 1000,
        "askSize": 1000,
        "quoteTimestamp": timestamp.isoformat(),
        "lastTradeTimestamp": timestamp.isoformat(),
        "marketDataReceiptTimestamp": timestamp.isoformat(),
        "maxQuoteAgeSeconds": 5,
        "maxReceiptAgeSeconds": 5,
        "source": "backtest_fixed_quote_model_not_candle_range_or_volume",
    }


def _market_candle_from_voting(candle: VotingCandle, *, symbol: str, timeframe: str) -> MarketCandle:
    normalized_timeframe = timeframe if timeframe in {"1Min", "5Min", "15Min"} else None
    return MarketCandle(
        timestamp=candle.timestamp,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        symbol=symbol.upper(),
        timeframe=normalized_timeframe,
    )


def _normalize_algo_signal(value: Any) -> AlgoSignal:
    if value in {"Buy", "Sell"}:
        return value
    return "Hold"


def _evaluation_strategy(evaluation: dict[str, Any]) -> str:
    votes = evaluation.get("votes")
    if isinstance(votes, list) and votes:
        first = votes[0]
        if isinstance(first, dict):
            return str(first.get("strategy") or "Voting Ensemble V2")
    return "Voting Ensemble V2"


def _evaluation_family(evaluation: dict[str, Any]) -> str:
    support = evaluation.get("family_support")
    if isinstance(support, dict) and support:
        return str(next(iter(support)))
    votes = evaluation.get("votes")
    if isinstance(votes, list) and votes:
        first = votes[0]
        if isinstance(first, dict):
            return str(first.get("family") or "unknown_family")
    return "unknown_family"


def _evaluation_regime(evaluation: dict[str, Any]) -> str:
    profile = evaluation.get("resolved_trading_profile")
    if isinstance(profile, dict):
        overlays = profile.get("activeOverlays")
        if isinstance(overlays, list | tuple) and overlays:
            return str(overlays[0])
    return "unknown_regime"


def _record_family(record: dict[str, Any]) -> str:
    return _evaluation_family({"family_support": record.get("familySupport"), "votes": record.get("strategyOutputs")})


def _record_regime(record: dict[str, Any]) -> str:
    return "unknown_regime"


def _aggregate_trade_performance(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, dict[str, float | int]] = {}
    for row in rows:
        name = str(row.get(key) or "unknown")
        bucket = grouped.setdefault(name, {"trades": 0, "grossPnl": 0.0, "netPnl": 0.0, "costs": 0.0})
        bucket["trades"] = int(bucket["trades"]) + 1
        bucket["grossPnl"] = round(float(bucket["grossPnl"]) + float(row.get("grossPnl") or 0.0), 6)
        bucket["netPnl"] = round(float(bucket["netPnl"]) + float(row.get("netPnl") if row.get("netPnl") is not None else row.get("pnl") or 0.0), 6)
        bucket["costs"] = round(float(bucket["costs"]) + float(row.get("expenses") or 0.0), 6)
    return grouped


def _stress_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_scenario.setdefault(str(row.get("scenario") or "unknown"), []).append(row)
    scenario_summary = {
        scenario: {
            "trades": len(items),
            "filledTrades": sum(1 for item in items if item.get("fillStatus") in {"FILLED", "PARTIAL"}),
            "grossPnl": round(sum(float(item.get("grossPnl") or 0.0) for item in items), 6),
            "netPnl": round(sum(float(item.get("netPnl") or 0.0) for item in items), 6),
            "costs": round(sum(float((item.get("costs") or {}).get("total") or 0.0) for item in items), 6),
            "promotionBlocked": any(bool(item.get("promotionBlocked")) for item in items),
        }
        for scenario, items in sorted(by_scenario.items())
    }
    promotion_blocked = any(summary["promotionBlocked"] for summary in scenario_summary.values())
    return {
        "scenarioDriven": True,
        "scenarioResults": scenario_summary,
        "netPerformanceByStrategy": _aggregate_stress_performance(rows, "strategy"),
        "netPerformanceByFamily": _aggregate_stress_performance(rows, "family"),
        "netPerformanceByRegime": _aggregate_stress_performance(rows, "regime"),
        "netPerformanceBySession": _aggregate_stress_performance(rows, "session"),
        "promotionGate": {
            "promotionBlocked": promotion_blocked,
            "basis": "net_performance_after_estimated_costs",
            "reasonCodes": ["voting_ensemble.backtest.promotion_requires_net_stress_performance"],
        },
        "reasonCodes": ["voting_ensemble.backtest.execution_stress_scenarios_evaluated"],
    }


def _aggregate_stress_performance(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, dict[str, float | int]] = {}
    for row in rows:
        name = str(row.get(key) or "unknown")
        bucket = grouped.setdefault(name, {"trades": 0, "grossPnl": 0.0, "netPnl": 0.0, "costs": 0.0, "blockedScenarios": 0})
        bucket["trades"] = int(bucket["trades"]) + 1
        bucket["grossPnl"] = round(float(bucket["grossPnl"]) + float(row.get("grossPnl") or 0.0), 6)
        bucket["netPnl"] = round(float(bucket["netPnl"]) + float(row.get("netPnl") or 0.0), 6)
        bucket["costs"] = round(float(bucket["costs"]) + float((row.get("costs") or {}).get("total") or 0.0), 6)
        bucket["blockedScenarios"] = int(bucket["blockedScenarios"]) + (1 if row.get("promotionBlocked") else 0)
    return grouped


def _number(payload: dict[str, Any], key: str) -> float | None:
    try:
        value = payload.get(key)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None

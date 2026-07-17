"""Dedicated backtest runner for the backend-authoritative Voting Ensemble."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig, backtest_config_reason_codes
from backend.app.algorithms.voting_ensemble.models import AlgoSignal, VotingCandle
from backend.app.algorithms.voting_ensemble.exit_policy import VotingEnsembleExecutionSimulator, exit_policy_reason_codes
from backend.app.algorithms.voting_ensemble.profit_target_policy import initial_target_price, profit_target_reason_codes
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService
from backend.app.algorithms.voting_ensemble.stop_loss_policy import initial_stop_price, stop_loss_reason_codes
from backend.app.domain.feature_engine import MarketCandle
from backend.app.domain.models import OrderPlan, Signal


VOTING_ENSEMBLE_BACKTEST_VERSION = "voting_ensemble_dedicated_backtest_v1"
VOTING_ENSEMBLE_DIRECTIONAL_CATALOG = (
    "Multi-Timeframe Trend Alignment",
    "First Pullback After Open",
    "Failed Breakout Strategy",
    "Liquidity Sweep Reversal",
    "Bollinger Band Reversion",
    "ATR Overextension Reversion",
    "Economic Event Reaction Strategy",
)
VOTING_ENSEMBLE_CONTEXT_CATALOG = (
    "Relative Strength vs QQQ/IWM",
    "Market Breadth Momentum",
)


class VotingBacktestService(Protocol):
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class VotingEnsembleBacktestRunner:
    service: VotingBacktestService = field(default_factory=VotingEnsembleService)
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
            "algorithmVersion": "voting_ensemble_backend_v1",
            "strategyCatalog": {
                "directional": list(VOTING_ENSEMBLE_DIRECTIONAL_CATALOG),
                "context": list(VOTING_ENSEMBLE_CONTEXT_CATALOG),
                "removedVoters": ["Ensemble Strategy Voting"],
            },
            "dataQuality": self._data_quality(five_minute, fifteen_minute, qqq, iwm, breadth, bool(spy_15m_candles)),
            "decisionCount": decision_count,
            "stageResultCount": decision_count,
            "stageResults": stage_results,
            "decisionRecords": stage_results,
            "explanation": "Dedicated Voting Ensemble backtest used the isolated backend VotingEnsembleService, the Voting Ensemble-only catalog, point-in-time prefixes, and shared realistic execution simulation.",
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
        }
        return self.service.evaluate(payload)

    def _order_plan(self, symbol: str, evaluation: dict[str, Any], candle: VotingCandle, session_date: date) -> OrderPlan | None:
        final_signal = _normalize_algo_signal(evaluation.get("final_signal"))
        if final_signal == "Hold":
            return None
        side = Signal.BUY if final_signal == "Buy" else Signal.SELL
        entry = candle.close
        if side == Signal.BUY:
            stop = initial_stop_price(side=side, entry_price=entry, stop_distance=self.config.stopDistance)
            target = initial_target_price(side=side, entry_price=entry, target_distance=self.config.targetDistance)
        else:
            stop = initial_stop_price(side=side, entry_price=entry, stop_distance=self.config.stopDistance)
            target = initial_target_price(side=side, entry_price=entry, target_distance=self.config.targetDistance)
        return OrderPlan(
            orderPlanId=f"voting-ensemble-order-{int(candle.timestamp.timestamp())}",
            candidateId=f"voting-ensemble-candidate-{int(candle.timestamp.timestamp())}",
            symbol=symbol.upper(),
            side=side,
            orderType="MARKET",
            quantity=self.config.quantity,
            entryPrice=entry,
            stopPrice=stop,
            targetPrice=target,
            maximumHoldingMinutes=self.config.maximumHoldingMinutes,
            timeInForce="DAY",
            eligible=True,
            explanation="Dedicated Voting Ensemble backtest market order generated with Voting Ensemble stop-loss policy.",
            generatedAt=candle.timestamp,
            sessionDate=session_date,
            configurationHash=f"{VOTING_ENSEMBLE_BACKTEST_VERSION}:{self.config.configVersion}:{','.join((*backtest_config_reason_codes(), *stop_loss_reason_codes(), *profit_target_reason_codes(), *exit_policy_reason_codes()))}",
        )

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
            "fill": execution.fill.model_dump(mode="json") if execution else None,
            "exit": execution.exit.model_dump(mode="json") if execution and execution.exit else None,
            "reasonCodes": evaluation.get("reason_codes", []),
        }

    def _trade_record(self, record: dict[str, Any], order_plan: OrderPlan, execution: Any) -> dict[str, Any]:
        side = "Long" if order_plan.side == Signal.BUY.value else "Short"
        exit_result = execution.exit
        return {
            "side": side,
            "decisionTimestampUtc": record["decisionTimestampUtc"],
            "entryAt": execution.fill.filledAt.isoformat().replace("+00:00", "Z") if execution.fill.filledAt else None,
            "exitAt": exit_result.exitAt.isoformat().replace("+00:00", "Z") if exit_result and exit_result.exitAt else None,
            "entryPrice": execution.fill.averagePrice,
            "exitPrice": exit_result.exitPrice if exit_result else None,
            "quantity": execution.fill.filledQuantity,
            "pnl": round(exit_result.pnl if exit_result else 0.0, 2),
            "expenses": round(execution.fill.costs.get("total", 0.0) + (exit_result.costs.get("total", 0.0) if exit_result else 0.0), 2),
            "exitReason": exit_result.exitReason if exit_result else "open",
            "strategy": "Voting Ensemble V2",
            "reasonCodes": execution.reasonCodes,
        }

    def _metrics(self, *, trades: list[dict[str, Any]], bars: int, sessions: int, timeframe: str, date_label: str) -> dict[str, Any]:
        total_pnl = round(sum(float(trade.get("pnl") or 0.0) for trade in trades), 2)
        gross_profit = round(sum(float(trade.get("pnl") or 0.0) for trade in trades if float(trade.get("pnl") or 0.0) > 0), 2)
        gross_loss = round(abs(sum(float(trade.get("pnl") or 0.0) for trade in trades if float(trade.get("pnl") or 0.0) < 0)), 2)
        winners = sum(1 for trade in trades if float(trade.get("pnl") or 0.0) > 0)
        losers = sum(1 for trade in trades if float(trade.get("pnl") or 0.0) < 0)
        final_equity = round(self.config.startingCapital + total_pnl, 2)
        max_drawdown = abs(min(0.0, total_pnl))
        return {
            "dateLabel": date_label,
            "trades": trades,
            "totalPnl": total_pnl,
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
            "expectancy": round(total_pnl / len(trades), 2) if trades else 0,
            "winners": winners,
            "losers": losers,
            "bars": bars,
            "sessions": sessions,
            "riskConfig": self.config.model_dump(mode="json"),
            "timeframe": timeframe,
            "strategyDescription": "Dedicated Voting Ensemble backend backtest",
            "totalTrades": len(trades),
        }

    def _empty_result(self, *, symbol: str, timeframe: str, data_quality: dict[str, Any]) -> dict[str, Any]:
        return {
            **self._metrics(trades=[], bars=0, sessions=0, timeframe=timeframe, date_label="No candles"),
            "engineVersion": "voting_ensemble_v2",
            "backtestVersion": VOTING_ENSEMBLE_BACKTEST_VERSION,
            "backtestConfigVersion": self.config.configVersion,
            "backtestConfigReasonCodes": list(backtest_config_reason_codes()),
            "algorithmVersion": "voting_ensemble_backend_v1",
            "symbol": symbol.upper(),
            "strategyCatalog": {
                "directional": list(VOTING_ENSEMBLE_DIRECTIONAL_CATALOG),
                "context": list(VOTING_ENSEMBLE_CONTEXT_CATALOG),
                "removedVoters": ["Ensemble Strategy Voting"],
            },
            "dataQuality": data_quality,
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

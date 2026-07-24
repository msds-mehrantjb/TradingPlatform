"""Session runtime parity engine for replay, shadow paper, and historical backtest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Literal

from pydantic import Field, field_validator

from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.execution import (
    SessionCandidateDecision,
    build_session_candidate_decision,
    evaluate_session_candidate_order_gate,
)
from backend.app.algorithms.session.models import SessionClassification
from backend.app.algorithms.session.persistence import BufferedSessionDecisionWriter, build_session_decision_record
from backend.app.algorithms.session.router import resolve_session_profile, session_route_permissions
from backend.app.algorithms.session.runtime import EventDrivenSessionRuntime
from backend.app.algorithms.session.state import FINALIZED_ONE_MINUTE_BAR
from backend.app.algorithms.session.transition import SessionTransitionManager, SessionTransitionState
from backend.app.algorithms.session.backtest.result import SessionRuntimeDecisionSnapshot
from backend.app.domain.models import DomainModel, Signal, _require_utc
from backend.app.gates import GlobalGateResponse


SESSION_RUNTIME_PARITY_VERSION = "session_runtime_parity_v1"
SessionRuntimeMode = Literal["direct_replay", "backtest", "paper_shadow", "paper_affecting"]


class SessionBacktestExecutionConfig(DomainModel):
    decisionLatencyMs: int = Field(default=100, ge=0)
    submissionLatencyMs: int = Field(default=100, ge=0)
    spreadCost: float = Field(default=0.01, ge=0)
    slippage: float = Field(default=0.01, ge=0)
    fees: float = Field(default=0.001, ge=0)
    marketImpact: float = Field(default=0.001, ge=0)
    adverseSelectionBuffer: float = Field(default=0.002, ge=0)
    fillProbability: float = Field(default=0.8, ge=0, le=1)
    missedLimitFillRate: float = Field(default=0.0, ge=0, le=1)
    partialFillRatio: float | None = Field(default=None, ge=0, le=1)
    endOfDayFlatten: bool = True
    conservativeAmbiguousSameBar: bool = True


class SessionBacktestExecutionResult(DomainModel):
    status: Literal["NO_CANDIDATE", "NO_FILL", "FILLED", "STOP", "TARGET", "AMBIGUOUS_STOP", "EOD_FLATTEN"]
    side: Signal | None = None
    requestedQuantity: int = Field(default=0, ge=0)
    filledQuantity: int = Field(default=0, ge=0)
    entryPrice: float | None = Field(default=None, gt=0)
    exitPrice: float | None = Field(default=None, gt=0)
    entryTimestamp: datetime | None = None
    exitTimestamp: datetime | None = None
    grossPnl: float | None = None
    netPnl: float | None = None
    reasonCodes: tuple[str, ...]

    @field_validator("entryTimestamp", "exitTimestamp")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class SessionBacktestEngine:
    """Runs Session decisions through the same event-driven stack in every mode."""

    def __init__(
        self,
        *,
        config: SessionConfig = DEFAULT_SESSION_CONFIG,
        execution_config: SessionBacktestExecutionConfig | None = None,
        persistence_writer: BufferedSessionDecisionWriter | None = None,
    ) -> None:
        self.config = config
        self.execution_config = execution_config or SessionBacktestExecutionConfig()
        self.persistence_writer = persistence_writer

    def run(self, events: Iterable[dict[str, Any]], *, mode: SessionRuntimeMode) -> tuple[SessionRuntimeDecisionSnapshot, ...]:
        runtime = EventDrivenSessionRuntime(config=self.config)
        transitions = SessionTransitionManager(config=self.config)
        transition_state: SessionTransitionState | None = None
        snapshots: list[SessionRuntimeDecisionSnapshot] = []
        for raw_event in events:
            event = _mode_event(raw_event, mode)
            classification = runtime.process_event(event)
            if classification is None:
                continue
            if str(event.get("type") or event.get("event_type") or "") != FINALIZED_ONE_MINUTE_BAR:
                continue
            transition_state = transitions.process(classification, transition_state)
            profile = resolve_session_profile(classification, config=self.config)
            permissions = session_route_permissions(classification, config=self.config)
            candidate = self._candidate_for(classification, profile)
            gate = None
            if candidate is not None:
                gate = evaluate_session_candidate_order_gate(
                    candidate=candidate,
                    profile=profile,
                    current_classification=classification,
                    current_price=candidate.entryPrice,
                    current_time=classification.decision_time + timedelta(milliseconds=self.execution_config.submissionLatencyMs),
                    quote_age_seconds=_quote_age_seconds(classification),
                    global_gate_response=_allow_response(candidate, classification.decision_time),
                    config=self.config,
                )
            if self.persistence_writer is not None:
                record = build_session_decision_record(
                    classification=classification,
                    output_mode=_output_mode(mode),
                    config=self.config,
                    profile=profile,
                    transition_state=transition_state,
                    strategy_permissions=permissions,
                    candidate=candidate,
                    order_gate_decision=gate,
                )
                self.persistence_writer.enqueue(record)
            snapshots.append(
                _snapshot(
                    mode=mode,
                    classification=classification,
                    transition_state=transition_state,
                    profile=profile.as_dict(),
                    route_permissions=permissions,
                    gate=gate.model_dump(mode="json") if gate else None,
                    output_mode=_output_mode(mode),
                )
            )
        return tuple(snapshots)

    def simulate_execution(
        self,
        candidate: SessionCandidateDecision,
        future_bars: Iterable[dict[str, Any]],
    ) -> SessionBacktestExecutionResult:
        bars = tuple(sorted((_bar_payload(bar) for bar in future_bars), key=lambda item: item["timestamp"]))
        if not bars:
            return SessionBacktestExecutionResult(status="NO_FILL", side=candidate.side, requestedQuantity=candidate.desiredQuantity, reasonCodes=("session.backtest.no_future_bar",))
        fill_bar = bars[0]
        quantity = min(candidate.desiredQuantity, candidate.quantityCap)
        if self.execution_config.partialFillRatio is not None:
            quantity = int(quantity * self.execution_config.partialFillRatio)
        if quantity <= 0 or self.execution_config.missedLimitFillRate >= 1.0 or not _limit_touched(candidate, fill_bar):
            return SessionBacktestExecutionResult(status="NO_FILL", side=candidate.side, requestedQuantity=candidate.desiredQuantity, reasonCodes=("session.backtest.limit_not_filled",))
        entry = _next_executable_price(candidate, fill_bar, self.execution_config)
        exit_result = _exit_from_bars(candidate, bars, entry, self.execution_config)
        gross = None if exit_result["exitPrice"] is None else _gross_pnl(candidate.side, entry, exit_result["exitPrice"], quantity)
        total_cost = (candidate.spreadEstimate + candidate.slippageEstimate + candidate.fees + candidate.marketImpactEstimate + candidate.adverseSelectionBuffer) * quantity
        net = None if gross is None else round(gross - total_cost, 10)
        return SessionBacktestExecutionResult(
            status=exit_result["status"],
            side=candidate.side,
            requestedQuantity=candidate.desiredQuantity,
            filledQuantity=quantity,
            entryPrice=entry,
            exitPrice=exit_result["exitPrice"],
            entryTimestamp=fill_bar["timestamp"],
            exitTimestamp=exit_result["exitTimestamp"],
            grossPnl=gross,
            netPnl=net,
            reasonCodes=exit_result["reasonCodes"],
        )

    def _candidate_for(self, classification: SessionClassification, profile) -> SessionCandidateDecision | None:
        if profile.block_new_entries or classification.block_new_entries:
            return None
        if classification.direction_bias not in {"long", "short"}:
            return None
        side = Signal.BUY if classification.direction_bias == "long" else Signal.SELL
        price = _latest_close(classification)
        if price is None:
            return None
        edge = max(profile.minimum_net_expected_edge + 0.05, 0.06)
        return build_session_candidate_decision(
            classification=classification,
            profile=profile,
            originating_strategy_candidate_id=f"session-backtest-{classification.evidence.get('classificationId')}",
            side=side,
            order_type="limit",
            desired_quantity=10,
            entry_price=price,
            permitted_entry_price_range=(round(price - 0.05, 4), round(price + 0.05, 4)),
            expected_gross_edge=edge,
            spread_estimate=self.execution_config.spreadCost,
            slippage_estimate=self.execution_config.slippage,
            fees=self.execution_config.fees,
            market_impact_estimate=self.execution_config.marketImpact,
            adverse_selection_buffer=self.execution_config.adverseSelectionBuffer,
            fill_probability=self.execution_config.fillProbability,
            quantity_cap=10,
            stop_price=round(price - 0.50, 4) if side == Signal.BUY else round(price + 0.50, 4),
            target_price=round(price + 0.75, 4) if side == Signal.BUY else round(price - 0.75, 4),
            planned_risk_dollars=5.0,
            feature_ready_latency_ms=self.execution_config.decisionLatencyMs,
            inference_classification_latency_ms=0.0,
        )


def run_session_event_stream(events: Iterable[dict[str, Any]], *, mode: SessionRuntimeMode, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> tuple[SessionRuntimeDecisionSnapshot, ...]:
    return SessionBacktestEngine(config=config).run(events, mode=mode)


def run_session_backtest(events: Iterable[dict[str, Any]], *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> tuple[SessionRuntimeDecisionSnapshot, ...]:
    return run_session_event_stream(events, mode="backtest", config=config)


def _snapshot(
    *,
    mode: str,
    classification: SessionClassification,
    transition_state: SessionTransitionState,
    profile: dict[str, Any],
    route_permissions: dict[str, Any],
    gate: dict[str, Any] | None,
    output_mode: str,
) -> SessionRuntimeDecisionSnapshot:
    payload = {
        "classification": classification.model_dump(mode="json"),
        "transition": transition_state.as_dict(),
        "profile": profile,
        "routePermissions": route_permissions,
        "gate": gate,
        "blockNewEntries": classification.block_new_entries,
    }
    return SessionRuntimeDecisionSnapshot(
        mode=mode,
        symbol=classification.symbol,
        timestamp=classification.decision_time,
        classificationId=str(classification.evidence.get("classificationId")),
        classification=payload["classification"],
        transitionState=payload["transition"],
        transitionReason=transition_state.transition_reason,
        profile=profile,
        routePermissions=route_permissions,
        blockNewEntries=classification.block_new_entries,
        orderGate=gate,
        outputMode=output_mode,
        decisionHash=_hash_json(payload),
    )


def _mode_event(raw_event: dict[str, Any], mode: str) -> dict[str, Any]:
    return {**raw_event, "runtime_mode": "session_authoritative"}


def _output_mode(mode: str) -> str:
    if mode == "paper_affecting":
        return "paper_affecting"
    if mode == "paper_shadow":
        return "shadow"
    return "display_only" if mode == "direct_replay" else "shadow"


def _allow_response(candidate: SessionCandidateDecision, evaluated_at: datetime) -> GlobalGateResponse:
    return GlobalGateResponse(
        action="ALLOW",
        maximumAllowedQuantity=candidate.quantityCap,
        maximumAdditionalRiskDollars=candidate.plannedRiskDollars,
        rejectionReasons=(),
        evaluatedAt=evaluated_at,
        configurationHash="session-backtest-global-allow",
    )


def _quote_age_seconds(classification: SessionClassification) -> float | None:
    liquidity = classification.evidence.get("liquidityEvidence") or {}
    age = liquidity.get("quoteAgeSeconds")
    if age is None:
        age_ms = liquidity.get("quoteAgeMs")
        return None if age_ms is None else float(age_ms) / 1000.0
    return float(age)


def _latest_close(classification: SessionClassification) -> float | None:
    close = classification.evidence.get("latestClose")
    return None if close is None else float(close)


def _bar_payload(raw: dict[str, Any]) -> dict[str, Any]:
    timestamp = raw.get("timestamp")
    if isinstance(timestamp, str):
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    else:
        parsed = timestamp
    return {
        "timestamp": _require_utc(parsed),
        "open": float(raw["open"]),
        "high": float(raw["high"]),
        "low": float(raw["low"]),
        "close": float(raw["close"]),
    }


def _limit_touched(candidate: SessionCandidateDecision, bar: dict[str, Any]) -> bool:
    return bool(bar["low"] <= candidate.entryPrice <= bar["high"])


def _next_executable_price(candidate: SessionCandidateDecision, bar: dict[str, Any], config: SessionBacktestExecutionConfig) -> float:
    if candidate.side == Signal.BUY:
        return round(min(max(candidate.entryPrice + config.slippage, bar["low"]), bar["high"]), 10)
    return round(max(min(candidate.entryPrice - config.slippage, bar["high"]), bar["low"]), 10)


def _exit_from_bars(candidate: SessionCandidateDecision, bars: tuple[dict[str, Any], ...], entry: float, config: SessionBacktestExecutionConfig) -> dict[str, Any]:
    stop = candidate.stopPrice
    target = candidate.targetPrice
    for bar in bars:
        if candidate.side == Signal.BUY:
            stop_hit = stop is not None and bar["low"] <= stop
            target_hit = target is not None and bar["high"] >= target
            if stop_hit and target_hit and config.conservativeAmbiguousSameBar:
                return {"status": "AMBIGUOUS_STOP", "exitPrice": stop, "exitTimestamp": bar["timestamp"], "reasonCodes": ("session.backtest.ambiguous_same_bar_stop_first",)}
            if stop_hit:
                return {"status": "STOP", "exitPrice": stop, "exitTimestamp": bar["timestamp"], "reasonCodes": ("session.backtest.long_stop_hit",)}
            if target_hit:
                return {"status": "TARGET", "exitPrice": target, "exitTimestamp": bar["timestamp"], "reasonCodes": ("session.backtest.long_target_hit",)}
        else:
            stop_hit = stop is not None and bar["high"] >= stop
            target_hit = target is not None and bar["low"] <= target
            if stop_hit and target_hit and config.conservativeAmbiguousSameBar:
                return {"status": "AMBIGUOUS_STOP", "exitPrice": stop, "exitTimestamp": bar["timestamp"], "reasonCodes": ("session.backtest.ambiguous_same_bar_stop_first",)}
            if stop_hit:
                return {"status": "STOP", "exitPrice": stop, "exitTimestamp": bar["timestamp"], "reasonCodes": ("session.backtest.short_stop_hit",)}
            if target_hit:
                return {"status": "TARGET", "exitPrice": target, "exitTimestamp": bar["timestamp"], "reasonCodes": ("session.backtest.short_target_hit",)}
    if config.endOfDayFlatten:
        last = bars[-1]
        return {"status": "EOD_FLATTEN", "exitPrice": last["close"], "exitTimestamp": last["timestamp"], "reasonCodes": ("session.backtest.end_of_day_flatten",)}
    return {"status": "FILLED", "exitPrice": None, "exitTimestamp": None, "reasonCodes": ("session.backtest.position_open",)}


def _gross_pnl(side: Signal | str, entry: float, exit_price: float, quantity: int) -> float:
    if str(side) == Signal.BUY.value:
        return round((exit_price - entry) * quantity, 10)
    return round((entry - exit_price) * quantity, 10)


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "SESSION_RUNTIME_PARITY_VERSION",
    "SessionBacktestEngine",
    "SessionBacktestExecutionConfig",
    "SessionBacktestExecutionResult",
    "SessionRuntimeMode",
    "run_session_backtest",
    "run_session_event_stream",
]

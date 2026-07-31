"""Typed Regime runtime state for pure state-in/state-out decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.app.algorithms.regime.contracts import RegimeHysteresisState
from backend.app.algorithms.regime.exchange_calendar import exchange_session


REGIME_RUNTIME_STATE_SCHEMA_VERSION = "regime_runtime_state_v1"


@dataclass(frozen=True)
class RegimeRuntimeState:
    algorithm_id: str
    algorithm_instance_id: str
    account_id: str
    runtime_mode: str
    symbol: str
    schema_version: str
    confirmed_regime: str
    previous_confirmed_regime: str | None
    candidate_regime: str | None
    candidate_start_timestamp: str | None
    candidate_confirmation_count: int
    regime_confidence: float
    regime_start_timestamp: str
    last_transition_timestamp: str
    regime_dwell_bars: int
    transition_reason: str
    unknown_data_count: int
    last_processed_bar_timestamp: str | None
    last_decision_id: str | None
    cooldown_until: str | None = None
    cooldown_state: dict[str, Any] = field(default_factory=dict)
    open_position_summary: dict[str, Any] = field(default_factory=dict)
    daily_counters: dict[str, Any] = field(default_factory=dict)
    strategy_cooldowns: dict[str, Any] = field(default_factory=dict)
    family_cooldowns: dict[str, Any] = field(default_factory=dict)
    circuit_breaker_state: dict[str, Any] = field(default_factory=dict)
    sequence_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "algorithmInstanceId": self.algorithm_instance_id,
            "accountId": self.account_id,
            "runtimeMode": self.runtime_mode,
            "symbol": self.symbol,
            "schemaVersion": self.schema_version,
            "confirmedRegime": self.confirmed_regime,
            "previousConfirmedRegime": self.previous_confirmed_regime,
            "candidateRegime": self.candidate_regime,
            "candidateStartTimestamp": self.candidate_start_timestamp,
            "candidateConfirmationCount": self.candidate_confirmation_count,
            "regimeConfidence": self.regime_confidence,
            "regimeStartTimestamp": self.regime_start_timestamp,
            "regimeStartedAt": self.regime_start_timestamp,
            "lastTransitionTimestamp": self.last_transition_timestamp,
            "regimeDwellBars": self.regime_dwell_bars,
            "barsInCurrentRegime": self.regime_dwell_bars,
            "transitionReason": self.transition_reason,
            "unknownDataCount": self.unknown_data_count,
            "unknownBarCount": self.unknown_data_count,
            "lastProcessedBarTimestamp": self.last_processed_bar_timestamp,
            "lastDecisionId": self.last_decision_id,
            "cooldownUntil": self.cooldown_until,
            "cooldownState": dict(self.cooldown_state),
            "openPositionSummary": dict(self.open_position_summary),
            "dailyCounters": dict(self.daily_counters),
            "strategyCooldowns": dict(self.strategy_cooldowns),
            "familyCooldowns": dict(self.family_cooldowns),
            "circuitBreakerState": dict(self.circuit_breaker_state),
            "sequenceVersion": self.sequence_version,
            "stateVersion": self.sequence_version,
        }


def initial_regime_runtime_state(identity: dict[str, Any], *, timestamp: str) -> RegimeRuntimeState:
    return RegimeRuntimeState(
        algorithm_id="regime",
        algorithm_instance_id=str(identity.get("algorithmInstanceId") or "regime-default"),
        account_id=str(identity.get("accountId") or "default"),
        runtime_mode=str(identity.get("runtimeMode") or "shadow"),
        symbol=str(identity.get("symbol") or "SPY").upper(),
        schema_version=REGIME_RUNTIME_STATE_SCHEMA_VERSION,
        confirmed_regime="unknown",
        previous_confirmed_regime=None,
        candidate_regime=None,
        candidate_start_timestamp=None,
        candidate_confirmation_count=0,
        regime_confidence=0.0,
        regime_start_timestamp=timestamp,
        last_transition_timestamp=timestamp,
        regime_dwell_bars=0,
        transition_reason="initial_runtime_state",
        unknown_data_count=0,
        last_processed_bar_timestamp=None,
        last_decision_id=None,
        cooldown_until=None,
        cooldown_state={"remainingBars": 0, "reason": None},
        open_position_summary={},
        daily_counters={"decisionCount": 0, "orderProposalCount": 0, "tradeCount": 0, "lossCount": 0},
        strategy_cooldowns={},
        family_cooldowns={},
        circuit_breaker_state={"tripped": False, "reason": None},
        sequence_version=0,
    )


def migrate_regime_runtime_state(payload: dict[str, Any] | None, identity: dict[str, Any], *, timestamp: str) -> RegimeRuntimeState:
    if not payload:
        return initial_regime_runtime_state(identity, timestamp=timestamp)
    schema_version = str(payload.get("schemaVersion") or payload.get("schema_version") or "")
    if schema_version == REGIME_RUNTIME_STATE_SCHEMA_VERSION:
        return runtime_state_from_dict(payload, identity=identity, timestamp=timestamp)
    hysteresis = payload.get("hysteresisState") if isinstance(payload.get("hysteresisState"), dict) else payload
    return RegimeRuntimeState(
        algorithm_id="regime",
        algorithm_instance_id=str(payload.get("algorithmInstanceId") or identity.get("algorithmInstanceId") or "regime-default"),
        account_id=str(payload.get("accountId") or identity.get("accountId") or "default"),
        runtime_mode=str(payload.get("runtimeMode") or identity.get("runtimeMode") or "shadow"),
        symbol=str(payload.get("symbol") or identity.get("symbol") or "SPY").upper(),
        schema_version=REGIME_RUNTIME_STATE_SCHEMA_VERSION,
        confirmed_regime=str(hysteresis.get("confirmedRegime") or hysteresis.get("confirmed_regime") or "unknown"),
        previous_confirmed_regime=_optional_str(hysteresis.get("previousRegime") or hysteresis.get("previous_regime") or hysteresis.get("previousConfirmedRegime")),
        candidate_regime=_optional_str(hysteresis.get("candidateRegime") or hysteresis.get("candidate_regime")),
        candidate_start_timestamp=_optional_str(hysteresis.get("candidateStartTimestamp") or hysteresis.get("candidate_start_time") or hysteresis.get("candidateStartTime")),
        candidate_confirmation_count=max(0, int(hysteresis.get("candidateConfirmationCount") or hysteresis.get("candidate_confirmation_count") or 0)),
        regime_confidence=_float(hysteresis.get("regimeConfidence") or hysteresis.get("regime_confidence") or hysteresis.get("transitionConfidence") or hysteresis.get("transition_confidence")),
        regime_start_timestamp=str(hysteresis.get("regimeStartTime") or hysteresis.get("regime_start_time") or hysteresis.get("regimeStartTimestamp") or hysteresis.get("regimeStartedAt") or timestamp),
        last_transition_timestamp=str(hysteresis.get("lastTransitionTimestamp") or hysteresis.get("last_transition_time") or hysteresis.get("regimeStartTime") or hysteresis.get("regime_start_time") or timestamp),
        regime_dwell_bars=max(0, int(payload.get("regimeDwellBars") or payload.get("regime_dwell_bars") or 0)),
        transition_reason=str(hysteresis.get("transitionReason") or hysteresis.get("transition_reason") or payload.get("transitionReason") or "migrated_runtime_state"),
        unknown_data_count=max(0, int(payload.get("unknownDataCount") or payload.get("unknownBarCount") or payload.get("unknown_data_count") or 0)),
        last_processed_bar_timestamp=_optional_str(payload.get("lastProcessedBarTimestamp") or payload.get("last_processed_bar_timestamp")),
        last_decision_id=_optional_str(payload.get("lastDecisionId") or payload.get("last_decision_id")),
        cooldown_until=_optional_str(payload.get("cooldownUntil") or payload.get("cooldown_until")),
        cooldown_state=_record(payload.get("cooldownState") or payload.get("cooldown_state")) or {"remainingBars": 0, "reason": None},
        open_position_summary=_record(payload.get("openPositionSummary") or payload.get("open_position_summary")),
        daily_counters=_record(payload.get("dailyCounters") or payload.get("daily_counters")) or {"decisionCount": 0, "orderProposalCount": 0, "tradeCount": 0, "lossCount": 0},
        strategy_cooldowns=_record(payload.get("strategyCooldowns") or payload.get("strategy_cooldowns")),
        family_cooldowns=_record(payload.get("familyCooldowns") or payload.get("family_cooldowns")),
        circuit_breaker_state=_record(payload.get("circuitBreakerState") or payload.get("circuit_breaker_state")) or {"tripped": False, "reason": None},
        sequence_version=max(0, int(payload.get("sequenceVersion") or payload.get("stateVersion") or payload.get("sequence_version") or 0)),
    )


def runtime_state_from_dict(payload: dict[str, Any], *, identity: dict[str, Any], timestamp: str) -> RegimeRuntimeState:
    return RegimeRuntimeState(
        algorithm_id="regime",
        algorithm_instance_id=str(payload.get("algorithmInstanceId") or identity.get("algorithmInstanceId") or "regime-default"),
        account_id=str(payload.get("accountId") or identity.get("accountId") or "default"),
        runtime_mode=str(payload.get("runtimeMode") or identity.get("runtimeMode") or "shadow"),
        symbol=str(payload.get("symbol") or identity.get("symbol") or "SPY").upper(),
        schema_version=REGIME_RUNTIME_STATE_SCHEMA_VERSION,
        confirmed_regime=str(payload.get("confirmedRegime") or "unknown"),
        previous_confirmed_regime=_optional_str(payload.get("previousConfirmedRegime")),
        candidate_regime=_optional_str(payload.get("candidateRegime")),
        candidate_start_timestamp=_optional_str(payload.get("candidateStartTimestamp") or payload.get("candidateStartTime")),
        candidate_confirmation_count=max(0, int(payload.get("candidateConfirmationCount") or 0)),
        regime_confidence=_float(payload.get("regimeConfidence")),
        regime_start_timestamp=str(payload.get("regimeStartTimestamp") or payload.get("regimeStartedAt") or timestamp),
        last_transition_timestamp=str(payload.get("lastTransitionTimestamp") or payload.get("regimeStartTimestamp") or payload.get("regimeStartedAt") or timestamp),
        regime_dwell_bars=max(0, int(payload.get("regimeDwellBars") or 0)),
        transition_reason=str(payload.get("transitionReason") or "restored_runtime_state"),
        unknown_data_count=max(0, int(payload.get("unknownDataCount") or payload.get("unknownBarCount") or 0)),
        last_processed_bar_timestamp=_optional_str(payload.get("lastProcessedBarTimestamp")),
        last_decision_id=_optional_str(payload.get("lastDecisionId")),
        cooldown_until=_optional_str(payload.get("cooldownUntil")),
        cooldown_state=_record(payload.get("cooldownState")) or {"remainingBars": 0, "reason": None},
        open_position_summary=_record(payload.get("openPositionSummary")),
        daily_counters=_record(payload.get("dailyCounters")) or {"decisionCount": 0, "orderProposalCount": 0, "tradeCount": 0, "lossCount": 0},
        strategy_cooldowns=_record(payload.get("strategyCooldowns")),
        family_cooldowns=_record(payload.get("familyCooldowns")),
        circuit_breaker_state=_record(payload.get("circuitBreakerState")) or {"tripped": False, "reason": None},
        sequence_version=max(0, int(payload.get("sequenceVersion") or payload.get("stateVersion") or 0)),
    )


def runtime_state_to_hysteresis(state: RegimeRuntimeState | None) -> RegimeHysteresisState | None:
    if state is None or state.last_processed_bar_timestamp is None:
        return None
    return RegimeHysteresisState(
        confirmed_regime=state.confirmed_regime,
        previous_regime=state.previous_confirmed_regime,
        candidate_regime=state.candidate_regime,
        candidate_confirmation_count=state.candidate_confirmation_count,
        regime_start_time=state.regime_start_timestamp,
        transition_confidence=state.regime_confidence,
        transition_reason=state.transition_reason,
        transition_evidence={"runtimeStateSchemaVersion": state.schema_version, "restoredFromDurableRuntimeState": True},
        candidate_start_time=state.candidate_start_timestamp,
        regime_confidence=state.regime_confidence,
        last_transition_time=state.last_transition_timestamp,
        bars_in_current_regime=state.regime_dwell_bars,
        state_version=state.sequence_version,
    )


def next_regime_runtime_state(
    previous_state: RegimeRuntimeState,
    *,
    identity: dict[str, Any],
    decision_id: str,
    bar_timestamp: str,
    confirmed_regime: str,
    previous_regime: str | None,
    candidate_regime: str | None,
    candidate_start_timestamp: str | None,
    candidate_confirmation_count: int,
    regime_confidence: float,
    regime_start_timestamp: str,
    last_transition_timestamp: str,
    transition_reason: str,
    missing_inputs: tuple[str, ...],
    open_position_summary: dict[str, Any],
    order_proposed: bool,
) -> RegimeRuntimeState:
    same_confirmed = previous_state.confirmed_regime == confirmed_regime
    session_boundary = _session_boundary_crossed(previous_state.last_processed_bar_timestamp, bar_timestamp)
    dwell_bars = previous_state.regime_dwell_bars + 1 if same_confirmed and not session_boundary else 1
    cooldown = {} if session_boundary else dict(previous_state.cooldown_state)
    cooldown["remainingBars"] = max(0, int(cooldown.get("remainingBars") or 0) - 1)
    daily = (
        {"decisionCount": 0, "orderProposalCount": 0, "tradeCount": 0, "lossCount": 0}
        if session_boundary
        else dict(previous_state.daily_counters)
    )
    daily["decisionCount"] = int(daily.get("decisionCount") or 0) + 1
    if order_proposed:
        daily["orderProposalCount"] = int(daily.get("orderProposalCount") or 0) + 1
    return RegimeRuntimeState(
        algorithm_id="regime",
        algorithm_instance_id=str(identity.get("algorithmInstanceId") or previous_state.algorithm_instance_id),
        account_id=str(identity.get("accountId") or previous_state.account_id),
        runtime_mode=str(identity.get("runtimeMode") or previous_state.runtime_mode),
        symbol=str(identity.get("symbol") or previous_state.symbol).upper(),
        schema_version=REGIME_RUNTIME_STATE_SCHEMA_VERSION,
        confirmed_regime=confirmed_regime,
        previous_confirmed_regime=previous_regime,
        candidate_regime=candidate_regime,
        candidate_start_timestamp=candidate_start_timestamp,
        candidate_confirmation_count=max(0, int(candidate_confirmation_count)),
        regime_confidence=float(regime_confidence),
        regime_start_timestamp=regime_start_timestamp,
        last_transition_timestamp=last_transition_timestamp,
        regime_dwell_bars=dwell_bars,
        transition_reason=transition_reason,
        unknown_data_count=previous_state.unknown_data_count + (1 if missing_inputs else 0),
        last_processed_bar_timestamp=bar_timestamp,
        last_decision_id=decision_id,
        cooldown_until=_optional_str(cooldown.get("until") or cooldown.get("cooldownUntil")),
        cooldown_state=cooldown,
        open_position_summary=dict(open_position_summary),
        daily_counters=daily,
        strategy_cooldowns={} if session_boundary else dict(previous_state.strategy_cooldowns),
        family_cooldowns={} if session_boundary else dict(previous_state.family_cooldowns),
        circuit_breaker_state=dict(previous_state.circuit_breaker_state),
        sequence_version=previous_state.sequence_version + 1,
    )


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _session_boundary_crossed(previous_timestamp: str | None, current_timestamp: str) -> bool:
    if not previous_timestamp:
        return False
    previous_session = exchange_session(previous_timestamp).session_date
    current_session = exchange_session(current_timestamp).session_date
    return bool(previous_session and current_session and previous_session != current_session)


__all__ = [
    "REGIME_RUNTIME_STATE_SCHEMA_VERSION",
    "RegimeRuntimeState",
    "initial_regime_runtime_state",
    "migrate_regime_runtime_state",
    "next_regime_runtime_state",
    "runtime_state_from_dict",
    "runtime_state_to_hysteresis",
]

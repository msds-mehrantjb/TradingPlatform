"""Stateful transition manager for Session classifications."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.models import DataQualityState, EventRiskState, LiquidityState, SessionBehavior, SessionClassification, SessionPhase, VolatilityState


TransitionDecision = Literal["accepted", "rejected", "candidate"]

SESSION_TRANSITION_INITIALIZED = "SESSION_TRANSITION_INITIALIZED"
SESSION_TRANSITION_CANDIDATE_STARTED = "SESSION_TRANSITION_CANDIDATE_STARTED"
SESSION_TRANSITION_CANDIDATE_CONFIRMED = "SESSION_TRANSITION_CANDIDATE_CONFIRMED"
SESSION_TRANSITION_CANDIDATE_NOT_CONFIRMED = "SESSION_TRANSITION_CANDIDATE_NOT_CONFIRMED"
SESSION_TRANSITION_CONFIDENCE_TOO_LOW = "SESSION_TRANSITION_CONFIDENCE_TOO_LOW"
SESSION_TRANSITION_CONFIDENCE_MARGIN_TOO_SMALL = "SESSION_TRANSITION_CONFIDENCE_MARGIN_TOO_SMALL"
SESSION_TRANSITION_MIN_DWELL_NOT_MET = "SESSION_TRANSITION_MIN_DWELL_NOT_MET"
SESSION_TRANSITION_EMERGENCY_ACCEPTED = "SESSION_TRANSITION_EMERGENCY_ACCEPTED"
SESSION_TRANSITION_RECOVERY_PENDING = "SESSION_TRANSITION_RECOVERY_PENDING"
SESSION_TRANSITION_RECOVERY_ACCEPTED = "SESSION_TRANSITION_RECOVERY_ACCEPTED"
SESSION_TRANSITION_OSCILLATION_GUARD = "SESSION_TRANSITION_OSCILLATION_GUARD"


EMERGENCY_BEHAVIORS = frozenset({SessionBehavior.EVENT_DRIVEN, SessionBehavior.LIQUIDITY_STRESS})
OSCILLATION_PAIRS = frozenset(
    {
        frozenset({SessionBehavior.BALANCED_RANGE, SessionBehavior.MEAN_REVERTING}),
        frozenset({SessionBehavior.TREND_UP, SessionBehavior.BREAKOUT_UP}),
        frozenset({SessionBehavior.TREND_DOWN, SessionBehavior.BREAKOUT_DOWN}),
        frozenset({SessionBehavior.EXPANSION, SessionBehavior.BALANCED_RANGE}),
        frozenset({SessionBehavior.CHOPPY, SessionBehavior.BALANCED_RANGE}),
    }
)


@dataclass(frozen=True)
class SessionTransitionRecord:
    timestamp: datetime
    decision: TransitionDecision
    from_behavior: SessionBehavior | None
    to_behavior: SessionBehavior
    candidate_behavior: SessionBehavior | None
    consecutive_confirmation_count: int
    reason_codes: tuple[str, ...]
    transition_reason: str
    classification_hash: str

    def as_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "decision": self.decision,
            "fromBehavior": self.from_behavior.value if self.from_behavior else None,
            "toBehavior": self.to_behavior.value,
            "candidateBehavior": self.candidate_behavior.value if self.candidate_behavior else None,
            "consecutiveConfirmationCount": self.consecutive_confirmation_count,
            "reasonCodes": self.reason_codes,
            "transitionReason": self.transition_reason,
            "classificationHash": self.classification_hash,
        }


@dataclass(frozen=True)
class SessionTransitionState:
    current_classification: SessionClassification | None = None
    candidate_behavior: SessionBehavior | None = None
    candidate_start_timestamp: datetime | None = None
    consecutive_confirmation_count: int = 0
    current_state_start_timestamp: datetime | None = None
    last_transition_timestamp: datetime | None = None
    transition_reason: str | None = None
    transition_history: tuple[SessionTransitionRecord, ...] = ()
    config_version: str = DEFAULT_SESSION_CONFIG.config_version

    def as_dict(self) -> dict[str, object]:
        return {
            "currentClassificationHash": self.current_classification.deterministic_hash() if self.current_classification else None,
            "currentBehavior": self.current_classification.behavior.value if self.current_classification else None,
            "candidateBehavior": self.candidate_behavior.value if self.candidate_behavior else None,
            "candidateStartTimestamp": self.candidate_start_timestamp.isoformat() if self.candidate_start_timestamp else None,
            "consecutiveConfirmationCount": self.consecutive_confirmation_count,
            "currentStateStartTimestamp": self.current_state_start_timestamp.isoformat() if self.current_state_start_timestamp else None,
            "lastTransitionTimestamp": self.last_transition_timestamp.isoformat() if self.last_transition_timestamp else None,
            "transitionReason": self.transition_reason,
            "transitionHistory": [record.as_dict() for record in self.transition_history],
            "configVersion": self.config_version,
        }


class SessionTransitionManager:
    """Consumes finalized Session classifications and stabilizes behavior routing."""

    def __init__(self, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> None:
        self.config = config

    def process(
        self,
        classification: SessionClassification,
        state: SessionTransitionState | None = None,
    ) -> SessionTransitionState:
        state = state or SessionTransitionState(config_version=self.config.config_version)
        timestamp = classification.decision_time
        if state.current_classification is None:
            return self._accept(
                state,
                classification,
                reason_codes=(SESSION_TRANSITION_INITIALIZED,),
                reason=SESSION_TRANSITION_INITIALIZED,
                decision="accepted",
                timestamp=timestamp,
            )

        current = state.current_classification
        if classification.behavior == current.behavior:
            return replace(
                state,
                current_classification=classification,
                candidate_behavior=None,
                candidate_start_timestamp=None,
                consecutive_confirmation_count=0,
                transition_reason="SESSION_TRANSITION_STATE_REFRESHED",
            )

        if _is_emergency(classification):
            return self._accept(
                state,
                classification,
                reason_codes=tuple(dict.fromkeys((*classification.reason_codes, SESSION_TRANSITION_EMERGENCY_ACCEPTED))),
                reason=SESSION_TRANSITION_EMERGENCY_ACCEPTED,
                decision="accepted",
                timestamp=timestamp,
            )

        if _is_emergency(current) and not _recovery_ready(classification):
            return self._reject_candidate(state, classification, SESSION_TRANSITION_RECOVERY_PENDING, timestamp, preserve_candidate=False)

        required_confirmations = self._required_confirmations(current.behavior, classification.behavior, recovering=_is_emergency(current))
        candidate_count = state.consecutive_confirmation_count + 1 if state.candidate_behavior == classification.behavior else 1
        candidate_start = state.candidate_start_timestamp if state.candidate_behavior == classification.behavior else timestamp

        if classification.overall_confidence < self._minimum_confidence(recovering=_is_emergency(current)):
            return self._candidate_state(state, classification, candidate_count, candidate_start, SESSION_TRANSITION_CONFIDENCE_TOO_LOW)

        if not _is_emergency(current) and not _confidence_margin_ok(current, classification, self.config):
            return self._candidate_state(state, classification, candidate_count, candidate_start, SESSION_TRANSITION_CONFIDENCE_MARGIN_TOO_SMALL)

        if not _minimum_dwell_met(state, classification, self.config) and not _is_emergency(current):
            return self._candidate_state(state, classification, candidate_count, candidate_start, SESSION_TRANSITION_MIN_DWELL_NOT_MET)

        if _is_emergency(current) and candidate_count < required_confirmations:
            return self._candidate_state(state, classification, candidate_count, candidate_start, SESSION_TRANSITION_RECOVERY_PENDING)

        if _is_oscillation_pair(current.behavior, classification.behavior) and candidate_count < self.config.transition_oscillation_confirmation_bars:
            return self._candidate_state(state, classification, candidate_count, candidate_start, SESSION_TRANSITION_OSCILLATION_GUARD)

        if candidate_count < required_confirmations:
            return self._candidate_state(state, classification, candidate_count, candidate_start, SESSION_TRANSITION_CANDIDATE_NOT_CONFIRMED)

        reason = SESSION_TRANSITION_RECOVERY_ACCEPTED if _is_emergency(current) else SESSION_TRANSITION_CANDIDATE_CONFIRMED
        return self._accept(
            state,
            classification,
            reason_codes=tuple(dict.fromkeys((*classification.reason_codes, reason))),
            reason=reason,
            decision="accepted",
            timestamp=timestamp,
            candidate_count=candidate_count,
        )

    def _required_confirmations(self, current: SessionBehavior, candidate: SessionBehavior, *, recovering: bool) -> int:
        if recovering:
            return self.config.transition_recovery_confirmation_bars
        if _is_oscillation_pair(current, candidate):
            return max(self.config.transition_confirmation_bars, self.config.transition_oscillation_confirmation_bars)
        return self.config.transition_confirmation_bars

    def _minimum_confidence(self, *, recovering: bool) -> float:
        if recovering:
            return self.config.transition_recovery_min_confidence
        return self.config.transition_min_candidate_confidence

    def _candidate_state(
        self,
        state: SessionTransitionState,
        classification: SessionClassification,
        count: int,
        candidate_start: datetime,
        reason: str,
    ) -> SessionTransitionState:
        record = self._record(state, classification, "candidate", reason, count)
        return replace(
            state,
            candidate_behavior=classification.behavior,
            candidate_start_timestamp=candidate_start,
            consecutive_confirmation_count=count,
            transition_reason=reason,
            transition_history=self._append_history(state.transition_history, record),
        )

    def _reject_candidate(
        self,
        state: SessionTransitionState,
        classification: SessionClassification,
        reason: str,
        timestamp: datetime,
        *,
        preserve_candidate: bool,
    ) -> SessionTransitionState:
        record = self._record(state, classification, "rejected", reason, state.consecutive_confirmation_count)
        return replace(
            state,
            candidate_behavior=state.candidate_behavior if preserve_candidate else None,
            candidate_start_timestamp=state.candidate_start_timestamp if preserve_candidate else None,
            consecutive_confirmation_count=state.consecutive_confirmation_count if preserve_candidate else 0,
            transition_reason=reason,
            last_transition_timestamp=state.last_transition_timestamp or timestamp,
            transition_history=self._append_history(state.transition_history, record),
        )

    def _accept(
        self,
        state: SessionTransitionState,
        classification: SessionClassification,
        *,
        reason_codes: tuple[str, ...],
        reason: str,
        decision: TransitionDecision,
        timestamp: datetime,
        candidate_count: int = 0,
    ) -> SessionTransitionState:
        record = SessionTransitionRecord(
            timestamp=timestamp,
            decision=decision,
            from_behavior=state.current_classification.behavior if state.current_classification else None,
            to_behavior=classification.behavior,
            candidate_behavior=classification.behavior,
            consecutive_confirmation_count=candidate_count,
            reason_codes=reason_codes,
            transition_reason=reason,
            classification_hash=classification.deterministic_hash(),
        )
        return SessionTransitionState(
            current_classification=classification,
            candidate_behavior=None,
            candidate_start_timestamp=None,
            consecutive_confirmation_count=0,
            current_state_start_timestamp=timestamp,
            last_transition_timestamp=timestamp,
            transition_reason=reason,
            transition_history=self._append_history(state.transition_history, record),
            config_version=self.config.config_version,
        )

    def _record(
        self,
        state: SessionTransitionState,
        classification: SessionClassification,
        decision: TransitionDecision,
        reason: str,
        count: int,
    ) -> SessionTransitionRecord:
        return SessionTransitionRecord(
            timestamp=classification.decision_time,
            decision=decision,
            from_behavior=state.current_classification.behavior if state.current_classification else None,
            to_behavior=classification.behavior,
            candidate_behavior=classification.behavior,
            consecutive_confirmation_count=count,
            reason_codes=tuple(dict.fromkeys((*classification.reason_codes, reason))),
            transition_reason=reason,
            classification_hash=classification.deterministic_hash(),
        )

    def _append_history(self, history: tuple[SessionTransitionRecord, ...], record: SessionTransitionRecord) -> tuple[SessionTransitionRecord, ...]:
        return (*history, record)[-self.config.transition_history_limit :]


def _is_emergency(classification: SessionClassification) -> bool:
    return bool(
        classification.data_quality_state in {DataQualityState.STALE, DataQualityState.INVALID}
        or classification.liquidity_state in {LiquidityState.STRESSED, LiquidityState.STALE}
        or classification.event_risk_state == EventRiskState.BLACKOUT
        or classification.volatility_state == VolatilityState.EXTREME
        or classification.phase in {SessionPhase.CLOSING_AUCTION, SessionPhase.CLOSED}
        or classification.behavior in EMERGENCY_BEHAVIORS
        or any(code in classification.reason_codes for code in {"SESSION_MARKET_HALT", "SESSION_EVENT_BLACKOUT"})
    )


def _recovery_ready(classification: SessionClassification) -> bool:
    return bool(
        classification.data_quality_state == DataQualityState.READY
        and classification.liquidity_state in {LiquidityState.HEALTHY, LiquidityState.CONSTRAINED}
        and classification.event_risk_state in {EventRiskState.CLEAR, EventRiskState.ELEVATED}
        and classification.volatility_state != VolatilityState.EXTREME
        and classification.phase not in {SessionPhase.CLOSING_AUCTION, SessionPhase.CLOSED}
        and classification.behavior not in EMERGENCY_BEHAVIORS
        and not classification.block_new_entries
    )


def _confidence_margin_ok(current: SessionClassification, candidate: SessionClassification, config: SessionConfig) -> bool:
    return candidate.overall_confidence >= current.overall_confidence + config.transition_min_confidence_improvement


def _minimum_dwell_met(state: SessionTransitionState, classification: SessionClassification, config: SessionConfig) -> bool:
    if state.current_state_start_timestamp is None:
        return True
    dwell_seconds = (classification.decision_time - state.current_state_start_timestamp).total_seconds()
    return dwell_seconds >= config.transition_min_dwell_seconds


def _is_oscillation_pair(current: SessionBehavior, candidate: SessionBehavior) -> bool:
    return frozenset({current, candidate}) in OSCILLATION_PAIRS

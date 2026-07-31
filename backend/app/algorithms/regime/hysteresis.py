"""Backend-owned Regime hysteresis state."""

from __future__ import annotations

from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeHysteresisState
from backend.app.algorithms.regime.exchange_calendar import exchange_session


RISK_OFF_REGIMES = {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}
NO_TRADE_SAFETY_REGIMES = {*RISK_OFF_REGIMES, "unknown"}


def confirm_regime_transition(
    classification: RegimeClassification,
    previous: RegimeHysteresisState | None = None,
    settings: dict | None = None,
) -> RegimeHysteresisState:
    config = validate_regime_settings(settings)
    candidate = classification.raw_regime
    confirmation_bars = int(config["confirmationBars"])
    exit_confirmation_bars = max(1, int(config.get("exitConfirmationBars") or confirmation_bars - 1))
    minimum_dwell_bars = max(0, int(config.get("minimumDwellBars") or 0))
    reason_codes = _classification_reason_codes(classification)
    if previous is None:
        return RegimeHysteresisState(
            confirmed_regime=candidate,
            previous_regime=None,
            candidate_regime=None,
            candidate_confirmation_count=1,
            regime_start_time=classification.timestamp,
            transition_confidence=classification.confidence,
            transition_reason="initial_confirmation",
            candidate_start_time=None,
            regime_confidence=classification.confidence,
            last_transition_time=classification.timestamp,
            bars_in_current_regime=1,
            state_version=1,
            transition_evidence={
                "rawRegime": candidate,
                "priorRegime": None,
                "transitionCandidate": None,
                "candidateStartTimestamp": None,
                "confirmationCount": 1,
                "enterThresholdBars": confirmation_bars,
                "exitThresholdBars": exit_confirmation_bars,
                "minimumDwellBars": minimum_dwell_bars,
                "barsInCurrentRegime": 1,
                "requiredConfirmationBars": 1,
                "lastTransitionTimestamp": classification.timestamp,
                "stateVersion": 1,
                "reasonCodes": reason_codes,
            },
        )
    session_boundary_reset = _session_boundary_crossed(previous.regime_start_time, classification.timestamp)
    previous_bars = 0 if session_boundary_reset else max(0, int(getattr(previous, "bars_in_current_regime", 0) or 0))
    next_bars_in_current_regime = previous_bars + 1
    next_state_version = max(0, int(getattr(previous, "state_version", 0) or 0)) + 1
    previous_last_transition = getattr(previous, "last_transition_time", None) or previous.regime_start_time
    if candidate == previous.confirmed_regime:
        return RegimeHysteresisState(
            confirmed_regime=previous.confirmed_regime,
            previous_regime=previous.previous_regime,
            candidate_regime=None,
            candidate_confirmation_count=0,
            regime_start_time=classification.timestamp if session_boundary_reset else previous.regime_start_time,
            transition_confidence=classification.confidence,
            transition_reason="confirmed_regime_held_session_reset" if session_boundary_reset else "confirmed_regime_held",
            candidate_start_time=None,
            regime_confidence=classification.confidence,
            last_transition_time=classification.timestamp if session_boundary_reset else previous_last_transition,
            bars_in_current_regime=next_bars_in_current_regime,
            state_version=next_state_version,
            transition_evidence={
                "rawRegime": candidate,
                "priorRegime": previous.confirmed_regime,
                "transitionCandidate": None,
                "candidateStartTimestamp": None,
                "confirmationCount": 0,
                "enterThresholdBars": confirmation_bars,
                "exitThresholdBars": exit_confirmation_bars,
                "minimumDwellBars": minimum_dwell_bars,
                "barsInCurrentRegime": next_bars_in_current_regime,
                "requiredConfirmationBars": 0,
                "sessionBoundaryReset": session_boundary_reset,
                "lastTransitionTimestamp": classification.timestamp if session_boundary_reset else previous_last_transition,
                "stateVersion": next_state_version,
                "reasonCodes": reason_codes,
            },
        )
    immediate = candidate in NO_TRADE_SAFETY_REGIMES
    required_confirmation_bars = _required_confirmation_bars(candidate, previous.confirmed_regime, confirmation_bars, exit_confirmation_bars)
    candidate_start_time = (
        getattr(previous, "candidate_start_time", None)
        if previous.candidate_regime == candidate and not session_boundary_reset
        else classification.timestamp
    )
    count = previous.candidate_confirmation_count + 1 if previous.candidate_regime == candidate and not session_boundary_reset else 1
    minimum_dwell_satisfied = previous.confirmed_regime in NO_TRADE_SAFETY_REGIMES or next_bars_in_current_regime >= minimum_dwell_bars
    can_confirm = immediate or (count >= required_confirmation_bars and minimum_dwell_satisfied)
    if can_confirm:
        return RegimeHysteresisState(
            confirmed_regime=candidate,
            previous_regime=previous.confirmed_regime,
            candidate_regime=None,
            candidate_confirmation_count=count,
            regime_start_time=classification.timestamp,
            transition_confidence=classification.confidence,
            transition_reason=(
                "risk_off_immediate"
                if candidate in RISK_OFF_REGIMES
                else "safety_regime_immediate"
                if candidate in NO_TRADE_SAFETY_REGIMES
                else "candidate_confirmed"
            ),
            candidate_start_time=None,
            regime_confidence=classification.confidence,
            last_transition_time=classification.timestamp,
            bars_in_current_regime=1,
            state_version=next_state_version,
            transition_evidence={
                "rawRegime": candidate,
                "priorRegime": previous.confirmed_regime,
                "transitionCandidate": candidate,
                "candidateStartTimestamp": candidate_start_time,
                "confirmationCount": count,
                "enterThresholdBars": confirmation_bars,
                "exitThresholdBars": exit_confirmation_bars,
                "minimumDwellBars": minimum_dwell_bars,
                "minimumDwellSatisfied": minimum_dwell_satisfied,
                "barsInCurrentRegime": 1,
                "requiredConfirmationBars": required_confirmation_bars,
                "sessionBoundaryReset": session_boundary_reset,
                "immediate": immediate,
                "lastTransitionTimestamp": classification.timestamp,
                "stateVersion": next_state_version,
                "reasonCodes": reason_codes,
            },
        )
    waiting_reason = "candidate_waiting_minimum_dwell" if count >= required_confirmation_bars and not minimum_dwell_satisfied else "candidate_waiting"
    return RegimeHysteresisState(
        confirmed_regime=previous.confirmed_regime,
        previous_regime=previous.previous_regime,
        candidate_regime=candidate,
        candidate_confirmation_count=count,
        regime_start_time=previous.regime_start_time,
        transition_confidence=classification.confidence,
        transition_reason=waiting_reason,
        candidate_start_time=candidate_start_time,
        regime_confidence=getattr(previous, "regime_confidence", None) or previous.transition_confidence,
        last_transition_time=previous_last_transition,
        bars_in_current_regime=next_bars_in_current_regime,
        state_version=next_state_version,
        transition_evidence={
            "rawRegime": candidate,
            "priorRegime": previous.confirmed_regime,
            "transitionCandidate": candidate,
            "candidateStartTimestamp": candidate_start_time,
            "confirmationCount": count,
            "enterThresholdBars": confirmation_bars,
            "exitThresholdBars": exit_confirmation_bars,
            "minimumDwellBars": minimum_dwell_bars,
            "minimumDwellSatisfied": minimum_dwell_satisfied,
            "barsInCurrentRegime": next_bars_in_current_regime,
            "requiredConfirmationBars": required_confirmation_bars,
            "sessionBoundaryReset": session_boundary_reset,
            "lastTransitionTimestamp": previous_last_transition,
            "stateVersion": next_state_version,
            "reasonCodes": reason_codes,
        },
    )


def _required_confirmation_bars(candidate: str, confirmed: str, enter_bars: int, exit_bars: int) -> int:
    if candidate in NO_TRADE_SAFETY_REGIMES:
        return 1
    if confirmed in NO_TRADE_SAFETY_REGIMES and candidate not in NO_TRADE_SAFETY_REGIMES:
        return enter_bars
    return enter_bars


def _classification_reason_codes(classification: RegimeClassification) -> tuple[str, ...]:
    codes: list[str] = []
    codes.extend(str(item) for item in classification.no_trade_reasons)
    codes.extend(str(item) for item in classification.missing_inputs)
    evidence = classification.evidence or {}
    for key in (
        "directionEvidence",
        "trendStrengthEvidence",
        "volatilityEvidence",
        "structureEvidence",
        "liquidityEvidence",
        "eventEvidence",
        "dataQualityEvidence",
        "warmupEvidence",
    ):
        value = evidence.get(key)
        if isinstance(value, dict):
            codes.extend(str(item) for item in value.get("reasonCodes") or ())
    return tuple(dict.fromkeys(codes))


def _session_boundary_crossed(previous_timestamp: str, current_timestamp: str) -> bool:
    previous_session = exchange_session(previous_timestamp).session_date
    current_session = exchange_session(current_timestamp).session_date
    return bool(previous_session and current_session and previous_session != current_session)

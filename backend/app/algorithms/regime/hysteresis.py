"""Backend-owned Regime hysteresis state."""

from __future__ import annotations

from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeHysteresisState


RISK_OFF_REGIMES = {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}


def confirm_regime_transition(
    classification: RegimeClassification,
    previous: RegimeHysteresisState | None = None,
    settings: dict | None = None,
) -> RegimeHysteresisState:
    config = validate_regime_settings(settings)
    candidate = classification.raw_regime
    if previous is None:
        return RegimeHysteresisState(
            confirmed_regime=candidate,
            previous_regime=None,
            candidate_regime=None,
            candidate_confirmation_count=1,
            regime_start_time=classification.timestamp,
            transition_confidence=classification.confidence,
            transition_reason="initial_confirmation",
            transition_evidence={"rawRegime": candidate},
        )
    if candidate == previous.confirmed_regime:
        return RegimeHysteresisState(
            confirmed_regime=previous.confirmed_regime,
            previous_regime=previous.previous_regime,
            candidate_regime=None,
            candidate_confirmation_count=0,
            regime_start_time=previous.regime_start_time,
            transition_confidence=classification.confidence,
            transition_reason="confirmed_regime_held",
            transition_evidence={"rawRegime": candidate},
        )
    immediate = candidate in RISK_OFF_REGIMES or classification.confidence >= float(config["immediateConfidenceThreshold"])
    count = previous.candidate_confirmation_count + 1 if previous.candidate_regime == candidate else 1
    if immediate or count >= int(config["confirmationBars"]):
        return RegimeHysteresisState(
            confirmed_regime=candidate,
            previous_regime=previous.confirmed_regime,
            candidate_regime=None,
            candidate_confirmation_count=count,
            regime_start_time=classification.timestamp,
            transition_confidence=classification.confidence,
            transition_reason="risk_off_immediate" if candidate in RISK_OFF_REGIMES else "candidate_confirmed",
            transition_evidence={"rawRegime": candidate, "confirmationCount": count},
        )
    return RegimeHysteresisState(
        confirmed_regime=previous.confirmed_regime,
        previous_regime=previous.previous_regime,
        candidate_regime=candidate,
        candidate_confirmation_count=count,
        regime_start_time=previous.regime_start_time,
        transition_confidence=classification.confidence,
        transition_reason="candidate_waiting",
        transition_evidence={"rawRegime": candidate, "confirmationCount": count},
    )


"""Backend-owned Regime order intent creation."""

from __future__ import annotations

from hashlib import sha256

from backend.app.algorithms.regime.contracts import REGIME_ALGORITHM_ID, REGIME_ALGORITHM_VERSION, REGIME_SETTINGS_VERSION, RegimeDecision, RegimeOrderIntent, RegimeSizingResult


def build_regime_order_intent(decision: RegimeDecision, sizing: RegimeSizingResult) -> RegimeOrderIntent | None:
    if decision.signal == "Hold" or sizing.quantity <= 0:
        return None
    side = decision.signal
    position_effect = "enter_long" if side == "Buy" else "enter_short"
    key = f"{decision.decision_id}:{decision.symbol}:{side}:{sizing.quantity}:{decision.confirmed_state.confirmed_regime}"
    return RegimeOrderIntent(
        algorithm_id=REGIME_ALGORITHM_ID,
        algorithm_version=REGIME_ALGORITHM_VERSION,
        settings_version=REGIME_SETTINGS_VERSION,
        decision_id=decision.decision_id,
        order_intent_id="regime-intent-" + sha256(key.encode("utf-8")).hexdigest()[:16],
        symbol=decision.symbol,
        side=side,
        position_effect=position_effect,
        quantity=sizing.quantity,
        entry_price=decision.raw_classification.evidence["close"],
        stop_price=sizing.stop_price,
        target_price=sizing.target_price,
        risk_dollars=sizing.risk_dollars,
        regime=decision.confirmed_state.confirmed_regime,
        confidence=decision.confidence,
    )


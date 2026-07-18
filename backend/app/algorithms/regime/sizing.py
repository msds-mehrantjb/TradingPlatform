"""Backend-owned Regime position sizing."""

from __future__ import annotations

from backend.app.algorithms.regime.contracts import RegimeDecision, RegimeMarketSnapshot, RegimeSizingResult


def calculate_regime_position_size(decision: RegimeDecision, snapshot: RegimeMarketSnapshot, account: dict | None = None) -> RegimeSizingResult:
    if decision.signal == "Hold" or not decision.trade_allowed:
        return RegimeSizingResult(0, 0.0, 0.0, None, None, "blocked", (), tuple(decision.trade_blockers))
    profile = decision.effective_settings
    latest_price = snapshot.latest.close
    buying_power = float((account or {}).get("availableBuyingPower") or profile["startingCapital"])
    remaining_risk = float((account or {}).get("remainingAlgorithmRiskDollars") or profile["startingCapital"])
    atr_value = decision.raw_classification.features.get("atr") or max(0.01, latest_price * float(profile["minimumStopDistancePercent"]) / 100)
    stop_distance = max(float(atr_value) * float(profile["atrStopMultiplier"]), latest_price * float(profile["minimumStopDistancePercent"]) / 100)
    risk_dollars = min(
        float(profile["startingCapital"]) * float(profile["baseRiskPercent"]) / 100,
        remaining_risk,
    )
    risk_quantity = int(risk_dollars / max(stop_distance, 0.01))
    allocation_quantity = int((buying_power * float(profile["maxPositionPercent"]) / 100) / max(latest_price, 0.01))
    liquidity_quantity = int(max(0, snapshot.latest.volume) * float(profile["maxParticipationPercent"]))
    share_limit = int(profile.get("maxAllowedShares") or 0)
    caps = [
        {"label": "risk", "quantity": risk_quantity},
        {"label": "allocation", "quantity": allocation_quantity},
        {"label": "liquidity", "quantity": liquidity_quantity},
    ]
    if share_limit > 0:
        caps.append({"label": "share_limit", "quantity": share_limit})
    final = max(0, min(cap["quantity"] for cap in caps))
    limiting = min(caps, key=lambda cap: cap["quantity"])["label"]
    if decision.signal == "Buy":
        stop_price = latest_price - stop_distance
        target_price = latest_price + (stop_distance * float(profile["takeProfitR"]))
    else:
        stop_price = latest_price + stop_distance
        target_price = latest_price - (stop_distance * float(profile["takeProfitR"]))
    return RegimeSizingResult(final, risk_dollars, stop_distance, stop_price, target_price, limiting, tuple(caps), ())


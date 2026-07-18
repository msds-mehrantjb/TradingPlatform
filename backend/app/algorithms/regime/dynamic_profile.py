"""Backend-owned bounded Regime dynamic profile."""

from __future__ import annotations

from copy import deepcopy


def resolve_effective_regime_profile(settings: dict, confirmed_regime: str) -> dict:
    effective = deepcopy(settings)
    reasons: list[str] = []
    if confirmed_regime in {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}:
        effective["baseRiskPercent"] = 0.0
        effective["maxPositionPercent"] = 0.0
        reasons.append("regime.profile.no_entry_risk_off")
    elif confirmed_regime in {"high_volatility_trend", "intraday_expansion"}:
        effective["baseRiskPercent"] = min(float(settings["baseRiskPercent"]), 0.15)
        effective["maxPositionPercent"] = min(float(settings["maxPositionPercent"]), 25.0)
        effective["atrStopMultiplier"] = max(float(settings["atrStopMultiplier"]), 2.5)
        reasons.append("regime.profile.high_volatility_defensive_reduction")
    elif confirmed_regime == "low_volatility_quiet":
        effective["baseRiskPercent"] = min(float(settings["baseRiskPercent"]), 0.12)
        reasons.append("regime.profile.quiet_market_reduction")
    effective["profileId"] = f"{confirmed_regime}:regime_profile_matrix_v2_backend"
    effective["profileReasons"] = reasons
    return effective


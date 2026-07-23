"""Backend-owned bounded Regime dynamic profile."""

from __future__ import annotations

from copy import deepcopy


PROFILE_VERSION = "regime_profile_matrix_v3_backend"


NO_ENTRY_PROFILE = {
    "noNewEntries": True,
    "preferredStrategyFamilies": (),
    "allowedStrategyFamilies": (),
    "disabledStrategyFamilies": ("trend", "momentum", "breakout", "mean_reversion", "vwap", "reversal", "structure", "event"),
    "minimumWinningScore": 1.0,
    "minimumIndependentFamilies": 99,
    "minimumNetExpectedEdge": 1.0,
    "maxSpreadPercent": 0.0,
    "orderType": "none",
    "entryTimeoutSeconds": 0,
    "stopGeometry": "none",
    "targetGeometry": "none",
    "maximumHoldingMinutes": 0,
    "pyramidingEnabled": False,
    "baseRiskPercent": 0.0,
    "maxPositionPercent": 0.0,
    "maxParticipationPercent": 0.0,
}


REGIME_PROFILE_POLICIES: dict[str, dict] = {
    "strong_uptrend": {
        "preferredStrategyFamilies": ("trend", "momentum", "vwap", "structure"),
        "allowedStrategyFamilies": ("trend", "momentum", "vwap", "breakout", "structure", "event"),
        "entryStyle": "pullback_continuation",
        "orderType": "limit",
        "entryTimeoutSeconds": 90,
        "stopGeometry": "atr_trailing_or_structure_swing",
        "targetGeometry": "runner_with_trailing_exit",
        "maximumHoldingMinutes": 45,
        "trailingExitsEnabled": True,
        "pyramidingEnabled": True,
        "profileReason": "regime.profile.strong_trend_pullback_continuation",
    },
    "strong_downtrend": {
        "preferredStrategyFamilies": ("trend", "momentum", "vwap", "structure"),
        "allowedStrategyFamilies": ("trend", "momentum", "vwap", "breakout", "structure", "event"),
        "entryStyle": "pullback_continuation",
        "orderType": "limit",
        "entryTimeoutSeconds": 90,
        "stopGeometry": "atr_trailing_or_structure_swing",
        "targetGeometry": "runner_with_trailing_exit",
        "maximumHoldingMinutes": 45,
        "trailingExitsEnabled": True,
        "pyramidingEnabled": True,
        "profileReason": "regime.profile.strong_trend_pullback_continuation",
    },
    "weak_uptrend": {
        "preferredStrategyFamilies": ("trend", "vwap", "structure"),
        "allowedStrategyFamilies": ("trend", "momentum", "vwap", "structure"),
        "minimumWinningScore": 0.70,
        "minimumIndependentFamilies": 3,
        "minimumNetExpectedEdge": 0.28,
        "baseRiskPercentCap": 0.12,
        "maxPositionPercentCap": 25.0,
        "orderType": "limit",
        "entryTimeoutSeconds": 60,
        "stopGeometry": "structure_or_atr",
        "targetGeometry": "measured_move",
        "maximumHoldingMinutes": 25,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.weak_trend_stricter_confirmation",
    },
    "weak_downtrend": {
        "preferredStrategyFamilies": ("trend", "vwap", "structure"),
        "allowedStrategyFamilies": ("trend", "momentum", "vwap", "structure"),
        "minimumWinningScore": 0.70,
        "minimumIndependentFamilies": 3,
        "minimumNetExpectedEdge": 0.28,
        "baseRiskPercentCap": 0.12,
        "maxPositionPercentCap": 25.0,
        "orderType": "limit",
        "entryTimeoutSeconds": 60,
        "stopGeometry": "structure_or_atr",
        "targetGeometry": "measured_move",
        "maximumHoldingMinutes": 25,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.weak_trend_stricter_confirmation",
    },
    "range_bound": {
        "preferredStrategyFamilies": ("mean_reversion", "vwap", "reversal", "structure"),
        "allowedStrategyFamilies": ("mean_reversion", "vwap", "reversal", "structure"),
        "disabledStrategyFamilies": ("breakout", "momentum"),
        "minimumWinningScore": 0.68,
        "minimumNetExpectedEdge": 0.24,
        "baseRiskPercentCap": 0.10,
        "maxPositionPercentCap": 20.0,
        "orderType": "limit",
        "entryTimeoutSeconds": 45,
        "stopGeometry": "range_extreme_invalidated",
        "targetGeometry": "opposite_range_or_vwap",
        "takeProfitRCap": 1.10,
        "maximumHoldingMinutes": 18,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.range_mean_reversion_no_breakout_chasing",
    },
    "sideways_range": {
        "aliasOf": "range_bound",
    },
    "opening_breakout": {
        "preferredStrategyFamilies": ("breakout", "momentum", "structure"),
        "allowedStrategyFamilies": ("breakout", "momentum", "trend", "vwap", "structure"),
        "minimumWinningScore": 0.72,
        "minimumIndependentFamilies": 3,
        "minimumNetExpectedEdge": 0.32,
        "maxSpreadPercentCap": 0.0015,
        "maximumSlippageBps": 8.0,
        "orderType": "stop_limit",
        "entryTimeoutSeconds": 30,
        "validityWindowSeconds": 300,
        "stopGeometry": "opening_range_retest_failure",
        "targetGeometry": "opening_range_measured_move",
        "maximumHoldingMinutes": 12,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.opening_breakout_short_validity_strict_execution",
    },
    "intraday_expansion": {
        "preferredStrategyFamilies": ("breakout", "momentum", "trend", "structure"),
        "allowedStrategyFamilies": ("breakout", "momentum", "trend", "vwap", "structure", "event"),
        "minimumWinningScore": 0.72,
        "minimumIndependentFamilies": 3,
        "minimumNetExpectedEdge": 0.35,
        "baseRiskPercentCap": 0.15,
        "maxPositionPercentCap": 25.0,
        "atrStopMultiplierMin": 2.5,
        "orderType": "limit_or_stop_limit",
        "entryTimeoutSeconds": 45,
        "stopGeometry": "wide_atr_with_structure_anchor",
        "targetGeometry": "expansion_continuation",
        "maximumHoldingMinutes": 25,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.intraday_expansion_reduced_size_higher_edge",
    },
    "high_volatility_trend": {
        "preferredStrategyFamilies": ("trend", "momentum", "vwap", "structure"),
        "allowedStrategyFamilies": ("trend", "momentum", "vwap", "breakout", "structure", "event"),
        "minimumWinningScore": 0.70,
        "minimumNetExpectedEdge": 0.32,
        "baseRiskPercentCap": 0.15,
        "maxPositionPercentCap": 25.0,
        "atrStopMultiplierMin": 2.5,
        "orderType": "limit",
        "entryTimeoutSeconds": 45,
        "stopGeometry": "wide_atr_trailing",
        "targetGeometry": "trend_runner",
        "maximumHoldingMinutes": 30,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.high_volatility_defensive_reduction",
    },
    "choppy_mixed": {
        **NO_ENTRY_PROFILE,
        "profileReason": "regime.profile.choppy_mixed_no_trade",
    },
    "event_risk": {
        **NO_ENTRY_PROFILE,
        "eventBlackoutBeforeMinutes": 15,
        "eventBlackoutAfterMinutes": 10,
        "profileReason": "regime.profile.event_blackout",
    },
    "liquidity_stress": {
        **NO_ENTRY_PROFILE,
        "profileReason": "regime.profile.no_entry_liquidity_stress",
    },
    "extreme_volatility_no_trade": {
        **NO_ENTRY_PROFILE,
        "profileReason": "regime.profile.no_entry_extreme_volatility",
    },
    "low_volatility_quiet": {
        "preferredStrategyFamilies": ("mean_reversion", "vwap", "structure"),
        "allowedStrategyFamilies": ("mean_reversion", "vwap", "reversal", "structure"),
        "minimumWinningScore": 0.70,
        "minimumNetExpectedEdge": 0.35,
        "maxExecutionCostToEdgeRatio": 0.20,
        "baseRiskPercentCap": 0.12,
        "maxPositionPercentCap": 20.0,
        "orderType": "limit",
        "entryTimeoutSeconds": 45,
        "stopGeometry": "tight_structure_invalidated",
        "targetGeometry": "small_mean_reversion",
        "takeProfitRCap": 1.15,
        "maximumHoldingMinutes": 15,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.quiet_market_cost_edge_filter",
    },
    "failed_breakout_reversal": {
        "preferredStrategyFamilies": ("reversal", "mean_reversion", "vwap", "structure"),
        "allowedStrategyFamilies": ("reversal", "mean_reversion", "vwap", "structure"),
        "minimumWinningScore": 0.72,
        "minimumIndependentFamilies": 3,
        "minimumNetExpectedEdge": 0.30,
        "baseRiskPercentCap": 0.10,
        "orderType": "limit",
        "entryTimeoutSeconds": 45,
        "stopGeometry": "failed_acceptance_reference_level",
        "targetGeometry": "return_to_value_or_opposite_level",
        "maximumHoldingMinutes": 20,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.failed_breakout_reversal_confirmation",
    },
    "gap_session": {
        "preferredStrategyFamilies": ("event", "trend", "vwap", "structure"),
        "allowedStrategyFamilies": ("event", "trend", "vwap", "breakout", "structure", "mean_reversion"),
        "minimumWinningScore": 0.72,
        "minimumIndependentFamilies": 3,
        "minimumNetExpectedEdge": 0.34,
        "baseRiskPercentCap": 0.10,
        "orderType": "limit",
        "entryTimeoutSeconds": 30,
        "stopGeometry": "gap_extreme_invalidated",
        "targetGeometry": "gap_continuation_or_fade",
        "maximumHoldingMinutes": 18,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.gap_session_strict_confirmation",
    },
}


def resolve_effective_regime_profile(settings: dict, confirmed_regime: str) -> dict:
    effective = deepcopy(settings)
    policy = _policy_for_regime(confirmed_regime)
    reasons = [str(policy.get("profileReason") or "regime.profile.default")]
    _apply_profile_policy(effective, settings, policy)
    effective["profileId"] = f"{confirmed_regime}:{PROFILE_VERSION}"
    effective["profileReasons"] = reasons
    effective["profilePolicy"] = _public_policy(policy)
    return effective


def _policy_for_regime(regime: str) -> dict:
    policy = REGIME_PROFILE_POLICIES.get(regime)
    if policy is None:
        return {
            "preferredStrategyFamilies": (),
            "allowedStrategyFamilies": (),
            "orderType": "limit",
            "entryTimeoutSeconds": 45,
            "stopGeometry": "default_atr",
            "targetGeometry": "default_reward_risk",
            "maximumHoldingMinutes": 20,
            "profileReason": "regime.profile.default_bounded",
        }
    alias = policy.get("aliasOf")
    if alias:
        return REGIME_PROFILE_POLICIES[str(alias)]
    return policy


def _apply_profile_policy(effective: dict, settings: dict, policy: dict) -> None:
    if policy.get("noNewEntries"):
        effective["baseRiskPercent"] = 0.0
        effective["maxPositionPercent"] = 0.0
        effective["maxParticipationPercent"] = 0.0
    else:
        _cap_float(effective, settings, "baseRiskPercent", policy.get("baseRiskPercentCap"))
        _cap_float(effective, settings, "maxPositionPercent", policy.get("maxPositionPercentCap"))
        _cap_float(effective, settings, "maxParticipationPercent", policy.get("maxParticipationPercentCap"))
    _floor_float(effective, settings, "minimumWinningScore", policy.get("minimumWinningScore"))
    _floor_float(effective, settings, "minimumSignalEdge", policy.get("minimumSignalEdge"))
    effective["minimumNetExpectedEdge"] = max(
        float(settings.get("minimumNetExpectedEdge", settings.get("minimumSignalEdge", 0.0))),
        float(policy.get("minimumNetExpectedEdge", settings.get("minimumSignalEdge", 0.0))),
    )
    if policy.get("minimumIndependentFamilies") is not None:
        effective["minimumIndependentFamilies"] = max(int(settings["minimumIndependentFamilies"]), int(policy["minimumIndependentFamilies"]))
    _cap_float(effective, settings, "maxSpreadPercent", policy.get("maxSpreadPercentCap"))
    _floor_float(effective, settings, "atrStopMultiplier", policy.get("atrStopMultiplierMin"))
    _cap_float(effective, settings, "takeProfitR", policy.get("takeProfitRCap"))
    effective["noNewEntries"] = bool(policy.get("noNewEntries", False))
    effective["preferredStrategyFamilies"] = tuple(policy.get("preferredStrategyFamilies", ()))
    effective["allowedStrategyFamilies"] = tuple(policy.get("allowedStrategyFamilies", ()))
    effective["disabledStrategyFamilies"] = tuple(policy.get("disabledStrategyFamilies", ()))
    effective["entryStyle"] = str(policy.get("entryStyle") or "default")
    effective["orderType"] = str(policy.get("orderType") or "limit")
    effective["entryTimeoutSeconds"] = max(0, int(policy.get("entryTimeoutSeconds", 45)))
    effective["validityWindowSeconds"] = max(0, int(policy.get("validityWindowSeconds", 0)))
    effective["maximumHoldingMinutes"] = max(0, int(policy.get("maximumHoldingMinutes", 20)))
    effective["stopGeometry"] = str(policy.get("stopGeometry") or "default_atr")
    effective["targetGeometry"] = str(policy.get("targetGeometry") or "default_reward_risk")
    effective["trailingExitsEnabled"] = bool(policy.get("trailingExitsEnabled", False))
    effective["pyramidingEnabled"] = bool(effective.get("pyramidingEnabled", False) and policy.get("pyramidingEnabled", effective.get("pyramidingEnabled", False)))
    effective["maximumSlippageBps"] = float(policy.get("maximumSlippageBps", effective.get("maximumSlippageBps", 12.0)))
    effective["maxExecutionCostToEdgeRatio"] = float(policy.get("maxExecutionCostToEdgeRatio", effective.get("maxExecutionCostToEdgeRatio", 0.35)))
    if policy.get("eventBlackoutBeforeMinutes") is not None:
        effective["eventBlackoutBeforeMinutes"] = int(policy["eventBlackoutBeforeMinutes"])
    if policy.get("eventBlackoutAfterMinutes") is not None:
        effective["eventBlackoutAfterMinutes"] = int(policy["eventBlackoutAfterMinutes"])


def _cap_float(effective: dict, settings: dict, key: str, cap) -> None:
    if cap is None:
        return
    effective[key] = min(float(settings[key]), float(cap))


def _floor_float(effective: dict, settings: dict, key: str, floor) -> None:
    if floor is None:
        return
    effective[key] = max(float(settings[key]), float(floor))


def _public_policy(policy: dict) -> dict:
    public = {
        key: value
        for key, value in policy.items()
        if key not in {"profileReason", "aliasOf"}
    }
    return deepcopy(public)

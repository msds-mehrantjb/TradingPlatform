"""Backend-owned bounded Regime dynamic profile."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PROFILE_VERSION = "regime_profile_matrix_v3_backend"
PROFILE_FAMILY_SET = ("trend", "momentum", "breakout", "mean_reversion", "vwap", "reversal", "structure", "event")


NO_ENTRY_PROFILE = {
    "noNewEntries": True,
    "preferredStrategyFamilies": (),
    "allowedStrategyFamilies": (),
    "disabledStrategyFamilies": ("trend", "momentum", "breakout", "mean_reversion", "vwap", "reversal", "structure", "event"),
    "minimumWinningScore": 1.0,
    "minimumIndependentFamilies": 99,
    "minimumNetExpectedEdge": 1.0,
    "minimumNetExpectedEdgeBps": 100.0,
    "maximumCostToEdgeRatio": 0.0,
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
    "riskMultiplier": 0.0,
    "maximumPositionMultiplier": 0.0,
    "maximumHoldingBars": 0,
    "cooldownBars": 999,
    "slippageAllowanceBps": 0.0,
    "minimumEdge": 1.0,
    "minimumSignalScore": 1.0,
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
        "maximumHoldingBars": 45,
        "riskMultiplier": 1.0,
        "maximumPositionMultiplier": 1.0,
        "minimumSignalScore": 0.60,
        "minimumEdge": 0.20,
        "stopMultiplier": 2.0,
        "targetMultiple": 1.5,
        "cooldownBars": 5,
        "entryWindowEt": ("09:35", "15:30"),
        "slippageAllowanceBps": 8.0,
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
        "maximumHoldingBars": 45,
        "riskMultiplier": 1.0,
        "maximumPositionMultiplier": 1.0,
        "minimumSignalScore": 0.60,
        "minimumEdge": 0.20,
        "stopMultiplier": 2.0,
        "targetMultiple": 1.5,
        "cooldownBars": 5,
        "entryWindowEt": ("09:35", "15:30"),
        "slippageAllowanceBps": 8.0,
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
        "maximumHoldingBars": 25,
        "riskMultiplier": 0.75,
        "maximumPositionMultiplier": 0.75,
        "minimumSignalScore": 0.70,
        "minimumEdge": 0.24,
        "stopMultiplier": 2.0,
        "targetMultiple": 1.35,
        "cooldownBars": 6,
        "entryWindowEt": ("09:45", "15:15"),
        "slippageAllowanceBps": 6.0,
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
        "maximumHoldingBars": 25,
        "riskMultiplier": 0.75,
        "maximumPositionMultiplier": 0.75,
        "minimumSignalScore": 0.70,
        "minimumEdge": 0.24,
        "stopMultiplier": 2.0,
        "targetMultiple": 1.35,
        "cooldownBars": 6,
        "entryWindowEt": ("09:45", "15:15"),
        "slippageAllowanceBps": 6.0,
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
        "maximumHoldingBars": 18,
        "riskMultiplier": 0.65,
        "maximumPositionMultiplier": 0.65,
        "minimumSignalScore": 0.68,
        "minimumEdge": 0.22,
        "stopMultiplier": 1.7,
        "targetMultiple": 1.10,
        "cooldownBars": 7,
        "entryWindowEt": ("10:00", "15:00"),
        "slippageAllowanceBps": 5.0,
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
        "minimumIndependentFamilies": 2,
        "minimumNetExpectedEdge": 0.32,
        "maxSpreadPercentCap": 0.0015,
        "maximumSlippageBps": 8.0,
        "orderType": "stop_limit",
        "entryTimeoutSeconds": 30,
        "validityWindowSeconds": 300,
        "stopGeometry": "opening_range_retest_failure",
        "targetGeometry": "opening_range_measured_move",
        "maximumHoldingMinutes": 12,
        "maximumHoldingBars": 12,
        "riskMultiplier": 0.70,
        "maximumPositionMultiplier": 0.70,
        "minimumSignalScore": 0.72,
        "minimumEdge": 0.28,
        "stopMultiplier": 2.2,
        "targetMultiple": 1.4,
        "cooldownBars": 8,
        "entryWindowEt": ("09:31", "10:15"),
        "slippageAllowanceBps": 8.0,
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
        "maximumHoldingBars": 25,
        "riskMultiplier": 0.65,
        "maximumPositionMultiplier": 0.70,
        "minimumSignalScore": 0.72,
        "minimumEdge": 0.30,
        "stopMultiplier": 2.5,
        "targetMultiple": 1.45,
        "cooldownBars": 8,
        "entryWindowEt": ("10:00", "15:15"),
        "slippageAllowanceBps": 8.0,
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
        "maximumHoldingBars": 30,
        "riskMultiplier": 0.60,
        "maximumPositionMultiplier": 0.65,
        "minimumSignalScore": 0.70,
        "minimumEdge": 0.28,
        "stopMultiplier": 2.5,
        "targetMultiple": 1.35,
        "cooldownBars": 10,
        "entryWindowEt": ("09:45", "15:00"),
        "slippageAllowanceBps": 8.0,
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
    "unknown": {
        **NO_ENTRY_PROFILE,
        "profileReason": "regime.profile.unknown_no_trade",
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
        "maximumHoldingBars": 15,
        "riskMultiplier": 0.50,
        "maximumPositionMultiplier": 0.55,
        "minimumSignalScore": 0.70,
        "minimumEdge": 0.30,
        "stopMultiplier": 1.6,
        "targetMultiple": 1.15,
        "cooldownBars": 9,
        "entryWindowEt": ("10:00", "14:45"),
        "slippageAllowanceBps": 4.0,
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
        "maximumHoldingBars": 20,
        "riskMultiplier": 0.55,
        "maximumPositionMultiplier": 0.60,
        "minimumSignalScore": 0.72,
        "minimumEdge": 0.28,
        "stopMultiplier": 1.9,
        "targetMultiple": 1.25,
        "cooldownBars": 8,
        "entryWindowEt": ("10:00", "15:00"),
        "slippageAllowanceBps": 6.0,
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
        "maximumHoldingBars": 18,
        "riskMultiplier": 0.50,
        "maximumPositionMultiplier": 0.55,
        "minimumSignalScore": 0.72,
        "minimumEdge": 0.30,
        "stopMultiplier": 2.0,
        "targetMultiple": 1.20,
        "cooldownBars": 10,
        "entryWindowEt": ("09:35", "10:30"),
        "slippageAllowanceBps": 6.0,
        "pyramidingEnabled": False,
        "profileReason": "regime.profile.gap_session_strict_confirmation",
    },
}


def resolve_effective_regime_profile(settings: dict, confirmed_regime: str, classification: Any | None = None, snapshot: Any | None = None) -> dict:
    effective = deepcopy(settings)
    policy = _policy_for_regime(confirmed_regime)
    reasons = [str(policy.get("profileReason") or "regime.profile.default")]
    overlay_chain = ["regime_immutable_baseline"]
    _apply_profile_policy(effective, settings, policy)
    overlay_chain.append("confirmed_regime_profile")
    _apply_configured_regime_overlay(effective, settings, confirmed_regime, reasons)
    for overlay_name, overlay in _dynamic_market_overlays(classification, snapshot):
        _apply_bounded_overlay(effective, settings, overlay, overlay_name, reasons)
        overlay_chain.append(overlay_name)
    effective["profileId"] = f"{confirmed_regime}:{PROFILE_VERSION}"
    effective["baselineSettingsVersion"] = str(settings.get("settingsVersion") or "regime_base_settings")
    effective["baselineProfileVersion"] = str(settings.get("profileVersion") or PROFILE_VERSION)
    effective["profileVersion"] = PROFILE_VERSION
    effective["profileReasons"] = reasons
    effective["overlayReasons"] = tuple(reasons)
    effective["effectiveSettingsOrder"] = tuple(
        (
            *overlay_chain,
            "regime_local_risk_reduction",
            "shared_global_risk_reduction_or_rejection",
        )
    )
    effective["profilePolicy"] = _public_policy(policy)
    effective["finalValues"] = _final_values(effective)
    effective["riskOffPositionManagementAllowed"] = True
    _assert_profile_bounds(settings, effective)
    return effective


def _apply_configured_regime_overlay(effective: dict, settings: dict, confirmed_regime: str, reasons: list[str]) -> None:
    overlays = {}
    dynamic_profiles = settings.get("dynamic_profiles") if isinstance(settings.get("dynamic_profiles"), dict) else settings.get("dynamicProfiles")
    if isinstance(dynamic_profiles, dict):
        overlays = dynamic_profiles.get("overlays") if isinstance(dynamic_profiles.get("overlays"), dict) else {}
    overlay = overlays.get(confirmed_regime)
    if isinstance(overlay, dict) and overlay:
        _apply_bounded_overlay(effective, settings, overlay, "configured_regime_overlay", reasons)


def _dynamic_market_overlays(classification: Any | None, snapshot: Any | None) -> tuple[tuple[str, dict], ...]:
    axes = getattr(classification, "axes", None)
    overlays: list[tuple[str, dict]] = []
    volatility = str(getattr(axes, "volatility", "") or "")
    liquidity = str(getattr(axes, "liquidity", "") or "")
    session = str(getattr(axes, "session", "") or "")
    event_risk = str(getattr(axes, "event_risk", "") or "")
    if volatility in {"high", "extreme"}:
        overlays.append(
            (
                "volatility_overlay",
                {
                    "baseRiskPercentCap": 0.05 if volatility == "extreme" else 0.08,
                    "maxPositionPercentCap": 5.0 if volatility == "extreme" else 8.0,
                    "maximumSlippageBps": 4.0 if volatility == "extreme" else 6.0,
                    "minimumNetExpectedEdge": 0.35 if volatility == "extreme" else 0.28,
                    "minimumNetExpectedEdgeBps": 35.0 if volatility == "extreme" else 28.0,
                    "maximumCostToEdgeRatio": 0.45 if volatility == "extreme" else 0.55,
                    "maximumHoldingBars": 15 if volatility == "extreme" else 25,
                    "reasonCode": f"regime.overlay.volatility.{volatility}",
                },
            )
        )
    if liquidity in {"thin", "poor", "stress"}:
        overlays.append(
            (
                "liquidity_overlay",
                {
                    "maxParticipationPercentCap": 0.005,
                    "maximumSlippageBps": 3.0,
                    "orderType": "limit",
                    "minimumNetExpectedEdge": 0.35,
                    "minimumNetExpectedEdgeBps": 35.0,
                    "maximumCostToEdgeRatio": 0.45,
                    "reasonCode": f"regime.overlay.liquidity.{liquidity}",
                },
            )
        )
    if session in {"open", "close", "unsupported"}:
        overlays.append(
            (
                "session_overlay",
                {
                    "orderTimeToLiveSeconds": 30,
                    "maximumHoldingBars": 12 if session == "close" else 20,
                    "noNewEntries": session == "unsupported",
                    "reasonCode": f"regime.overlay.session.{session}",
                },
            )
        )
    if event_risk in {"blackout", "elevated"}:
        overlays.append(
            (
                "economic_event_overlay",
                {
                    "noNewEntries": event_risk == "blackout",
                    "baseRiskPercentCap": 0.0 if event_risk == "blackout" else 0.04,
                    "maxPositionPercentCap": 0.0 if event_risk == "blackout" else 4.0,
                    "maximumHoldingBars": 0 if event_risk == "blackout" else 10,
                    "reasonCode": f"regime.overlay.economic_event.{event_risk}",
                },
            )
        )
    return tuple(overlays)


def _apply_bounded_overlay(effective: dict, settings: dict, overlay: dict, overlay_name: str, reasons: list[str]) -> None:
    reason = str(overlay.get("reasonCode") or f"regime.overlay.{overlay_name}")
    reasons.append(reason)
    if overlay.get("noNewEntries"):
        effective["noNewEntries"] = True
        effective["baseRiskPercent"] = 0.0
        effective["maxPositionPercent"] = 0.0
        effective["maxParticipationPercent"] = 0.0
    for field, target in (
        ("baseRiskPercentCap", "baseRiskPercent"),
        ("maxPositionPercentCap", "maxPositionPercent"),
        ("maxParticipationPercentCap", "maxParticipationPercent"),
        ("maximumSlippageBps", "maximumSlippageBps"),
        ("maximumCostToEdgeRatio", "maximumCostToEdgeRatio"),
    ):
        if field in overlay:
            effective[target] = min(float(effective.get(target, settings.get(target, 0.0))), float(overlay[field]), float(settings.get(target, overlay[field])))
    if "minimumNetExpectedEdge" in overlay:
        effective["minimumNetExpectedEdge"] = max(float(effective.get("minimumNetExpectedEdge", 0.0)), float(overlay["minimumNetExpectedEdge"]), float(settings.get("minimumNetExpectedEdge", 0.0)))
    if "minimumNetExpectedEdgeBps" in overlay:
        effective["minimumNetExpectedEdgeBps"] = max(float(effective.get("minimumNetExpectedEdgeBps", 0.0)), float(overlay["minimumNetExpectedEdgeBps"]), float(settings.get("minimumNetExpectedEdgeBps", 0.0)))
    if "orderTimeToLiveSeconds" in overlay:
        effective["orderTimeToLiveSeconds"] = min(int(effective.get("orderTimeToLiveSeconds", settings.get("orderTimeToLiveSeconds", 60))), int(overlay["orderTimeToLiveSeconds"]))
    if "maximumHoldingBars" in overlay:
        effective["maximumHoldingBars"] = min(int(effective.get("maximumHoldingBars", settings.get("maxHoldingBars", 1))), int(overlay["maximumHoldingBars"]))
        effective["maxHoldingBars"] = min(int(effective.get("maxHoldingBars", settings.get("maxHoldingBars", 1))), effective["maximumHoldingBars"])
    if "orderType" in overlay:
        effective["orderType"] = str(overlay["orderType"])


def _policy_for_regime(regime: str) -> dict:
    policy = REGIME_PROFILE_POLICIES.get(regime)
    if policy is None:
        return {
            "preferredStrategyFamilies": (),
            "allowedStrategyFamilies": PROFILE_FAMILY_SET,
            "orderType": "limit",
            "entryTimeoutSeconds": 45,
            "stopGeometry": "default_atr",
            "targetGeometry": "default_reward_risk",
            "maximumHoldingMinutes": 20,
            "maximumHoldingBars": 20,
            "riskMultiplier": 1.0,
            "maximumPositionMultiplier": 1.0,
            "minimumSignalScore": 0.60,
            "minimumEdge": 0.20,
            "stopMultiplier": 2.0,
            "targetMultiple": 1.5,
            "cooldownBars": 5,
            "entryWindowEt": ("09:35", "15:30"),
            "slippageAllowanceBps": 8.0,
            "profileReason": "regime.profile.default_bounded",
        }
    alias = policy.get("aliasOf")
    if alias:
        return REGIME_PROFILE_POLICIES[str(alias)]
    return policy


def _apply_profile_policy(effective: dict, settings: dict, policy: dict) -> None:
    risk_multiplier = max(0.0, min(1.0, float(policy.get("riskMultiplier", 1.0))))
    position_multiplier = max(0.0, min(1.0, float(policy.get("maximumPositionMultiplier", 1.0))))
    if policy.get("noNewEntries"):
        effective["baseRiskPercent"] = 0.0
        effective["maxPositionPercent"] = 0.0
        effective["maxParticipationPercent"] = 0.0
    else:
        effective["baseRiskPercent"] = min(float(effective["baseRiskPercent"]), float(settings["baseRiskPercent"]) * risk_multiplier)
        effective["maxPositionPercent"] = min(float(effective["maxPositionPercent"]), float(settings["maxPositionPercent"]) * position_multiplier)
        _cap_float(effective, settings, "baseRiskPercent", policy.get("baseRiskPercentCap"))
        _cap_float(effective, settings, "maxPositionPercent", policy.get("maxPositionPercentCap"))
        _cap_float(effective, settings, "maxParticipationPercent", policy.get("maxParticipationPercentCap"))
    effective["riskMultiplier"] = risk_multiplier
    effective["maximumPositionMultiplier"] = position_multiplier
    _floor_float(effective, settings, "minimumWinningScore", policy.get("minimumSignalScore", policy.get("minimumWinningScore")))
    _floor_float(effective, settings, "minimumSignalEdge", policy.get("minimumEdge", policy.get("minimumSignalEdge")))
    effective["minimumNetExpectedEdge"] = max(
        float(settings.get("minimumNetExpectedEdge", settings.get("minimumSignalEdge", 0.0))),
        float(policy.get("minimumNetExpectedEdge", settings.get("minimumSignalEdge", 0.0))),
    )
    effective["minimumNetExpectedEdgeBps"] = max(
        float(settings.get("minimumNetExpectedEdgeBps", effective["minimumNetExpectedEdge"] * 100.0)),
        float(policy.get("minimumNetExpectedEdgeBps", effective["minimumNetExpectedEdge"] * 100.0)),
    )
    if policy.get("minimumIndependentFamilies") is not None:
        effective["minimumIndependentFamilies"] = max(int(settings["minimumIndependentFamilies"]), int(policy["minimumIndependentFamilies"]))
    _cap_float(effective, settings, "maxSpreadPercent", policy.get("maxSpreadPercentCap"))
    _floor_float(effective, settings, "atrStopMultiplier", policy.get("stopMultiplier", policy.get("atrStopMultiplierMin")))
    _cap_float(effective, settings, "takeProfitR", policy.get("targetMultiple", policy.get("takeProfitRCap")))
    effective["noNewEntries"] = bool(policy.get("noNewEntries", False))
    effective["preferredStrategyFamilies"] = tuple(policy.get("preferredStrategyFamilies", ()))
    effective["allowedStrategyFamilies"] = tuple(policy.get("allowedStrategyFamilies", ()))
    effective["disabledStrategyFamilies"] = tuple(policy.get("disabledStrategyFamilies", ()))
    effective["entryStyle"] = str(policy.get("entryStyle") or "default")
    effective["orderType"] = str(policy.get("orderType") or "limit")
    effective["entryTimeoutSeconds"] = max(0, int(policy.get("entryTimeoutSeconds", 45)))
    effective["validityWindowSeconds"] = max(0, int(policy.get("validityWindowSeconds", 0)))
    effective["maximumHoldingMinutes"] = max(0, int(policy.get("maximumHoldingMinutes", 20)))
    effective["maximumHoldingBars"] = max(0, int(policy.get("maximumHoldingBars", effective.get("maxHoldingBars", 20))))
    effective["maxHoldingBars"] = min(int(settings.get("maxHoldingBars", effective["maximumHoldingBars"])), effective["maximumHoldingBars"]) if effective["maximumHoldingBars"] else 0
    effective["cooldownBars"] = max(int(settings.get("cooldownBars", 0)), int(policy.get("cooldownBars", settings.get("cooldownBars", 0))))
    effective["entryWindowEt"] = tuple(policy.get("entryWindowEt", ("09:35", settings.get("entryCutoffTimeEt", "15:30"))))
    effective["stopGeometry"] = str(policy.get("stopGeometry") or "default_atr")
    effective["targetGeometry"] = str(policy.get("targetGeometry") or "default_reward_risk")
    effective["trailingExitsEnabled"] = bool(policy.get("trailingExitsEnabled", False))
    effective["pyramidingEnabled"] = bool(effective.get("pyramidingEnabled", False) and policy.get("pyramidingEnabled", effective.get("pyramidingEnabled", False)))
    effective["maximumSlippageBps"] = min(float(settings.get("maximumSlippageBps", 0.0)), float(policy.get("slippageAllowanceBps", policy.get("maximumSlippageBps", settings.get("maximumSlippageBps", 0.0)))))
    effective["slippageAllowanceBps"] = effective["maximumSlippageBps"]
    effective["maximumCostToEdgeRatio"] = min(
        float(settings.get("maximumCostToEdgeRatio", 0.0)),
        float(policy.get("maximumCostToEdgeRatio", policy.get("maxExecutionCostToEdgeRatio", settings.get("maximumCostToEdgeRatio", 0.0)))),
    )
    effective["maxExecutionCostToEdgeRatio"] = effective["maximumCostToEdgeRatio"]
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


def _final_values(effective: dict) -> dict:
    keys = (
        "noNewEntries",
        "allowedStrategyFamilies",
        "preferredStrategyFamilies",
        "disabledStrategyFamilies",
        "riskMultiplier",
        "maximumPositionMultiplier",
        "baseRiskPercent",
        "maxPositionPercent",
        "maxParticipationPercent",
        "minimumWinningScore",
        "minimumSignalEdge",
        "minimumNetExpectedEdge",
        "minimumNetExpectedEdgeBps",
        "maximumCostToEdgeRatio",
        "maxExecutionCostToEdgeRatio",
        "atrStopMultiplier",
        "takeProfitR",
        "maximumHoldingBars",
        "cooldownBars",
        "entryWindowEt",
        "orderType",
        "orderTimeToLiveSeconds",
        "maximumSlippageBps",
    )
    return {key: deepcopy(effective.get(key)) for key in keys}


def _assert_profile_bounds(settings: dict, effective: dict) -> None:
    for key in ("baseRiskPercent", "maxPositionPercent", "maxParticipationPercent", "maximumSlippageBps", "maximumCostToEdgeRatio"):
        if float(effective.get(key, 0.0)) > float(settings.get(key, 0.0)):
            raise ValueError(f"Regime dynamic profile exceeded baseline {key}")
    if float(effective.get("minimumNetExpectedEdgeBps", 0.0)) < float(settings.get("minimumNetExpectedEdgeBps", 0.0)):
        raise ValueError("Regime dynamic profile reduced baseline minimumNetExpectedEdgeBps")
    if bool(effective.get("pyramidingEnabled")) and not bool(settings.get("pyramidingEnabled")):
        raise ValueError("Regime dynamic profile cannot enable pyramiding beyond baseline")

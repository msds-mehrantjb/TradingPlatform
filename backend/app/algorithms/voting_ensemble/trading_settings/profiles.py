"""Profile overlays for one-minute Voting Ensemble settings."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any


@dataclass(frozen=True)
class TradingProfileOverlay:
    name: str
    risk_multiplier: float = 1.0
    allocation_multiplier: float = 1.0
    daily_allocation_multiplier: float = 1.0
    max_trades_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    minimum_final_score: float | None = None
    minimum_family_support: int | None = None
    minimum_net_edge_r: float | None = None
    minimum_edge_to_cost_ratio: float | None = None
    maximum_spread_bps: float | None = None
    maximum_spread_dollars: float | None = None
    maximum_slippage_per_share: float | None = None
    stop_multiplier: float = 1.0
    target_multiplier: float = 1.0
    maximum_holding_multiplier: float = 1.0
    limit_offset_bps: float = 0.0
    cancel_replace_timeout_seconds: int | None = None
    cooldown_seconds: int = 0
    block_new_entries: bool = False


BASELINE_TRADING_PROFILE = TradingProfileOverlay("baseline")
TRADING_PROFILE_PRESETS: dict[str, tuple[TradingProfileOverlay, ...]] = {
    "baseline": (BASELINE_TRADING_PROFILE,),
    "reduced": (
        BASELINE_TRADING_PROFILE,
        TradingProfileOverlay("manual.reduced", risk_multiplier=0.70, allocation_multiplier=0.75, max_trades_multiplier=0.75),
    ),
    "defensive": (
        BASELINE_TRADING_PROFILE,
        TradingProfileOverlay(
            "manual.defensive",
            risk_multiplier=0.40,
            allocation_multiplier=0.50,
            daily_allocation_multiplier=0.50,
            max_trades_multiplier=0.50,
            slippage_multiplier=1.5,
        ),
    ),
    "no_new_entries": (
        BASELINE_TRADING_PROFILE,
        TradingProfileOverlay(
            "manual.no_new_entries",
            risk_multiplier=0.0,
            allocation_multiplier=0.0,
            daily_allocation_multiplier=0.0,
            max_trades_multiplier=0.0,
            slippage_multiplier=2.0,
            block_new_entries=True,
        ),
    ),
}


def resolve_dynamic_trading_profile(settings_payload: dict[str, Any]) -> dict[str, Any]:
    settings_dict = settings_payload if isinstance(settings_payload, dict) else {}
    requested_profile = _first_string(settings_dict, ("dynamicTradingProfile", "tradingProfile", "profile", "profileId"))
    overlays = list(TRADING_PROFILE_PRESETS.get((requested_profile or "baseline").lower(), (BASELINE_TRADING_PROFILE,)))
    overlays.extend(_market_profile_overlays(settings_dict))

    risk_multiplier = min(overlay.risk_multiplier for overlay in overlays)
    allocation_multiplier = min(overlay.allocation_multiplier for overlay in overlays)
    daily_allocation_multiplier = min(overlay.daily_allocation_multiplier for overlay in overlays)
    max_trades_multiplier = min(overlay.max_trades_multiplier for overlay in overlays)
    slippage_multiplier = max(overlay.slippage_multiplier for overlay in overlays)
    stop_multiplier = max(overlay.stop_multiplier for overlay in overlays)
    target_multiplier = min(overlay.target_multiplier for overlay in overlays)
    maximum_holding_multiplier = min(overlay.maximum_holding_multiplier for overlay in overlays)
    limit_offset_bps = max(overlay.limit_offset_bps for overlay in overlays)
    cooldown_seconds = max(overlay.cooldown_seconds for overlay in overlays)
    block_new_entries = any(overlay.block_new_entries for overlay in overlays)
    minimum_final_score = max((value for overlay in overlays if (value := overlay.minimum_final_score) is not None), default=None)
    minimum_family_support = max((value for overlay in overlays if (value := overlay.minimum_family_support) is not None), default=None)
    minimum_net_edge_r = max((value for overlay in overlays if (value := overlay.minimum_net_edge_r) is not None), default=None)
    minimum_edge_to_cost_ratio = max((value for overlay in overlays if (value := overlay.minimum_edge_to_cost_ratio) is not None), default=None)
    maximum_spread_bps = min((value for overlay in overlays if (value := overlay.maximum_spread_bps) is not None), default=None)
    maximum_spread_dollars = min((value for overlay in overlays if (value := overlay.maximum_spread_dollars) is not None), default=None)
    maximum_slippage_per_share = min((value for overlay in overlays if (value := overlay.maximum_slippage_per_share) is not None), default=None)
    cancel_replace_timeout_seconds = min((value for overlay in overlays if (value := overlay.cancel_replace_timeout_seconds) is not None), default=None)
    active_overlays = tuple(dict.fromkeys(overlay.name for overlay in overlays))
    profile_id = "baseline" if active_overlays == ("baseline",) else "dynamic-" + "-".join(name.replace(".", "_") for name in active_overlays if name != "baseline")

    return {
        "profileId": profile_id,
        "activeOverlays": active_overlays,
        "riskMultiplier": round(risk_multiplier, 4),
        "allocationMultiplier": round(allocation_multiplier, 4),
        "dailyAllocationMultiplier": round(daily_allocation_multiplier, 4),
        "maxTradesMultiplier": round(max_trades_multiplier, 4),
        "slippageMultiplier": round(slippage_multiplier, 4),
        "estimatedCostMultiplier": round(slippage_multiplier, 4),
        "stopMultiplier": round(stop_multiplier, 4),
        "targetMultiplier": round(target_multiplier, 4),
        "maximumHoldingMultiplier": round(maximum_holding_multiplier, 4),
        "limitOrderOffsetBps": round(limit_offset_bps, 4),
        "cooldownSeconds": cooldown_seconds,
        "minimumFinalScore": minimum_final_score,
        "minimumIndependentFamilySupport": minimum_family_support,
        "minimumNetEdgeR": minimum_net_edge_r,
        "minimumEdgeToCostRatio": minimum_edge_to_cost_ratio,
        "maximumSpreadBps": maximum_spread_bps,
        "maximumSpreadDollars": maximum_spread_dollars,
        "maximumSlippagePerShare": maximum_slippage_per_share,
        "cancelReplaceTimeoutSeconds": cancel_replace_timeout_seconds,
        "blockNewEntries": block_new_entries,
        "reasonCodes": tuple(f"voting_ensemble.trading_profile.{name}" for name in active_overlays),
        "sourceInputs": _source_inputs(settings_dict),
    }


def apply_profile_to_config(config: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    effective = dict(config)
    effective["riskPerTradePercent"] = round(float(effective["riskPerTradePercent"]) * float(profile["riskMultiplier"]), 4)
    effective["orderAllocationPercent"] = round(float(effective["orderAllocationPercent"]) * float(profile["allocationMultiplier"]), 4)
    effective["dailyAllocationPercent"] = round(float(effective["dailyAllocationPercent"]) * float(profile["dailyAllocationMultiplier"]), 4)
    effective["maxTradesPerDay"] = max(0, floor(int(effective["maxTradesPerDay"]) * float(profile["maxTradesMultiplier"])))
    effective["slippagePerShare"] = round(float(effective["slippagePerShare"]) * float(profile["slippageMultiplier"]), 4)
    if profile.get("minimumFinalScore") is not None:
        effective["minVoteEdge"] = max(float(effective["minVoteEdge"]), float(profile["minimumFinalScore"]))
    if profile.get("minimumIndependentFamilySupport") is not None:
        effective["minimumFamiliesForTrade"] = max(int(effective["minimumFamiliesForTrade"]), int(profile["minimumIndependentFamilySupport"]))
    if profile.get("minimumNetEdgeR") is not None:
        effective["minimumNetEdgeR"] = max(float(effective["minimumNetEdgeR"]), float(profile["minimumNetEdgeR"]))
    if profile.get("maximumSpreadBps") is not None:
        effective["maximumSpreadBps"] = min(float(effective["maximumSpreadBps"]), float(profile["maximumSpreadBps"]))
    if profile.get("maximumSpreadDollars") is not None:
        effective["maximumSpreadDollars"] = min(float(effective["maximumSpreadDollars"]), float(profile["maximumSpreadDollars"]))
    if profile.get("maximumSlippagePerShare") is not None:
        effective["maxSlippagePerShare"] = min(float(effective["maxSlippagePerShare"]), float(profile["maximumSlippagePerShare"]))
    effective["fixedStopDistanceDollars"] = round(float(effective["fixedStopDistanceDollars"]) * float(profile["stopMultiplier"]), 4)
    effective["takeProfitR"] = round(float(effective["takeProfitR"]) * float(profile["targetMultiplier"]), 4)
    effective["maximumHoldingMinutes"] = max(1, floor(int(effective["maximumHoldingMinutes"]) * float(profile["maximumHoldingMultiplier"])))
    if profile.get("cancelReplaceTimeoutSeconds") is not None:
        effective["cancelUnfilledAfterSeconds"] = min(int(effective["cancelUnfilledAfterSeconds"]), int(profile["cancelReplaceTimeoutSeconds"]))
    effective["minimumEdgeToCostRatio"] = max(float(effective.get("minimumEdgeToCostRatio", 1.0)), float(profile.get("minimumEdgeToCostRatio") or 1.0))
    effective["limitOrderOffsetBps"] = max(float(effective.get("limitOrderOffsetBps", 0.0)), float(profile["limitOrderOffsetBps"]))
    effective["cooldownSeconds"] = max(int(effective.get("cooldownSeconds", 0)), int(profile["cooldownSeconds"]))
    effective["entriesBlocked"] = bool(profile["blockNewEntries"])
    return effective


def _market_profile_overlays(settings_dict: dict[str, Any]) -> list[TradingProfileOverlay]:
    overlays: list[TradingProfileOverlay] = []
    volatility = _normalized(settings_dict, "volatility", "marketVolatility", "volatilityRegime")
    liquidity = _normalized(settings_dict, "liquidity", "liquidityRegime", "marketLiquidity")
    event_risk = _normalized(settings_dict, "eventRisk", "eventRiskLevel", "newsRisk")
    data_quality = _normalized(settings_dict, "dataQuality", "feedQuality")
    market_regime = _normalized(settings_dict, "marketRegime", "regime")
    spread_bps = _number_value(settings_dict.get("spreadBps") or settings_dict.get("currentSpreadBps"), 0.0)
    expected_cost = _number_value(settings_dict.get("expectedTransactionCostBps") or settings_dict.get("expectedCostBps"), 0.0)
    data_age = _number_value(settings_dict.get("marketDataAgeSeconds") or settings_dict.get("dataAgeSeconds"), 0.0)
    latency = _number_value(settings_dict.get("executionLatencyMs") or settings_dict.get("decisionLatencyMs"), 0.0)
    consecutive_losses = _number_value(settings_dict.get("consecutiveLosses"), 0.0)
    exposure = _number_value(settings_dict.get("currentExposurePercent") or settings_dict.get("exposurePercent"), 0.0)
    family_support = _number_value(settings_dict.get("strategyFamilySupport") or settings_dict.get("familySupport"), 0.0)
    vote_edge = _number_value(settings_dict.get("voteEdge") or settings_dict.get("finalScore"), 0.0)
    time_of_day = _first_string(settings_dict, ("timeOfDay", "sessionTime", "decisionTime")) or ""
    drawdown = _number_value(settings_dict.get("currentDrawdownPercent") or settings_dict.get("drawdownPercent"), 0.0)

    if volatility in {"high", "elevated"}:
        overlays.append(TradingProfileOverlay("volatility.high", risk_multiplier=0.55, allocation_multiplier=0.70, max_trades_multiplier=0.75, slippage_multiplier=1.5, minimum_final_score=0.25, stop_multiplier=1.15, target_multiplier=0.90))
    elif volatility in {"extreme", "halt", "unsafe"}:
        overlays.append(TradingProfileOverlay("volatility.extreme", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, slippage_multiplier=2.0, block_new_entries=True))

    if liquidity in {"thin", "poor", "degraded"}:
        overlays.append(TradingProfileOverlay("liquidity.thin", risk_multiplier=0.60, allocation_multiplier=0.60, max_trades_multiplier=0.75, slippage_multiplier=1.75, maximum_spread_bps=18.0, maximum_slippage_per_share=0.05))
    elif liquidity in {"unsafe", "invalid", "halted"}:
        overlays.append(TradingProfileOverlay("liquidity.unsafe", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, slippage_multiplier=2.0, block_new_entries=True))

    if event_risk in {"elevated", "high"}:
        overlays.append(TradingProfileOverlay("event_risk.elevated", risk_multiplier=0.50, allocation_multiplier=0.60, max_trades_multiplier=0.50, slippage_multiplier=1.5, minimum_final_score=0.30, cooldown_seconds=300))
    elif event_risk in {"blocked", "unsafe"}:
        overlays.append(TradingProfileOverlay("event_risk.blocked", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, block_new_entries=True))

    if data_quality in {"degraded", "partial"}:
        overlays.append(TradingProfileOverlay("data_quality.degraded", risk_multiplier=0.50, allocation_multiplier=0.75, max_trades_multiplier=0.75, minimum_final_score=0.28))
    elif data_quality in {"invalid", "stale", "missing"}:
        overlays.append(TradingProfileOverlay("data_quality.invalid", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, block_new_entries=True))

    if market_regime in {"mixed", "chop", "vwap_chop", "range_bound"}:
        overlays.append(TradingProfileOverlay("regime.choppy", risk_multiplier=0.70, allocation_multiplier=0.80, max_trades_multiplier=0.75, minimum_final_score=0.25))

    if spread_bps >= 20:
        overlays.append(TradingProfileOverlay("spread.wide", risk_multiplier=0.50, allocation_multiplier=0.50, slippage_multiplier=1.75, minimum_net_edge_r=0.05, minimum_edge_to_cost_ratio=2.0, maximum_spread_bps=20.0))
    if expected_cost >= 8:
        overlays.append(TradingProfileOverlay("transaction_cost.elevated", risk_multiplier=0.60, allocation_multiplier=0.70, slippage_multiplier=2.0, minimum_edge_to_cost_ratio=2.5))
    if data_age >= 60:
        overlays.append(TradingProfileOverlay("market_data_age.stale", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, block_new_entries=True))
    elif data_age >= 30:
        overlays.append(TradingProfileOverlay("market_data_age.elevated", risk_multiplier=0.60, allocation_multiplier=0.70, minimum_final_score=0.28))
    if latency >= 2000:
        overlays.append(TradingProfileOverlay("execution_latency.blocked", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, block_new_entries=True))
    elif latency >= 1000:
        overlays.append(TradingProfileOverlay("execution_latency.elevated", risk_multiplier=0.70, allocation_multiplier=0.75, cancel_replace_timeout_seconds=30))
    if time_of_day >= "15:20":
        overlays.append(TradingProfileOverlay("time_of_day.late_session", risk_multiplier=0.50, allocation_multiplier=0.50, max_trades_multiplier=0.50, maximum_holding_multiplier=0.50, block_new_entries=time_of_day >= "15:30"))
    if consecutive_losses >= 3:
        overlays.append(TradingProfileOverlay("loss_streak.cooldown", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, cooldown_seconds=900, block_new_entries=True))
    elif consecutive_losses >= 2:
        overlays.append(TradingProfileOverlay("loss_streak.reduced", risk_multiplier=0.50, allocation_multiplier=0.60, max_trades_multiplier=0.50, cooldown_seconds=300))
    if exposure >= 50:
        overlays.append(TradingProfileOverlay("exposure.blocked", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, block_new_entries=True))
    elif exposure >= 25:
        overlays.append(TradingProfileOverlay("exposure.reduced", risk_multiplier=0.60, allocation_multiplier=0.60))
    if 0 < family_support < 2:
        overlays.append(TradingProfileOverlay("family_support.thin", risk_multiplier=0.70, allocation_multiplier=0.75, minimum_family_support=2))
    if 0 < abs(vote_edge) < 0.25:
        overlays.append(TradingProfileOverlay("vote_edge.thin", risk_multiplier=0.70, allocation_multiplier=0.75, minimum_final_score=0.25, minimum_edge_to_cost_ratio=1.5))

    if drawdown >= 3.0:
        overlays.append(TradingProfileOverlay("drawdown.daily_stop", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, block_new_entries=True))
    elif drawdown >= 2.0:
        overlays.append(TradingProfileOverlay("drawdown.defensive", risk_multiplier=0.35, allocation_multiplier=0.50, daily_allocation_multiplier=0.50, max_trades_multiplier=0.50))
    elif drawdown >= 1.0:
        overlays.append(TradingProfileOverlay("drawdown.reduced", risk_multiplier=0.70, allocation_multiplier=0.75, max_trades_multiplier=0.75))

    return overlays


def _source_inputs(settings_dict: dict[str, Any]) -> dict[str, str | int | float | bool]:
    allowed = (
        "regime", "marketRegime", "volatility", "liquidity", "spreadBps", "expectedTransactionCostBps",
        "eventRisk", "dataQuality", "marketDataAgeSeconds", "executionLatencyMs", "timeOfDay",
        "currentDrawdownPercent", "consecutiveLosses", "currentExposurePercent", "strategyFamilySupport", "voteEdge",
    )
    return {key: value for key in allowed if isinstance((value := settings_dict.get(key)), (str, int, float, bool))}


def _first_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalized(payload: dict[str, Any], *keys: str) -> str:
    value = _first_string(payload, keys)
    return (value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _number_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

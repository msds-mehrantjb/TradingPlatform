from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from math import floor
from typing import Any


VOTING_ENSEMBLE_BASELINE_SETTINGS_VERSION = "voting_ensemble_baseline_settings_v1"
VOTING_ENSEMBLE_TRADING_PROFILE_VERSION = "voting_ensemble_trading_profile_v1"


VOTING_ENSEMBLE_RISK_CONFIG: dict[str, Any] = {
    "startingCapital": 25000.0,
    "riskPerTradePercent": 0.5,
    "maxDailyLossPercent": 2.0,
    "maxTradesPerDay": 3,
    "sessionStart": "09:35",
    "newTradesUntil": "15:30",
    "forceClose": "15:55",
    "execution": "next candle open",
    "stopLossPercent": 0.35,
    "fixedStopDistanceDollars": 1.0,
    "takeProfitR": 1.5,
    "slippagePerShare": 0.02,
    "expenseModel": {
        "description": "Estimated SPY share expenses: adverse slippage is priced into entry/exit, plus extra liquidity reserve and sell-side regulatory fee estimates.",
        "additionalLiquidityCostPerSharePerSide": 0.01,
        "commissionPerSharePerSide": 0.0,
        "secFeeRateOnSellNotional": 0.0000278,
        "finraTafPerSellShare": 0.000166,
        "finraTafMaxPerTrade": 8.30,
    },
    "positionSizing": "shares = risk dollars / stop distance, capped by available capital",
    "entryConfirmationBars": 3,
    "entryConfirmationBarsByTimeframe": {
        "1Min": 3,
        "5Min": 3,
        "1Hour": 2,
    },
    "warmupBarsByTimeframe": {
        "1Min": 50,
        "5Min": 20,
        "1Hour": 2,
        "1Day": 50,
        "1Week": 20,
    },
    "directionalWinnerMinVotesByTimeframe": {
        "1Hour": 2,
        "1Day": 3,
        "1Week": 3,
    },
    "signalFadeExit": "disabled",
    "allowedEntryHoursByTimeframe": {
        "1Min": ["10:00", "11:00"],
        "5Min": ["13:00", "14:00"],
        "1Hour": [],
    },
    "hybridOneHour": {
        "label": "1h filter + 5m execution",
        "directionTimeframe": "1Hour",
        "executionTimeframe": "5Min",
        "blockedDirectionHours": ["12:00", "14:00"],
        "blockedRegimes": ["VWAP Chop"],
        "requireDailyTrendAlignment": True,
        "allowedDailySignals": ["Buy"],
        "takeProfitR": 2.0,
        "atrPeriod": 14,
        "atrMultiplier": 0.75,
        "minDirectionalVotes": 2,
    },
    "swing": {
        "1Day": {
            "label": "Daily swing vote",
            "maxHoldingBars": 5,
            "stopPercent": 1.0,
            "atrPeriod": 14,
            "atrMultiplier": 1.5,
            "takeProfitR": 2.0,
        },
        "1Week": {
            "label": "Weekly swing vote",
            "maxHoldingBars": 8,
            "stopPercent": 2.0,
            "atrPeriod": 10,
            "atrMultiplier": 1.0,
            "takeProfitR": 2.5,
        },
    },
    "openCloseEvents": {
        "label": "Opening/Closing Event Ensemble",
        "weeklyFilter": "approved weekly vote",
        "openingWindow": "09:45-10:30",
        "closingWindow": "15:30-15:50",
        "openingRangeMinutes": 15,
        "closingStart": "15:30",
        "closingEnd": "15:50",
        "openingEnd": "10:30",
        "forceClose": "15:55",
        "takeProfitR": 1.5,
        "stopLossPercent": 0.35,
        "fixedStopDistanceDollars": 1.0,
        "maxTradesPerDay": 2,
        "minOpeningWeeklyDirectionalVotes": 3,
        "minClosingWeeklyDirectionalVotes": 4,
        "enableClosingEvents": True,
        "blockedRegimes": ["Mixed"],
    },
}


@dataclass(frozen=True)
class _TradingProfileOverlay:
    name: str
    risk_multiplier: float = 1.0
    allocation_multiplier: float = 1.0
    daily_allocation_multiplier: float = 1.0
    max_trades_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    block_new_entries: bool = False


BASELINE_TRADING_PROFILE = _TradingProfileOverlay("baseline")
TRADING_PROFILE_PRESETS: dict[str, tuple[_TradingProfileOverlay, ...]] = {
    "baseline": (BASELINE_TRADING_PROFILE,),
    "reduced": (
        BASELINE_TRADING_PROFILE,
        _TradingProfileOverlay("manual.reduced", risk_multiplier=0.70, allocation_multiplier=0.75, max_trades_multiplier=0.75),
    ),
    "defensive": (
        BASELINE_TRADING_PROFILE,
        _TradingProfileOverlay("manual.defensive", risk_multiplier=0.40, allocation_multiplier=0.50, daily_allocation_multiplier=0.50, max_trades_multiplier=0.50, slippage_multiplier=1.5),
    ),
    "no_new_entries": (
        BASELINE_TRADING_PROFILE,
        _TradingProfileOverlay("manual.no_new_entries", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, slippage_multiplier=2.0, block_new_entries=True),
    ),
}


def dynamic_risk_config(settings_payload: dict[str, Any]) -> dict[str, Any]:
    config = deepcopy(VOTING_ENSEMBLE_RISK_CONFIG)
    settings_dict = settings_payload if isinstance(settings_payload, dict) else {}

    def number(name: str, default: float, *, minimum: float, maximum: float) -> float:
        try:
            value = float(settings_dict.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    config["startingCapital"] = number("startingCapital", 25000.0, minimum=1000.0, maximum=10_000_000.0)
    config["orderAllocationPercent"] = number("orderAllocationPercent", 10.0, minimum=0.1, maximum=100.0)
    config["dailyAllocationPercent"] = number("dailyAllocationPercent", 30.0, minimum=0.1, maximum=100.0)
    config["riskBudgetPercentOfOrder"] = number("riskBudgetPercentOfOrder", 50.0, minimum=0.1, maximum=100.0)
    config["riskPerTradePercent"] = number("riskPerTradePercent", 0.5, minimum=0.01, maximum=100.0)
    config["maxDailyLossPercent"] = number("maxDailyLossPercent", 2.0, minimum=0.1, maximum=100.0)
    requested_max_trades = int(number("maxTradesPerDay", 3, minimum=1, maximum=50))
    allocation_trade_cap = max(1, int(config["dailyAllocationPercent"] // max(config["orderAllocationPercent"], 0.1)))
    config["maxTradesPerDay"] = min(requested_max_trades, allocation_trade_cap)
    config["stopLossPercent"] = number("stopLossPercent", 0.35, minimum=0.01, maximum=20.0)
    config["fixedStopDistanceDollars"] = number("fixedStopDistanceDollars", 1.0, minimum=0.0, maximum=100.0)
    config["takeProfitR"] = number("takeProfitR", 1.5, minimum=0.1, maximum=20.0)
    config["slippagePerShare"] = number("slippagePerShare", 0.02, minimum=0.0, maximum=10.0)
    config["positionSizingMode"] = str(settings_dict.get("positionSizingMode") or "allocation")
    config["positionSizing"] = (
        "shares = per-order allocation dollars / entry price, with planned risk checked against order risk budget"
        if config["positionSizingMode"] == "allocation"
        else VOTING_ENSEMBLE_RISK_CONFIG["positionSizing"]
    )
    config.setdefault("openCloseEvents", {})
    config["openCloseEvents"]["maxTradesPerDay"] = min(
        int(config["openCloseEvents"].get("maxTradesPerDay", 2)),
        config["maxTradesPerDay"],
    )
    config["openCloseEvents"].setdefault("fixedStopDistanceDollars", config["fixedStopDistanceDollars"])
    return apply_dynamic_trading_profile(config, settings_dict)


def resolve_dynamic_trading_profile(settings_payload: dict[str, Any]) -> dict[str, Any]:
    settings_dict = settings_payload if isinstance(settings_payload, dict) else {}
    requested_profile = _first_string(
        settings_dict,
        (
            "dynamicTradingProfile",
            "tradingProfile",
            "profile",
            "profileId",
        ),
    )
    overlays = list(TRADING_PROFILE_PRESETS.get((requested_profile or "baseline").lower(), (BASELINE_TRADING_PROFILE,)))
    overlays.extend(_market_profile_overlays(settings_dict))

    risk_multiplier = min(overlay.risk_multiplier for overlay in overlays)
    allocation_multiplier = min(overlay.allocation_multiplier for overlay in overlays)
    daily_allocation_multiplier = min(overlay.daily_allocation_multiplier for overlay in overlays)
    max_trades_multiplier = min(overlay.max_trades_multiplier for overlay in overlays)
    slippage_multiplier = max(overlay.slippage_multiplier for overlay in overlays)
    block_new_entries = any(overlay.block_new_entries for overlay in overlays)
    active_overlays = tuple(dict.fromkeys(overlay.name for overlay in overlays))
    profile_id = "baseline" if active_overlays == ("baseline",) else "dynamic-" + "-".join(name.replace(".", "_") for name in active_overlays if name != "baseline")

    return {
        "profileId": profile_id,
        "profileVersion": VOTING_ENSEMBLE_TRADING_PROFILE_VERSION,
        "baselineSettingsVersion": VOTING_ENSEMBLE_BASELINE_SETTINGS_VERSION,
        "activeOverlays": active_overlays,
        "riskMultiplier": round(risk_multiplier, 4),
        "allocationMultiplier": round(allocation_multiplier, 4),
        "dailyAllocationMultiplier": round(daily_allocation_multiplier, 4),
        "maxTradesMultiplier": round(max_trades_multiplier, 4),
        "slippageMultiplier": round(slippage_multiplier, 4),
        "blockNewEntries": block_new_entries,
        "reasonCodes": tuple(f"voting_ensemble.trading_profile.{name}" for name in active_overlays),
    }


def apply_dynamic_trading_profile(config: dict[str, Any], settings_payload: dict[str, Any]) -> dict[str, Any]:
    effective = deepcopy(config)
    profile = resolve_dynamic_trading_profile(settings_payload)

    effective["riskPerTradePercent"] = round(float(effective["riskPerTradePercent"]) * float(profile["riskMultiplier"]), 4)
    effective["orderAllocationPercent"] = round(float(effective["orderAllocationPercent"]) * float(profile["allocationMultiplier"]), 4)
    effective["dailyAllocationPercent"] = round(float(effective["dailyAllocationPercent"]) * float(profile["dailyAllocationMultiplier"]), 4)
    effective["maxTradesPerDay"] = max(0, floor(int(effective["maxTradesPerDay"]) * float(profile["maxTradesMultiplier"])))
    effective["slippagePerShare"] = round(float(effective["slippagePerShare"]) * float(profile["slippageMultiplier"]), 4)
    effective["entriesBlocked"] = bool(profile["blockNewEntries"])
    effective["tradingProfile"] = profile
    effective["settingsVersion"] = VOTING_ENSEMBLE_BASELINE_SETTINGS_VERSION
    effective["profileVersion"] = VOTING_ENSEMBLE_TRADING_PROFILE_VERSION
    effective["configurationHash"] = risk_config_hash(effective)

    effective.setdefault("openCloseEvents", {})
    effective["openCloseEvents"]["maxTradesPerDay"] = min(
        int(effective["openCloseEvents"].get("maxTradesPerDay", 2)),
        effective["maxTradesPerDay"],
    )
    return effective


def risk_config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _market_profile_overlays(settings_dict: dict[str, Any]) -> list[_TradingProfileOverlay]:
    overlays: list[_TradingProfileOverlay] = []
    volatility = _normalized(settings_dict, "volatility", "marketVolatility", "volatilityRegime")
    liquidity = _normalized(settings_dict, "liquidity", "liquidityRegime", "marketLiquidity")
    event_risk = _normalized(settings_dict, "eventRisk", "eventRiskLevel", "newsRisk")
    data_quality = _normalized(settings_dict, "dataQuality", "feedQuality")
    market_regime = _normalized(settings_dict, "marketRegime", "regime")
    drawdown = _number_value(settings_dict.get("currentDrawdownPercent") or settings_dict.get("drawdownPercent"), 0.0)

    if volatility in {"high", "elevated"}:
        overlays.append(_TradingProfileOverlay("volatility.high", risk_multiplier=0.55, allocation_multiplier=0.70, max_trades_multiplier=0.75, slippage_multiplier=1.5))
    elif volatility in {"extreme", "halt", "unsafe"}:
        overlays.append(_TradingProfileOverlay("volatility.extreme", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, slippage_multiplier=2.0, block_new_entries=True))

    if liquidity in {"thin", "poor", "degraded"}:
        overlays.append(_TradingProfileOverlay("liquidity.thin", risk_multiplier=0.60, allocation_multiplier=0.60, max_trades_multiplier=0.75, slippage_multiplier=1.75))
    elif liquidity in {"unsafe", "invalid", "halted"}:
        overlays.append(_TradingProfileOverlay("liquidity.unsafe", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, slippage_multiplier=2.0, block_new_entries=True))

    if event_risk in {"elevated", "high"}:
        overlays.append(_TradingProfileOverlay("event_risk.elevated", risk_multiplier=0.50, allocation_multiplier=0.60, max_trades_multiplier=0.50, slippage_multiplier=1.5))
    elif event_risk in {"blocked", "unsafe"}:
        overlays.append(_TradingProfileOverlay("event_risk.blocked", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, block_new_entries=True))

    if data_quality in {"degraded", "partial"}:
        overlays.append(_TradingProfileOverlay("data_quality.degraded", risk_multiplier=0.50, allocation_multiplier=0.75, max_trades_multiplier=0.75))
    elif data_quality in {"invalid", "stale", "missing"}:
        overlays.append(_TradingProfileOverlay("data_quality.invalid", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, block_new_entries=True))

    if market_regime in {"mixed", "chop", "vwap_chop", "range_bound"}:
        overlays.append(_TradingProfileOverlay("regime.choppy", risk_multiplier=0.70, allocation_multiplier=0.80, max_trades_multiplier=0.75))

    if drawdown >= 3.0:
        overlays.append(_TradingProfileOverlay("drawdown.daily_stop", risk_multiplier=0.0, allocation_multiplier=0.0, daily_allocation_multiplier=0.0, max_trades_multiplier=0.0, block_new_entries=True))
    elif drawdown >= 2.0:
        overlays.append(_TradingProfileOverlay("drawdown.defensive", risk_multiplier=0.35, allocation_multiplier=0.50, daily_allocation_multiplier=0.50, max_trades_multiplier=0.50))
    elif drawdown >= 1.0:
        overlays.append(_TradingProfileOverlay("drawdown.reduced", risk_multiplier=0.70, allocation_multiplier=0.75, max_trades_multiplier=0.75))

    return overlays


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

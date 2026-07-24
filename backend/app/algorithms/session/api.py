"""Authoritative read-only API for the Session subsystem."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SESSION_CONFIG_VERSION, SessionConfig
from backend.app.algorithms.session.models import (
    SESSION_ALGORITHM_ID,
    SESSION_CLASSIFIER_VERSION,
    SESSION_FEATURE_SCHEMA_VERSION,
    DataQualityState,
    EventRiskState,
    LiquidityState,
    SessionBehavior,
    SessionPhase,
    VolatilityState,
)
from backend.app.algorithms.session.persistence import SESSION_PERSISTENCE_ROOT, SessionDecisionJsonlStore, SessionDecisionPersistenceRecord
from backend.app.algorithms.session.profile import baseline_session_profile
from backend.app.algorithms.session.rollout import session_rollout_status


SESSION_API_VERSION = "session_api_v1"
SESSION_SUBSYSTEM_VERSION = "session_subsystem_v1"
SESSION_API_NAMESPACE = "/api/session"
SESSION_API_TAG = "session"
SESSION_API_STORE = SessionDecisionJsonlStore(root=SESSION_PERSISTENCE_ROOT)

router = APIRouter(prefix=SESSION_API_NAMESPACE, tags=[SESSION_API_TAG])


@router.get("/inventory")
def session_inventory() -> dict[str, Any]:
    config = DEFAULT_SESSION_CONFIG
    profile = baseline_session_profile(config=config)
    rollout = session_rollout_status()
    return {
        "apiVersion": SESSION_API_VERSION,
        "subsystemVersion": SESSION_SUBSYSTEM_VERSION,
        "algorithmId": SESSION_ALGORITHM_ID,
        "classifierVersion": SESSION_CLASSIFIER_VERSION,
        "featureSchemaVersion": SESSION_FEATURE_SCHEMA_VERSION,
        "configVersion": SESSION_CONFIG_VERSION,
        "availablePhaseValues": [item.value for item in SessionPhase],
        "availableBehaviorValues": [item.value for item in SessionBehavior],
        "axisValues": {
            "volatility": [item.value for item in VolatilityState],
            "liquidity": [item.value for item in LiquidityState],
            "dataQuality": [item.value for item in DataQualityState],
            "eventRisk": [item.value for item in EventRiskState],
        },
        "moduleStatus": {
            "runtime": "shadow",
            "classifier": "active",
            "transitionManager": "active",
            "profileResolver": "active",
            "costGate": "shadow",
            "orderSubmission": "disabled",
        },
        "dataReadiness": {
            "state": "unknown",
            "minimumBehaviorBars": config.minimum_behavior_bars,
            "requiresFinalizedOneMinuteBars": True,
            "unknownIsNotHealthy": True,
        },
        "currentProfile": profile.as_dict(),
        "supportedIndicators": [
            "exchange_calendar_phase",
            "opening_range_or5_or15_or30",
            "session_vwap",
            "vwap_slope_and_crossing_frequency",
            "time_of_day_normalized_range_percentile",
            "time_of_day_normalized_realized_volatility_percentile",
            "time_of_day_normalized_volume_pace",
            "nbbo_spread_and_quote_age",
            "top_of_book_imbalance",
            "swing_structure_bos_choch",
            "breakout_retest_rejection",
            "pullback_depth_and_duration",
            "event_risk_state",
        ],
        "requiredInputFeeds": [
            {"id": "finalized_1m_bars", "required": True, "description": "Finalized one-minute OHLCV bars only."},
            {"id": "nbbo_quote", "required": True, "description": "Best bid/ask and quote timestamp for liquidity state."},
            {"id": "exchange_calendar", "required": True, "description": "NYSE/Arca calendar, holidays, early closes, and DST-aware phase boundaries."},
            {"id": "scheduled_event_risk", "required": False, "description": "CPI, FOMC, jobs, Fed events, halt/LULD state when available."},
            {"id": "historical_minute_baselines", "required": False, "description": "Same-minute volatility and volume percentile baselines."},
        ],
        "orderAffectingStatus": {
            "enabled": rollout["control"]["order_authority"]["order_affecting"],
            "status": rollout["control"]["effective_stage"],
            "reasonCodes": rollout["control"]["reason_codes"],
            "authority": rollout["control"]["order_authority"],
        },
        "rollout": rollout,
        "frontendAuthority": {
            "typescriptClassificationAllowed": False,
            "displayOnly": True,
            "reasonCodes": ("session.api.backend_authoritative", "session.api.frontend_display_cannot_change_trading_behavior"),
        },
    }


@router.get("/current")
def session_current(
    symbol: str = Query(default="SPY", min_length=1),
    session_date: str | None = Query(default=None),
) -> dict[str, Any]:
    records = SESSION_API_STORE.read_records(symbol=symbol.upper(), session_date=session_date)
    latest = records[-1] if records else None
    return {
        "apiVersion": SESSION_API_VERSION,
        "subsystemVersion": SESSION_SUBSYSTEM_VERSION,
        "symbol": symbol.upper(),
        "status": "ready" if latest else "unavailable",
        "current": _record_payload(latest) if latest else _unavailable_payload(symbol.upper()),
        "orderAffectingStatus": "disabled",
        "reasonCodes": ("session.api.current_from_authoritative_store",) if latest else ("session.api.no_session_decision_record",),
    }


@router.get("/history")
def session_history(
    symbol: str = Query(default="SPY", min_length=1),
    session_date: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1_000),
) -> dict[str, Any]:
    records = SESSION_API_STORE.read_records(symbol=symbol.upper(), session_date=session_date)
    limited = records[-limit:]
    return {
        "apiVersion": SESSION_API_VERSION,
        "subsystemVersion": SESSION_SUBSYSTEM_VERSION,
        "symbol": symbol.upper(),
        "sessionDate": session_date,
        "count": len(limited),
        "records": [_record_payload(record) for record in limited],
        "reasonCodes": ("session.api.history_from_authoritative_store",),
    }


def _record_payload(record: SessionDecisionPersistenceRecord) -> dict[str, Any]:
    payload = record.model_dump(mode="json")
    return {
        **payload,
        "display": {
            "phase": _humanize(record.phase),
            "behavior": _humanize(record.behavior),
            "volatility": _humanize(record.volatilityState),
            "liquidity": _humanize(record.liquidityState),
            "readiness": _humanize(record.dataQualityState),
            "reasonCodes": [_humanize_reason(code) for code in record.reasonCodes],
            "unknownOrStale": record.liquidityState in {"unknown", "stale"} or record.dataQualityState in {"unknown", "stale", "incomplete", "invalid"},
        },
    }


def _unavailable_payload(symbol: str) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "classificationId": "session-unavailable",
        "symbol": symbol,
        "sessionDate": None,
        "decisionTime": now,
        "phase": "unknown",
        "behavior": "unknown",
        "volatilityState": "unknown",
        "liquidityState": "unknown",
        "dataQualityState": "incomplete",
        "eventRiskState": "unknown",
        "directionBias": "cash",
        "overallConfidence": 0.0,
        "reasonCodes": ("session.api.no_session_decision_record",),
        "transitionState": {},
        "strategyPermissions": {"canRouteNewEntries": False, "readOnly": True, "cannotBypassGlobalGates": True},
        "safetyBlocks": {"blockNewEntries": True, "profileBlockNewEntries": True},
        "outputMode": "display_only",
        "orderAffectingStatus": "disabled",
        "display": {
            "phase": "Unknown",
            "behavior": "Unknown",
            "volatility": "Unknown",
            "liquidity": "Unknown",
            "readiness": "Incomplete",
            "reasonCodes": ["No Session decision record"],
            "unknownOrStale": True,
        },
    }


def _humanize(value: str) -> str:
    return value.replace("_", " ").title()


def _humanize_reason(value: str) -> str:
    text = value
    for prefix in ("session.", "SESSION_"):
        text = text.replace(prefix, "")
    return text.replace(".", " ").replace("_", " ").strip().title()


__all__ = [
    "SESSION_API_NAMESPACE",
    "SESSION_API_STORE",
    "SESSION_API_TAG",
    "SESSION_API_VERSION",
    "SESSION_SUBSYSTEM_VERSION",
    "router",
    "session_current",
    "session_history",
    "session_inventory",
]

"""Backend-owned trade-management policy and worker bridge."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.algorithms.regime.contracts import REGIME_ALGORITHM_ID, REGIME_ALGORITHM_VERSION
from backend.app.algorithms.regime.exits import evaluate_regime_exit
from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.algorithms.regime.repository import RegimeRepository


REGIME_TRADE_MANAGEMENT_WORKER_VERSION = "regime_trade_management_worker_v2"


def manage_regime_positions_for_completed_bar(
    *,
    repository: RegimeRepository,
    identity: dict[str, Any],
    candle: dict[str, Any],
    settings_snapshot: dict[str, Any],
    confirmed_regime: str,
    entry_paused: bool = False,
    global_emergency_flatten: bool = False,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate Regime-owned open positions and enqueue paper exit intents."""

    evaluated_at = _as_utc(evaluated_at or datetime.now(UTC))
    manager = RegimePositionManager(repository)
    positions = manager.restore_open_positions(identity)
    exit_intents: list[dict[str, Any]] = []
    held_positions = 0
    duplicate_exit_intents = 0
    blocked_positions = 0
    reason_codes: list[str] = ["regime.trade_management.no_open_positions"] if not positions else []

    for position in positions:
        if str(position.get("algorithmId") or REGIME_ALGORITHM_ID) != REGIME_ALGORITHM_ID:
            blocked_positions += 1
            reason_codes.append("regime.trade_management.cross_algorithm_position_rejected")
            continue
        if not _has_fill_level_regime_attribution(position):
            blocked_positions += 1
            reason_codes.append("regime.trade_management.shared_account_position_rejected")
            continue
        evaluation = manager.evaluate_position(
            identity,
            position,
            candle=candle,
            settings_snapshot=settings_snapshot,
            confirmed_regime=confirmed_regime,
            entry_paused=entry_paused,
            global_emergency_flatten=global_emergency_flatten,
        )
        if evaluation.get("action") != "exit":
            held_positions += 1
            continue
        exit_action = evaluation.get("exitAction")
        if not isinstance(exit_action, dict):
            blocked_positions += 1
            reason_codes.append("regime.trade_management.exit_action_missing")
            continue
        intent = _exit_order_intent(
            identity=identity,
            position=position,
            exit_action=exit_action,
            settings_snapshot=settings_snapshot,
            confirmed_regime=confirmed_regime,
            evaluated_at=evaluated_at,
        )
        risk = _exit_local_risk_result(identity=identity, intent=intent, exit_action=exit_action, evaluated_at=evaluated_at)
        repository.record_local_risk_result(identity, risk)
        inserted = repository.insert_order_intent(intent)
        if inserted.get("inserted"):
            exit_intents.append(intent)
        else:
            duplicate_exit_intents += 1
            if inserted.get("reason") == "duplicate_order_intent":
                reason_codes.append("regime.trade_management.exit_intent_duplicate")
            else:
                reason_codes.append(str(inserted.get("reason") or "regime.trade_management.exit_intent_not_inserted"))

    if exit_intents:
        reason_codes.append("regime.trade_management.exit_intents_enqueued")
    if held_positions:
        reason_codes.append("regime.trade_management.positions_held")
    if blocked_positions:
        reason_codes.append("regime.trade_management.positions_blocked")

    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "tradeManagementWorkerVersion": REGIME_TRADE_MANAGEMENT_WORKER_VERSION,
        "runtimeMode": identity.get("runtimeMode") or identity.get("runtime_mode"),
        "symbol": identity.get("symbol"),
        "evaluatedAt": _iso(evaluated_at),
        "openPositionsEvaluated": len(positions),
        "heldPositions": held_positions,
        "exitIntentsCreated": len(exit_intents),
        "duplicateExitIntents": duplicate_exit_intents,
        "blockedPositions": blocked_positions,
        "exitIntents": exit_intents,
        "newEntriesPaused": bool(entry_paused),
        "riskReducingExitsAllowed": True,
        "paperOnly": True,
        "liveTradingEnabled": False,
        "reasonCodes": tuple(dict.fromkeys(reason_codes)),
    }


def _exit_order_intent(
    *,
    identity: dict[str, Any],
    position: dict[str, Any],
    exit_action: dict[str, Any],
    settings_snapshot: dict[str, Any],
    confirmed_regime: str,
    evaluated_at: datetime,
) -> dict[str, Any]:
    quantity = abs(int(exit_action.get("quantity") or position.get("filledQuantity") or position.get("quantity") or 0))
    owned_quantity = abs(int(position.get("filledQuantity") or position.get("quantity") or 0))
    quantity = min(quantity, owned_quantity)
    exit_price = _positive_float(exit_action.get("exitPrice") or exit_action.get("exit_price") or position.get("averageFillPrice") or 0.01)
    stop_price = _optional_positive(position.get("stopPrice") or position.get("stop_price"))
    target_price = _optional_positive(position.get("targetPrice") or position.get("target_price"))
    settings_version = str(settings_snapshot.get("settingsVersion") or settings_snapshot.get("settings_version") or position.get("settingsVersion") or "regime_unknown_settings")
    profile_version = str(settings_snapshot.get("profileVersion") or settings_snapshot.get("profile_version") or position.get("profileVersion") or "regime_unknown_profile")
    order_intent_id = str(exit_action.get("orderIntentId") or exit_action.get("order_intent_id") or "")
    if not order_intent_id:
        order_intent_id = "regime-exit-" + _digest(f"{position.get('positionId')}:{exit_action.get('reason')}:{quantity}")[:16]
    decision_id = str(position.get("decisionId") or position.get("decision_id") or f"{order_intent_id}:position-management")
    return {
        **identity,
        "algorithmId": REGIME_ALGORITHM_ID,
        "algorithmVersion": REGIME_ALGORITHM_VERSION,
        "tradeManagementWorkerVersion": REGIME_TRADE_MANAGEMENT_WORKER_VERSION,
        "settingsVersion": settings_version,
        "profileVersion": profile_version,
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "idempotencyKey": str(exit_action.get("idempotencyKey") or f"regime-exit-{_digest(order_intent_id)[:24]}"),
        "symbol": str(identity.get("symbol") or position.get("symbol") or "SPY").upper(),
        "side": str(exit_action.get("side") or "Sell"),
        "positionEffect": str(exit_action.get("action") or "exit_long"),
        "quantity": quantity,
        "entryPrice": exit_price,
        "limitPrice": exit_price,
        "stopPrice": stop_price,
        "targetPrice": target_price,
        "riskDollars": 0.0,
        "regime": confirmed_regime,
        "confidence": 1.0,
        "positionId": position.get("positionId"),
        "tradeId": position.get("tradeId"),
        "exitReason": exit_action.get("reason"),
        "settingsSnapshot": settings_snapshot,
        "createdAt": _iso(evaluated_at),
        "expiresAt": _iso(evaluated_at + timedelta(seconds=_order_ttl_seconds(settings_snapshot))),
        "paperOnly": True,
        "liveTradingEnabled": False,
        "exitAuthority": "regime_owned_position_fill_attribution",
        "ownedPositionQuantity": owned_quantity,
        "opensReversePosition": False,
        "shortEntriesSeparatelyApproved": False,
        "cancelReplacePolicy": "cancel_existing_exit_replace_requires_new_regime_intent",
        "reasonCodes": list(exit_action.get("reasonCodes") or ["regime.trade_management.exit_intent_created"]),
    }


def _exit_local_risk_result(
    *,
    identity: dict[str, Any],
    intent: dict[str, Any],
    exit_action: dict[str, Any],
    evaluated_at: datetime,
) -> dict[str, Any]:
    order_intent_id = str(intent.get("orderIntentId") or "")
    decision_id = str(intent.get("decisionId") or "")
    settings_version = str(intent.get("settingsVersion") or "")
    quantity = int(intent.get("quantity") or 0)
    return {
        **identity,
        "localRiskResultId": f"regime-local-risk-exit-{_digest(f'{decision_id}:{order_intent_id}:{quantity}')[:16]}",
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "settingsVersion": settings_version,
        "passed": quantity > 0,
        "requestedQuantity": quantity,
        "approvedQuantity": quantity,
        "estimatedGrossEdge": 0.0,
        "estimatedTransactionCost": 0.0,
        "estimatedNetEdge": 0.0,
        "blockers": [] if quantity > 0 else ["regime.trade_management.zero_exit_quantity"],
        "reductions": [],
        "riskReducingExit": True,
        "exitReason": exit_action.get("reason"),
        "evaluatedAt": _iso(evaluated_at),
        "expiresAt": str(intent.get("expiresAt") or _iso(evaluated_at + timedelta(minutes=5))),
    }


def _order_ttl_seconds(settings_snapshot: dict[str, Any]) -> int:
    execution = settings_snapshot.get("execution") if isinstance(settings_snapshot.get("execution"), dict) else {}
    try:
        return max(1, int(execution.get("orderTimeToLiveSeconds") or execution.get("order_ttl_seconds") or 300))
    except (TypeError, ValueError):
        return 300


def _positive_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.01
    return max(0.01, parsed)


def _optional_positive(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return _positive_float(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _has_fill_level_regime_attribution(position: dict[str, Any]) -> bool:
    return bool(
        str(position.get("algorithmId") or REGIME_ALGORITHM_ID) == REGIME_ALGORITHM_ID
        and position.get("positionId")
        and position.get("tradeId")
        and position.get("orderIntentId")
        and (position.get("appliedFillIds") or position.get("lastFillId") or position.get("authoritativeInventorySnapshot"))
    )


__all__ = [
    "REGIME_TRADE_MANAGEMENT_WORKER_VERSION",
    "evaluate_regime_exit",
    "manage_regime_positions_for_completed_bar",
]

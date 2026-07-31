"""Regime-owned position and trade management."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from backend.app.algorithms.regime.persistence import RegimeSqliteRepository


REGIME_POSITION_MANAGER_VERSION = "regime_position_manager_v2"
RISK_OFF_REGIMES = {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}
EXIT_REASONS = (
    "initial_stop",
    "profit_target",
    "time_stop",
    "maximum_holding_bars",
    "end_of_day_flatten",
    "risk_off_transition",
    "regime_invalidation",
    "strategy_invalidation",
    "global_emergency_flatten",
    "signal_invalidation",
    "stale_protective_order",
    "broker_reconciliation_discrepancy",
)


@dataclass(frozen=True)
class RegimeExitAction:
    action: str
    position_id: str
    trade_id: str
    order_intent_id: str
    side: str
    quantity: int
    exit_price: float
    reason: str
    reason_codes: tuple[str, ...]
    idempotency_key: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": "regime",
            "action": self.action,
            "positionId": self.position_id,
            "tradeId": self.trade_id,
            "orderIntentId": self.order_intent_id,
            "side": self.side,
            "quantity": self.quantity,
            "exitPrice": self.exit_price,
            "reason": self.reason,
            "reasonCodes": list(self.reason_codes),
            "idempotencyKey": self.idempotency_key,
        }


class RegimePositionManager:
    def __init__(self, repository: RegimeSqliteRepository) -> None:
        self.repository = repository

    def restore_open_positions(self, identity: dict[str, Any]) -> list[dict[str, Any]]:
        return self.repository.latest_open_regime_positions(identity)

    def apply_fill_observation(
        self,
        identity: dict[str, Any],
        fill: dict[str, Any],
        *,
        settings_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if str(fill.get("algorithmId") or fill.get("algorithm_id") or "") != "regime":
            raise ValueError("Regime position manager rejects cross-algorithm fill observations")
        order_intent_id = str(fill.get("orderIntentId") or fill.get("order_intent_id") or "")
        decision_id = str(fill.get("decisionId") or fill.get("decision_id") or "")
        if not order_intent_id:
            raise ValueError("Regime fill observation requires orderIntentId")
        filled_quantity = int(fill.get("filledQuantity") or fill.get("filled_quantity") or fill.get("quantity") or 0)
        if filled_quantity <= 0:
            return {"updated": False, "reason": "regime.position.fill_zero_quantity"}
        fill_id = str(fill.get("fillId") or fill.get("fill_id") or f"{order_intent_id}:{fill.get('filledAt') or fill.get('timestamp') or filled_quantity}")
        side = _normal_side(fill.get("side") or "Buy")
        position_effect = str(fill.get("positionEffect") or fill.get("position_effect") or "")
        all_positions = self.repository.latest_regime_positions(identity)
        supplied_position_id = str(fill.get("positionId") or fill.get("position_id") or "")
        existing = (
            _latest_by_id(all_positions, supplied_position_id)
            if supplied_position_id
            else _exit_target_position(all_positions, side, position_effect) or _entry_target_position(all_positions, order_intent_id)
        )
        if position_effect.startswith("exit") and not existing:
            raise ValueError("Regime exit fill cannot open a reverse position without an owned position")
        position_id = str(existing.get("positionId") or supplied_position_id or f"regime-position-{identity.get('symbol', 'SPY')}-{order_intent_id}")
        trade_id = str(existing.get("tradeId") or fill.get("tradeId") or fill.get("trade_id") or f"regime-trade-{identity.get('symbol', 'SPY')}-{order_intent_id}")
        applied = list(existing.get("appliedFillIds") or [])
        if fill_id in applied:
            return {"updated": False, "duplicate": True, "position": existing, "reason": "regime.position.duplicate_fill_ignored"}
        inventory_update = self.repository.apply_inventory_fill(identity, fill, settings_snapshot=settings_snapshot)
        previous_quantity = int(existing.get("filledQuantity") or 0)
        previous_average = float(existing.get("averageFillPrice") or fill.get("averageFillPrice") or fill.get("average_fill_price") or 0)
        fill_price = float(fill.get("averageFillPrice") or fill.get("average_fill_price") or previous_average or 0)
        existing_side = str(existing.get("side") or ("Short" if side == "Sell" and position_effect == "enter_short" else "Long"))
        closes_existing = (existing_side == "Long" and side == "Sell") or (existing_side == "Short" and side == "Buy")
        new_quantity = max(0, previous_quantity - filled_quantity) if closes_existing else previous_quantity + filled_quantity
        average = previous_average if closes_existing and new_quantity >= 0 else ((previous_average * previous_quantity) + (fill_price * filled_quantity)) / max(1, new_quantity)
        requested_quantity = int(fill.get("submittedQuantity") or fill.get("requestedQuantity") or existing.get("requestedQuantity") or new_quantity)
        remaining = 0 if closes_existing else max(0, requested_quantity - new_quantity)
        timestamp = _iso(_parse_datetime(fill.get("filledAt") or fill.get("timestamp")))
        stop = _number(fill.get("stopPrice") or fill.get("stop_price") or existing.get("stopPrice"))
        target = _number(fill.get("targetPrice") or fill.get("target_price") or existing.get("targetPrice"))
        position = {
            **identity,
            "positionManagerVersion": REGIME_POSITION_MANAGER_VERSION,
            "positionId": position_id,
            "tradeId": trade_id,
            "decisionId": decision_id or existing.get("decisionId") or "",
            "orderIntentId": order_intent_id,
            "brokerOrderId": fill.get("brokerOrderId") or fill.get("broker_order_id") or existing.get("brokerOrderId"),
            "side": existing_side if existing else ("Long" if side == "Buy" else "Short"),
            "entryState": existing.get("entryState") or ("partially_filled" if remaining else "filled"),
            "positionStatus": "closed" if closes_existing and new_quantity == 0 else "open",
            "averageFillPrice": round(average, 8),
            "filledQuantity": new_quantity,
            "quantity": new_quantity,
            "remainingQuantity": remaining,
            "requestedQuantity": requested_quantity,
            "stopPrice": stop,
            "targetPrice": target,
            "initialProtectiveStopRequired": stop is not None,
            "initialProfitTargetPresent": target is not None,
            "protectiveOrderQuantity": int(existing.get("protectiveOrderQuantity") or 0),
            "partialFillProtectionState": "pending_protection" if remaining and stop is not None else existing.get("partialFillProtectionState") or "not_required",
            "filledQuantityProtected": False if remaining and stop is not None else bool(existing.get("filledQuantityProtected", False)),
            "highestFavorablePrice": max(float(existing.get("highestFavorablePrice") or fill_price), fill_price),
            "lowestFavorablePrice": min(float(existing.get("lowestFavorablePrice") or fill_price), fill_price),
            "realizedPnl": _realized_after_fill(existing, side, filled_quantity, fill_price),
            "unrealizedPnl": float(existing.get("unrealizedPnl") or 0.0),
            "holdingBars": int(existing.get("holdingBars") or 0),
            "exitState": existing.get("exitState") or "none",
            "reconciliationState": existing.get("reconciliationState") or "reconciled",
            "authoritativeInventorySnapshot": inventory_update.get("snapshot"),
            "appliedFillIds": (*applied, fill_id),
            "stopTargetHistory": list(existing.get("stopTargetHistory") or []),
            "settingsVersion": _settings_version(settings_snapshot, fill),
            "openedAt": existing.get("openedAt") or timestamp,
            "updatedAt": timestamp,
        }
        self.repository.record_position_state(identity, position)
        self.repository.record_trade_state(identity, {**position, "tradeStatus": "closed" if position["positionStatus"] == "closed" else "open"})
        return {"updated": True, "position": position}

    def apply_protective_order_observation(self, identity: dict[str, Any], protective_order: dict[str, Any]) -> dict[str, Any]:
        if str(protective_order.get("algorithmId") or protective_order.get("algorithm_id") or "") != "regime":
            raise ValueError("Regime position manager rejects cross-algorithm protective orders")
        order_intent_id = str(protective_order.get("orderIntentId") or protective_order.get("order_intent_id") or "")
        if not order_intent_id:
            raise ValueError("Regime protective order requires orderIntentId")
        positions = self.repository.latest_open_regime_positions(identity)
        position = next((item for item in positions if str(item.get("orderIntentId")) == order_intent_id), None)
        if position is None:
            return {"updated": False, "reason": "regime.position.protective_order_without_owned_position"}
        protected_quantity = int(protective_order.get("quantity") or 0)
        filled_quantity = int(position.get("filledQuantity") or position.get("quantity") or 0)
        updated = {
            **position,
            "protectiveOrderId": protective_order.get("clientOrderId") or protective_order.get("protectiveOrderId"),
            "protectiveParentClientOrderId": protective_order.get("parentClientOrderId"),
            "protectiveOrderQuantity": protected_quantity,
            "filledQuantityProtected": protected_quantity >= filled_quantity > 0,
            "partialFillProtectionState": "protected" if protected_quantity >= filled_quantity > 0 else "under_protected",
            "stopPrice": protective_order.get("stopPrice") or position.get("stopPrice"),
            "targetPrice": protective_order.get("targetPrice") or position.get("targetPrice"),
            "updatedAt": _iso(datetime.now(timezone.utc)),
        }
        self.repository.record_position_state(identity, updated)
        self.repository.record_trade_state(identity, {**updated, "tradeStatus": "open"})
        return {"updated": True, "position": updated}

    def evaluate_position(
        self,
        identity: dict[str, Any],
        position: dict[str, Any],
        *,
        candle: dict[str, Any],
        settings_snapshot: dict[str, Any],
        confirmed_regime: str,
        entry_paused: bool = False,
        global_emergency_flatten: bool = False,
    ) -> dict[str, Any]:
        if position.get("algorithmId") not in {None, "regime"}:
            raise ValueError("Regime position manager rejects cross-algorithm position state")
        if not _has_fill_level_regime_attribution(position):
            protected_position = {**position, "positionStatus": "blocked", "reconciliationState": "missing_fill_attribution"}
            self.repository.record_position_state(identity, protected_position)
            return {"action": "hold", "position": protected_position, "reasonCodes": ("regime.position.shared_account_position_rejected",)}
        position = self._mark_to_market(position, candle)
        position, trailing_reasons = _apply_trailing_stop(position, candle, settings_snapshot)
        reason, exit_price = _exit_reason(position, candle, settings_snapshot, confirmed_regime, global_emergency_flatten=global_emergency_flatten)
        if reason is None:
            protected_position = {**position, "positionStatus": "open", "entryPausedWhileProtected": bool(entry_paused)}
            self.repository.record_position_state(identity, protected_position)
            return {"action": "hold", "position": protected_position, "reasonCodes": trailing_reasons}
        existing_exit = position.get("exitIntentId")
        if existing_exit:
            return {"action": "exit", "idempotent": True, "exitAction": position.get("exitAction"), "reasonCodes": ("regime.position.exit_already_requested",)}
        exit_action = _exit_action(position, reason, exit_price)
        closed = _closed_position(position, exit_action, candle)
        self.repository.record_position_state(identity, closed)
        self.repository.record_trade_state(identity, {**closed, "tradeStatus": "closed", "exitAction": exit_action.as_dict()})
        return {"action": "exit", "exitAction": exit_action.as_dict(), "position": closed, "reasonCodes": (*trailing_reasons, *exit_action.reason_codes)}

    def update_stop_target(
        self,
        identity: dict[str, Any],
        position: dict[str, Any],
        *,
        stop_price: float | None = None,
        target_price: float | None = None,
        reason: str,
        settings_version: str,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        side = str(position.get("side") or "Long")
        current_stop = _number(position.get("stopPrice"))
        if stop_price is not None and current_stop is not None:
            if side == "Long" and stop_price < current_stop:
                return {"updated": False, "reason": "regime.position.stop_widening_rejected"}
            if side == "Short" and stop_price > current_stop:
                return {"updated": False, "reason": "regime.position.stop_widening_rejected"}
        history = list(position.get("stopTargetHistory") or [])
        updated = {
            **position,
            "stopPrice": current_stop if stop_price is None else stop_price,
            "targetPrice": position.get("targetPrice") if target_price is None else target_price,
            "settingsVersion": settings_version,
            "updatedAt": timestamp or _iso(datetime.now(timezone.utc)),
        }
        history.append(
            {
                "reason": reason,
                "settingsVersion": settings_version,
                "timestamp": updated["updatedAt"],
                "previousStopPrice": current_stop,
                "newStopPrice": updated["stopPrice"],
                "previousTargetPrice": position.get("targetPrice"),
                "newTargetPrice": updated["targetPrice"],
            }
        )
        updated["stopTargetHistory"] = history
        self.repository.record_position_state(identity, updated)
        return {"updated": True, "position": updated}

    def reconcile_broker_observations(self, identity: dict[str, Any], broker_positions: list[dict[str, Any]]) -> dict[str, Any]:
        own_positions = {str(position.get("positionId")): position for position in self.repository.latest_open_regime_positions(identity)}
        discrepancies: list[str] = []
        for broker_position in broker_positions:
            if str(broker_position.get("algorithmId") or broker_position.get("algorithm_id") or "") != "regime":
                if int(broker_position.get("quantity") or broker_position.get("filledQuantity") or 0) != 0:
                    discrepancies.append(f"unattributed_broker_position:{broker_position.get('positionId') or broker_position.get('symbol') or 'unknown'}")
                continue
            position_id = str(broker_position.get("positionId") or broker_position.get("position_id") or "")
            if position_id and position_id not in own_positions:
                discrepancies.append(f"missing_regime_position:{position_id}")
            elif position_id:
                ledger_qty = int(own_positions[position_id].get("filledQuantity") or own_positions[position_id].get("quantity") or 0)
                broker_qty = int(broker_position.get("quantity") or broker_position.get("filledQuantity") or 0)
                if ledger_qty != broker_qty:
                    discrepancies.append(f"quantity_mismatch:{position_id}")
        if discrepancies:
            for position in own_positions.values():
                self.repository.record_position_state(identity, {**position, "reconciliationState": "unresolved_discrepancy", "reconciliationDiscrepancies": discrepancies})
            return {"reconciled": False, "blockNewEntries": True, "reasonCodes": ("regime.position.reconciliation_discrepancy",), "discrepancies": discrepancies}
        return {"reconciled": True, "blockNewEntries": False, "reasonCodes": ("regime.position.reconciled",), "discrepancies": ()}

    def _mark_to_market(self, position: dict[str, Any], candle: dict[str, Any]) -> dict[str, Any]:
        side = str(position.get("side") or "Long")
        close = float(candle.get("close") or position.get("averageFillPrice") or 0)
        high = float(candle.get("high") or close)
        low = float(candle.get("low") or close)
        entry = float(position.get("averageFillPrice") or close)
        quantity = int(position.get("filledQuantity") or position.get("quantity") or 0)
        multiplier = 1 if side == "Long" else -1
        favourable = high if side == "Long" else low
        adverse = low if side == "Long" else high
        return {
            **position,
            "highestFavorablePrice": max(float(position.get("highestFavorablePrice") or favourable), favourable),
            "lowestFavorablePrice": min(float(position.get("lowestFavorablePrice") or adverse), adverse),
            "unrealizedPnl": round((close - entry) * quantity * multiplier, 6),
            "holdingBars": int(position.get("holdingBars") or 0) + 1,
            "lastProcessedBarTimestamp": str(candle.get("timestamp") or candle.get("barTimestamp") or ""),
        }


def _exit_reason(
    position: dict[str, Any],
    candle: dict[str, Any],
    settings_snapshot: dict[str, Any],
    confirmed_regime: str,
    *,
    global_emergency_flatten: bool,
) -> tuple[str | None, float]:
    close = float(candle.get("close") or position.get("averageFillPrice") or 0)
    high = float(candle.get("high") or close)
    low = float(candle.get("low") or close)
    side = str(position.get("side") or "Long")
    stop = _number(position.get("stopPrice"))
    target = _number(position.get("targetPrice"))
    if global_emergency_flatten:
        return "global_emergency_flatten", close
    if str(position.get("reconciliationState") or "") == "unresolved_discrepancy":
        return "broker_reconciliation_discrepancy", close
    if position.get("staleProtectiveOrder"):
        return "stale_protective_order", close
    if side == "Long":
        if stop is not None and low <= stop:
            return "initial_stop", stop
        if target is not None and high >= target:
            return "profit_target", target
    else:
        if stop is not None and high >= stop:
            return "initial_stop", stop
        if target is not None and low <= target:
            return "profit_target", target
    if _time_stop_hit(position, settings_snapshot):
        return "time_stop", close
    if _maximum_holding_hit(position, settings_snapshot):
        return "maximum_holding_bars", close
    if _flatten_time_reached(candle, settings_snapshot):
        return "end_of_day_flatten", close
    if confirmed_regime in RISK_OFF_REGIMES:
        return "risk_off_transition", close
    if position.get("signalInvalidated") or position.get("invalidatedBySignal"):
        return "signal_invalidation", close
    if position.get("invalidatedByRegime") or (position.get("entryRegime") and position.get("entryRegime") != confirmed_regime and (position.get("regimeMustPersist") or _exit_on_regime_transition(settings_snapshot))):
        return "regime_invalidation", close
    if position.get("strategyInvalidated"):
        return "strategy_invalidation", close
    return None, close


def _exit_action(position: dict[str, Any], reason: str, exit_price: float) -> RegimeExitAction:
    side = str(position.get("side") or "Long")
    action = "exit_long" if side == "Long" else "exit_short"
    quantity = abs(int(position.get("filledQuantity") or position.get("quantity") or 0))
    position_id = str(position.get("positionId") or "")
    trade_id = str(position.get("tradeId") or f"regime-trade-{position_id}")
    order_intent_id = f"regime-exit-{_digest(f'{position_id}:{trade_id}:{reason}')[:16]}"
    key = f"regime-exit:{position_id}:{trade_id}:{reason}:{quantity}"
    return RegimeExitAction(
        action=action,
        position_id=position_id,
        trade_id=trade_id,
        order_intent_id=order_intent_id,
        side="Sell" if side == "Long" else "Buy",
        quantity=quantity,
        exit_price=exit_price,
        reason=reason,
        reason_codes=(f"regime.position.exit.{reason}",),
        idempotency_key=f"regime-exit-{_digest(key)[:24]}",
    )


def _closed_position(position: dict[str, Any], exit_action: RegimeExitAction, candle: dict[str, Any]) -> dict[str, Any]:
    entry = float(position.get("averageFillPrice") or exit_action.exit_price)
    side = str(position.get("side") or "Long")
    multiplier = 1 if side == "Long" else -1
    realized = round((exit_action.exit_price - entry) * exit_action.quantity * multiplier, 6)
    return {
        **position,
        "positionStatus": "closed",
        "exitState": "exit_requested",
        "exitIntentId": exit_action.order_intent_id,
        "exitAction": exit_action.as_dict(),
        "exitReason": exit_action.reason,
        "exitPrice": exit_action.exit_price,
        "exitAt": str(candle.get("timestamp") or candle.get("barTimestamp") or _iso(datetime.now(timezone.utc))),
        "realizedPnl": realized,
        "unrealizedPnl": 0.0,
        "remainingQuantity": 0,
        "quantity": 0,
    }


def _apply_trailing_stop(position: dict[str, Any], candle: dict[str, Any], settings_snapshot: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not _trailing_exits_enabled(settings_snapshot):
        return position, ()
    side = str(position.get("side") or "Long")
    close = float(candle.get("close") or position.get("averageFillPrice") or 0)
    high = float(candle.get("high") or close)
    low = float(candle.get("low") or close)
    current_stop = _number(position.get("stopPrice"))
    if current_stop is None:
        return position, ("regime.position.trailing_stop_skipped_missing_initial_stop",)
    distance = _trailing_distance(position, settings_snapshot, close)
    if side == "Long":
        candidate = max(current_stop, high - distance)
        tightened = candidate > current_stop
    else:
        candidate = min(current_stop, low + distance)
        tightened = candidate < current_stop
    if not tightened:
        return position, ()
    history = list(position.get("stopTargetHistory") or [])
    timestamp = str(candle.get("timestamp") or candle.get("barTimestamp") or _iso(datetime.now(timezone.utc)))
    history.append(
        {
            "reason": "trailing_stop",
            "timestamp": timestamp,
            "previousStopPrice": current_stop,
            "newStopPrice": round(candidate, 8),
            "profileEnabled": True,
        }
    )
    return (
        {
            **position,
            "stopPrice": round(candidate, 8),
            "trailingStopActive": True,
            "trailingStopDistance": distance,
            "stopTargetHistory": history,
            "updatedAt": timestamp,
        },
        ("regime.position.trailing_stop_tightened",),
    )


def _trailing_exits_enabled(settings_snapshot: dict[str, Any]) -> bool:
    if bool(settings_snapshot.get("trailingExitsEnabled")):
        return True
    exit_policy = settings_snapshot.get("exit_policy") if isinstance(settings_snapshot.get("exit_policy"), dict) else settings_snapshot.get("exitPolicy") if isinstance(settings_snapshot.get("exitPolicy"), dict) else {}
    return bool(exit_policy.get("trailingExitsEnabled") or exit_policy.get("trailing_exits_enabled"))


def _trailing_distance(position: dict[str, Any], settings_snapshot: dict[str, Any], close: float) -> float:
    exit_policy = settings_snapshot.get("exit_policy") if isinstance(settings_snapshot.get("exit_policy"), dict) else settings_snapshot.get("exitPolicy") if isinstance(settings_snapshot.get("exitPolicy"), dict) else {}
    for key in ("trailingStopDistance", "trailing_stop_distance"):
        value = _number(exit_policy.get(key) or settings_snapshot.get(key))
        if value is not None and value > 0:
            return value
    bps = _number(exit_policy.get("trailingStopBps") or exit_policy.get("trailing_stop_bps") or settings_snapshot.get("trailingStopBps"))
    if bps is not None and bps > 0:
        return max(0.01, close * bps / 10_000.0)
    current_stop = _number(position.get("stopPrice")) or close
    entry = float(position.get("averageFillPrice") or close)
    return max(0.01, abs(entry - current_stop))


def _exit_on_regime_transition(settings_snapshot: dict[str, Any]) -> bool:
    exit_policy = settings_snapshot.get("exit_policy") if isinstance(settings_snapshot.get("exit_policy"), dict) else settings_snapshot.get("exitPolicy") if isinstance(settings_snapshot.get("exitPolicy"), dict) else {}
    return bool(exit_policy.get("exitOnRegimeTransition") or exit_policy.get("exit_on_regime_transition"))


def _has_fill_level_regime_attribution(position: dict[str, Any]) -> bool:
    if str(position.get("algorithmId") or "regime") != "regime":
        return False
    return bool(
        position.get("positionId")
        and position.get("tradeId")
        and position.get("orderIntentId")
        and (position.get("appliedFillIds") or position.get("lastFillId") or position.get("authoritativeInventorySnapshot"))
    )


def _time_stop_hit(position: dict[str, Any], settings_snapshot: dict[str, Any]) -> bool:
    bars = int(position.get("holdingBars") or 0)
    exit_policy = settings_snapshot.get("exit_policy") if isinstance(settings_snapshot.get("exit_policy"), dict) else settings_snapshot.get("exitPolicy") if isinstance(settings_snapshot.get("exitPolicy"), dict) else {}
    try:
        time_stop_bars = int(exit_policy.get("timeStopBars") or exit_policy.get("time_stop_bars") or 0)
    except (TypeError, ValueError):
        time_stop_bars = 0
    return time_stop_bars > 0 and bars >= time_stop_bars


def _maximum_holding_hit(position: dict[str, Any], settings_snapshot: dict[str, Any]) -> bool:
    bars = int(position.get("holdingBars") or 0)
    flattened = settings_snapshot.get("flatSettings") if isinstance(settings_snapshot.get("flatSettings"), dict) else settings_snapshot
    try:
        maximum = int(flattened.get("maximumHoldingBars") or flattened.get("maximum_holding_bars") or 0)
    except (TypeError, ValueError):
        maximum = 0
    return maximum > 0 and bars >= maximum


def _flatten_time_reached(candle: dict[str, Any], settings_snapshot: dict[str, Any]) -> bool:
    timestamp = _parse_datetime(candle.get("timestamp") or candle.get("barTimestamp"))
    if timestamp is None:
        return False
    flattened = settings_snapshot.get("flatSettings") if isinstance(settings_snapshot.get("flatSettings"), dict) else settings_snapshot
    configured = str(flattened.get("flattenTimeEt") or flattened.get("flatten_time_et") or "15:55")
    try:
        hour, minute = (int(part) for part in configured.split(":", 1))
    except ValueError:
        hour, minute = 15, 55
    et_timestamp = timestamp.astimezone(ZoneInfo("America/New_York"))
    return et_timestamp.time() >= time(hour, minute)


def _latest_by_id(positions: list[dict[str, Any]], position_id: str) -> dict[str, Any]:
    for position in positions:
        if str(position.get("positionId") or position.get("position_id")) == position_id:
            return position
    return {}


def _exit_target_position(positions: list[dict[str, Any]], fill_side: str, position_effect: str) -> dict[str, Any]:
    if not position_effect.startswith("exit"):
        return {}
    wanted_side = "Long" if fill_side == "Sell" else "Short"
    for position in positions:
        if str(position.get("positionStatus") or "open").lower() in {"closed", "flat", "cancelled", "canceled"}:
            continue
        if str(position.get("side") or "Long") == wanted_side and int(position.get("filledQuantity") or position.get("quantity") or 0) > 0:
            return position
    return {}


def _entry_target_position(positions: list[dict[str, Any]], order_intent_id: str) -> dict[str, Any]:
    for position in positions:
        if str(position.get("orderIntentId") or position.get("order_intent_id")) == order_intent_id:
            return position
    return {}


def _realized_after_fill(existing: dict[str, Any], fill_side: str, filled_quantity: int, fill_price: float) -> float:
    previous = float(existing.get("realizedPnl") or 0.0)
    if not existing:
        return previous
    position_side = str(existing.get("side") or "Long")
    closes_existing = (position_side == "Long" and fill_side == "Sell") or (position_side == "Short" and fill_side == "Buy")
    if not closes_existing:
        return previous
    entry = float(existing.get("averageFillPrice") or fill_price)
    multiplier = 1 if position_side == "Long" else -1
    return round(previous + ((fill_price - entry) * min(filled_quantity, int(existing.get("filledQuantity") or existing.get("quantity") or filled_quantity)) * multiplier), 6)


def _settings_version(settings_snapshot: dict[str, Any] | None, fallback: dict[str, Any]) -> str:
    settings = settings_snapshot or {}
    return str(settings.get("settingsVersion") or settings.get("settings_version") or fallback.get("settingsVersion") or fallback.get("settings_version") or "unknown_settings")


def _normal_side(value: Any) -> str:
    text = str(getattr(value, "value", value)).upper()
    return "Sell" if text in {"SELL", "SHORT"} else "Buy"


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "EXIT_REASONS",
    "REGIME_POSITION_MANAGER_VERSION",
    "RegimeExitAction",
    "RegimePositionManager",
]

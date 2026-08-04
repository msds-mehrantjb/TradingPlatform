from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping


REGIME_ACCOUNT_SNAPSHOT_VERSION = "regime_authoritative_account_snapshot_v1"
REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY = "backend_account_and_global_risk_services"
REGIME_ACCOUNT_SNAPSHOT_ALLOWED_AUTHORITIES = {
    REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY,
    "shared_backend_service",
}
REGIME_ACCOUNT_SNAPSHOT_FORBIDDEN_FIELDS = {
    "settings",
    "settingsSnapshot",
    "inventory",
    "inventorySnapshot",
    "positions",
    "orders",
    "fills",
    "runtimeState",
}


def build_regime_authoritative_account_snapshot_provider(
    *,
    account_provider: Callable[[], Mapping[str, Any] | None] | None,
    global_risk_manager: Any | None = None,
) -> Callable[[dict[str, str]], dict[str, Any]]:
    def provider(identity: dict[str, str]) -> dict[str, Any]:
        if not callable(account_provider):
            return fail_closed_regime_account_snapshot(
                identity,
                reason_codes=("regime.account_snapshot.provider_unavailable",),
                source_authority="shared_backend_unavailable",
            )
        try:
            account_record = dict(account_provider() or {})
        except Exception:
            return fail_closed_regime_account_snapshot(
                identity,
                reason_codes=("regime.account_snapshot.provider_failed",),
                source_authority="shared_backend_unavailable",
            )

        observed_at = _utc_now()
        active_reserved_buying_power = _active_reserved_buying_power(global_risk_manager)
        raw_available = _number(account_record.get("availableBuyingPower") or account_record.get("buyingPower"))
        available_buying_power = max(0.0, (raw_available or 0.0) - active_reserved_buying_power)
        account_record["availableBuyingPower"] = available_buying_power
        account_record["globalRiskCapacityQuantity"] = _global_risk_capacity_quantity(
            account_record,
            available_buying_power=available_buying_power,
        )
        account_record["sourceAuthority"] = REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY
        account_record["runtimeMode"] = identity.get("runtimeMode")
        account_record.setdefault("accountId", identity.get("accountId"))
        account_record.setdefault("cash", _number(account_record.get("settledCash") or account_record.get("cash")) or 0.0)
        account_record.setdefault("dailyAccountPnl", _number(account_record.get("dailyAccountPnl") or account_record.get("dailyPnl") or account_record.get("realizedDailyPnl")) or 0.0)
        account_record.setdefault("positionsReconciled", bool(account_record.get("positionsReconciled", True)))
        account_record.setdefault("openOrdersReconciled", bool(account_record.get("openOrdersReconciled", True)))
        account_record.setdefault("accountTradingBlocked", bool(account_record.get("accountTradingBlocked", False)))
        account_record.setdefault("observedAt", observed_at.isoformat().replace("+00:00", "Z"))
        account_record.setdefault("accountSnapshotFresh", True)
        account_record.setdefault("buyingPowerCurrent", True)
        account_record["reasonCodes"] = list(account_record.get("reasonCodes") or ())
        return normalize_regime_account_snapshot(account_record, identity=identity, observed_at=observed_at)

    return provider


def normalize_regime_account_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    identity: Mapping[str, str],
    observed_at: datetime | None = None,
    max_age_seconds: float | None = 30.0,
) -> dict[str, Any]:
    record = sanitize_regime_account_snapshot(snapshot)
    now = _as_utc(observed_at) if observed_at is not None else _utc_now()
    blockers = authoritative_regime_account_snapshot_blockers(
        record,
        identity=identity,
        observed_at=now,
        max_age_seconds=max_age_seconds,
    )
    existing_reasons = [str(code) for code in record.get("reasonCodes") or () if code]
    reason_codes = list(dict.fromkeys([*existing_reasons, *blockers]))
    normalized = {
        "algorithmId": "regime",
        "accountSnapshotVersion": REGIME_ACCOUNT_SNAPSHOT_VERSION,
        "sourceAuthority": str(record.get("sourceAuthority") or "shared_backend_unavailable"),
        "accountId": str(record.get("accountId") or identity.get("accountId") or ""),
        "runtimeMode": str(record.get("runtimeMode") or identity.get("runtimeMode") or ""),
        "equity": max(0.0, _number(record.get("equity") or record.get("accountEquity") or record.get("portfolioValue")) or 0.0),
        "cash": max(0.0, _number(record.get("cash") or record.get("settledCash")) or 0.0),
        "buyingPower": max(0.0, _number(record.get("buyingPower")) or 0.0),
        "availableBuyingPower": max(0.0, _number(record.get("availableBuyingPower") or record.get("buyingPower")) or 0.0),
        "globalRiskCapacityQuantity": _nonnegative_int(record.get("globalRiskCapacityQuantity")),
        "dailyAccountPnl": float(_number(record.get("dailyAccountPnl") or record.get("dailyPnl") or record.get("realizedDailyPnl")) or 0.0),
        "positionsReconciled": record.get("positionsReconciled") is True,
        "openOrdersReconciled": record.get("openOrdersReconciled") is True,
        "accountTradingBlocked": bool(record.get("accountTradingBlocked")),
        "accountSnapshotFresh": record.get("accountSnapshotFresh") is True,
        "buyingPowerCurrent": record.get("buyingPowerCurrent") is True,
        "observedAt": _observed_at(record, default=now),
        "reasonCodes": reason_codes,
    }
    if blockers:
        normalized["availableBuyingPower"] = 0.0
        normalized["buyingPower"] = 0.0
        normalized["globalRiskCapacityQuantity"] = 0
        normalized["accountTradingBlocked"] = True
        normalized["accountSnapshotFresh"] = False
        normalized["buyingPowerCurrent"] = False
    safe_extras = {
        key: value
        for key, value in record.items()
        if key not in REGIME_ACCOUNT_SNAPSHOT_FORBIDDEN_FIELDS and key not in normalized
    }
    return {**safe_extras, **normalized}


def fail_closed_regime_account_snapshot(
    identity: Mapping[str, str],
    *,
    reason_codes: tuple[str, ...] | list[str],
    source_authority: str = "shared_backend_unavailable",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    now = _as_utc(observed_at) if observed_at is not None else _utc_now()
    return {
        "algorithmId": "regime",
        "accountSnapshotVersion": REGIME_ACCOUNT_SNAPSHOT_VERSION,
        "sourceAuthority": source_authority,
        "accountId": str(identity.get("accountId") or ""),
        "runtimeMode": str(identity.get("runtimeMode") or ""),
        "equity": 0.0,
        "cash": 0.0,
        "buyingPower": 0.0,
        "availableBuyingPower": 0.0,
        "globalRiskCapacityQuantity": 0,
        "dailyAccountPnl": 0.0,
        "positionsReconciled": False,
        "openOrdersReconciled": False,
        "accountTradingBlocked": True,
        "accountSnapshotFresh": False,
        "buyingPowerCurrent": False,
        "observedAt": now.isoformat().replace("+00:00", "Z"),
        "reasonCodes": list(dict.fromkeys(str(code) for code in reason_codes if code)),
    }


def sanitize_regime_account_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    record = dict(snapshot or {})
    for key in REGIME_ACCOUNT_SNAPSHOT_FORBIDDEN_FIELDS:
        record.pop(key, None)
    return record


def authoritative_regime_account_snapshot_blockers(
    snapshot: Mapping[str, Any] | None,
    *,
    identity: Mapping[str, str] | None = None,
    observed_at: datetime | None = None,
    max_age_seconds: float | None = None,
) -> list[str]:
    record = dict(snapshot or {})
    runtime_mode = str(record.get("runtimeMode") or (identity or {}).get("runtimeMode") or "").lower()
    source = str(record.get("sourceAuthority") or "").lower()
    blockers: list[str] = []
    if not record:
        return ["regime.account_snapshot.missing"]
    if source not in REGIME_ACCOUNT_SNAPSHOT_ALLOWED_AUTHORITIES:
        blockers.append("regime.account_snapshot.source_not_authoritative")
    if identity is not None:
        expected_account_id = str(identity.get("accountId") or "")
        if expected_account_id and str(record.get("accountId") or "") != expected_account_id:
            blockers.append("regime.account_snapshot.account_id_mismatch")
        expected_runtime_mode = str(identity.get("runtimeMode") or "")
        if expected_runtime_mode and str(record.get("runtimeMode") or "") != expected_runtime_mode:
            blockers.append("regime.account_snapshot.runtime_mode_mismatch")
    if runtime_mode == "paper" and record.get("runtimeMode") != "paper":
        blockers.append("regime.account_snapshot.runtime_mode_not_paper")
    if record.get("accountSnapshotFresh") is not True:
        blockers.append("regime.account_snapshot.stale")
    if record.get("buyingPowerCurrent") is not True:
        blockers.append("regime.account_snapshot.buying_power_stale")
    if record.get("accountTradingBlocked") is True:
        blockers.append("regime.account_snapshot.account_trading_blocked")
    if record.get("positionsReconciled") is not True:
        blockers.append("regime.account_snapshot.positions_not_reconciled")
    if record.get("openOrdersReconciled") is not True:
        blockers.append("regime.account_snapshot.open_orders_not_reconciled")
    if _number(record.get("equity") or record.get("accountEquity") or record.get("portfolioValue")) is None:
        blockers.append("regime.account_snapshot.equity_missing")
    if _number(record.get("buyingPower")) is None or _number(record.get("availableBuyingPower") or record.get("buyingPower")) is None:
        blockers.append("regime.account_snapshot.buying_power_missing")
    if _number(record.get("globalRiskCapacityQuantity")) is None:
        blockers.append("regime.account_snapshot.global_risk_capacity_missing")
    elif float(_number(record.get("globalRiskCapacityQuantity")) or 0.0) < 0:
        blockers.append("regime.account_snapshot.global_risk_capacity_negative")
    observed = _parse_time(record.get("observedAt"))
    if observed is None:
        blockers.append("regime.account_snapshot.observed_at_missing")
    elif max_age_seconds is not None:
        now = _as_utc(observed_at) if observed_at is not None else _utc_now()
        if observed > now + timedelta(seconds=5) or now - observed > timedelta(seconds=max_age_seconds):
            blockers.append("regime.account_snapshot.observed_at_stale")
    return list(dict.fromkeys(blockers))


def _active_reserved_buying_power(global_risk_manager: Any | None) -> float:
    reservations = getattr(getattr(global_risk_manager, "reservations", None), "all", None)
    if not callable(reservations):
        return 0.0
    try:
        rows = reservations()
    except Exception:
        return 0.0
    return sum(
        max(0.0, float(getattr(row, "reservedBuyingPower", 0.0) or 0.0))
        for row in rows
        if str(getattr(row, "status", "")) == "reserved"
    )


def _global_risk_capacity_quantity(record: Mapping[str, Any], *, available_buying_power: float) -> int:
    supplied = _number(record.get("globalRiskCapacityQuantity"))
    if supplied is not None:
        return max(0, int(supplied))
    price = _number(record.get("lastPrice") or record.get("markPrice") or record.get("currentPrice"))
    if price and price > 0:
        return max(0, int(available_buying_power // price))
    return max(0, int(available_buying_power))


def _observed_at(record: Mapping[str, Any], *, default: datetime) -> str:
    parsed = _parse_time(record.get("observedAt"))
    return (parsed or default).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if value is None or (isinstance(value, str) and value == ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _number(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, str) and value == ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _nonnegative_int(value: Any) -> int:
    number = _number(value)
    if number is None:
        return 0
    return max(0, int(number))


__all__ = [
    "REGIME_ACCOUNT_SNAPSHOT_ALLOWED_AUTHORITIES",
    "REGIME_ACCOUNT_SNAPSHOT_FORBIDDEN_FIELDS",
    "REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY",
    "REGIME_ACCOUNT_SNAPSHOT_VERSION",
    "authoritative_regime_account_snapshot_blockers",
    "build_regime_authoritative_account_snapshot_provider",
    "fail_closed_regime_account_snapshot",
    "normalize_regime_account_snapshot",
    "sanitize_regime_account_snapshot",
]

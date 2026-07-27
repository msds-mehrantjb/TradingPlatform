"""Ownership declarations for the Meta-Strategy package boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID, ALGORITHM_NAME
from backend.app.algorithms.meta_strategy.interfaces import META_STRATEGY_APPROVED_SHARED_INTERFACE_IDS
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_OWNERSHIP_VERSION


META_STRATEGY_DEFAULT_CAPITAL_PARTITION = "meta_strategy.paper.default"
META_STRATEGY_SETTINGS_NAMESPACE = "meta_strategy"
META_STRATEGY_REQUIRED_IDENTITY_FIELDS = (
    "algorithm_id",
    "capital_partition_id",
    "decision_id",
    "job_id",
    "event_id",
    "order_intent_id",
    "client_order_id",
    "broker_order_id",
    "settings_version",
    "model_version",
)


@dataclass(frozen=True)
class MetaStrategyOwnedDomain:
    domain_id: str
    authority: str
    repository_artifact_type: str | None = None
    mutable_by_meta_strategy_only: bool = True
    may_read_sibling_private_state: bool = False
    may_mutate_foreign_algorithm_state: bool = False


META_STRATEGY_AUTHORITATIVE_DOMAINS: tuple[MetaStrategyOwnedDomain, ...] = (
    MetaStrategyOwnedDomain("settings", "meta_strategy_settings_namespace", "configurations"),
    MetaStrategyOwnedDomain("inventory", "meta_strategy_package_inventory"),
    MetaStrategyOwnedDomain("strategy_state", "meta_strategy_strategy_state", "effective_profiles"),
    MetaStrategyOwnedDomain("decisions", "meta_strategy_decision_ledger", "decisions"),
    MetaStrategyOwnedDomain("order_intents", "meta_strategy_order_intent_ledger", "order_intents"),
    MetaStrategyOwnedDomain("orders", "meta_strategy_order_ledger", "trades"),
    MetaStrategyOwnedDomain("fills", "meta_strategy_fill_ledger", "trades"),
    MetaStrategyOwnedDomain("trades", "meta_strategy_trade_ledger", "trades"),
    MetaStrategyOwnedDomain("model_artifacts", "meta_strategy_model_registry", "model_artifacts"),
    MetaStrategyOwnedDomain("training", "meta_strategy_training_jobs", "training_runs"),
    MetaStrategyOwnedDomain("replay", "meta_strategy_replay_records", "backtests"),
    MetaStrategyOwnedDomain("backtesting", "meta_strategy_backtest_records", "backtests"),
    MetaStrategyOwnedDomain("promotion_evidence", "meta_strategy_promotion_evidence", "promotions"),
)
META_STRATEGY_AUTHORITATIVE_DOMAIN_IDS = tuple(domain.domain_id for domain in META_STRATEGY_AUTHORITATIVE_DOMAINS)


def assert_meta_strategy_ownership(record: Any) -> None:
    algorithm_id = _algorithm_id(record)
    if algorithm_id != ALGORITHM_ID:
        raise ValueError(f"Meta-Strategy cannot mutate records owned by {algorithm_id or 'unknown'}")


def is_meta_strategy_owned(record: Any) -> bool:
    return _algorithm_id(record) == ALGORITHM_ID


def meta_strategy_ownership_boundary() -> dict[str, Any]:
    contract = meta_strategy_ownership_contract()
    return {
        "algorithmId": ALGORITHM_ID,
        "algorithmName": ALGORITHM_NAME,
        "ownershipVersion": META_STRATEGY_OWNERSHIP_VERSION,
        "defaultCapitalPartition": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        "settingsNamespace": META_STRATEGY_SETTINGS_NAMESPACE,
        "requiredIdentityFields": META_STRATEGY_REQUIRED_IDENTITY_FIELDS,
        "authoritativeDomains": contract["authoritativeDomains"],
        "approvedSharedInterfaces": contract["approvedSharedInterfaces"],
        "mayMutateForeignAlgorithmState": False,
        "mayReadSiblingPrivateState": False,
        "ownsPositions": True,
        "ownsOrderIntents": True,
        "ownsPersistenceNamespace": True,
        "reasonCodes": ("meta_strategy.ownership.boundary_ready",),
        "explanation": "Meta-Strategy records must carry meta_strategy ownership before this package mutates them.",
    }


def meta_strategy_ownership_contract() -> dict[str, Any]:
    return {
        "algorithmId": ALGORITHM_ID,
        "algorithmName": ALGORITHM_NAME,
        "ownershipVersion": META_STRATEGY_OWNERSHIP_VERSION,
        "settingsNamespace": META_STRATEGY_SETTINGS_NAMESPACE,
        "defaultCapitalPartition": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        "requiredIdentityFields": META_STRATEGY_REQUIRED_IDENTITY_FIELDS,
        "approvedSharedInterfaces": META_STRATEGY_APPROVED_SHARED_INTERFACE_IDS,
        "authoritativeDomains": tuple(asdict(domain) for domain in META_STRATEGY_AUTHORITATIVE_DOMAINS),
        "mayMutateForeignAlgorithmState": False,
        "mayReadSiblingPrivateState": False,
    }


def _algorithm_id(record: Any) -> str | None:
    if isinstance(record, dict):
        value = record.get("algorithmId", record.get("algorithm_id"))
    else:
        value = getattr(record, "algorithmId", getattr(record, "algorithm_id", None))
    return str(value) if value is not None else None


__all__ = [
    "META_STRATEGY_AUTHORITATIVE_DOMAIN_IDS",
    "META_STRATEGY_AUTHORITATIVE_DOMAINS",
    "META_STRATEGY_DEFAULT_CAPITAL_PARTITION",
    "META_STRATEGY_REQUIRED_IDENTITY_FIELDS",
    "META_STRATEGY_SETTINGS_NAMESPACE",
    "MetaStrategyOwnedDomain",
    "assert_meta_strategy_ownership",
    "is_meta_strategy_owned",
    "meta_strategy_ownership_boundary",
    "meta_strategy_ownership_contract",
]

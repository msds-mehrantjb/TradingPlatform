"""Builds per-strategy reliability observations from realised Voting Ensemble trades.

The ensemble weights each strategy by how accurate it has been. That requires turning
closed trades back into per-strategy outcomes, which this module does by walking the
attribution chain already recorded by the paper-execution runtime:

    closed trade -> entry order (decisionId, side, entry/stop) -> persisted decision (votes, scope)

Two modelling choices are worth stating outright:

* **Outcome is expressed in R**, the realised P&L divided by the risk that was on at
  entry (``|entryPrice - stopPrice| * quantity``). A trade whose risk cannot be
  reconstructed is skipped rather than guessed at, because an unnormalised P&L would
  quietly distort every estimate that follows it.

* **Dissenting strategies are scored counterfactually.** A trade has one direction, but
  the strategies that voted against it made a claim too. A strategy that voted SELL
  while the ensemble bought and lost 1R was right, so it is credited ``+1R`` for its
  SELL call. Concretely, a vote is scored ``+R`` when it agrees with the traded side and
  ``-R`` when it opposes. Without this, only the winning side of each vote ever
  accumulates evidence and a persistently wrong strategy stays at neutral forever.

Observations are filed under the scope returned by ``service.reliability_scope`` so a
recorded outcome lands in the same bucket the estimator later reads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE,
    VOTING_ENSEMBLE_PAPER_EXECUTION_RUNTIME,
)


VOTING_ENSEMBLE_RELIABILITY_FEED_VERSION = "voting_ensemble_reliability_feed_v1"

_DECISION_KEY_PREFIX = f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.decisions."
_SIDE_TO_DIRECTION = {"buy": "BUY", "long": "BUY", "sell": "SELL", "short": "SELL"}
_MAX_OBSERVATIONS = 2000


def build_reliability_observations(
    *,
    repository: Any | None = None,
    sample_window: str = "rolling_60_trades",
    limit: int = _MAX_OBSERVATIONS,
) -> list[dict[str, Any]]:
    """Return reliability observations for every attributable closed trade, oldest first."""
    store = repository if repository is not None else getattr(VOTING_ENSEMBLE_PAPER_EXECUTION_RUNTIME, "repository", None)
    if store is None:
        return []
    ledger = getattr(store, "inventory_ledger", None)
    if ledger is None:
        return []

    try:
        closed_trades = list(ledger.closed_trades())
        orders_by_client_id = {str(order.get("clientOrderId")): order for order in ledger.orders()}
    except Exception:
        return []

    observations: list[dict[str, Any]] = []
    for trade in sorted(closed_trades, key=lambda row: str(row.get("closedAt") or "")):
        observations.extend(
            _observations_for_trade(
                trade=trade,
                orders_by_client_id=orders_by_client_id,
                store=store,
                sample_window=sample_window,
            )
        )
    return observations[-limit:] if limit and len(observations) > limit else observations


def _observations_for_trade(
    *,
    trade: Mapping[str, Any],
    orders_by_client_id: Mapping[str, Mapping[str, Any]],
    store: Any,
    sample_window: str,
) -> list[dict[str, Any]]:
    # A closed trade is stamped with the *closing* fill: its clientOrderId is the exit
    # order and its side is the closing side. Entry risk and the direction the ensemble
    # actually took both come from the entry order, so resolve that one.
    order = orders_by_client_id.get(str(trade.get("entryOrderId") or ""))
    if order is None:
        return []

    risk_dollars = _risk_dollars(order)
    if risk_dollars is None:
        return []

    realized_pnl = _number(trade.get("realizedPnl"))
    closed_at = _timestamp(trade.get("closedAt"))
    traded_direction = _direction(order.get("side"))
    if realized_pnl is None or closed_at is None or traded_direction is None:
        return []

    decision = _decision_for(store, str(order.get("decisionId") or ""))
    if not decision:
        return []
    scope = decision.get("reliability_scope") or {}
    if not isinstance(scope, Mapping) or not scope.get("regime"):
        return []

    decision_timestamp = _timestamp(decision.get("data_timestamp") or decision.get("evaluated_at")) or closed_at
    outcome_r = realized_pnl / risk_dollars
    cost_r = _cost_r(trade, order, risk_dollars)

    observations: list[dict[str, Any]] = []
    for vote in _votes(decision):
        direction = _direction(vote.get("signal"))
        strategy_id = _strategy_id(vote)
        if direction is None or not strategy_id:
            continue
        agreed = direction == traded_direction
        observations.append(
            {
                "algorithmId": "voting_ensemble",
                "strategyId": strategy_id,
                "direction": direction,
                "regime": str(scope.get("regime")),
                "sessionSegment": str(scope.get("sessionSegment") or "regular_session"),
                "volatilityState": str(scope.get("volatilityState") or "normal"),
                "sampleWindow": sample_window,
                "outcomeR": round(outcome_r if agreed else -outcome_r, 6),
                "transactionCostR": round(cost_r, 6),
                "decisionTimestamp": decision_timestamp.isoformat().replace("+00:00", "Z"),
                "completedAt": closed_at.isoformat().replace("+00:00", "Z"),
                "source": "paper_trade",
            }
        )
    return observations


def _decision_for(store: Any, decision_id: str) -> dict[str, Any]:
    if not decision_id:
        return {}
    try:
        record = store.read_snapshot(f"{_DECISION_KEY_PREFIX}{decision_id}")
    except Exception:
        return {}
    decision = record.get("decision") if isinstance(record, Mapping) else None
    return dict(decision) if isinstance(decision, Mapping) else {}


def _votes(decision: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    votes = decision.get("votes")
    if not isinstance(votes, list):
        return ()
    return [vote for vote in votes if isinstance(vote, Mapping)]


def _strategy_id(vote: Mapping[str, Any]) -> str:
    features = vote.get("features")
    if isinstance(features, Mapping):
        candidate = features.get("strategyId")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    candidate = vote.get("strategyId") or vote.get("strategy")
    return str(candidate).strip() if candidate else ""


def _risk_dollars(order: Mapping[str, Any]) -> float | None:
    """Risk at entry, in dollars. Returns None when it cannot be reconstructed."""
    planned = _number(order.get("plannedRiskDollars"))
    if planned is not None and planned > 0:
        return planned
    entry = _number(order.get("entryPrice"))
    stop = _number(order.get("stopPrice"))
    quantity = _number(order.get("quantity"))
    if entry is None or stop is None or quantity is None or quantity <= 0:
        return None
    risk = abs(entry - stop) * quantity
    return risk if risk > 0 else None


def _cost_r(trade: Mapping[str, Any], order: Mapping[str, Any], risk_dollars: float) -> float:
    for source in (trade, order):
        costs = source.get("costs")
        if isinstance(costs, Mapping):
            total = sum(value for value in (_number(item) for item in costs.values()) if value is not None)
            return total / risk_dollars
        total = _number(source.get("totalCosts") or source.get("transactionCosts"))
        if total is not None:
            return total / risk_dollars
    return 0.0


def _direction(value: Any) -> str | None:
    if value is None:
        return None
    return _SIDE_TO_DIRECTION.get(str(value).strip().lower())


def _number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "VOTING_ENSEMBLE_RELIABILITY_FEED_VERSION",
    "build_reliability_observations",
]

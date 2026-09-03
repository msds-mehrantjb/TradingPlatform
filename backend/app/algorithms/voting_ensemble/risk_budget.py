from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from math import floor
from typing import Any


VOTING_ENSEMBLE_RISK_BUDGET_VERSION = "voting_ensemble_risk_budget_v2"
VOTING_ENSEMBLE_VOTE_EDGE_SIZING_VERSION = "voting_ensemble_vote_edge_sizing_v2"


@dataclass(frozen=True)
class VotingEnsembleSizingCap:
    cap_id: str
    quantity: int
    basis: float
    reason_codes: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class VotingEnsembleRiskBudget:
    quantity: int
    planned_risk: float
    sizing_mode: str
    risk_budget: float
    order_limit: float
    vote_edge: float | None
    vote_edge_multiplier: float
    caps: tuple[VotingEnsembleSizingCap, ...]
    selected_cap_ids: tuple[str, ...]
    minimum_tradable_size: int
    reason_codes: tuple[str, ...]
    configuration_hash: str
    risk_budget_version: str = VOTING_ENSEMBLE_RISK_BUDGET_VERSION

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def resolve_voting_ensemble_risk_budget(
    config: dict[str, Any],
    *,
    equity: float,
    entry_price: float,
    stop_distance: float,
) -> VotingEnsembleRiskBudget:
    candidate_signal = str(config.get("candidateSignal") or config.get("signal") or "").upper()
    gates_passed = bool(config.get("gatesPassed", True))
    net_edge_passed = bool(config.get("netEdgePassed", True))
    profile_allows_entries = not bool(config.get("entriesBlocked")) and bool(config.get("profileAllowsEntries", True))
    minimum_tradable_size = max(1, int(_number(config, "minimumTradableSize", 1.0)))
    vote_edge = _vote_edge(config)
    vote_edge_multiplier = _vote_edge_size_multiplier(vote_edge, config)
    vote_edge_reason_codes = _vote_edge_reason_codes(vote_edge, vote_edge_multiplier)
    invalid_reasons = _zero_quantity_reasons(
        config=config,
        equity=equity,
        entry_price=entry_price,
        stop_distance=stop_distance,
        candidate_signal=candidate_signal,
        gates_passed=gates_passed,
        net_edge_passed=net_edge_passed,
        profile_allows_entries=profile_allows_entries,
        vote_edge=vote_edge,
        vote_edge_multiplier=vote_edge_multiplier,
    )
    if invalid_reasons:
        return _budget(
            quantity=0,
            planned_risk=0.0,
            sizing_mode="blocked",
            risk_budget=0.0,
            order_limit=0.0,
            vote_edge=vote_edge,
            vote_edge_multiplier=vote_edge_multiplier,
            caps=(),
            selected_cap_ids=(),
            minimum_tradable_size=minimum_tradable_size,
            reason_codes=tuple([*invalid_reasons, *vote_edge_reason_codes]),
            config=config,
            equity=equity,
            entry_price=entry_price,
            stop_distance=stop_distance,
        )

    dynamic_cap = min(
        _fraction(config, "dynamicRiskCap", 1.0),
        _fraction(config, "eventRiskCap", 1.0),
        _fraction(config, "drawdownCap", 1.0),
        _fraction(config, "liquidityCap", 1.0),
        _fraction(config, "sessionCap", 1.0),
        _fraction(config, "regimeFit", 1.0),
        _family_support_multiplier(config),
    )
    risk_budget = equity * (_percent(config, "riskPerTradePercent", 0.5) / 100.0) * dynamic_cap * vote_edge_multiplier
    # The day's remaining loss budget bounds the risk of one more trade. This is what
    # lets the daily-loss limit, rather than a trade count, govern the day: as losses
    # and open risk accumulate, each new position is sized to what is left.
    daily_budget = _number(config, "remainingDailyLossBudgetDollars", None)
    daily_budget_bound = daily_budget is not None and risk_budget > float(daily_budget)
    if daily_budget_bound:
        risk_budget = max(0.0, float(daily_budget))
    order_limit = min(
        equity * (_percent(config, "orderAllocationPercent", 10.0) / 100.0),
        equity * (_percent(config, "dailyAllocationPercent", 30.0) / 100.0),
        _number(config, "availableBuyingPower", equity),
        _number(config, "buyingPower", equity),
    ) * dynamic_cap
    caps = _sizing_caps(
        config=config,
        equity=equity,
        entry_price=entry_price,
        stop_distance=stop_distance,
        risk_budget=risk_budget,
        order_limit=order_limit,
    )
    quantity = min((cap.quantity for cap in caps), default=0)
    selected_cap_ids = tuple(cap.cap_id for cap in caps if cap.quantity == quantity)
    reason_codes = [
        "voting_ensemble.risk_budget.authoritative_sizing",
        f"voting_ensemble.risk_budget.dynamic_cap:{dynamic_cap:.4f}",
        *vote_edge_reason_codes,
    ]
    if daily_budget_bound:
        reason_codes.append(
            "voting_ensemble.risk_budget.daily_loss_budget_exhausted"
            if risk_budget <= 0
            else f"voting_ensemble.risk_budget.daily_loss_budget_bound:{risk_budget:.2f}"
        )
    if risk_budget <= 0:
        reason_codes.append("voting_ensemble.risk_budget.zero_risk_budget")
        quantity = 0
    if quantity < minimum_tradable_size:
        reason_codes.append("voting_ensemble.risk_budget.below_minimum_tradable_size")
        quantity = 0
    for cap in caps:
        reason_codes.extend(cap.reason_codes)
    return _budget(
        quantity=max(0, quantity),
        planned_risk=round(max(0, quantity) * stop_distance * max(_number(config, "contractMultiplier", 1.0), 1e-9), 6),
        sizing_mode="minimum_cap",
        risk_budget=round(max(0.0, risk_budget), 6),
        order_limit=round(max(0.0, order_limit), 6),
        vote_edge=vote_edge,
        vote_edge_multiplier=vote_edge_multiplier,
        caps=caps,
        selected_cap_ids=selected_cap_ids,
        minimum_tradable_size=minimum_tradable_size,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        config=config,
        equity=equity,
        entry_price=entry_price,
        stop_distance=stop_distance,
    )


def position_size_for_config(
    config: dict[str, Any],
    *,
    equity: float,
    entry_price: float,
    stop_distance: float,
) -> tuple[int, float, str]:
    budget = resolve_voting_ensemble_risk_budget(
        config,
        equity=equity,
        entry_price=entry_price,
        stop_distance=stop_distance,
    )
    return budget.quantity, budget.planned_risk, budget.sizing_mode


def _sizing_caps(
    *,
    config: dict[str, Any],
    equity: float,
    entry_price: float,
    stop_distance: float,
    risk_budget: float,
    order_limit: float,
) -> tuple[VotingEnsembleSizingCap, ...]:
    buying_power = min(_number(config, "availableBuyingPower", equity), _number(config, "buyingPower", equity))
    position_notional_cap = equity * (_percent(config, "maximumPositionPercent", 50.0) / 100.0)
    available_equity_cap = min(equity, buying_power)
    global_exposure = _number(config, "globalExposureAllowanceDollars", position_notional_cap)
    local_exposure = _number(config, "localExposureAllowanceDollars", position_notional_cap)
    fillable = _number(config, "availableFillableQuantity", _number(config, "liquidityShares", 0.0))
    current_volume = max(_number(config, "currentOneMinuteVolume", 0.0), _number(config, "volumeCurrent", 0.0), 0.0)
    participation_shares = current_volume * (_percent(config, "maximumVolumeParticipationPercent", 1.0) / 100.0)
    profile_max = _number(config, "profileMaximumShares", _number(config, "maximumShares", _number(config, "maxShareQuantity", 0.0)))
    # Dollars per point of price. A share moves one dollar per dollar; an MES contract moves
    # five and an MNQ two. Every cap below turns a dollar budget into a quantity, so the
    # multiplier belongs exactly at those divisions and nowhere else -- sizing a future
    # through the share arithmetic returns a plausible number wrong by this factor.
    multiplier = max(_number(config, "contractMultiplier", 1.0), 1e-9)
    notional_per_unit = entry_price * multiplier
    risk_per_unit = stop_distance * multiplier
    return (
        _cap("risk_based_shares", risk_budget / risk_per_unit, risk_budget, "voting_ensemble.risk_budget.cap.risk_based", "Risk budget divided by initial stop distance."),
        _cap("position_notional_cap_shares", position_notional_cap / notional_per_unit, position_notional_cap, "voting_ensemble.risk_budget.cap.position_notional", "Voting Ensemble position/notional cap."),
        _cap("available_equity_buying_power_shares", available_equity_cap / notional_per_unit, available_equity_cap, "voting_ensemble.risk_budget.cap.buying_power", "Available equity and buying power cap."),
        _cap("liquidity_based_shares", fillable, fillable, "voting_ensemble.risk_budget.cap.liquidity", "Point-in-time displayed fillable quantity cap."),
        _cap("participation_rate_shares", participation_shares, participation_shares, "voting_ensemble.risk_budget.cap.participation", "Configured maximum participation rate cap."),
        _cap("profile_maximum_shares", profile_max, profile_max, "voting_ensemble.risk_budget.cap.profile_maximum_shares", "Resolved profile maximum shares cap."),
        _cap("global_exposure_allowance_shares", global_exposure / notional_per_unit, global_exposure, "voting_ensemble.risk_budget.cap.global_exposure", "Read-only global exposure allowance cap."),
        _cap("local_exposure_allowance_shares", local_exposure / notional_per_unit, local_exposure, "voting_ensemble.risk_budget.cap.local_exposure", "Voting Ensemble local exposure allowance cap."),
        _cap("order_allocation_shares", order_limit / notional_per_unit, order_limit, "voting_ensemble.risk_budget.cap.order_allocation", "Resolved order allocation cap."),
    )


def _zero_quantity_reasons(
    *,
    config: dict[str, Any],
    equity: float,
    entry_price: float,
    stop_distance: float,
    candidate_signal: str,
    gates_passed: bool,
    net_edge_passed: bool,
    profile_allows_entries: bool,
    vote_edge: float | None,
    vote_edge_multiplier: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if candidate_signal in {"", "HOLD"}:
        reasons.append("voting_ensemble.risk_budget.hold_candidate")
    if not gates_passed:
        reasons.append("voting_ensemble.risk_budget.gates_failed")
    if not net_edge_passed:
        reasons.append("voting_ensemble.risk_budget.net_edge_failed")
    if not profile_allows_entries:
        reasons.append("voting_ensemble.risk_budget.entries_blocked_by_profile")
    if stop_distance <= 0 or entry_price <= 0 or equity <= 0:
        reasons.append("voting_ensemble.risk_budget.invalid_inputs")
    if _number(config, "riskPerTradePercent", 0.5) <= 0:
        reasons.append("voting_ensemble.risk_budget.zero_risk_budget")
    if vote_edge is not None and vote_edge_multiplier <= 0.0:
        reasons.append("voting_ensemble.risk_budget.vote_edge_below_minimum")
    return tuple(reasons)


def _budget(
    *,
    quantity: int,
    planned_risk: float,
    sizing_mode: str,
    risk_budget: float,
    order_limit: float,
    vote_edge: float | None,
    vote_edge_multiplier: float,
    caps: tuple[VotingEnsembleSizingCap, ...],
    selected_cap_ids: tuple[str, ...],
    minimum_tradable_size: int,
    reason_codes: tuple[str, ...],
    config: dict[str, Any],
    equity: float,
    entry_price: float,
    stop_distance: float,
) -> VotingEnsembleRiskBudget:
    payload = {
        "version": VOTING_ENSEMBLE_RISK_BUDGET_VERSION,
        "quantity": quantity,
        "plannedRisk": planned_risk,
        "riskBudget": risk_budget,
        "orderLimit": order_limit,
        "voteEdge": vote_edge,
        "voteEdgeMultiplier": vote_edge_multiplier,
        "caps": [asdict(cap) for cap in caps],
        "selectedCapIds": selected_cap_ids,
        "minimumTradableSize": minimum_tradable_size,
        "reasonCodes": reason_codes,
        "equity": equity,
        "entryPrice": entry_price,
        "stopDistance": stop_distance,
        "configHashInputs": {
            key: config.get(key)
            for key in sorted(config)
            if key
            in {
                "candidateSignal",
                "entriesBlocked",
                "riskPerTradePercent",
                "orderAllocationPercent",
                "dailyAllocationPercent",
                "maximumPositionPercent",
                "profileMaximumShares",
                "maximumVolumeParticipationPercent",
                "globalExposureAllowanceDollars",
                "localExposureAllowanceDollars",
                "voteEdge",
                "regimeFit",
                "dynamicRiskCap",
                "eventRiskCap",
                "drawdownCap",
                "liquidityCap",
                "sessionCap",
                "contractMultiplier",
            }
        },
    }
    return VotingEnsembleRiskBudget(
        quantity=quantity,
        planned_risk=planned_risk,
        sizing_mode=sizing_mode,
        risk_budget=risk_budget,
        order_limit=order_limit,
        vote_edge=vote_edge,
        vote_edge_multiplier=vote_edge_multiplier,
        caps=caps,
        selected_cap_ids=selected_cap_ids,
        minimum_tradable_size=minimum_tradable_size,
        reason_codes=reason_codes,
        configuration_hash=hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16],
    )


def _cap(cap_id: str, raw_quantity: float, basis: float, reason_code: str, explanation: str) -> VotingEnsembleSizingCap:
    return VotingEnsembleSizingCap(
        cap_id=cap_id,
        quantity=max(0, floor(raw_quantity)),
        basis=round(max(0.0, basis), 6),
        reason_codes=(reason_code,),
        explanation=explanation,
    )


def _number(config: dict[str, Any], key: str, default: float) -> float:
    try:
        value = config.get(key, default)
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _percent(config: dict[str, Any], key: str, default: float) -> float:
    return max(0.0, min(100.0, _number(config, key, default)))


def _fraction(config: dict[str, Any], key: str, default: float) -> float:
    return max(0.0, min(1.0, _number(config, key, default)))


def _family_support_multiplier(config: dict[str, Any]) -> float:
    support = _number(config, "independentFamilySupport", 0.0)
    required = max(1.0, _number(config, "minimumIndependentFamilySupport", 1.0))
    return max(0.0, min(1.0, support / required))


def _vote_edge(config: dict[str, Any]) -> float | None:
    nested = []
    for key in ("voteSummary", "ensembleDecision", "decision", "voting"):
        value = config.get(key)
        if isinstance(value, dict):
            nested.append(value)
    for payload in (config, *nested):
        for key in (
            "voteEdge",
            "winnerEdge",
            "edge",
            "voteStrength",
            "finalScore",
            "contextAdjustedScore",
            "baseScore",
            "confidence",
        ):
            if key not in payload:
                continue
            edge = _optional_abs_float(payload.get(key))
            if edge is not None:
                return max(0.0, min(1.0, edge))
    return None


def _vote_edge_size_multiplier(vote_edge: float | None, config: dict[str, Any]) -> float:
    if vote_edge is None:
        return 1.0
    minimum = _fraction(config, "minimumVoteEdgeForSizing", 0.20)
    low = _fraction(config, "lowVoteEdgeThreshold", 0.30)
    medium = _fraction(config, "mediumVoteEdgeThreshold", 0.45)
    full = _fraction(config, "fullVoteEdgeThreshold", 0.60)
    low_multiplier = _fraction(config, "lowVoteEdgeMultiplier", 0.25)
    medium_multiplier = _fraction(config, "mediumVoteEdgeMultiplier", 0.50)
    high_multiplier = _fraction(config, "highVoteEdgeMultiplier", 0.75)
    if vote_edge >= full:
        return 1.0
    if vote_edge >= medium:
        return high_multiplier
    if vote_edge >= low:
        return medium_multiplier
    if vote_edge >= minimum:
        return low_multiplier
    return 0.0


def _vote_edge_reason_codes(vote_edge: float | None, multiplier: float) -> tuple[str, ...]:
    if vote_edge is None:
        return ()
    return (
        VOTING_ENSEMBLE_VOTE_EDGE_SIZING_VERSION,
        f"voting_ensemble.vote_edge.multiplier:{multiplier:.2f}",
    )


def _optional_abs_float(value: Any) -> float | None:
    try:
        parsed = abs(float(value))
    except (TypeError, ValueError):
        return None
    return parsed

"""Turns shadow strategy activity into the evidence the lifecycle gate demands.

A shadow strategy runs on every bar but never votes, so it never owns a trade and its
`trade_count` in any production result is structurally zero. Promotion evidence therefore
has to come from a counterfactual: what this strategy *would* have traded on its own.

Two sources feed that, and they are deliberately kept separate:

* **Recorded live shadow observations.** Every evaluation of a shadow strategy is
  appended to its own key so the record survives a restart. Before this existed the only
  trace was an in-memory counter on the runtime supervisor's metrics.
* **A counterfactual trade simulation** over candles, reusing the backtest engine's
  execution cost model so entries, exits and costs are priced the same way a real
  backtest would price them. It does not touch the live decision path: nothing here can
  make a shadow strategy vote.

**Unavailable evidence fails closed.** Two of the gate's metrics -- `paper_shadow_stability`
and `paper_backtest_divergence` -- compare live shadow behaviour against a backtest, and
cannot be honestly computed until live shadow data has actually accumulated. They are
emitted as the value that *fails* their gate, never as a neutral or optimistic default, and
the missing metrics are named in the result so a caller can see exactly what is outstanding.
An unvalidated strategy staying in shadow is the safe outcome; a plausible-looking number
that lets it onto capital is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean, pstdev
from typing import Any, Iterable, Mapping, Sequence

from backend.app.algorithms.weighted_voting.backtest.execution_simulator import (
    WeightedBacktestExecutionCostModel,
    entry_fee,
    exit_fee,
)
from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS,
    WEIGHTED_VOTING_SHADOW_STRATEGY_IDS,
    weighted_voting_catalog_entry,
)
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.models import WeightedSide
from backend.app.algorithms.weighted_voting.strategy_lifecycle import (
    WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW,
    WeightedVotingStrategyLifecycleEvidence,
)


WEIGHTED_VOTING_SHADOW_EVIDENCE_VERSION = "weighted_voting_shadow_evidence_v1"
WEIGHTED_VOTING_SHADOW_OBSERVATION_PREFIX = "weighted_voting.strategies."
WEIGHTED_VOTING_SHADOW_OBSERVATION_SUFFIX = ".shadow_performance"

# How many observations a strategy's key retains. One regular session is ~390 one-minute
# bars, so this holds roughly a quarter of shadow sessions -- comfortably more than the
# 120 eligible opportunities and 40 completed trades promotion asks for.
WEIGHTED_VOTING_SHADOW_OBSERVATION_LIMIT = 25_000

# Exit rules for the counterfactual. A shadow strategy publishes an invalidation level and
# nothing else, so the simulation has to supply the rest of the trade management. These
# mirror the shape of the live exit policy rather than trying to reproduce it exactly, and
# they are recorded on every simulated trade so the assumption is visible in the evidence.
WEIGHTED_VOTING_SHADOW_TARGET_R_MULTIPLE = 2.0
WEIGHTED_VOTING_SHADOW_TIME_STOP_MINUTES = 120

# Fail-closed values for evidence that cannot be computed. Each one is the value that makes
# its own gate fail, so a missing metric can never read as a pass.
WEIGHTED_VOTING_SHADOW_UNAVAILABLE_STABILITY = 0.0
WEIGHTED_VOTING_SHADOW_UNAVAILABLE_DIVERGENCE = 1.0


def shadow_observation_key(strategy_id: str) -> str:
    """The key a strategy's own shadow record lives under."""
    return f"{WEIGHTED_VOTING_SHADOW_OBSERVATION_PREFIX}{strategy_id}{WEIGHTED_VOTING_SHADOW_OBSERVATION_SUFFIX}"


@dataclass(frozen=True)
class WeightedVotingShadowObservation:
    """One evaluation of one shadow strategy, as it happened."""

    algorithm_id: str
    strategy_id: str
    data_timestamp: datetime
    signal: str
    directional_confidence: float
    expected_return: float
    data_ready: bool
    data_quality_status: str
    invalidation_level: float | None
    reference_close: float | None
    session_label: str
    regime_label: str
    errored: bool = False
    observation_version: str = WEIGHTED_VOTING_SHADOW_EVIDENCE_VERSION

    @property
    def directional(self) -> bool:
        return self.signal in (WeightedSide.BUY.value, WeightedSide.SELL.value)

    @property
    def tradable(self) -> bool:
        """A directional call the counterfactual can actually open a position from."""
        return (
            self.directional
            and self.data_ready
            and not self.errored
            and self.invalidation_level is not None
            and self.reference_close is not None
            and self.reference_close > 0
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "strategyId": self.strategy_id,
            "dataTimestamp": self.data_timestamp.isoformat(),
            "signal": self.signal,
            "directionalConfidence": self.directional_confidence,
            "expectedReturn": self.expected_return,
            "dataReady": self.data_ready,
            "dataQualityStatus": self.data_quality_status,
            "invalidationLevel": self.invalidation_level,
            "referenceClose": self.reference_close,
            "sessionLabel": self.session_label,
            "regimeLabel": self.regime_label,
            "errored": self.errored,
            "observationVersion": self.observation_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WeightedVotingShadowObservation":
        return cls(
            algorithm_id=str(payload.get("algorithmId") or WEIGHTED_VOTING_ALGORITHM_ID),
            strategy_id=str(payload["strategyId"]),
            data_timestamp=_utc(payload["dataTimestamp"]),
            signal=str(payload.get("signal") or WeightedSide.HOLD.value),
            directional_confidence=float(payload.get("directionalConfidence") or 0.0),
            expected_return=float(payload.get("expectedReturn") or 0.0),
            data_ready=bool(payload.get("dataReady")),
            data_quality_status=str(payload.get("dataQualityStatus") or "unavailable"),
            invalidation_level=_optional_float(payload.get("invalidationLevel")),
            reference_close=_optional_float(payload.get("referenceClose")),
            session_label=str(payload.get("sessionLabel") or "regular"),
            regime_label=str(payload.get("regimeLabel") or "unknown"),
            errored=bool(payload.get("errored")),
        )


@dataclass(frozen=True)
class WeightedVotingShadowTrade:
    """A trade the counterfactual says this strategy would have taken alone."""

    strategy_id: str
    side: str
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    quantity: int
    risk_per_share: float
    gross_pnl: float
    total_costs: float
    net_pnl: float
    favorable_excursion: float
    adverse_excursion: float
    holding_minutes: float
    exit_reason: str
    session_label: str
    regime_label: str

    @property
    def r_multiple(self) -> float:
        """Net result as a multiple of the risk that was on at entry.

        Normalising by risk is what makes trades with different stop distances comparable;
        an unnormalised P&L would let one wide-stop winner dominate the expectancy.
        """
        risk = self.risk_per_share * max(1, self.quantity)
        return self.net_pnl / risk if risk > 0 else 0.0


@dataclass(frozen=True)
class WeightedVotingShadowEvidenceResult:
    """Evidence for one strategy, plus what could not be established."""

    strategy_id: str
    evidence: WeightedVotingStrategyLifecycleEvidence
    trades: tuple[WeightedVotingShadowTrade, ...]
    observation_count: int
    unavailable_metrics: tuple[str, ...]
    reason_codes: tuple[str, ...]
    explanation: str

    @property
    def complete(self) -> bool:
        """Whether every metric the gate reads was actually computed."""
        return not self.unavailable_metrics


# --------------------------------------------------------------------------- recording


def _read(signal: Any, snake: str, camel: str, default: Any = None) -> Any:
    """Read a field from a signal model or from the dict form the runtime passes around."""
    if isinstance(signal, Mapping):
        if snake in signal:
            return signal[snake]
        return signal.get(camel, default)
    return getattr(signal, snake, default)


def observation_from_signal(
    signal: Any,
    *,
    session_label: str = "regular",
    regime_label: str = "unknown",
    reference_close: float | None = None,
) -> WeightedVotingShadowObservation:
    """Capture one shadow signal as a durable observation."""
    feature_snapshot = _read(signal, "feature_snapshot", "featureSnapshot") or {}
    close = reference_close
    if close is None and isinstance(feature_snapshot, Mapping):
        close = _optional_float(feature_snapshot.get("latest_close"))
    return WeightedVotingShadowObservation(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        strategy_id=str(_read(signal, "strategy_id", "strategyId", "")),
        data_timestamp=_utc(_read(signal, "data_timestamp", "dataTimestamp")),
        signal=_side_value(_read(signal, "signal", "signal", WeightedSide.HOLD)),
        directional_confidence=float(_read(signal, "directional_confidence", "directionalConfidence", 0.0) or 0.0),
        expected_return=float(_read(signal, "expected_return_after_costs", "expectedReturnAfterCosts", 0.0) or 0.0),
        data_ready=bool(_read(signal, "data_ready", "dataReady", False)),
        data_quality_status=str(_read(signal, "data_quality_status", "dataQualityStatus", "unavailable")),
        invalidation_level=_optional_float(_read(signal, "invalidation_level", "invalidationLevel")),
        reference_close=close,
        session_label=session_label,
        regime_label=regime_label,
        errored=_signal_errored(signal),
    )


def record_shadow_observations(
    store: Any,
    signals: Iterable[Any],
    *,
    session_label: str = "regular",
    regime_label: str = "unknown",
    limit: int = WEIGHTED_VOTING_SHADOW_OBSERVATION_LIMIT,
) -> dict[str, int]:
    """Append this evaluation's signals to each catalogued strategy's own record.

    Anything outside the catalogue is not ours to write. Recording is best-effort per
    strategy so a storage failure for one cannot stop an evaluation or lose the others:
    losing an observation costs evidence, but failing an evaluation costs a decision.
    """
    # Active strategies are recorded too, and only for one reason: the promotion gate
    # caps how correlated a shadow candidate may be with a strategy that already votes,
    # and that comparison needs both series. Their weights and trades are untouched.
    recordable = set(WEIGHTED_VOTING_SHADOW_STRATEGY_IDS) | set(WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS)
    written: dict[str, int] = {}
    for signal in signals:
        strategy_id = str(_read(signal, "strategy_id", "strategyId", ""))
        if strategy_id not in recordable:
            continue
        observation = observation_from_signal(signal, session_label=session_label, regime_label=regime_label)
        try:
            existing = load_shadow_observations(store, strategy_id)
            retained = [*existing, observation][-max(1, limit) :]
            store.write_snapshot(
                shadow_observation_key(strategy_id),
                {
                    "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                    "strategyId": strategy_id,
                    "observationVersion": WEIGHTED_VOTING_SHADOW_EVIDENCE_VERSION,
                    "observations": [item.as_dict() for item in retained],
                },
            )
            written[strategy_id] = len(retained)
        except Exception:
            continue
    return written


def load_shadow_observations(store: Any, strategy_id: str) -> list[WeightedVotingShadowObservation]:
    """Every recorded observation for a strategy, oldest first."""
    try:
        payload = store.read_snapshot(shadow_observation_key(strategy_id))
    except KeyError:
        return []
    except Exception:
        return []
    rows = payload.get("observations") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return []
    observations: list[WeightedVotingShadowObservation] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            observations.append(WeightedVotingShadowObservation.from_dict(row))
        except Exception:
            continue
    return observations


# ------------------------------------------------------------------------- simulation


def simulate_shadow_trades(
    observations: Sequence[WeightedVotingShadowObservation],
    candles: Sequence[Any],
    *,
    strategy_id: str,
    cost_model: WeightedBacktestExecutionCostModel | None = None,
    quantity: int = 100,
    target_r_multiple: float = WEIGHTED_VOTING_SHADOW_TARGET_R_MULTIPLE,
    time_stop_minutes: int = WEIGHTED_VOTING_SHADOW_TIME_STOP_MINUTES,
) -> tuple[WeightedVotingShadowTrade, ...]:
    """What this strategy would have traded on its own, priced like the backtest prices it.

    One position at a time, entered at the close of the signalling bar and exited on the
    first of: the published invalidation level, a target at a fixed R multiple, or the time
    stop. Stop and target are checked against the same bar's range, and a bar that spans
    both is resolved as the stop -- the pessimistic reading, because intrabar order is
    unknowable from one-minute OHLC and assuming the favourable one inflates every metric
    downstream.
    """
    model = cost_model or WeightedBacktestExecutionCostModel()
    by_timestamp = {_utc(candle.timestamp): candle for candle in candles}
    ordered = sorted(by_timestamp)
    index_of = {timestamp: index for index, timestamp in enumerate(ordered)}
    trades: list[WeightedVotingShadowTrade] = []
    busy_until: datetime | None = None

    for observation in observations:
        if observation.strategy_id != strategy_id or not observation.tradable:
            continue
        entry_time = observation.data_timestamp
        if busy_until is not None and entry_time <= busy_until:
            continue
        start = index_of.get(entry_time)
        if start is None or start + 1 >= len(ordered):
            continue

        entry_price = float(observation.reference_close or 0.0)
        stop_price = float(observation.invalidation_level or 0.0)
        is_long = observation.signal == WeightedSide.BUY.value
        risk_per_share = (entry_price - stop_price) if is_long else (stop_price - entry_price)
        if risk_per_share <= 0:
            # The published invalidation level sits the wrong side of entry, so there is no
            # coherent risk to normalise by. Skipping beats inventing a stop distance.
            continue
        target_price = entry_price + risk_per_share * target_r_multiple if is_long else entry_price - risk_per_share * target_r_multiple

        trade = _simulate_one(
            observation=observation,
            ordered=ordered,
            by_timestamp=by_timestamp,
            start=start,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            risk_per_share=risk_per_share,
            is_long=is_long,
            quantity=max(1, quantity),
            model=model,
            time_stop_minutes=time_stop_minutes,
        )
        if trade is None:
            continue
        trades.append(trade)
        busy_until = trade.exit_timestamp
    return tuple(trades)


def _simulate_one(
    *,
    observation: WeightedVotingShadowObservation,
    ordered: list[datetime],
    by_timestamp: dict[datetime, Any],
    start: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
    risk_per_share: float,
    is_long: bool,
    quantity: int,
    model: WeightedBacktestExecutionCostModel,
    time_stop_minutes: int,
) -> WeightedVotingShadowTrade | None:
    slip = model.entry_slippage_per_share
    filled_entry = entry_price + slip if is_long else entry_price - slip
    best = 0.0
    worst = 0.0
    for index in range(start + 1, len(ordered)):
        timestamp = ordered[index]
        candle = by_timestamp[timestamp]
        high = float(candle.high)
        low = float(candle.low)
        favourable = (high - filled_entry) if is_long else (filled_entry - low)
        adverse = (filled_entry - low) if is_long else (high - filled_entry)
        best = max(best, favourable)
        worst = max(worst, adverse)

        stop_hit = low <= stop_price if is_long else high >= stop_price
        target_hit = high >= target_price if is_long else low <= target_price
        elapsed = (timestamp - observation.data_timestamp).total_seconds() / 60.0
        if stop_hit:
            return _close(observation, filled_entry, stop_price, timestamp, "stop", is_long, quantity, risk_per_share, model, best, worst, elapsed)
        if target_hit:
            return _close(observation, filled_entry, target_price, timestamp, "target", is_long, quantity, risk_per_share, model, best, worst, elapsed)
        if elapsed >= time_stop_minutes:
            return _close(observation, filled_entry, float(candle.close), timestamp, "time_stop", is_long, quantity, risk_per_share, model, best, worst, elapsed)

    # The candle series ended while the position was open. An unresolved trade is not a
    # result, so it is dropped rather than marked to the last close as if it had exited.
    return None


def _close(
    observation: WeightedVotingShadowObservation,
    entry_price: float,
    raw_exit: float,
    exit_timestamp: datetime,
    exit_reason: str,
    is_long: bool,
    quantity: int,
    risk_per_share: float,
    model: WeightedBacktestExecutionCostModel,
    favorable: float,
    adverse: float,
    holding_minutes: float,
) -> WeightedVotingShadowTrade:
    exit_price = raw_exit - model.exit_slippage_per_share if is_long else raw_exit + model.exit_slippage_per_share
    gross = (exit_price - entry_price) * quantity if is_long else (entry_price - exit_price) * quantity
    costs = entry_fee(quantity, model) + exit_fee(quantity, model)
    return WeightedVotingShadowTrade(
        strategy_id=observation.strategy_id,
        side=observation.signal,
        entry_timestamp=observation.data_timestamp,
        exit_timestamp=exit_timestamp,
        entry_price=round(entry_price, 10),
        exit_price=round(exit_price, 10),
        stop_price=float(observation.invalidation_level or 0.0),
        quantity=quantity,
        risk_per_share=round(risk_per_share, 10),
        gross_pnl=round(gross, 10),
        total_costs=round(costs, 10),
        net_pnl=round(gross - costs, 10),
        favorable_excursion=round(favorable, 10),
        adverse_excursion=round(adverse, 10),
        holding_minutes=round(holding_minutes, 4),
        exit_reason=exit_reason,
        session_label=observation.session_label,
        regime_label=observation.regime_label,
    )


def _utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError(f"expected a timestamp, got {type(value)!r}")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _side_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _signal_errored(signal: Any) -> bool:
    codes = tuple(_read(signal, "reason_codes", "reasonCodes", ()) or ())
    return any("error" in str(code) or "exception" in str(code) for code in codes)


# --------------------------------------------------------------------------- evidence

# Notional equity the drawdown fraction is measured against. The gate compares drawdown to
# 0.08, a fraction, so the simulated currency drawdown needs a denominator; this matches the
# backtest engine's default account equity so the two are read on the same scale.
WEIGHTED_VOTING_SHADOW_NOTIONAL_EQUITY = 100_000.0
WEIGHTED_VOTING_SHADOW_WALK_FORWARD_FOLDS = 4
WEIGHTED_VOTING_SHADOW_HOLDOUT_FRACTION = 0.25
WEIGHTED_VOTING_SHADOW_SEVERE_TAIL_LOSS_R = -3.0

# Named so a caller can see which evidence is outstanding rather than inferring it from a
# gate failure that looks the same as a genuine rejection.
WEIGHTED_VOTING_SHADOW_LIVE_ONLY_METRICS = ("paper_shadow_stability", "paper_backtest_divergence")


def build_shadow_evidence(
    strategy_id: str,
    *,
    observations: Sequence[WeightedVotingShadowObservation],
    candles: Sequence[Any],
    peer_observations: Mapping[str, Sequence[WeightedVotingShadowObservation]] | None = None,
    evaluated_at: datetime | None = None,
    workflow: str = WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW,
    after_market_session_complete: bool = True,
    cost_model: WeightedBacktestExecutionCostModel | None = None,
    notional_equity: float = WEIGHTED_VOTING_SHADOW_NOTIONAL_EQUITY,
) -> WeightedVotingShadowEvidenceResult:
    """Assemble a lifecycle evidence record for one shadow strategy.

    Everything derivable from the strategy's own recorded behaviour is computed here. What
    is not derivable is emitted at the value that fails its gate and named in
    ``unavailable_metrics``, so a promotion can never be approved on evidence that was
    never established.
    """
    weighted_voting_catalog_entry(strategy_id)  # rejects a strategy that is not ours
    evaluated = _utc(evaluated_at or datetime.now(timezone.utc))
    own = [item for item in observations if item.strategy_id == strategy_id]
    trades = simulate_shadow_trades(own, candles, strategy_id=strategy_id, cost_model=cost_model)
    returns = [trade.r_multiple for trade in trades]

    unavailable: list[str] = list(WEIGHTED_VOTING_SHADOW_LIVE_ONLY_METRICS)
    correlation = _peer_correlation(strategy_id, own, peer_observations or {})
    if correlation is None:
        unavailable.append("correlation_with_active_strategies")
        correlation = 1.0

    expectancy = fmean(returns) if returns else 0.0
    evidence = WeightedVotingStrategyLifecycleEvidence(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        strategy_id=strategy_id,
        evidence_id=f"shadow-{strategy_id}-{evaluated.date().isoformat()}",
        evaluated_at=evaluated,
        workflow=workflow,
        after_market_session_complete=after_market_session_complete,
        eligible_opportunities=sum(1 for item in own if item.tradable),
        completed_trades=len(trades),
        net_expectancy_after_costs=round(expectancy, 10),
        conservative_expectancy_lower_bound=round(_conservative_lower_bound(returns), 10),
        maximum_drawdown=round(_maximum_drawdown(trades, notional_equity), 10),
        mae_quality=round(_mae_quality(trades), 10),
        mfe_quality=round(_mfe_quality(trades), 10),
        walk_forward_stability=round(_walk_forward_stability(returns), 10),
        holdout_stability=round(_holdout_stability(returns), 10),
        # Only a real live-versus-backtest comparison can establish this one.
        paper_shadow_stability=WEIGHTED_VOTING_SHADOW_UNAVAILABLE_STABILITY,
        session_consistency=round(_label_consistency(trades, "session_label"), 10),
        regime_consistency=round(_label_consistency(trades, "regime_label"), 10),
        severe_tail_loss_pattern=any(value <= WEIGHTED_VOTING_SHADOW_SEVERE_TAIL_LOSS_R for value in returns),
        correlation_with_active_strategies=round(correlation, 10),
        incremental_portfolio_value=round(expectancy * max(0.0, 1.0 - correlation), 10),
        data_quality_stability=round(_ratio(own, lambda item: item.data_quality_status == "full"), 10),
        recent_net_expectancy_after_costs=round(_recent_expectancy(returns), 10),
        data_readiness_rate=round(_ratio(own, lambda item: item.data_ready), 10),
        execution_cost_edge_ratio=round(_cost_edge_ratio(trades), 10),
        paper_backtest_divergence=WEIGHTED_VOTING_SHADOW_UNAVAILABLE_DIVERGENCE,
        strategy_error_rate=round(_ratio(own, lambda item: item.errored), 10),
    )
    missing = tuple(dict.fromkeys(unavailable))
    return WeightedVotingShadowEvidenceResult(
        strategy_id=strategy_id,
        evidence=evidence,
        trades=trades,
        observation_count=len(own),
        unavailable_metrics=missing,
        reason_codes=tuple(f"weighted_voting.shadow_evidence.unavailable.{name}" for name in missing),
        explanation=(
            f"Shadow evidence for {strategy_id} was built from {len(own)} recorded observations "
            f"and {len(trades)} counterfactual trades. "
            + (
                f"{len(missing)} metric(s) could not be established and are reported at their "
                f"failing value: {', '.join(missing)}."
                if missing
                else "Every metric the lifecycle gate reads was established."
            )
        ),
    )


def _conservative_lower_bound(returns: Sequence[float]) -> float:
    """Lower bound of the expectancy, so a small lucky sample cannot clear the gate."""
    if len(returns) < 2:
        return 0.0
    spread = pstdev(returns)
    if spread == 0.0:
        return fmean(returns)
    return fmean(returns) - 1.96 * (spread / (len(returns) ** 0.5))


def _maximum_drawdown(trades: Sequence[WeightedVotingShadowTrade], notional_equity: float) -> float:
    if not trades:
        # No trades is not a clean record; it is an absent one, and the drawdown gate must
        # not be the thing that lets an empty sample through.
        return 1.0
    if notional_equity <= 0:
        return 1.0
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for trade in trades:
        equity += trade.net_pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst / notional_equity


def _mae_quality(trades: Sequence[WeightedVotingShadowTrade]) -> float:
    """Share of trades whose worst adverse move stayed inside the risk they declared."""
    if not trades:
        return 0.0
    return sum(1 for trade in trades if trade.adverse_excursion <= trade.risk_per_share) / len(trades)


def _mfe_quality(trades: Sequence[WeightedVotingShadowTrade]) -> float:
    """Share of trades that reached at least one unit of risk in their favour."""
    if not trades:
        return 0.0
    return sum(1 for trade in trades if trade.favorable_excursion >= trade.risk_per_share) / len(trades)


def _walk_forward_stability(returns: Sequence[float], folds: int = WEIGHTED_VOTING_SHADOW_WALK_FORWARD_FOLDS) -> float:
    """Share of chronological folds that were profitable, not just the total.

    An edge that exists in only one stretch of the sample fails here even when the overall
    expectancy looks healthy.
    """
    if len(returns) < folds:
        return 0.0
    size = len(returns) // folds
    windows = [returns[index * size : (index + 1) * size] for index in range(folds)]
    return sum(1 for window in windows if window and fmean(window) > 0.0) / folds


def _holdout_stability(returns: Sequence[float], fraction: float = WEIGHTED_VOTING_SHADOW_HOLDOUT_FRACTION) -> float:
    """How much of the in-sample edge survives into the untouched tail of the sample."""
    split = int(len(returns) * (1.0 - fraction))
    if split < 2 or len(returns) - split < 2:
        return 0.0
    in_sample = fmean(returns[:split])
    holdout = fmean(returns[split:])
    if in_sample <= 0.0 or holdout <= 0.0:
        return 0.0
    return min(1.0, holdout / in_sample)


def _label_consistency(trades: Sequence[WeightedVotingShadowTrade], attribute: str) -> float:
    """Share of the labelled buckets the strategy was actually profitable in."""
    if not trades:
        return 0.0
    buckets: dict[str, list[float]] = {}
    for trade in trades:
        buckets.setdefault(str(getattr(trade, attribute)), []).append(trade.r_multiple)
    if not buckets:
        return 0.0
    return sum(1 for values in buckets.values() if fmean(values) > 0.0) / len(buckets)


def _recent_expectancy(returns: Sequence[float], fraction: float = WEIGHTED_VOTING_SHADOW_HOLDOUT_FRACTION) -> float:
    if not returns:
        return 0.0
    tail = returns[-max(1, int(len(returns) * fraction)) :]
    return fmean(tail)


def _cost_edge_ratio(trades: Sequence[WeightedVotingShadowTrade]) -> float:
    """How much of the gross edge execution costs consume. 1.0 means all of it."""
    gross = sum(trade.gross_pnl for trade in trades)
    costs = sum(trade.total_costs for trade in trades)
    if gross <= 0.0:
        return 1.0
    return min(1.0, costs / gross)


def _ratio(observations: Sequence[WeightedVotingShadowObservation], predicate) -> float:
    if not observations:
        return 0.0
    return sum(1 for item in observations if predicate(item)) / len(observations)


def _peer_correlation(
    strategy_id: str,
    own: Sequence[WeightedVotingShadowObservation],
    peers: Mapping[str, Sequence[WeightedVotingShadowObservation]],
) -> float | None:
    """Strongest agreement with any strategy that already votes.

    A shadow strategy that merely restates an active one adds no diversification, so the
    gate caps this. Returns None when no active series was recorded to compare against --
    the caller turns that into a failing value rather than assuming independence.
    """
    active = [peer_id for peer_id in peers if peer_id in set(WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS)]
    if not active or not own:
        return None
    mine = {item.data_timestamp: _direction(item) for item in own}
    best: float | None = None
    for peer_id in active:
        theirs = {item.data_timestamp: _direction(item) for item in peers[peer_id]}
        shared = sorted(set(mine) & set(theirs))
        if len(shared) < 2:
            continue
        value = _pearson([mine[stamp] for stamp in shared], [theirs[stamp] for stamp in shared])
        if value is None:
            continue
        best = abs(value) if best is None else max(best, abs(value))
    return best


def _direction(observation: WeightedVotingShadowObservation) -> float:
    if observation.signal == WeightedSide.BUY.value:
        return 1.0
    if observation.signal == WeightedSide.SELL.value:
        return -1.0
    return 0.0


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = fmean(left)
    right_mean = fmean(right)
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_spread = sum((a - left_mean) ** 2 for a in left) ** 0.5
    right_spread = sum((b - right_mean) ** 2 for b in right) ** 0.5
    if left_spread == 0.0 or right_spread == 0.0:
        # A series that never varies has no correlation to measure. Treating that as
        # independence would be a free pass, so it is reported as unmeasurable.
        return None
    return covariance / (left_spread * right_spread)


# ------------------------------------------------------------------------- entry point


@dataclass(frozen=True)
class WeightedVotingShadowPromotionReview:
    """What the lifecycle gate says about one shadow strategy right now."""

    strategy_id: str
    evidence_result: WeightedVotingShadowEvidenceResult
    decision: Any
    failed_gates: tuple[str, ...]
    passed_gates: tuple[str, ...]

    @property
    def approved(self) -> bool:
        return bool(getattr(self.decision, "approved", False))

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "strategyId": self.strategy_id,
            "approved": self.approved,
            "action": getattr(self.decision, "action", "reject"),
            "observationCount": self.evidence_result.observation_count,
            "simulatedTrades": len(self.evidence_result.trades),
            "passedGates": list(self.passed_gates),
            "failedGates": list(self.failed_gates),
            "unavailableMetrics": list(self.evidence_result.unavailable_metrics),
            "evidenceComplete": self.evidence_result.complete,
            "explanation": self.evidence_result.explanation,
        }


def review_shadow_promotion(
    store: Any,
    strategy_id: str,
    *,
    candles: Sequence[Any],
    evaluated_at: datetime | None = None,
    workflow: str = WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW,
    after_market_session_complete: bool = True,
) -> WeightedVotingShadowPromotionReview:
    """Build a strategy's evidence from its record and put it through the promotion gate.

    Deliberately read-only. Applying a lifecycle change stays a separate, operator-approved
    step through ``apply_strategy_lifecycle_decision``: this reports what the gate says, it
    does not act on it, so nothing here can move a strategy onto capital on its own.
    """
    from backend.app.algorithms.weighted_voting.strategy_lifecycle import (
        evaluate_strategy_lifecycle_change,
        load_latest_strategy_lifecycle_snapshot,
    )

    evaluated = _utc(evaluated_at or datetime.now(timezone.utc))
    observations = load_shadow_observations(store, strategy_id)
    peers = {
        peer_id: load_shadow_observations(store, peer_id)
        for peer_id in WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS
    }
    evidence_result = build_shadow_evidence(
        strategy_id,
        observations=observations,
        candles=candles,
        peer_observations={peer_id: rows for peer_id, rows in peers.items() if rows},
        evaluated_at=evaluated,
        workflow=workflow,
        after_market_session_complete=after_market_session_complete,
    )
    snapshot = load_latest_strategy_lifecycle_snapshot(store, timestamp=evaluated)
    decision = evaluate_strategy_lifecycle_change(evidence_result.evidence, current_snapshot=snapshot)
    gates = tuple(getattr(decision, "gates", ()) or ())
    return WeightedVotingShadowPromotionReview(
        strategy_id=strategy_id,
        evidence_result=evidence_result,
        decision=decision,
        failed_gates=tuple(gate.gate_id for gate in gates if not gate.passed),
        passed_gates=tuple(gate.gate_id for gate in gates if gate.passed),
    )


def shadow_evidence_report(store: Any, *, candles: Sequence[Any], evaluated_at: datetime | None = None) -> dict[str, Any]:
    """Where every shadow strategy stands against the promotion gate."""
    reviews = [
        review_shadow_promotion(store, strategy_id, candles=candles, evaluated_at=evaluated_at)
        for strategy_id in WEIGHTED_VOTING_SHADOW_STRATEGY_IDS
    ]
    return {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "evidenceVersion": WEIGHTED_VOTING_SHADOW_EVIDENCE_VERSION,
        "liveOnlyMetrics": list(WEIGHTED_VOTING_SHADOW_LIVE_ONLY_METRICS),
        "strategies": [review.as_dict() for review in reviews],
    }

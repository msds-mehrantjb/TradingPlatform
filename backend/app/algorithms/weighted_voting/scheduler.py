"""Independent after-market scheduler for Weighted Voting weight updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from backend.app.algorithms.weighted_voting.backtest.data_validation import validate_historical_data
from backend.app.algorithms.weighted_voting.backtest.engine import WeightedBacktestEngineConfig, WeightedBacktestResult, run_weighted_voting_backtest
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle
from backend.app.algorithms.weighted_voting.models import WeightedStrategyOutcome, WeightedWeightState, WeightedWeightStateStatus
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore
from backend.app.algorithms.weighted_voting.strategies.common import eastern_datetime, eastern_minutes
from backend.app.algorithms.weighted_voting.weight_engine import create_unseeded_equal_weight_state, update_performance_weight_state


WEIGHTED_VOTING_SCHEDULER_VERSION = "weighted_voting_scheduler_v2"
ACTIVE_WEIGHT_STATE_KEY = "weighted_voting.weights.active"
OUTCOME_HISTORY_KEY = "weighted_voting.outcomes.history"
UPDATE_RECORD_PREFIX = "weighted_voting.scheduler.daily_update."
PUBLISHED_WEIGHT_PREFIX = "weighted_voting.weights.published_for_session."
STATISTICS_PREFIX = "weighted_voting.statistics."


class WeightedVotingDatasetProvider(Protocol):
    def candles_for_session(self, session_date: date) -> tuple[WeightedVotingCandle, ...]:
        ...


@dataclass(frozen=True)
class WeightedVotingDailySchedulerConfig:
    symbol: str
    run_id: str = "weighted-voting-daily-scheduler"
    source: str = "weighted_voting_after_market_scheduler"
    weighted_config: WeightedVotingConfig = WeightedVotingConfig()


@dataclass(frozen=True)
class WeightedVotingDailyWeightUpdateResult:
    session_date: date
    status: str
    idempotency_key: str
    previous_weight_version: str
    candidate_weight_version: str | None
    active_weight_version: str
    published_for_session_date: date | None
    replay_result: WeightedBacktestResult | None
    finalized_outcome_count: int
    reason_codes: tuple[str, ...]
    explanation: str


def scheduler_status() -> dict[str, str]:
    return {
        "version": WEIGHTED_VOTING_SCHEDULER_VERSION,
        "status": "implemented",
        "explanation": "Weighted Voting has an independent after-market, idempotent, retryable daily weight-update scheduler.",
    }


def run_after_market_daily_weight_update(
    *,
    session_date: date,
    store: WeightedVotingStateStore,
    dataset_provider: WeightedVotingDatasetProvider,
    completed_at: datetime,
    config: WeightedVotingDailySchedulerConfig,
) -> WeightedVotingDailyWeightUpdateResult:
    idempotency_key = _update_key(session_date)
    existing = _read_optional(store, idempotency_key)
    if existing and existing.get("status") == "published":
        active_state = _load_active_weight_state(store, completed_at)
        return WeightedVotingDailyWeightUpdateResult(
            session_date=session_date,
            status="idempotent_noop",
            idempotency_key=idempotency_key,
            previous_weight_version=str(existing.get("previous_weight_version", active_state.weight_version)),
            candidate_weight_version=existing.get("candidate_weight_version"),
            active_weight_version=str(existing.get("active_weight_version", active_state.weight_version)),
            published_for_session_date=date.fromisoformat(existing["published_for_session_date"]) if existing.get("published_for_session_date") else None,
            replay_result=None,
            finalized_outcome_count=int(existing.get("finalized_outcome_count", 0)),
            reason_codes=("weighted_voting.scheduler.idempotent_noop",),
            explanation="Weighted Voting daily update already published for this completed session; no duplicate update was created.",
        )

    if _intraday(session_date, completed_at):
        active_state = _load_active_weight_state(store, completed_at)
        return WeightedVotingDailyWeightUpdateResult(
            session_date=session_date,
            status="skipped_intraday",
            idempotency_key=idempotency_key,
            previous_weight_version=active_state.weight_version,
            candidate_weight_version=None,
            active_weight_version=active_state.weight_version,
            published_for_session_date=None,
            replay_result=None,
            finalized_outcome_count=0,
            reason_codes=("weighted_voting.scheduler.intraday_update_blocked",),
            explanation="Weighted Voting weights are never updated intraday.",
        )

    previous_state = _load_active_weight_state(store, completed_at)
    candles = tuple(sorted(dataset_provider.candles_for_session(session_date), key=lambda candle: candle.timestamp))
    validation = validate_historical_data(
        symbol=config.symbol,
        candles_by_timeframe={"1m": candles},
        source=config.source,
        created_at=completed_at,
        fill_policy="none",
    )
    if validation.blocks_run:
        _write_update_record(
            store,
            idempotency_key,
            status="failed_validation",
            session_date=session_date,
            previous_weight_version=previous_state.weight_version,
            active_weight_version=previous_state.weight_version,
            candidate_weight_version=None,
            finalized_outcome_count=0,
            published_for_session_date=None,
            reason_codes=validation.errors,
        )
        return WeightedVotingDailyWeightUpdateResult(
            session_date=session_date,
            status="failed_validation",
            idempotency_key=idempotency_key,
            previous_weight_version=previous_state.weight_version,
            candidate_weight_version=None,
            active_weight_version=previous_state.weight_version,
            published_for_session_date=None,
            replay_result=None,
            finalized_outcome_count=0,
            reason_codes=validation.errors,
            explanation="Refreshed neutral dataset failed validation; previous active Weighted Voting weights were preserved.",
        )

    replay = run_weighted_voting_backtest(
        candles=candles,
        config=WeightedBacktestEngineConfig(
            symbol=config.symbol,
            source=config.source,
            run_id=f"{config.run_id}-{session_date.isoformat()}-replay",
            weighted_config=config.weighted_config,
            use_performance_weights=False,
            use_dynamic_settings=False,
            initial_weight_state=previous_state,
        ),
        created_at=completed_at,
    )
    finalized_outcomes = tuple(replay.historical_outcomes)
    outcome_history = _load_outcome_history(store)
    all_outcomes = tuple(outcome_history + finalized_outcomes)
    candidate = update_performance_weight_state(
        previous_state,
        all_outcomes,
        update_timestamp=completed_at,
        data_timestamp=candles[-1].timestamp if candles else completed_at,
        session_date=session_date,
        config=config.weighted_config,
    )
    if not _candidate_valid(candidate, previous_state, session_date):
        _write_active_weight_state(store, previous_state)
        _write_update_record(
            store,
            idempotency_key,
            status="failed_candidate_validation",
            session_date=session_date,
            previous_weight_version=previous_state.weight_version,
            active_weight_version=previous_state.weight_version,
            candidate_weight_version=candidate.weight_version,
            finalized_outcome_count=len(finalized_outcomes),
            published_for_session_date=None,
            reason_codes=candidate.reason_codes + ("weighted_voting.scheduler.candidate_rejected",),
        )
        return WeightedVotingDailyWeightUpdateResult(
            session_date=session_date,
            status="failed_candidate_validation",
            idempotency_key=idempotency_key,
            previous_weight_version=previous_state.weight_version,
            candidate_weight_version=candidate.weight_version,
            active_weight_version=previous_state.weight_version,
            published_for_session_date=None,
            replay_result=replay,
            finalized_outcome_count=len(finalized_outcomes),
            reason_codes=candidate.reason_codes + ("weighted_voting.scheduler.candidate_rejected",),
            explanation="Candidate Weighted Voting weight state failed validation; previous active weights remain published.",
        )

    next_session = _next_business_session(session_date)
    _write_active_weight_state(store, candidate)
    _write_outcome_history(store, all_outcomes)
    _write_statistics(store, session_date, all_outcomes)
    store.write_snapshot(_published_key(next_session), candidate.model_dump(mode="json"))
    _write_update_record(
        store,
        idempotency_key,
        status="published",
        session_date=session_date,
        previous_weight_version=previous_state.weight_version,
        active_weight_version=candidate.weight_version,
        candidate_weight_version=candidate.weight_version,
        finalized_outcome_count=len(finalized_outcomes),
        published_for_session_date=next_session,
        reason_codes=(
            "weighted_voting.scheduler.session_replayed_with_frozen_weights",
            "weighted_voting.scheduler.weights_published_for_next_session",
        ),
    )
    return WeightedVotingDailyWeightUpdateResult(
        session_date=session_date,
        status="published",
        idempotency_key=idempotency_key,
        previous_weight_version=previous_state.weight_version,
        candidate_weight_version=candidate.weight_version,
        active_weight_version=candidate.weight_version,
        published_for_session_date=next_session,
        replay_result=replay,
        finalized_outcome_count=len(finalized_outcomes),
        reason_codes=(
            "weighted_voting.scheduler.session_replayed_with_frozen_weights",
            "weighted_voting.scheduler.outcomes_finalized",
            "weighted_voting.scheduler.weights_published_for_next_session",
        ),
        explanation="After-market Weighted Voting update completed independently and published weights for the next session before its open.",
    )


def _load_active_weight_state(store: WeightedVotingStateStore, timestamp: datetime) -> WeightedWeightState:
    snapshot = _read_optional(store, ACTIVE_WEIGHT_STATE_KEY)
    if snapshot:
        return WeightedWeightState.model_validate(snapshot)
    state = create_unseeded_equal_weight_state(timestamp=timestamp, data_timestamp=timestamp)
    _write_active_weight_state(store, state)
    return state


def _write_active_weight_state(store: WeightedVotingStateStore, state: WeightedWeightState) -> None:
    store.write_snapshot(ACTIVE_WEIGHT_STATE_KEY, state.model_dump(mode="json"))


def _load_outcome_history(store: WeightedVotingStateStore) -> tuple[WeightedStrategyOutcome, ...]:
    snapshot = _read_optional(store, OUTCOME_HISTORY_KEY)
    if not snapshot:
        return ()
    return tuple(WeightedStrategyOutcome.model_validate(item) for item in snapshot.get("outcomes", ()))


def _write_outcome_history(store: WeightedVotingStateStore, outcomes: tuple[WeightedStrategyOutcome, ...]) -> None:
    store.write_snapshot(OUTCOME_HISTORY_KEY, {"outcomes": [outcome.model_dump(mode="json") for outcome in outcomes]})


def _write_statistics(store: WeightedVotingStateStore, session_date: date, outcomes: tuple[WeightedStrategyOutcome, ...]) -> None:
    counts: dict[str, int] = {}
    expectancy: dict[str, float] = {}
    for outcome in outcomes:
        counts[outcome.strategy_id] = counts.get(outcome.strategy_id, 0) + 1
        expectancy[outcome.strategy_id] = expectancy.get(outcome.strategy_id, 0.0) + float(outcome.outcome_return or 0.0)
    stats = {
        strategy_id: {
            "trade_count": counts[strategy_id],
            "expectancy": expectancy[strategy_id] / counts[strategy_id],
        }
        for strategy_id in sorted(counts)
    }
    store.write_snapshot(f"{STATISTICS_PREFIX}{session_date.isoformat()}", {"session_date": session_date.isoformat(), "statistics": stats})


def _candidate_valid(candidate: WeightedWeightState, previous_state: WeightedWeightState, session_date: date) -> bool:
    if candidate.state_status == WeightedWeightStateStatus.VALIDATION_FAILED.value:
        return False
    if candidate.weight_version == previous_state.weight_version and candidate.state_status == WeightedWeightStateStatus.VALIDATION_FAILED.value:
        return False
    if candidate.active_session_date != session_date.isoformat():
        return False
    weights = candidate.strategy_weights
    return bool(weights) and all(0.0 <= weight <= 1.0 for weight in weights.values()) and abs(sum(weights.values()) - 1.0) <= 0.000001


def _write_update_record(
    store: WeightedVotingStateStore,
    key: str,
    *,
    status: str,
    session_date: date,
    previous_weight_version: str,
    active_weight_version: str,
    candidate_weight_version: str | None,
    finalized_outcome_count: int,
    published_for_session_date: date | None,
    reason_codes: tuple[str, ...],
) -> None:
    store.write_snapshot(
        key,
        {
            "scheduler_version": WEIGHTED_VOTING_SCHEDULER_VERSION,
            "session_date": session_date.isoformat(),
            "status": status,
            "previous_weight_version": previous_weight_version,
            "candidate_weight_version": candidate_weight_version,
            "active_weight_version": active_weight_version,
            "finalized_outcome_count": finalized_outcome_count,
            "published_for_session_date": published_for_session_date.isoformat() if published_for_session_date else None,
            "reason_codes": tuple(reason_codes),
        },
    )


def _read_optional(store: WeightedVotingStateStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _intraday(session_date: date, completed_at: datetime) -> bool:
    local = eastern_datetime(completed_at)
    return local.date() == session_date and eastern_minutes(completed_at) < 960


def _next_business_session(session_date: date) -> date:
    candidate = session_date + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _update_key(session_date: date) -> str:
    return f"{UPDATE_RECORD_PREFIX}{session_date.isoformat()}"


def _published_key(session_date: date) -> str:
    return f"{PUBLISHED_WEIGHT_PREFIX}{session_date.isoformat()}"

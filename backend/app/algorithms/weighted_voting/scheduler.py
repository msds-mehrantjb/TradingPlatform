"""Independent after-market scheduler for Weighted Voting weight updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from backend.app.algorithms.weighted_voting.backtest.data_validation import validate_historical_data
from backend.app.algorithms.weighted_voting.backtest.engine import WeightedBacktestEngineConfig, WeightedBacktestResult, run_weighted_voting_backtest
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.models import WeightedStrategyOutcome, WeightedWeightState, WeightedWeightStateStatus
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore
from backend.app.algorithms.weighted_voting.strategies.common import eastern_datetime, eastern_minutes
from backend.app.algorithms.weighted_voting.weight_engine import append_weight_history, create_unseeded_equal_weight_state, rollback_weight_state, update_performance_weight_state


WEIGHTED_VOTING_SCHEDULER_VERSION = "weighted_voting_scheduler_v3"
WEIGHTED_VOTING_AFTER_MARKET_UPDATE_EASTERN_MINUTES = 970
ACTIVE_WEIGHT_STATE_KEY = "weighted_voting.weights.active"
OUTCOME_HISTORY_KEY = "weighted_voting.outcomes.history"
WEIGHT_HISTORY_KEY = "weighted_voting.weights.history"
UPDATE_RECORD_PREFIX = "weighted_voting.scheduler.daily_update."
UPDATE_AUDIT_PREFIX = "weighted_voting.scheduler.audit."
UPDATE_STATUS_KEY = "weighted_voting.scheduler.status.latest"
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
    after_market_update_eastern_minutes: int = WEIGHTED_VOTING_AFTER_MARKET_UPDATE_EASTERN_MINUTES
    performance_window_sessions: int = 60


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
    dataset_complete: bool = False
    performance_window_start: date | None = None
    performance_window_end: date | None = None
    audit_record_id: str | None = None


def scheduler_status() -> dict[str, object]:
    return {
        "version": WEIGHTED_VOTING_SCHEDULER_VERSION,
        "status": "implemented",
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "afterMarketUpdateEasternMinutes": WEIGHTED_VOTING_AFTER_MARKET_UPDATE_EASTERN_MINUTES,
        "ownedResponsibilities": [
            "after_market_update_time",
            "dataset_completeness_validation",
            "strategy_outcome_finalization",
            "performance_window_calculation",
            "active_weight_update",
            "weight_version_creation",
            "weight_history_persistence",
            "failed_update_handling",
            "previous_version_rollback",
            "update_status",
            "update_audit_trail",
        ],
        "isolation": "other_algorithm_backtests_or_daily_updates_do_not_block_weighted_voting",
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
            dataset_complete=bool(existing.get("dataset_complete", True)),
            performance_window_start=_date_optional(existing.get("performance_window_start")),
            performance_window_end=_date_optional(existing.get("performance_window_end")),
            audit_record_id=existing.get("audit_record_id"),
            reason_codes=("weighted_voting.scheduler.idempotent_noop",),
            explanation="Weighted Voting daily update already published for this completed session; no duplicate update was created.",
        )

    if _intraday(session_date, completed_at):
        active_state = _load_active_weight_state(store, completed_at)
        record = _write_update_record(
            store,
            idempotency_key,
            status="skipped_intraday",
            session_date=session_date,
            previous_weight_version=active_state.weight_version,
            active_weight_version=active_state.weight_version,
            candidate_weight_version=None,
            finalized_outcome_count=0,
            published_for_session_date=None,
            dataset_complete=False,
            performance_window=_performance_window((), session_date, config.performance_window_sessions),
            reason_codes=("weighted_voting.scheduler.intraday_update_blocked",),
        )
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
            dataset_complete=False,
            performance_window_start=_date_optional(record.get("performance_window_start")),
            performance_window_end=_date_optional(record.get("performance_window_end")),
            audit_record_id=record.get("audit_record_id"),
            reason_codes=("weighted_voting.scheduler.intraday_update_blocked",),
            explanation="Weighted Voting weights are never updated intraday.",
        )

    previous_state = _load_active_weight_state(store, completed_at)
    previous_history = _load_weight_history(store)
    candles = tuple(sorted(dataset_provider.candles_for_session(session_date), key=lambda candle: candle.timestamp))
    validation = validate_historical_data(
        symbol=config.symbol,
        candles_by_timeframe={"1m": candles},
        source=config.source,
        created_at=completed_at,
        fill_policy="none",
    )
    dataset_complete = not validation.blocks_run
    performance_window = _performance_window(_load_outcome_history(store), session_date, config.performance_window_sessions)
    if validation.blocks_run:
        record = _write_update_record(
            store,
            idempotency_key,
            status="failed_validation",
            session_date=session_date,
            previous_weight_version=previous_state.weight_version,
            active_weight_version=previous_state.weight_version,
            candidate_weight_version=None,
            finalized_outcome_count=0,
            published_for_session_date=None,
            dataset_complete=False,
            performance_window=performance_window,
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
            dataset_complete=False,
            performance_window_start=_date_optional(record.get("performance_window_start")),
            performance_window_end=_date_optional(record.get("performance_window_end")),
            audit_record_id=record.get("audit_record_id"),
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
    finalized_outcomes = _finalize_strategy_outcomes(tuple(replay.historical_outcomes))
    outcome_history = _load_outcome_history(store)
    all_outcomes = tuple(outcome_history + finalized_outcomes)
    performance_window = _performance_window(all_outcomes, session_date, config.performance_window_sessions)
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
        record = _write_update_record(
            store,
            idempotency_key,
            status="failed_candidate_validation",
            session_date=session_date,
            previous_weight_version=previous_state.weight_version,
            active_weight_version=previous_state.weight_version,
            candidate_weight_version=candidate.weight_version,
            finalized_outcome_count=len(finalized_outcomes),
            published_for_session_date=None,
            dataset_complete=dataset_complete,
            performance_window=performance_window,
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
            dataset_complete=dataset_complete,
            performance_window_start=_date_optional(record.get("performance_window_start")),
            performance_window_end=_date_optional(record.get("performance_window_end")),
            audit_record_id=record.get("audit_record_id"),
            reason_codes=candidate.reason_codes + ("weighted_voting.scheduler.candidate_rejected",),
            explanation="Candidate Weighted Voting weight state failed validation; previous active weights remain published.",
        )

    next_session = _next_business_session(session_date)
    _write_active_weight_state(store, candidate)
    _write_weight_history(store, append_weight_history(previous_history, previous_state))
    _write_weight_history(store, append_weight_history(_load_weight_history(store), candidate))
    _write_outcome_history(store, all_outcomes)
    _write_statistics(store, session_date, all_outcomes)
    store.write_snapshot(_published_key(next_session), candidate.model_dump(mode="json"))
    record = _write_update_record(
        store,
        idempotency_key,
        status="published",
        session_date=session_date,
        previous_weight_version=previous_state.weight_version,
        active_weight_version=candidate.weight_version,
        candidate_weight_version=candidate.weight_version,
        finalized_outcome_count=len(finalized_outcomes),
        published_for_session_date=next_session,
        dataset_complete=dataset_complete,
        performance_window=performance_window,
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
        dataset_complete=dataset_complete,
        performance_window_start=_date_optional(record.get("performance_window_start")),
        performance_window_end=_date_optional(record.get("performance_window_end")),
        audit_record_id=record.get("audit_record_id"),
        reason_codes=(
            "weighted_voting.scheduler.session_replayed_with_frozen_weights",
            "weighted_voting.scheduler.outcomes_finalized",
            "weighted_voting.scheduler.weights_published_for_next_session",
        ),
        explanation="After-market Weighted Voting update completed independently and published weights for the next session before its open.",
    )


def rollback_to_previous_weight_version(
    *,
    store: WeightedVotingStateStore,
    target_weight_version: str,
    rolled_back_at: datetime,
    session_date: date,
) -> WeightedWeightState:
    current = _load_active_weight_state(store, rolled_back_at)
    history = _load_weight_history(store)
    rolled_back = rollback_weight_state(
        current,
        history,
        target_weight_version=target_weight_version,
        rollback_timestamp=rolled_back_at,
    )
    if rolled_back.state_status != WeightedWeightStateStatus.VALIDATION_FAILED:
        _write_active_weight_state(store, rolled_back)
    _write_update_record(
        store,
        f"{UPDATE_RECORD_PREFIX}rollback.{target_weight_version}.{rolled_back_at.strftime('%Y%m%dT%H%M%S')}",
        status="rollback_applied" if rolled_back.state_status != WeightedWeightStateStatus.VALIDATION_FAILED else "rollback_failed",
        session_date=session_date,
        previous_weight_version=current.weight_version,
        active_weight_version=rolled_back.weight_version if rolled_back.state_status != WeightedWeightStateStatus.VALIDATION_FAILED else current.weight_version,
        candidate_weight_version=target_weight_version,
        finalized_outcome_count=0,
        published_for_session_date=None,
        dataset_complete=True,
        performance_window=_performance_window(_load_outcome_history(store), session_date, 60),
        reason_codes=rolled_back.reason_codes,
    )
    return rolled_back


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


def _load_weight_history(store: WeightedVotingStateStore) -> tuple[WeightedWeightState, ...]:
    snapshot = _read_optional(store, WEIGHT_HISTORY_KEY)
    if not snapshot:
        return ()
    return tuple(WeightedWeightState.model_validate(item) for item in snapshot.get("items", ()))


def _write_weight_history(store: WeightedVotingStateStore, history: tuple[WeightedWeightState, ...]) -> None:
    store.write_snapshot(
        WEIGHT_HISTORY_KEY,
        {
            "scheduler_version": WEIGHTED_VOTING_SCHEDULER_VERSION,
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "items": [state.model_dump(mode="json") for state in history],
            "reason_codes": ("weighted_voting.scheduler.weight_history_persisted",),
        },
    )


def _finalize_strategy_outcomes(outcomes: tuple[WeightedStrategyOutcome, ...]) -> tuple[WeightedStrategyOutcome, ...]:
    finalized = []
    for outcome in outcomes:
        if getattr(outcome, "algorithm_id", None) != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("foreign algorithm outcome cannot be finalized by Weighted Voting scheduler")
        if outcome.outcome_return is None:
            continue
        finalized.append(outcome)
    return tuple(finalized)


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
    dataset_complete: bool,
    performance_window: dict[str, object],
    reason_codes: tuple[str, ...],
    audit_details: dict[str, object] | None = None,
) -> dict[str, object]:
    audit_record_id = f"{UPDATE_AUDIT_PREFIX}{session_date.isoformat()}.{status}"
    record = {
        "scheduler_version": WEIGHTED_VOTING_SCHEDULER_VERSION,
        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
        "session_date": session_date.isoformat(),
        "status": status,
        "previous_weight_version": previous_weight_version,
        "candidate_weight_version": candidate_weight_version,
        "active_weight_version": active_weight_version,
        "finalized_outcome_count": finalized_outcome_count,
        "published_for_session_date": published_for_session_date.isoformat() if published_for_session_date else None,
        "after_market_update_eastern_minutes": WEIGHTED_VOTING_AFTER_MARKET_UPDATE_EASTERN_MINUTES,
        "dataset_complete": dataset_complete,
        "performance_window_start": performance_window.get("start_date"),
        "performance_window_end": performance_window.get("end_date"),
        "performance_window_outcome_count": performance_window.get("outcome_count", 0),
        "audit_record_id": audit_record_id,
        "isolation": "weighted_voting_update_ignores_other_algorithm_backtest_failures",
        "reason_codes": tuple(reason_codes),
    }
    store.write_snapshot(
        key,
        record,
    )
    _write_update_status(store, record)
    _write_update_audit(store, record, audit_details or {})
    return record


def _write_update_status(store: WeightedVotingStateStore, record: dict[str, object]) -> None:
    store.write_snapshot(
        UPDATE_STATUS_KEY,
        {
            "scheduler_version": WEIGHTED_VOTING_SCHEDULER_VERSION,
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "status": record["status"],
            "session_date": record["session_date"],
            "active_weight_version": record["active_weight_version"],
            "candidate_weight_version": record["candidate_weight_version"],
            "dataset_complete": record["dataset_complete"],
            "audit_record_id": record["audit_record_id"],
            "reason_codes": record["reason_codes"],
        },
    )


def _write_update_audit(store: WeightedVotingStateStore, record: dict[str, object], details: dict[str, object]) -> None:
    audit = {
        **record,
        "scheduler_owned_steps": [
            "dataset_completeness_validation",
            "strategy_outcome_finalization",
            "performance_window_calculation",
            "active_weight_update",
            "weight_version_creation",
            "weight_history_persistence",
            "failed_update_handling",
            "previous_version_rollback",
        ],
        "details": details,
    }
    store.write_snapshot(str(record["audit_record_id"]), audit)


def _performance_window(outcomes: tuple[WeightedStrategyOutcome, ...], session_date: date, window_sessions: int) -> dict[str, object]:
    dated = sorted(
        {
            outcome.exit_timestamp.date()
            for outcome in outcomes
            if getattr(outcome, "algorithm_id", None) == WEIGHTED_VOTING_ALGORITHM_ID and outcome.exit_timestamp is not None
        }
    )
    retained = dated[-max(1, window_sessions) :]
    return {
        "start_date": retained[0].isoformat() if retained else session_date.isoformat(),
        "end_date": retained[-1].isoformat() if retained else session_date.isoformat(),
        "outcome_count": sum(1 for outcome in outcomes if outcome.exit_timestamp is not None and outcome.exit_timestamp.date() in set(retained)),
        "window_sessions": window_sessions,
    }


def _read_optional(store: WeightedVotingStateStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _intraday(session_date: date, completed_at: datetime) -> bool:
    local = eastern_datetime(completed_at)
    return local.date() == session_date and eastern_minutes(completed_at) < WEIGHTED_VOTING_AFTER_MARKET_UPDATE_EASTERN_MINUTES


def _next_business_session(session_date: date) -> date:
    candidate = session_date + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _update_key(session_date: date) -> str:
    return f"{UPDATE_RECORD_PREFIX}{session_date.isoformat()}"


def _published_key(session_date: date) -> str:
    return f"{PUBLISHED_WEIGHT_PREFIX}{session_date.isoformat()}"


def _date_optional(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return None

"""Chronological walk-forward testing for Weighted Voting."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json

from backend.app.algorithms.weighted_voting.backtest.data_validation import WeightedBacktestDataManifest, validate_historical_data
from backend.app.algorithms.weighted_voting.backtest.engine import WeightedBacktestEngineConfig, WeightedBacktestResult, run_weighted_voting_backtest
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle
from backend.app.algorithms.weighted_voting.strategies.common import eastern_datetime


WEIGHTED_VOTING_WALK_FORWARD_VERSION = "weighted_voting_walk_forward_v2"

MODE_STATIC_EQUAL = "static_defaults_equal_weights"
MODE_PERFORMANCE_STATIC = "performance_weights_static_settings"
MODE_PERFORMANCE_DYNAMIC = "performance_weights_dynamic_settings"
WALK_FORWARD_MODES = (MODE_STATIC_EQUAL, MODE_PERFORMANCE_STATIC, MODE_PERFORMANCE_DYNAMIC)


@dataclass(frozen=True)
class WeightedWalkForwardConfig:
    run_id: str
    symbol: str
    indicator_warmup_sessions: int = 20
    weight_calibration_sessions: int = 60
    validation_sessions: int = 20
    unseen_test_sessions: int = 5
    step_forward_sessions: int = 5
    source: str = "weighted_voting_walk_forward"
    base_engine_config: WeightedBacktestEngineConfig | None = None


@dataclass(frozen=True)
class WeightedWalkForwardFoldBoundary:
    fold_id: str
    warmup_start: date
    warmup_end: date
    calibration_start: date
    calibration_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date
    manifest_hash: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeightedWalkForwardModeResult:
    fold_id: str
    mode: str
    test_result: WeightedBacktestResult
    calibration_outcome_count: int
    weight_data_end: date
    settings_available_before: date
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeightedWalkForwardModeSummary:
    mode: str
    fold_count: int
    net_pnl: float
    return_percent: float
    sharpe: float
    sortino: float
    calmar: float
    maximum_drawdown: float
    profit_factor: float
    regime_dependence_score: float
    trade_count: int


@dataclass(frozen=True)
class WeightedWalkForwardResult:
    run_id: str
    manifest: WeightedBacktestDataManifest
    fold_boundaries: tuple[WeightedWalkForwardFoldBoundary, ...]
    mode_results: dict[str, tuple[WeightedWalkForwardModeResult, ...]]
    mode_summaries: dict[str, WeightedWalkForwardModeSummary]
    dynamic_promoted: bool
    reproducibility_key: str
    reason_codes: tuple[str, ...]
    explanation: str


def walk_forward_status() -> dict[str, str]:
    return {
        "version": WEIGHTED_VOTING_WALK_FORWARD_VERSION,
        "status": "implemented",
        "explanation": "Weighted Voting walk-forward testing builds chronological folds, calibrates only on pre-test data, and reports static and dynamic modes separately.",
    }


def run_chronological_walk_forward(
    *,
    candles: tuple[WeightedVotingCandle, ...],
    config: WeightedWalkForwardConfig,
    created_at: datetime,
) -> WeightedWalkForwardResult:
    ordered_candles = tuple(sorted(candles, key=lambda candle: candle.timestamp))
    validation = validate_historical_data(
        symbol=config.symbol,
        candles_by_timeframe={"1m": ordered_candles},
        source=config.source,
        created_at=created_at,
        fill_policy="none",
    )
    if validation.blocks_run:
        raise ValueError("Weighted Voting walk-forward data validation blocked the run: " + ",".join(validation.errors))
    sessions = _sessions_by_date(ordered_candles)
    boundaries = _fold_boundaries(sessions, config, validation.manifest.manifest_hash)
    if not boundaries:
        raise ValueError("not enough chronological sessions to build a Weighted Voting walk-forward fold")

    mode_results: dict[str, list[WeightedWalkForwardModeResult]] = {mode: [] for mode in WALK_FORWARD_MODES}
    for boundary in boundaries:
        calibration_candles = _candles_for_range(sessions, boundary.calibration_start, boundary.calibration_end)
        calibration_result = run_weighted_voting_backtest(
            candles=calibration_candles,
            config=_engine_config(config, run_id=f"{config.run_id}-{boundary.fold_id}-calibration", mode=MODE_STATIC_EQUAL, calibration_outcomes=()),
            created_at=created_at,
        )
        calibration_outcomes = calibration_result.historical_outcomes
        test_candles = _candles_for_range(sessions, boundary.test_start, boundary.test_end)
        for mode in WALK_FORWARD_MODES:
            result = run_weighted_voting_backtest(
                candles=test_candles,
                config=_engine_config(config, run_id=f"{config.run_id}-{boundary.fold_id}-{mode}", mode=mode, calibration_outcomes=calibration_outcomes),
                created_at=created_at,
            )
            mode_results[mode].append(
                WeightedWalkForwardModeResult(
                    fold_id=boundary.fold_id,
                    mode=mode,
                    test_result=result,
                    calibration_outcome_count=len(calibration_outcomes),
                    weight_data_end=boundary.calibration_end,
                    settings_available_before=boundary.test_start,
                    reason_codes=(
                        "weighted_voting.walk_forward.no_shuffle",
                        "weighted_voting.walk_forward.weights_use_data_through_d_minus_1",
                        f"weighted_voting.walk_forward.mode.{mode}",
                    ),
                )
            )

    frozen_results = {mode: tuple(results) for mode, results in mode_results.items()}
    summaries = {mode: _mode_summary(mode, results) for mode, results in frozen_results.items()}
    dynamic_promoted = _dynamic_promoted(summaries)
    return WeightedWalkForwardResult(
        run_id=config.run_id,
        manifest=validation.manifest,
        fold_boundaries=tuple(boundaries),
        mode_results=frozen_results,
        mode_summaries=summaries,
        dynamic_promoted=dynamic_promoted,
        reproducibility_key=_reproducibility_key(config, validation.manifest.manifest_hash),
        reason_codes=(
            "weighted_voting.walk_forward.chronological",
            "weighted_voting.walk_forward.fold_boundaries_stored",
            "weighted_voting.walk_forward.modes_reported_separately",
            "weighted_voting.walk_forward.dynamic_promotion_guarded",
        ),
        explanation="Chronological Weighted Voting walk-forward run; no fold shuffles data and unseen test sessions are evaluated separately from calibration.",
    )


def _engine_config(
    config: WeightedWalkForwardConfig,
    *,
    run_id: str,
    mode: str,
    calibration_outcomes,
) -> WeightedBacktestEngineConfig:
    base = config.base_engine_config or WeightedBacktestEngineConfig(symbol=config.symbol, source=config.source)
    use_performance = mode in (MODE_PERFORMANCE_STATIC, MODE_PERFORMANCE_DYNAMIC)
    use_dynamic = mode == MODE_PERFORMANCE_DYNAMIC
    dynamic_envelope = _walk_forward_dynamic_envelope() if use_dynamic else None
    return WeightedBacktestEngineConfig(
        symbol=config.symbol,
        account_equity=base.account_equity,
        starting_cash=base.starting_cash,
        source=config.source,
        run_id=run_id,
        allow_short=base.allow_short,
        session_cutoff_eastern_minutes=base.session_cutoff_eastern_minutes,
        force_close_eastern_minutes=base.force_close_eastern_minutes,
        decision_start_index=base.decision_start_index,
        cost_model=base.cost_model,
        weighted_config=base.weighted_config,
        calibration_outcomes=tuple(calibration_outcomes) if use_performance else (),
        use_performance_weights=use_performance,
        use_dynamic_settings=use_dynamic,
        default_settings=base.default_settings,
        dynamic_envelope=dynamic_envelope or base.dynamic_envelope,
        hard_limits=base.hard_limits,
    )


def _walk_forward_dynamic_envelope():
    return default_dynamic_envelope().model_copy(
        update={
            "enabled": True,
            "base_risk_per_trade_percent_delta": 0.15,
            "order_allocation_percent_delta": 2.0,
            "daily_allocation_percent_delta": 5.0,
            "maximum_position_percent_delta": 2.0,
            "maximum_participation_rate_delta": 0.004,
            "minimum_score_delta": 0.04,
            "minimum_edge_delta": 0.03,
            "maximum_spread_percent_delta": 0.0003,
            "minimum_liquidity_volume_delta": 2500.0,
            "atr_stop_multiplier_delta": 0.35,
            "target_r_delta": 0.4,
            "entry_buffer_percent_delta": 0.0002,
            "time_stop_minutes_delta": 30,
            "session_cutoff_minutes_delta": 5,
        }
    )


def _sessions_by_date(candles: tuple[WeightedVotingCandle, ...]) -> dict[date, tuple[WeightedVotingCandle, ...]]:
    grouped: dict[date, list[WeightedVotingCandle]] = defaultdict(list)
    for candle in candles:
        grouped[eastern_datetime(candle.timestamp).date()].append(candle)
    return {session_date: tuple(sorted(values, key=lambda candle: candle.timestamp)) for session_date, values in sorted(grouped.items())}


def _fold_boundaries(sessions: dict[date, tuple[WeightedVotingCandle, ...]], config: WeightedWalkForwardConfig, manifest_hash: str) -> list[WeightedWalkForwardFoldBoundary]:
    session_dates = tuple(sorted(sessions))
    width = config.indicator_warmup_sessions + config.weight_calibration_sessions + config.validation_sessions + config.unseen_test_sessions
    boundaries: list[WeightedWalkForwardFoldBoundary] = []
    start = 0
    while start + width <= len(session_dates):
        warmup_start = start
        calibration_start = warmup_start + config.indicator_warmup_sessions
        validation_start = calibration_start + config.weight_calibration_sessions
        test_start = validation_start + config.validation_sessions
        test_end = test_start + config.unseen_test_sessions - 1
        boundary = WeightedWalkForwardFoldBoundary(
            fold_id=f"fold-{len(boundaries) + 1}",
            warmup_start=session_dates[warmup_start],
            warmup_end=session_dates[calibration_start - 1],
            calibration_start=session_dates[calibration_start],
            calibration_end=session_dates[validation_start - 1],
            validation_start=session_dates[validation_start],
            validation_end=session_dates[test_start - 1],
            test_start=session_dates[test_start],
            test_end=session_dates[test_end],
            manifest_hash=manifest_hash,
            reason_codes=("weighted_voting.walk_forward.boundary.chronological",),
        )
        boundaries.append(boundary)
        start += config.step_forward_sessions
    return boundaries


def _candles_for_range(sessions: dict[date, tuple[WeightedVotingCandle, ...]], start: date, end: date) -> tuple[WeightedVotingCandle, ...]:
    return tuple(candle for session_date in sorted(sessions) if start <= session_date <= end for candle in sessions[session_date])


def _mode_summary(mode: str, results: tuple[WeightedWalkForwardModeResult, ...]) -> WeightedWalkForwardModeSummary:
    net_pnl = sum(result.test_result.algorithm_results.net_pnl for result in results)
    trade_count = sum(len(result.test_result.trades) for result in results)
    returns = [result.test_result.algorithm_results.return_percent for result in results]
    drawdown = max((result.test_result.algorithm_results.maximum_drawdown for result in results), default=0.0)
    regime_returns: dict[str, list[float]] = defaultdict(list)
    for result in results:
        for trade in result.test_result.trades:
            regime_returns[trade.regime_label].append(trade.net_pnl)
    regime_means = [_mean(values) for values in regime_returns.values()]
    regime_dependence = max(regime_means) - min(regime_means) if len(regime_means) > 1 else 0.0
    return WeightedWalkForwardModeSummary(
        mode=mode,
        fold_count=len(results),
        net_pnl=round(net_pnl, 10),
        return_percent=round(_mean(returns), 10),
        sharpe=round(_mean([result.test_result.algorithm_results.sharpe for result in results]), 10),
        sortino=round(_mean([result.test_result.algorithm_results.sortino for result in results]), 10),
        calmar=round(_mean([result.test_result.algorithm_results.calmar for result in results]), 10),
        maximum_drawdown=round(drawdown, 10),
        profit_factor=round(_mean([result.test_result.algorithm_results.profit_factor for result in results]), 10),
        regime_dependence_score=round(regime_dependence, 10),
        trade_count=trade_count,
    )


def _dynamic_promoted(summaries: dict[str, WeightedWalkForwardModeSummary]) -> bool:
    dynamic = summaries[MODE_PERFORMANCE_DYNAMIC]
    static = summaries[MODE_PERFORMANCE_STATIC]
    equal = summaries[MODE_STATIC_EQUAL]
    return (
        dynamic.sharpe > max(static.sharpe, equal.sharpe)
        and dynamic.sortino >= static.sortino
        and dynamic.maximum_drawdown <= max(static.maximum_drawdown, 1.0) * 1.10
        and dynamic.regime_dependence_score <= max(static.regime_dependence_score, 1.0) * 1.25
    )


def _reproducibility_key(config: WeightedWalkForwardConfig, manifest_hash: str) -> str:
    payload = {
        "runId": config.run_id,
        "manifestHash": manifest_hash,
        "version": WEIGHTED_VOTING_WALK_FORWARD_VERSION,
        "indicatorWarmupSessions": config.indicator_warmup_sessions,
        "weightCalibrationSessions": config.weight_calibration_sessions,
        "validationSessions": config.validation_sessions,
        "unseenTestSessions": config.unseen_test_sessions,
        "stepForwardSessions": config.step_forward_sessions,
        "symbol": config.symbol,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0

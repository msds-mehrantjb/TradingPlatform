from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.algorithms.wca.configuration import WcaConfiguration, WcaLimitedAutomaticPaperSettings, default_wca_configuration
from backend.app.algorithms.wca.contracts import (
    WcaAlgorithmRiskStatus,
    WcaCandle,
    WcaDataQualityStatus,
    WcaEvaluationStatus,
    WcaLiquidityStatus,
    WcaMarketSnapshot,
    WcaMarketStatus,
    WcaQuote,
    WcaRuntimeMode,
    WcaSide,
    WcaStrategyEvaluation,
    WcaVolatilityStatus,
)
from backend.app.algorithms.wca.dynamic_profile import WcaDynamicProfileConfig, resolve_dynamic_profile
from backend.app.algorithms.wca.execution_pipeline import (
    WcaExecutionPipelineInput,
    run_wca_paper_pipeline_adapter,
    run_wca_replay_pipeline_adapter,
)
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WcaRuntimeSettings, WcaRuntimeSupervisor
from backend.app.algorithms.wca.weights import baseline_weight_snapshot


TS = datetime(2026, 1, 5, 15, 29, tzinfo=timezone.utc)


def test_limited_automatic_paper_defaults_are_wca_configuration_not_worker_constants() -> None:
    configuration = default_wca_configuration()
    controls = configuration.limited_automatic_paper

    assert controls.symbol == "SPY"
    assert controls.max_quantity == 10
    assert controls.max_daily_trades == 3
    assert controls.max_daily_loss_dollars == 100
    assert controls.entry_windows == ("10:00-11:30 America/New_York", "13:30-15:30 America/New_York")
    assert controls.permitted_strategy_ids == ("C1", "C4", "C7")
    assert "backend.app.algorithms.weighted" not in Path("backend/app/algorithms/wca/configuration.py").read_text(encoding="utf-8")


def test_baseline_preservation_and_limited_overlay_bounds() -> None:
    configuration = default_wca_configuration()
    baseline = configuration.to_baseline_settings()
    limited = configuration.for_runtime_mode(WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER)
    limited_baseline = limited.to_baseline_settings()

    assert configuration.to_baseline_settings().deterministic_json() == baseline.deterministic_json()
    assert limited_baseline.max_allowed_shares == 10
    assert limited_baseline.max_daily_trades == 3
    assert limited_baseline.max_daily_loss_dollars == 100
    assert limited_baseline.permitted_strategy_ids == ("C1", "C4", "C7")
    assert limited_baseline.max_allowed_shares <= (baseline.max_allowed_shares or 10)
    assert limited_baseline.max_daily_trades <= baseline.max_daily_trades


def test_dynamic_overlay_only_tightens_and_persists_reproduction_metadata() -> None:
    baseline = default_wca_configuration().for_runtime_mode(WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER).to_baseline_settings()
    before = baseline.deterministic_json()

    profile = resolve_dynamic_profile(
        baseline=baseline,
        market_status=market_status(
            volatility=WcaVolatilityStatus.HIGH,
            liquidity=WcaLiquidityStatus.THIN,
            data_quality=WcaDataQualityStatus.DEGRADED,
            algorithm_risk=WcaAlgorithmRiskStatus.DEFENSIVE,
        ),
        calculation_timestamp=TS,
    )
    effective = profile.effective_settings

    assert baseline.deterministic_json() == before
    assert effective.final_risk_percent <= baseline.base_risk_percent
    assert effective.final_max_allowed_shares <= baseline.max_allowed_shares
    assert effective.final_max_daily_trades <= baseline.max_daily_trades
    assert effective.final_max_daily_loss_dollars == baseline.max_daily_loss_dollars
    assert effective.dynamic_profile_name == effective.profile_id
    assert effective.overlay_values
    assert effective.effective_configuration["baseline_configuration_version"] == baseline.settings_version
    assert effective.profile_transition_state == "calculated"


def test_dynamic_profile_hysteresis_holds_previous_defensive_profile() -> None:
    baseline = default_wca_configuration().to_baseline_settings()
    config = WcaDynamicProfileConfig(minimum_profile_hold_seconds=300, profile_ttl_seconds=900)
    defensive = resolve_dynamic_profile(
        baseline=baseline,
        market_status=market_status(volatility=WcaVolatilityStatus.HIGH, algorithm_risk=WcaAlgorithmRiskStatus.DEFENSIVE),
        calculation_timestamp=TS,
        config=config,
    )

    held = resolve_dynamic_profile(
        baseline=baseline,
        market_status=market_status(),
        calculation_timestamp=TS + timedelta(seconds=60),
        previous_profile=defensive,
        config=config,
    )

    assert held.profile_id == defensive.profile_id
    assert held.effective_settings.profile_transition_state == "held_previous"
    assert "wca.dynamic_profile.hold_previous" in held.reason_codes


def test_invalid_limited_settings_are_rejected() -> None:
    with pytest.raises(ValidationError, match="SPY-only"):
        WcaLimitedAutomaticPaperSettings(symbol="QQQ")
    with pytest.raises(ValidationError, match="unknown WCA limited-paper strategy IDs"):
        WcaLimitedAutomaticPaperSettings(permitted_strategy_ids=("C1", "C99"))
    with pytest.raises(ValidationError, match="entry window"):
        WcaLimitedAutomaticPaperSettings(entry_windows=("15:30-10:00 America/New_York",))


def test_configuration_activation_is_atomic_at_candle_boundary() -> None:
    repository = phase11_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    active = default_wca_configuration()
    repository.initialize_defaults(symbol="SPY", configuration=active.model_dump(mode="json"), weight_snapshot=baseline_weight_snapshot(), engine_version="phase11")
    candidate = active.model_copy(update={"configuration_version": "phase11-candidate", "content_hash": ""})
    repository.save_candidate_configuration(candidate, engine_version="phase11")
    boundary = TS + timedelta(minutes=1)
    command = runtime_command(
        WcaRuntimeCommandType.CONFIGURATION_ACTIVATION,
        account_id="paper",
        payload={"configuration_version": "phase11-candidate", "finalized_candle_timestamp": boundary.isoformat()},
    )
    runtime_repository.enqueue_command(command)
    supervisor = WcaRuntimeSupervisor(repository=repository, runtime_repository=runtime_repository, settings=WcaRuntimeSettings(account_id="paper"), owner_id="phase11")

    result = next(worker for worker in supervisor.workers if worker.worker_name == "configuration_activation_worker").run_once()

    assert result["status"] == "completed"
    assert repository.read_active_configuration().configuration_version == "phase11-candidate"
    assert repository.read_active_configuration().activation_timestamp == boundary
    assert command_status(repository, command.command_id) == "completed"


def test_limited_paper_pipeline_applies_configured_strategy_and_risk_controls() -> None:
    configuration = default_wca_configuration().for_runtime_mode(WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER)
    result = run_wca_paper_pipeline_adapter(
        WcaExecutionPipelineInput(
            run_id="phase11-limited",
            decision_id="phase11-limited-decision",
            order_intent_id="phase11-limited-intent",
            snapshot=snapshot(),
            configuration_version=configuration.configuration_version,
            configuration=configuration,
            runtime_mode=WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER,
            weight_snapshot=baseline_weight_snapshot(),
            account_equity=100_000,
            available_buying_power=100_000,
            authoritative_account_values=True,
        ),
        voters=(_AlwaysBuy("C1"), _AlwaysBuy("C2"), _AlwaysBuy("C4"), _AlwaysBuy("C7")),
    )

    effective = result.decision.effective_settings
    shadow = next(row for row in result.decision.aggregation.strategy_evaluations if row.strategy_id == "C2")
    assert effective.final_max_allowed_shares == 10
    assert effective.final_max_daily_trades == 3
    assert effective.final_max_daily_loss_dollars == 100
    assert effective.final_entry_windows == ("10:00-11:30 America/New_York", "13:30-15:30 America/New_York")
    assert effective.final_permitted_strategy_ids == ("C1", "C4", "C7")
    assert shadow.status == WcaEvaluationStatus.NOT_APPLICABLE
    assert "wca.limited_paper.strategy_shadow_only" in shadow.reason_codes


def test_replay_and_paper_resolve_identical_effective_settings() -> None:
    configuration = default_wca_configuration()
    common = dict(
        run_id="phase11-parity",
        decision_id="phase11-parity-decision",
        order_intent_id="phase11-parity-intent",
        snapshot=snapshot(),
        configuration_version=configuration.configuration_version,
        configuration=configuration,
        weight_snapshot=baseline_weight_snapshot(),
    )

    replay = run_wca_replay_pipeline_adapter(WcaExecutionPipelineInput(**common), voters=(_AlwaysBuy("C1"), _AlwaysBuy("C4"), _AlwaysBuy("C7")))
    paper = run_wca_paper_pipeline_adapter(WcaExecutionPipelineInput(**common), voters=(_AlwaysBuy("C1"), _AlwaysBuy("C4"), _AlwaysBuy("C7")))

    assert replay.dynamic_profile.effective_settings == paper.dynamic_profile.effective_settings
    assert replay.decision.effective_settings == paper.decision.effective_settings


class _AlwaysBuy:
    family = "trend"
    version = "phase11_test_voter_v1"
    name = "Always Buy"

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id

    def evaluate(self, market: WcaMarketSnapshot) -> WcaStrategyEvaluation:
        return WcaStrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            name=self.name,
            status=WcaEvaluationStatus.ACTIVE,
            signal=WcaSide.BUY,
            confidence=0.8,
            raw_confidence=0.8,
            calibrated_confidence=0.8,
            direction=WcaSide.BUY,
            applicability=WcaEvaluationStatus.ACTIVE,
            evidence_strength=0.8,
            data_quality_status=WcaEvaluationStatus.ACTIVE,
            base_weight=0.1,
            effective_weight=0.1,
            contribution=0.08,
        )


def snapshot() -> WcaMarketSnapshot:
    candles = tuple(
        WcaCandle(
            timestamp=TS - timedelta(minutes=59 - index),
            open=100 + index * 0.01,
            high=100.2 + index * 0.01,
            low=99.9 + index * 0.01,
            close=100.05 + index * 0.01,
            volume=300_000,
        )
        for index in range(60)
    )
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=candles[-1].timestamp,
        decision_timestamp=candles[-1].timestamp,
        candles=candles,
        quote=WcaQuote(timestamp=candles[-1].timestamp, bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01),
    )


def market_status(
    *,
    volatility: WcaVolatilityStatus = WcaVolatilityStatus.NORMAL,
    liquidity: WcaLiquidityStatus = WcaLiquidityStatus.NORMAL,
    data_quality: WcaDataQualityStatus = WcaDataQualityStatus.HEALTHY,
    algorithm_risk: WcaAlgorithmRiskStatus = WcaAlgorithmRiskStatus.NORMAL,
) -> WcaMarketStatus:
    return WcaMarketStatus(
        status=WcaEvaluationStatus.ACTIVE,
        volatility=volatility,
        liquidity=liquidity,
        data_quality=data_quality,
        algorithm_risk=algorithm_risk,
        input_timestamp=TS,
        profile_expiration=TS + timedelta(minutes=15),
    )


def phase11_repository() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-phase11-{uuid4().hex}.sqlite'}")


def command_status(repository: WcaSqliteRepository, command_id: str) -> str:
    with sqlite3.connect(repository.path) as conn:
        return str(conn.execute("SELECT status FROM wca_runtime_command_queue WHERE command_id = ?", (command_id,)).fetchone()[0])

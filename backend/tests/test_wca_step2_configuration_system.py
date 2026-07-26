from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.algorithms.wca.backtest.engine import run_wca_backtest
from backend.app.algorithms.wca.configuration import (
    WCA_HARD_FILTER_SETTINGS_MODELS,
    WCA_MODIFIER_SETTINGS_MODELS,
    WCA_PRIMARY_STRATEGY_SETTINGS_MODELS,
    WcaAggregationSettings,
    WcaConfiguration,
    WcaConfigurationUnavailable,
    WcaRiskSettings,
    default_wca_configuration,
)
from backend.app.algorithms.wca.contracts import (
    BacktestRunConfiguration,
    WcaBacktestRequest,
    WcaCandle,
    WcaEvaluationStatus,
    WcaMarketSnapshot,
    WcaSide,
    WcaStrategyEvaluation,
)
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_execution_pipeline
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.strategy_registry import WCA_HARD_FILTER_REGISTRY, WCA_MODIFIER_REGISTRY, WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.weights import baseline_weight_snapshot


def test_canonical_configuration_has_dedicated_immutable_settings() -> None:
    configuration = default_wca_configuration()

    assert len(configuration.primary_strategy_settings.model_dump()) == 11
    assert len(configuration.modifier_settings.model_dump()) == 11
    assert len(configuration.hard_filter_settings.model_dump()) == 7
    assert set(WCA_PRIMARY_STRATEGY_SETTINGS_MODELS) == {entry.slug for entry in WCA_STRATEGY_REGISTRY}
    assert set(WCA_MODIFIER_SETTINGS_MODELS) == {entry.slug for entry in WCA_MODIFIER_REGISTRY}
    assert set(WCA_HARD_FILTER_SETTINGS_MODELS) == {entry.slug for entry in WCA_HARD_FILTER_REGISTRY}
    assert all(entry.settings_model.endswith(WCA_PRIMARY_STRATEGY_SETTINGS_MODELS[entry.slug].__name__) for entry in WCA_STRATEGY_REGISTRY)
    assert all(entry.settings_model.endswith(WCA_MODIFIER_SETTINGS_MODELS[entry.slug].__name__) for entry in WCA_MODIFIER_REGISTRY)
    assert all(entry.settings_model.endswith(WCA_HARD_FILTER_SETTINGS_MODELS[entry.slug].__name__) for entry in WCA_HARD_FILTER_REGISTRY)
    assert sum(configuration.weights.baseline_weights.values()) == pytest.approx(1.0)
    with pytest.raises(ValidationError):
        configuration.risk.base_risk_percent = 2.0


def test_configuration_documentation_inventory_counts_match_code() -> None:
    docs = Path("docs/wca/canonical_configuration.md").read_text(encoding="utf-8")

    assert f"Primary strategies: {len(WCA_STRATEGY_REGISTRY)}" in docs
    assert f"Contextual modifiers: {len(WCA_MODIFIER_REGISTRY)}" in docs
    assert f"Hard filters: {len(WCA_HARD_FILTER_REGISTRY)}" in docs
    assert "wca_active_configuration" in docs
    assert "wca.configuration.missing_active_revision" in docs


def test_configuration_validation_rejects_bad_threshold_ordering_and_hard_cap_breach() -> None:
    with pytest.raises(ValidationError, match="threshold ordering"):
        WcaConfiguration(aggregation=WcaAggregationSettings(buy_threshold=0.70, strong_buy_threshold=0.60))

    with pytest.raises(ValidationError, match="hard_max_risk_percent"):
        WcaConfiguration(risk=WcaRiskSettings(base_risk_percent=2.0, hard_max_risk_percent=1.0))


def test_repository_can_activate_restart_and_rollback_complete_revisions() -> None:
    db_path = _workspace_db_path()
    repository = WcaSqliteRepository(f"sqlite:///{db_path.as_posix()}")
    first = default_wca_configuration()
    repository.initialize_defaults(
        symbol="SPY",
        configuration=first.model_dump(mode="json"),
        weight_snapshot=baseline_weight_snapshot(),
        engine_version="wca_test",
    )
    active = repository.read_active_configuration()
    assert active is not None

    replacement = active.model_copy(update={"configuration_version": "wca_step2_replacement", "content_hash": ""})
    repository.save_candidate_configuration(replacement, engine_version="wca_test")
    repository.activate_configuration_version("wca_step2_replacement")
    assert repository.read_active_configuration().configuration_version == "wca_step2_replacement"

    restarted = WcaSqliteRepository(f"sqlite:///{db_path.as_posix()}")
    assert restarted.read_active_configuration().configuration_version == "wca_step2_replacement"
    restored = restarted.rollback_configuration(active.configuration_version)
    assert restored.configuration_version == active.configuration_version
    assert restarted.read_configuration_by_version(active.configuration_version).content_hash == active.content_hash


def test_execution_pipeline_blocks_unversioned_defaults_and_tags_outputs() -> None:
    snapshot = _snapshot()
    with pytest.raises(WcaConfigurationUnavailable):
        run_wca_execution_pipeline(
            WcaExecutionPipelineInput(
                run_id="missing-config",
                decision_id="missing-config-decision",
                order_intent_id="missing-config-intent",
                snapshot=snapshot,
                configuration_version="unversioned",
            ),
            voters=(_AlwaysBuy("C1"), _AlwaysBuy("C2"), _AlwaysBuy("C3")),
        )

    configuration = default_wca_configuration()
    result = run_wca_execution_pipeline(
        WcaExecutionPipelineInput(
            run_id="with-config",
            decision_id="with-config-decision",
            order_intent_id="with-config-intent",
            snapshot=snapshot,
            configuration_version=configuration.configuration_version,
            configuration=configuration,
        ),
        voters=(_AlwaysBuy("C1"), _AlwaysBuy("C2"), _AlwaysBuy("C3")),
    )

    assert result.decision.configuration_version == configuration.configuration_version
    assert result.decision.configuration_hash == configuration.content_hash
    assert result.decision.market_snapshot.configuration_hash == configuration.content_hash
    assert all(row.configuration_hash == configuration.content_hash for row in result.decision.aggregation.strategy_evaluations)
    if result.decision.proposed_order is not None:
        assert result.decision.proposed_order.configuration_hash == configuration.content_hash


def test_backtest_uses_same_active_configuration_revision() -> None:
    configuration = default_wca_configuration()
    candles = _candles(70)
    request = WcaBacktestRequest(
        configuration=BacktestRunConfiguration(
            run_id="wca-step2-backtest",
            symbol="SPY",
            start=candles[0].timestamp,
            end=candles[-1].timestamp,
            configuration_version="request-boundary-version",
            data_manifest_hash="",
        ),
        candles=candles,
    )

    result = run_wca_backtest(request, configuration=configuration)

    assert result.run_configuration.configuration_version == configuration.configuration_version
    assert result.run_configuration.configuration_hash == configuration.content_hash
    assert result.decisions
    assert {decision.configuration_hash for decision in result.decisions} == {configuration.content_hash}
    assert all(decision.market_snapshot.configuration_hash == configuration.content_hash for decision in result.decisions)


class _AlwaysBuy:
    family = "trend"
    version = "test_voter_v1"
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


def _snapshot() -> WcaMarketSnapshot:
    candles = _candles(60)
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=candles[-1].timestamp,
        decision_timestamp=candles[-1].timestamp,
        candles=candles,
    )


def _candles(count: int) -> tuple[WcaCandle, ...]:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        price = 100 + index * 0.05
        rows.append(
            WcaCandle(
                timestamp=start + timedelta(minutes=index),
                open=price,
                high=price + 0.15,
                low=price - 0.10,
                close=price + 0.08,
                volume=300_000,
            )
        )
    return tuple(rows)


def _workspace_db_path() -> Path:
    root = Path("tmp")
    root.mkdir(exist_ok=True)
    return (root / f"wca-step2-{uuid4().hex}.db").resolve()

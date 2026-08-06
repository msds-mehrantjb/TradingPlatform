import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

from backend.app import main


def test_api_startup_does_not_embed_algorithm_runtimes_by_default() -> None:
    created_tasks = []

    def capture_task(coro):
        coro.close()
        task = Mock()
        created_tasks.append(task)
        return task

    async def run_startup() -> None:
        original_enabled = main.settings.api_embedded_algorithm_runtimes_enabled
        object.__setattr__(main.settings, "api_embedded_algorithm_runtimes_enabled", False)
        try:
            weighted_supervisor = Mock()
            weighted_supervisor.start = AsyncMock()
            meta_supervisor = Mock()
            meta_supervisor.start = AsyncMock()
            with (
                patch.object(main, "assert_wca_module_catalog_valid") as assert_catalog_valid,
                patch.object(main.asyncio, "create_task", side_effect=capture_task),
                patch.object(main.voting_ensemble_runtime_supervisor, "start", new_callable=AsyncMock) as voting_start,
                patch.object(main, "get_weighted_voting_runtime_supervisor", return_value=weighted_supervisor),
                patch.object(main.regime_runtime_supervisor, "start", new_callable=AsyncMock) as regime_start,
                patch.object(main, "_get_api_meta_strategy_runtime_supervisor", return_value=meta_supervisor) as meta_factory,
            ):
                await main.start_daily_backtest_refresh_scheduler()

            assert_catalog_valid.assert_called_once()
            assert len(created_tasks) == 2
            voting_start.assert_not_awaited()
            weighted_supervisor.start.assert_not_awaited()
            regime_start.assert_not_awaited()
            meta_factory.assert_not_called()
            meta_supervisor.start.assert_not_awaited()
            assert main.WCA_FINALIZED_BAR_TASK is None
        finally:
            object.__setattr__(main.settings, "api_embedded_algorithm_runtimes_enabled", original_enabled)

    asyncio.run(run_startup())


def test_api_startup_can_embed_only_meta_strategy_runtime() -> None:
    created_tasks = []

    def capture_task(coro):
        coro.close()
        task = Mock()
        created_tasks.append(task)
        return task

    async def run_startup() -> None:
        original_values = {
            "api_embedded_algorithm_runtimes_enabled": main.settings.api_embedded_algorithm_runtimes_enabled,
            "api_embedded_meta_strategy_runtime_enabled": main.settings.api_embedded_meta_strategy_runtime_enabled,
            "api_embedded_voting_ensemble_runtime_enabled": main.settings.api_embedded_voting_ensemble_runtime_enabled,
            "api_embedded_weighted_voting_runtime_enabled": main.settings.api_embedded_weighted_voting_runtime_enabled,
            "api_embedded_regime_runtime_enabled": main.settings.api_embedded_regime_runtime_enabled,
            "api_embedded_wca_finalized_bar_enabled": main.settings.api_embedded_wca_finalized_bar_enabled,
        }
        object.__setattr__(main.settings, "api_embedded_algorithm_runtimes_enabled", True)
        object.__setattr__(main.settings, "api_embedded_meta_strategy_runtime_enabled", True)
        object.__setattr__(main.settings, "api_embedded_voting_ensemble_runtime_enabled", False)
        object.__setattr__(main.settings, "api_embedded_weighted_voting_runtime_enabled", False)
        object.__setattr__(main.settings, "api_embedded_regime_runtime_enabled", False)
        object.__setattr__(main.settings, "api_embedded_wca_finalized_bar_enabled", False)
        try:
            weighted_supervisor = Mock()
            weighted_supervisor.start = AsyncMock()
            meta_supervisor = Mock()
            meta_supervisor.start = AsyncMock()
            with (
                patch.object(main, "assert_wca_module_catalog_valid") as assert_catalog_valid,
                patch.object(main.asyncio, "create_task", side_effect=capture_task),
                patch.object(main.voting_ensemble_runtime_supervisor, "start", new_callable=AsyncMock) as voting_start,
                patch.object(main, "get_weighted_voting_runtime_supervisor", return_value=weighted_supervisor),
                patch.object(main.regime_runtime_supervisor, "start", new_callable=AsyncMock) as regime_start,
                patch.object(main, "_get_api_meta_strategy_runtime_supervisor", return_value=meta_supervisor) as meta_factory,
            ):
                await main.start_daily_backtest_refresh_scheduler()

            assert_catalog_valid.assert_called_once()
            assert len(created_tasks) == 2
            voting_start.assert_not_awaited()
            weighted_supervisor.start.assert_not_awaited()
            regime_start.assert_not_awaited()
            meta_factory.assert_called_once()
            meta_supervisor.start.assert_awaited_once()
            assert main.WCA_FINALIZED_BAR_TASK is None
        finally:
            for key, value in original_values.items():
                object.__setattr__(main.settings, key, value)

    asyncio.run(run_startup())


def test_meta_strategy_readiness_provider_is_display_control_only_when_not_embedded() -> None:
    original_values = {
        "api_embedded_algorithm_runtimes_enabled": main.settings.api_embedded_algorithm_runtimes_enabled,
        "api_embedded_meta_strategy_runtime_enabled": main.settings.api_embedded_meta_strategy_runtime_enabled,
    }
    object.__setattr__(main.settings, "api_embedded_algorithm_runtimes_enabled", False)
    object.__setattr__(main.settings, "api_embedded_meta_strategy_runtime_enabled", False)
    try:
        with (
            patch.object(main, "_get_api_meta_strategy_runtime_supervisor") as meta_factory,
            patch.object(main.META_STRATEGY_SERVICE.job_repository, "read_gateway_snapshot", side_effect=KeyError("meta_strategy.runtime.readiness")),
        ):
            readiness = main._meta_strategy_runtime_readiness_for_api()

        meta_factory.assert_not_called()
        assert readiness["ready"] is False
        assert readiness["paperOrdersBlocked"] is True
        assert readiness["apiDisplayControlOnly"] is True
        assert readiness["reasonCodes"] == ("meta_strategy.runtime.external_worker_required",)
    finally:
        for key, value in original_values.items():
            object.__setattr__(main.settings, key, value)


def test_meta_strategy_readiness_provider_reads_external_worker_snapshot_when_not_embedded() -> None:
    original_values = {
        "api_embedded_algorithm_runtimes_enabled": main.settings.api_embedded_algorithm_runtimes_enabled,
        "api_embedded_meta_strategy_runtime_enabled": main.settings.api_embedded_meta_strategy_runtime_enabled,
    }
    object.__setattr__(main.settings, "api_embedded_algorithm_runtimes_enabled", False)
    object.__setattr__(main.settings, "api_embedded_meta_strategy_runtime_enabled", False)
    snapshot = {
        "algorithmId": "meta_strategy",
        "ready": True,
        "status": "ready",
        "paperOrdersBlocked": False,
        "lastHealthCheckAt": datetime.now(UTC).isoformat(),
        "reasonCodes": (),
    }
    try:
        with (
            patch.object(main, "_get_api_meta_strategy_runtime_supervisor") as meta_factory,
            patch.object(main.META_STRATEGY_SERVICE.job_repository, "read_gateway_snapshot", return_value=snapshot),
        ):
            readiness = main._meta_strategy_runtime_readiness_for_api()

        meta_factory.assert_not_called()
        assert readiness["ready"] is True
        assert readiness["paperOrdersBlocked"] is False
        assert readiness["apiDisplayControlOnly"] is True
        assert readiness["apiEmbeddedRuntimeEnabled"] is False
        assert readiness["readinessSource"] == "external_worker_snapshot"
    finally:
        for key, value in original_values.items():
            object.__setattr__(main.settings, key, value)

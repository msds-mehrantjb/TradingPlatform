import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.local_settings_risk import MetaStrategyLocalSettingsRiskSource
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore


_TMP_ROOT = Path.cwd() / "data" / "test_tmp"
_TMP_ROOT.mkdir(exist_ok=True)


class MetaStrategyTradingSettingsPanelContractTest(unittest.TestCase):
    def test_trading_settings_view_is_backend_owned_and_inventory_scoped(self) -> None:
        service = _service("view")

        response = service.query_trading_settings()
        payload = response["payload"]

        self.assertEqual(response["status"], "OK")
        self.assertEqual(payload["algorithmId"], ALGORITHM_ID)
        self.assertEqual(payload["capitalPartitionId"], META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
        self.assertTrue(payload["ownership"]["algorithmScoped"])
        self.assertEqual(payload["ownership"]["inventorySource"], "authoritative_meta_strategy_inventory_repository")
        self.assertNotIn("startingCapital", payload["readOnlyFinancialFields"])

    def test_trading_settings_update_persists_new_meta_strategy_version(self) -> None:
        prefix = f"persist-{uuid4().hex}"
        service = _service(prefix)
        before = service.query_trading_settings()["payload"]["settingsVersion"]

        response = service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "updatedBy": "test",
                "tradingSettings": {
                    "startingCapital": 25_000,
                    "dailyAllocationPercent": 50,
                    "baseRiskPercent": 0.25,
                    "maxDailyLossPercent": 1,
                    "orderAllocationPercent": 7.5,
                    "dailyAllocationPercent": 50,
                    "riskBudgetPercentOfOrder": 50,
                    "maxPositionPercent": 25,
                    "stopLossPercent": 0.35,
                    "minimumStopDistancePercent": 0.05,
                    "maxTradesPerDay": 4,
                    "pyramidingEnabled": False,
                    "mlInferenceMode": "DISABLED",
                },
            }
        )

        self.assertEqual(response["status"], "OK")
        self.assertNotEqual(response["payload"]["settingsVersion"], before)
        self.assertEqual(response["payload"]["tradingSettings"]["startingCapital"], 25_000)
        self.assertEqual(response["payload"]["tradingSettings"]["baseRiskPercent"], 0.25)
        self.assertEqual(response["payload"]["tradingSettings"]["maxDailyLossPercent"], 1.0)
        self.assertEqual(response["payload"]["tradingSettings"]["orderAllocationPercent"], 7.5)
        self.assertEqual(response["payload"]["tradingSettings"]["dailyAllocationPercent"], 50.0)
        self.assertEqual(response["payload"]["tradingSettings"]["riskBudgetPercentOfOrder"], 50.0)
        self.assertEqual(response["payload"]["tradingSettings"]["maxPositionPercent"], 25.0)
        self.assertAlmostEqual(response["payload"]["tradingSettings"]["stopLossPercent"], 0.35)
        self.assertAlmostEqual(response["payload"]["tradingSettings"]["minimumStopDistancePercent"], 0.05)
        self.assertEqual(response["payload"]["tradingSettings"]["mlInferenceMode"], "DISABLED")
        self.assertEqual(response["payload"]["targetOrder"]["orderLimitDollars"], 1875.0)
        self.assertEqual(response["payload"]["targetOrder"]["riskDollars"], 62.5)
        restarted = _service(prefix)
        restarted_payload = restarted.query_trading_settings()["payload"]
        self.assertEqual(restarted_payload["settingsVersion"], response["payload"]["settingsVersion"])
        self.assertEqual(restarted_payload["tradingSettings"]["maxTradesPerDay"], 4)
        self.assertEqual(restarted_payload["tradingSettings"]["startingCapital"], 25_000)
        self.assertEqual(restarted_payload["tradingSettings"]["mlInferenceMode"], "DISABLED")

    def test_trading_settings_feed_local_paper_account_and_risk_source(self) -> None:
        service = _service("local-risk")

        response = service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "updatedBy": "test",
                "tradingSettings": {
                    "startingCapital": 25_000,
                    "baseRiskPercent": 0.25,
                    "maxDailyLossPercent": 1,
                    "maxAllowedShares": 0,
                },
            }
        )
        source = MetaStrategyLocalSettingsRiskSource(
            settings_store=service.settings_store,
            inventory_repository=service.repository,
        )
        account = source.read_account_snapshot(at=response_time())
        risk = source.read_global_risk_snapshot(at=response_time(), capital_partition_id=META_STRATEGY_DEFAULT_CAPITAL_PARTITION)

        self.assertEqual(response["status"], "OK")
        self.assertEqual(account["source"], "meta_strategy_local_settings_risk")
        self.assertEqual(account["accountEquity"], 25_000)
        self.assertEqual(account["buyingPower"], 25_000)
        self.assertFalse(account["liveTradingEnabled"])
        self.assertEqual(risk["availableRiskDollars"], 62.5)
        self.assertGreater(risk["maxQuantity"], 0)
        self.assertEqual(service.repository.current_inventory_snapshot().open_positions, ())

    def test_foreign_algorithm_and_caller_inventory_are_rejected(self) -> None:
        service = _service("reject")

        foreign = service.update_trading_settings(
            {
                "algorithmId": "weighted_voting",
                "tradingSettings": {"baseRiskPercent": 0.1},
            }
        )
        caller_inventory = service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "tradingSettings": {"baseRiskPercent": 0.1, "inventory": {"SPY": 100}},
            }
        )

        self.assertEqual(foreign["status"], "REJECTED")
        self.assertIn("meta_strategy.service.foreign_algorithm_rejected", foreign["reasonCodes"])
        self.assertEqual(caller_inventory["status"], "REJECTED")
        self.assertIn("meta_strategy.service.caller_authoritative_state_rejected", caller_inventory["reasonCodes"])

    def test_zero_setting_remains_zero_and_does_not_touch_inventory(self) -> None:
        service = _service("zero")
        before_inventory = service.query_inventory()["payload"]["inventory"]

        response = service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "tradingSettings": {"baseRiskPercent": 0.0, "maxAllowedShares": 0},
            }
        )
        after_inventory = service.query_inventory()["payload"]["inventory"]

        self.assertEqual(response["status"], "OK")
        self.assertEqual(response["payload"]["tradingSettings"]["baseRiskPercent"], 0.0)
        self.assertEqual(response["payload"]["tradingSettings"]["maxAllowedShares"], 0)
        self.assertEqual(after_inventory["open_positions"], before_inventory["open_positions"])
        self.assertEqual(after_inventory["daily_trade_count"], before_inventory["daily_trade_count"])

    def test_trading_settings_reject_filter_ml_mode_without_promoted_model(self) -> None:
        service = _service("ml-filter-rejected")

        response = service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "tradingSettings": {"mlInferenceMode": "FILTER"},
            }
        )

        self.assertEqual(response["status"], "REJECTED")
        self.assertIn("meta_strategy.service.trading_settings_invalid", response["reasonCodes"])
        self.assertIn("ml_inference_mode_requires_disabled_or_shadow", response["payload"]["error"])


def _service(prefix: str) -> MetaStrategyApplicationService:
    settings = MetaStrategySettingsStore(_TMP_ROOT / f"{prefix}-settings.sqlite")
    jobs = MetaStrategyJobRepository(f"sqlite:///{_TMP_ROOT / f'{prefix}-jobs.sqlite'}")
    inventory = MetaStrategySqliteRepository(f"sqlite:///{_TMP_ROOT / f'{prefix}-inventory.sqlite'}")
    return MetaStrategyApplicationService(settings_store=settings, job_repository=jobs, repository=inventory)


def response_time() -> datetime:
    return datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


if __name__ == "__main__":
    unittest.main()

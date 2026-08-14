import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.local_settings_risk import MetaStrategyLocalSettingsRiskSource
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore
from backend.app.domain.models import Signal
from backend.app.gates import GlobalOrderProposal


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
        self.assertEqual(service.query_inventory()["payload"]["authoritativeInventoryApi"], "current_inventory_snapshot")
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

    def test_trading_settings_update_recursively_rejects_caller_supplied_authoritative_state_aliases(self) -> None:
        service = _service("recursive-reject")

        response = service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "tradingSettings": {
                    "baseRiskPercent": 0.1,
                    "cash": 999_999,
                    "balance": 999_999,
                    "positionLots": [],
                    "profitAndLoss": 123.45,
                    "risk": {"reservedRisk": 50.0},
                },
            }
        )

        self.assertEqual(response["status"], "REJECTED")
        self.assertIn("meta_strategy.service.caller_authoritative_state_rejected", response["reasonCodes"])
        self.assertEqual(
            set(response["payload"]["rejectedFields"]),
            {
                "tradingSettings.balance",
                "tradingSettings.cash",
                "tradingSettings.positionLots",
                "tradingSettings.profitAndLoss",
                "tradingSettings.risk.reservedRisk",
            },
        )

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


    def test_local_paper_account_uses_meta_strategy_capital_not_external_broker_balance(self) -> None:
        service = _service(f"local-account-isolated-{uuid4().hex}")
        service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "updatedBy": "test",
                "tradingSettings": {
                    "startingCapital": 31_000,
                    "baseRiskPercent": 1.0,
                    "maxDailyLossPercent": 5.0,
                    "maxAllowedShares": 50,
                },
            }
        )
        service.repository.record_order_intent(_inventory_payload("intent-1", reservedRiskDollars=250.0, quantity=5))

        source = MetaStrategyLocalSettingsRiskSource(
            settings_store=service.settings_store,
            inventory_repository=service.repository,
        )
        account = source.read_account_snapshot(at=response_time())
        risk = source.read_global_risk_snapshot(at=response_time(), capital_partition_id=META_STRATEGY_DEFAULT_CAPITAL_PARTITION)

        self.assertEqual(account["accountId"], f"{ALGORITHM_ID}:{META_STRATEGY_DEFAULT_CAPITAL_PARTITION}")
        self.assertEqual(account["accountType"], "paper")
        self.assertEqual(account["allocatedCapital"], 31_000)
        self.assertEqual(account["accountEquity"], 31_000)
        self.assertEqual(account["buyingPower"], 30_250)
        self.assertEqual(account["cashAvailable"], 30_250)
        self.assertEqual(account["reservedRiskDollars"], 250.0)
        self.assertEqual(account["reservedCapitalDollars"], 500.0)
        self.assertEqual(account["realisedPnl"], 0.0)
        self.assertEqual(account["unrealisedPnl"], 0.0)
        self.assertEqual(account["feesAndSlippage"], 0.0)
        self.assertEqual(account["dailyTradeCount"], 0)
        self.assertEqual(account["accountAuthority"], "meta_strategy_inventory.current_inventory_snapshot")
        self.assertFalse(account["liveTradingEnabled"])
        self.assertEqual(risk["availableRiskDollars"], 310.0)
        self.assertEqual(risk["maxQuantity"], 50)
        self.assertFalse(risk["reject"])

    def test_local_account_equity_cash_and_pnl_are_rebuilt_from_meta_strategy_inventory_only(self) -> None:
        service = _service(f"local-account-ledger-formula-{uuid4().hex}")
        service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "updatedBy": "test",
                "tradingSettings": {"startingCapital": 1_000, "baseRiskPercent": 1.0},
            }
        )
        service.repository.ingest_broker_fill(
            _inventory_payload(
                "entry-ledger-formula",
                brokerFillId="entry-ledger-formula-fill",
                quantity=2,
                price=100.0,
                side="BUY",
                commission=1.0,
                estimatedSlippage=0.5,
            )
        )
        service.repository.ingest_broker_fill(
            _inventory_payload(
                "exit-ledger-formula",
                brokerFillId="exit-ledger-formula-fill",
                quantity=1,
                price=110.0,
                side="SELL",
                commission=1.0,
                estimatedSlippage=0.5,
            )
        )

        source = MetaStrategyLocalSettingsRiskSource(
            settings_store=service.settings_store,
            inventory_repository=service.repository,
        )
        account = source.read_account_snapshot(at=response_time())

        self.assertEqual(account["algorithmId"], ALGORITHM_ID)
        self.assertEqual(account["capitalPartitionId"], META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
        self.assertEqual(account["allocatedCapital"], 1_000)
        self.assertEqual(account["realisedPnl"], 10.0)
        self.assertEqual(account["unrealisedPnl"], 0.0)
        self.assertEqual(account["feesAndSlippage"], 3.0)
        self.assertEqual(account["accountEquity"], 1_007.0)
        self.assertEqual(account["cashAvailable"], 907.0)
        self.assertEqual(account["buyingPower"], 907.0)
        self.assertEqual(account["reservedRiskDollars"], 0.0)
        self.assertEqual(account["reservedCapitalDollars"], 0.0)
        self.assertEqual(account["dailyTradeCount"], 1)

    def test_pending_orders_reserve_cash_and_partial_or_terminal_events_adjust_buying_power(self) -> None:
        service = _service(f"pending-cash-reservation-{uuid4().hex}")
        service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "updatedBy": "test",
                "tradingSettings": {"startingCapital": 1_000, "baseRiskPercent": 10.0, "maxAllowedShares": 20, "maxPositionPercent": 100.0},
            }
        )
        service.repository.record_order_intent(_inventory_payload("pending-cash", reservedRiskDollars=50.0, quantity=5, price=100.0))
        source = MetaStrategyLocalSettingsRiskSource(
            settings_store=service.settings_store,
            inventory_repository=service.repository,
        )

        pending = source.read_account_snapshot(at=response_time())
        approval = source.approve_order(_proposal(quantity=10, price=100.0, planned_risk=10.0))

        self.assertEqual(pending["reservedCapitalDollars"], 500.0)
        self.assertEqual(pending["reservedRiskDollars"], 50.0)
        self.assertEqual(pending["buyingPower"], 450.0)
        self.assertEqual(approval.action, "REDUCE_QUANTITY")
        self.assertEqual(approval.maximumAllowedQuantity, 4)
        self.assertIn("meta_strategy.local_settings_risk.quantity_reduced_to_buying_power", approval.rejectionReasons)

        service.repository.ingest_broker_fill(
            _inventory_payload(
                "pending-cash",
                brokerFillId="pending-cash-partial-fill",
                reservedRiskDollars=50.0,
                quantity=2,
                price=100.0,
                side="BUY",
            )
        )
        partial = source.read_account_snapshot(at=response_time())

        self.assertEqual(partial["reservedCapitalDollars"], 300.0)
        self.assertEqual(partial["reservedRiskDollars"], 30.0)
        self.assertEqual(partial["buyingPower"], 470.0)

        service.repository.record_order_status(
            {
                **_inventory_payload("pending-cash", reservedRiskDollars=50.0, quantity=5, price=100.0),
                "eventId": "pending-cash-cancelled",
                "orderStatus": "CANCELLED",
                "status": "CANCELLED",
            }
        )
        cancelled = source.read_account_snapshot(at=response_time())

        self.assertEqual(cancelled["reservedCapitalDollars"], 0.0)
        self.assertEqual(cancelled["reservedRiskDollars"], 0.0)
        self.assertEqual(cancelled["buyingPower"], 800.0)

    def test_risk_reducing_exit_is_allowed_when_new_entry_local_risk_is_exhausted(self) -> None:
        service = _service(f"risk-reducing-exit-{uuid4().hex}")
        service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "updatedBy": "test",
                "tradingSettings": {"startingCapital": 1_000, "baseRiskPercent": 0.0, "maxAllowedShares": 0},
            }
        )
        source = MetaStrategyLocalSettingsRiskSource(
            settings_store=service.settings_store,
            inventory_repository=service.repository,
        )

        approval = source.approve_order(
            _proposal(quantity=2, price=100.0, planned_risk=0.0, intent="risk_reducing", side=Signal.SELL)
        )

        self.assertEqual(approval.action, "ALLOW")
        self.assertEqual(approval.maximumAllowedQuantity, 2)
        self.assertNotIn("meta_strategy.sizing.zero_algorithm_risk", approval.rejectionReasons)
        self.assertNotIn("meta_strategy.sizing.zero_buying_power", approval.rejectionReasons)

    def test_meta_strategy_inventory_contract_is_complete_and_rejects_sibling_state(self) -> None:
        service = _service("inventory-contract")
        service.update_trading_settings(
            {
                "algorithmId": ALGORITHM_ID,
                "updatedBy": "test",
                "tradingSettings": {"startingCapital": 40_000, "baseRiskPercent": 0.5},
            }
        )
        service.repository.ingest_broker_fill(
            _inventory_payload(
                "entry-1",
                brokerFillId="fill-entry-1",
                quantity=3,
                price=100.0,
                side="BUY",
                strategyId="opening_range_breakout",
                family="breakout",
            )
        )

        with self.assertRaises(Exception):
            service.repository.ingest_broker_fill(
                {
                    **_inventory_payload("foreign-entry", brokerFillId="foreign-fill", quantity=10, price=1.0),
                    "algorithmId": "weighted_voting",
                    "capitalPartitionId": "weighted_voting.paper.default",
                }
            )

        inventory = service.query_inventory()["payload"]["inventory"]

        self.assertEqual(inventory["algorithm_id"], ALGORITHM_ID)
        self.assertEqual(inventory["capital_partition_id"], META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
        self.assertEqual(inventory["allocated_capital"], 40_000)
        self.assertEqual(len(inventory["open_positions"]), 1)
        self.assertEqual(len(inventory["open_lots"]), 1)
        self.assertEqual(inventory["realised_pnl"], 0.0)
        self.assertEqual(inventory["unrealised_pnl"], 0.0)
        self.assertEqual(inventory["fees_and_slippage"], 0.0)
        self.assertEqual(inventory["reserved_risk_dollars"], 0.0)
        self.assertEqual(inventory["daily_trade_count"], 0)
        self.assertEqual(inventory["symbol_exposure"], {"SPY": 300.0})
        self.assertEqual(inventory["strategy_exposure"], {"opening_range_breakout": 300.0})
        self.assertEqual(inventory["family_exposure"], {"breakout": 300.0})
        self.assertEqual(service.query_inventory_records("fills")["payload"]["records"][0]["algorithmId"], ALGORITHM_ID)

def _service(prefix: str) -> MetaStrategyApplicationService:
    settings = MetaStrategySettingsStore(_TMP_ROOT / f"{prefix}-settings.sqlite")
    jobs = MetaStrategyJobRepository(f"sqlite:///{_TMP_ROOT / f'{prefix}-jobs.sqlite'}")
    inventory = MetaStrategySqliteRepository(f"sqlite:///{_TMP_ROOT / f'{prefix}-inventory.sqlite'}")
    return MetaStrategyApplicationService(settings_store=settings, job_repository=jobs, repository=inventory)


def _inventory_payload(
    order_intent_id: str,
    *,
    brokerFillId: str = "",
    reservedRiskDollars: float = 0.0,
    quantity: float = 1.0,
    price: float = 100.0,
    side: str = "BUY",
    strategyId: str = "meta_strategy",
    family: str = "UNKNOWN",
    commission: float = 0.0,
    estimatedSlippage: float = 0.0,
) -> dict:
    return {
        "algorithmId": ALGORITHM_ID,
        "capitalPartitionId": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        "settingsVersion": "settings-local-paper-contract",
        "correlationId": f"correlation-{order_intent_id}",
        "decisionId": f"decision-{order_intent_id}",
        "orderIntentId": order_intent_id,
        "clientOrderId": f"client-{order_intent_id}",
        "brokerOrderId": f"broker-{order_intent_id}",
        "brokerFillId": brokerFillId,
        "symbol": "SPY",
        "side": side,
        "quantity": quantity,
        "price": price,
        "reservedRiskDollars": reservedRiskDollars,
        "reservedRiskDelta": reservedRiskDollars,
        "strategyId": strategyId,
        "family": family,
        "commission": commission,
        "estimatedSlippage": estimatedSlippage,
        "timestamp": response_time().isoformat(),
    }

def _proposal(*, quantity: int, price: float, planned_risk: float, intent: str = "new_entry", side: Signal = Signal.BUY) -> GlobalOrderProposal:
    return GlobalOrderProposal(
        algorithmId=ALGORITHM_ID,
        capitalPartitionId=META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        decisionId="decision-proposal",
        orderIntentId="intent-proposal",
        intent=intent,
        symbol="SPY",
        side=side,
        quantity=quantity,
        limitPrice=price,
        stopPrice=max(0.01, price - 1.0),
        targetPrice=price + 2.0,
        plannedRiskDollars=planned_risk,
        strategyStateHash="proposal-state",
        proposedAt=response_time(),
        sessionDate=date(2026, 8, 5),
        configurationHash="proposal-config",
    )

def response_time() -> datetime:
    return datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


if __name__ == "__main__":
    unittest.main()

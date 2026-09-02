"""Fakes and the assembled environment the required paper end-to-end tests drive.

Split out of test_required_paper_e2e.py, which the required-suite guard caps at 260
lines. The file was 770, and roughly two thirds of it was this harness rather than
tests -- deterministic market data, an authoritative clock, a paper broker and the
state providers around them. Moving the scaffolding keeps the guard honest without
weakening it or thinning the coverage it protects.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.decision_worker import MetaStrategyDecisionWorkerContext, MetaStrategyFinalisedBarDecisionEvent, MetaStrategyFinalisedBarDecisionWorker
from backend.app.algorithms.meta_strategy.execution import MetaStrategyPaperOrderReconciliationWorker, MetaStrategyPaperOrderSubmissionWorker
from backend.app.algorithms.meta_strategy.execution_pipeline import MetaStrategyExecutionPipelineConfig, run_meta_strategy_execution_pipeline
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.observability import build_meta_strategy_observability_snapshot
from backend.app.algorithms.meta_strategy.order_intent import build_meta_strategy_order_intent
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.workers import MetaStrategyPositionManagementWorker
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.gates import GlobalGateResponse
from backend.tests.meta_strategy.activation_fixtures import arm_automatic_paper_trading, readiness_report_ready
from backend.tests.test_meta_strategy_step7_market_snapshot import MetaStrategySnapshotQuote, request_with


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)

class PaperE2EEnv:
    def __init__(
        self,
        *,
        database_url: str | None = None,
        settings_path: Path | None = None,
        broker: "E2EPaperBroker | None" = None,
        market_clock: "AuthoritativeFakeMarketClock | None" = None,
        global_risk: "FakeGlobalRiskService | None" = None,
        arm_control: bool = True,
    ) -> None:
        database_url = database_url or f"sqlite:///{temp_db_path()}"
        settings_path = settings_path or temp_db_path(prefix="meta-strategy-required-e2e-settings")
        self.jobs = MetaStrategyJobRepository(database_url)
        self.inventory = MetaStrategySqliteRepository(database_url)
        self.sibling_inventory = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path(prefix='meta-strategy-required-e2e-sibling')}")
        self.settings_store = MetaStrategySettingsStore(settings_path)
        try:
            self.settings = self.settings_store.get_active_settings()
        except Exception:
            self.settings = self.settings_store.create_baseline(build_meta_strategy_settings(settings_version="required-e2e-settings", created_at=NOW), actor="test")
            self.settings_store.activate_settings(self.settings.settings_version, actor="test")
        self.market_data = DeterministicFakeMarketData()
        self.market_clock = market_clock or AuthoritativeFakeMarketClock()
        self.account_source = FakePaperAccountSource()
        self.global_risk = global_risk or FakeGlobalRiskService()
        self.broker = broker or E2EPaperBroker(clock=self.market_clock, account_source=self.account_source)
        self.gateway = PaperOrderGateway(self.broker, self.jobs.gateway_store())
        self.remaining_algorithm_risk = 1_000.0
        self.pipeline_runner = forced_buy_runner
        self.readiness_report = readiness_report_ready()
        self.state_provider = AuthoritativeFakeStateProvider(self)
        self.database_url = database_url
        self.settings_path = settings_path
        if arm_control and self.jobs.read_paper_trading_control() is None:
            arm_automatic_paper_trading(self.jobs, now=NOW)
        self.set_runtime()

    def enqueue_finalized_bar(self):
        return self.jobs.enqueue_finalised_bar_decision(
            mode="PAPER",
            symbol="SPY",
            timeframe="1m",
            bar_end=self.finalized_bar_end(),
            settings_version=self.settings.settings_version,
            now=NOW,
        )

    def decision_now(self) -> datetime:
        return NOW

    def finalized_bar_end(self) -> datetime:
        return NOW - timedelta(minutes=10) if self.market_data.stale_candle else NOW

    def decision_worker(self) -> MetaStrategyFinalisedBarDecisionWorker:
        return MetaStrategyFinalisedBarDecisionWorker(
            repository=self.jobs,
            state_provider=self.state_provider,
            pipeline_runner=self.pipeline_runner,
        )

    def submission_worker(self):
        return MetaStrategyPaperOrderSubmissionWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
            global_risk_source=self.global_risk,
            settings_store=self.settings_store,
            readiness_report_source=self.readiness,
            market_clock_source=self.market_clock,
        )

    def reconciliation_worker(self):
        return MetaStrategyPaperOrderReconciliationWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
        )

    def position_worker(self) -> MetaStrategyPositionManagementWorker:
        return MetaStrategyPositionManagementWorker(repository=self.jobs, inventory_repository=self.inventory)

    def enqueue_end_of_day_position_management(self, *, key: str = "required-e2e-eod-exit") -> None:
        self.jobs.enqueue_job(
            job_type="position_management",
            idempotency_key=key,
            payload={
                "capitalPartitionId": "meta_strategy.paper.default",
                "settingsVersion": self.settings.settings_version,
                "decisionId": f"{key}-decision",
                "eventId": f"{key}-event",
                "correlationId": key,
                "symbol": "SPY",
                "candle": {"symbol": "SPY", "timestamp": NOW.isoformat(), "open": 101.0, "high": 101.2, "low": 100.8, "close": 101.0},
                "markPrices": {"SPY": 101.0},
                "mode": "PAPER",
                "endOfDayExitAt": NOW.isoformat(),
                "noOvernight": True,
            },
            now=NOW,
        )

    def readiness(self) -> dict[str, object]:
        return self.readiness_report

    def set_readiness(self, *, status: str = "OK", complete: bool = True, paper_ready: bool = True) -> None:
        self.readiness_report = {
            **readiness_report_ready(),
            "status": status,
            "complete": complete,
            "paperReady": paper_ready,
            "currentShadowPaperStatus": {"paperOrdersBlocked": not paper_ready, "liveExecutionEnabled": False},
        }

    def set_runtime(self, *, enabled: bool = True, mode: str = "PAPER", ready: bool = True) -> None:
        self.jobs.write_gateway_snapshot(
            "meta_strategy.runtime.readiness",
            {
                "algorithmId": ALGORITHM_ID,
                "enabled": enabled,
                "ready": ready,
                "status": "ready" if ready else "unavailable",
                "mode": mode,
                "paperOrdersBlocked": False,
                "liveTradingEnabled": False,
                "reasonCodes": ("meta_strategy.runtime.ready",) if ready else ("meta_strategy.runtime.unavailable",),
            },
            now=NOW,
        )

    def activate_replacement_settings(self) -> None:
        replacement = self.settings_store.create_baseline(build_meta_strategy_settings(settings_version=f"required-e2e-settings-{uuid4().hex[:8]}", created_at=NOW), actor="test")
        self.settings_store.activate_settings(replacement.settings_version, actor="test")

    def mark_current_order_duplicate(self) -> None:
        outbox = self.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")
        client_order_id = outbox["payload"]["clientOrderId"]
        self.jobs.write_gateway_snapshot(
            f"paper_order_gateway.client_order.{client_order_id}",
            {"clientOrderId": client_order_id, "orderIntentId": "already-used", "algorithmId": ALGORITHM_ID},
            now=NOW,
        )

    def seed_meta_strategy_position(self, *, quantity: int = 3) -> None:
        self.inventory.ingest_broker_fill(
            {
                "algorithmId": ALGORITHM_ID,
                "capitalPartitionId": "meta_strategy.paper.default",
                "settingsVersion": self.settings.settings_version,
                "correlationId": "phase13-existing-position",
                "decisionId": "phase13-existing-position",
                "jobId": "phase13-existing-position",
                "eventId": "phase13-existing-position",
                "orderIntentId": "phase13-existing-position",
                "clientOrderId": "phase13-existing-position",
                "brokerOrderId": "phase13-existing-position",
                "brokerFillId": f"phase13-existing-position-{uuid4().hex}",
                "symbol": "SPY",
                "side": "BUY",
                "filledQuantity": quantity,
                "fillPrice": 100.0,
                "timestamp": NOW.isoformat(),
            }
        )

    def patch_current_outbox(self, updates: dict) -> None:
        outbox = self.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")
        self.jobs.update_execution_outbox(outbox["outboxId"], status="PENDING", payload=updates, now=NOW + timedelta(milliseconds=1))

    def latest_outcome(self) -> dict | None:
        outcomes = self.jobs.finalized_candle_outcomes(limit=1)
        return outcomes[0] if outcomes else None

    def restart(self) -> "PaperE2EEnv":
        restarted = PaperE2EEnv(
            database_url=self.database_url,
            settings_path=self.settings_path,
            broker=self.broker,
            market_clock=self.market_clock,
            global_risk=self.global_risk,
            arm_control=False,
        )
        restarted.account_source = self.account_source
        restarted.broker.account_source = self.account_source
        restarted.market_data = self.market_data
        restarted.remaining_algorithm_risk = self.remaining_algorithm_risk
        restarted.pipeline_runner = self.pipeline_runner
        restarted.readiness_report = self.readiness_report
        restarted.state_provider = AuthoritativeFakeStateProvider(restarted)
        return restarted


class DeterministicFakeMarketData:
    def __init__(self) -> None:
        self.stale_candle = False
        self.stale_quote = False

    def request_for(self, event: MetaStrategyFinalisedBarDecisionEvent):
        quote_timestamp = event.bar_end - timedelta(seconds=90) if self.stale_quote else event.bar_end - timedelta(seconds=10)
        request = request_with(decision_timestamp=event.bar_end, one_minute_end=event.bar_end - timedelta(minutes=1))
        return request.model_copy(update={"quotes": (MetaStrategySnapshotQuote(timestamp=quote_timestamp, bid=101.48, ask=101.5, symbol="SPY"),)})


class AuthoritativeFakeMarketClock:
    def __init__(self) -> None:
        self.is_open = True
        self.captured_at = NOW
        self.read_count = 0

    def snapshot(self, at: datetime) -> dict[str, object]:
        return {
            "source": "phase13_authoritative_fake_exchange_clock",
            "capturedAt": self.captured_at.isoformat(),
            "dataSourceTimestamp": self.captured_at.isoformat(),
            "isOpen": self.is_open,
            "status": "open" if self.is_open else "closed",
            "authoritativeReadOnly": True,
            "canAuthorizeNewEntries": self.is_open,
            "regularSessionOpen": at.replace(hour=14, minute=30, second=0, microsecond=0).isoformat(),
            "regularSessionClose": at.replace(hour=21, minute=0, second=0, microsecond=0).isoformat(),
            "nextOpen": (at + timedelta(days=1)).isoformat(),
            "nextClose": at.replace(hour=21, minute=0, second=0, microsecond=0).isoformat(),
        }

    def get_clock(self):
        self.read_count += 1
        return self.snapshot(NOW)


class FakePaperAccountSource:
    def __init__(self) -> None:
        self.equity: float | None = 100_000.0
        self.buying_power: float | None = 100_000.0

    def snapshot(self) -> dict[str, object]:
        payload: dict[str, object] = {"source": "fake_authoritative_paper_account", "timestamp": NOW.isoformat()}
        if self.equity is not None:
            payload["accountEquity"] = self.equity
        if self.buying_power is not None:
            payload["buyingPower"] = self.buying_power
        return payload

    def read_account_snapshot(self, at=None):
        return self.snapshot()


class FakeGlobalRiskService:
    def __init__(self) -> None:
        self.action = "ALLOW"
        self.maximum_risk = 1_000.0
        self.maximum_quantity = 10_000

    def snapshot(self) -> dict[str, object]:
        return {
            "source": "fake_authoritative_global_risk",
            "availableRiskDollars": self.maximum_risk,
            "maxQuantity": self.maximum_quantity,
            "evaluatedAt": NOW.isoformat(),
        }

    def approve_order(self, proposal):
        allowed_quantity = proposal.quantity if self.action == "ALLOW" else 0
        return GlobalGateResponse(
            action=self.action,
            maximumAllowedQuantity=min(int(allowed_quantity), int(self.maximum_quantity)),
            maximumAdditionalRiskDollars=float(self.maximum_risk if self.action == "ALLOW" else 0.0),
            rejectionReasons=() if self.action == "ALLOW" else ("phase13.global_risk_rejected",),
            evaluatedAt=NOW,
            configurationHash="phase13-fake-global-risk",
        )


class AuthoritativeFakeStateProvider:
    def __init__(self, env: PaperE2EEnv) -> None:
        self.env = env
        self.load_count = 0
        self.last_context: dict[str, object] = {}

    def load_context(self, event: MetaStrategyFinalisedBarDecisionEvent) -> MetaStrategyDecisionWorkerContext:
        self.load_count += 1
        settings = self.env.settings_store.get_active_settings()
        inventory = self.env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        inventory_snapshot = {
            "source": "authoritative_meta_strategy_inventory_repository",
            "reservedRiskDollars": inventory.reserved_risk_dollars,
            "remainingRiskDollars": self.env.remaining_algorithm_risk,
            "openPositions": tuple(position.__dict__ for position in inventory.open_positions),
            "dailyTradeCount": inventory.daily_trade_count,
        }
        account_snapshot = self.env.account_source.snapshot()
        global_snapshot = self.env.global_risk.snapshot()
        clock = self.env.market_clock.snapshot(event.bar_end)
        self.last_context = {
            "inventorySnapshot": inventory_snapshot,
            "accountSnapshot": account_snapshot,
            "globalRiskSnapshot": global_snapshot,
            "marketClock": clock,
        }
        return MetaStrategyDecisionWorkerContext(
            event=event,
            settings=settings,
            market_snapshot_request=self.env.market_data.request_for(event),
            inventory_snapshot=inventory_snapshot,
            account_snapshot=account_snapshot,
            global_risk_snapshot=global_snapshot,
            event_state={
                "featureSchemaVersion": "meta_strategy_feature_schema_v1",
                "sourceVersions": {"eventSettingsVersion": event.settings_version, "activeSettingsVersion": settings.settings_version},
                "sourceTimestamps": {"decisionCutoff": event.bar_end.isoformat(), "account": account_snapshot.get("timestamp"), "globalRisk": global_snapshot.get("evaluatedAt")},
            },
            operational_health={
                "tradingAllowed": True,
                "marketCalendar": clock,
                "runtimeHealth": self.env.jobs.read_gateway_snapshot("meta_strategy.runtime.readiness"),
                "paperControl": self.env.jobs.read_paper_trading_control().to_dict(),
                "operationalControls": {"controls": {}},
                "reasonCodes": (),
            },
            active_model_artifact=None,
        )


def forced_buy_runner(request, settings, global_risk_snapshot):
    result = run_meta_strategy_execution_pipeline(
        request,
        config=MetaStrategyExecutionPipelineConfig(submit_to_broker=False),
        config_settings=settings,
    )
    intent = build_meta_strategy_order_intent(
        snapshot=result.snapshot,
        side="BUY",
        quantity=10,
        stop_price=99.0,
        limit_price=100.05,
    ).intent
    assert intent is not None
    intent_payload = intent.model_dump(mode="json")
    intent_payload.update(
        {
            "orderIntentId": intent.order_intent_id,
            "limitPrice": intent.limit_price,
            "stopPrice": intent.stop_price,
            "targetPrice": 104.0,
            "reservedRiskDollars": 10.0,
            "localGatesPassed": True,
        }
    )
    concrete_stage_results = {
        **result.stage_results,
        "strategies": {
            "status": "OK",
            "eligible": True,
            "inputVersion": "phase13-input",
            "outputVersion": "phase13-strategies",
            "reasonCodes": ("meta_strategy.phase13.strategy_evidence_valid",),
            "directionalOutputs": {
                "relative_strength": {
                    "strategyId": "relative_strength",
                    "strategyVersion": "phase13-rs-v1",
                    "familyId": "trend",
                    "signal": "BUY",
                    "confidence": 0.74,
                    "eligible": True,
                    "dataQuality": "OK",
                    "evidence": {"qqqIwmRelativeStrength": 1.03},
                    "vetoes": (),
                    "reasonCodes": (),
                    "evaluatedAt": request.snapshot_request.decision_timestamp.isoformat() if hasattr(request, "snapshot_request") else NOW.isoformat(),
                },
                "breadth": {
                    "strategyId": "breadth",
                    "strategyVersion": "phase13-breadth-v1",
                    "familyId": "breadth",
                    "signal": "BUY",
                    "confidence": 0.68,
                    "eligible": True,
                    "dataQuality": "OK",
                    "evidence": {"advancingBreadth": 0.62},
                    "vetoes": (),
                    "reasonCodes": (),
                    "evaluatedAt": request.snapshot_request.decision_timestamp.isoformat() if hasattr(request, "snapshot_request") else NOW.isoformat(),
                },
            },
        },
        "family_aggregation": {
            "status": "OK",
            "eligible": True,
            "inputVersion": "phase13-input",
            "outputVersion": "phase13-family-aggregation",
            "familyScores": {"trend": 0.74, "breadth": 0.68},
            "activeStrategyCount": 2,
            "activeFamilyCount": 2,
            "supportingFamilies": ("trend", "breadth"),
            "opposingFamilies": (),
            "correlationPenalties": {},
            "winningScore": 0.71,
            "opposingScore": 0.0,
            "edge": 0.71,
            "reasonCodes": ("meta_strategy.phase13.family_aggregation_valid",),
        },
    }
    return replace(
        result,
        stage_results=concrete_stage_results,
        order_intent=intent_payload,
        final_valid=True,
        reason_codes=tuple(dict.fromkeys((*result.reason_codes, "meta_strategy.required_e2e.forced_paper_order"))),
    )


def blocked_runner(reason_code: str):
    def _runner(request, settings, global_risk_snapshot):
        result = run_meta_strategy_execution_pipeline(
            request,
            config=MetaStrategyExecutionPipelineConfig(submit_to_broker=False),
            config_settings=settings,
        )
        return replace(
            result,
            order_intent=None,
            final_valid=False,
            reason_codes=tuple(dict.fromkeys((*result.reason_codes, reason_code))),
        )

    return _runner


class E2EPaperBroker:
    broker_kind = "alpaca_paper"
    configured = True
    paper_endpoint = True

    def __init__(self, *, clock: AuthoritativeFakeMarketClock | None = None, account_source: FakePaperAccountSource | None = None) -> None:
        self.orders: dict[str, dict] = {}
        self.events: list[dict] = []
        self.clock = clock or AuthoritativeFakeMarketClock()
        self.account_source = account_source or FakePaperAccountSource()
        self.submit_count = 0
        self.timeout_after_submit = False

    def verify_paper_account(self) -> bool:
        return True

    def get_clock(self):
        return self.clock.get_clock()

    def read_account_snapshot(self, at=None):
        return self.account_source.snapshot()

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        client_order_id = intent.clientOrderId
        self.orders[str(intent.orderIntentId)] = {
            "brokerEventId": f"ack-{intent.orderIntentId}",
            "algorithmId": ALGORITHM_ID,
            "capitalPartitionId": "meta_strategy.paper.default",
            "clientOrderId": client_order_id,
            "brokerOrderId": f"broker-{intent.orderIntentId}",
            "orderIntentId": str(intent.orderIntentId),
            "status": "ACCEPTED",
            "symbol": intent.symbol,
            "side": intent.side.value if hasattr(intent.side, "value") else str(intent.side),
            "submittedQuantity": int(intent.submittedQuantity),
            "timestamp": NOW.isoformat(),
        }
        if self.timeout_after_submit:
            raise TimeoutError("phase13 broker timeout after paper order submission")
        return PaperGatewayBrokerAck(clientOrderId=client_order_id, brokerOrderId=f"broker-{intent.orderIntentId}", status="ACCEPTED", acceptedAt=NOW)

    def enqueue_fill(self, *, order_intent_id: str, quantity: int, price: float, side: str, event_id: str, status: str = "FILLED") -> None:
        order = self.orders[order_intent_id]
        self.events.append(
            {
                **order,
                "brokerEventId": event_id,
                "brokerFillId": event_id,
                "status": status,
                "side": side,
                "filledQuantity": quantity,
                "averageFillPrice": price,
                "timestamp": NOW.isoformat(),
            }
        )

    def refresh_order(self, client_order_id: str):
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return False

    def refresh_positions(self):
        return []

    def list_order_events(self):
        events = list(self.orders.values()) + list(self.events)
        self.events.clear()
        return events


class AllowRisk:
    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=NOW,
            configurationHash="required-e2e-allow-risk",
        )


def temp_db_path(*, prefix: str = "meta-strategy-required-e2e") -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"{prefix}-{uuid4().hex}.sqlite"

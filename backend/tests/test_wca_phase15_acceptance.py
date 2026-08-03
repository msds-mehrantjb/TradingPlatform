from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch
from uuid import uuid4

import httpx

from backend.app.algorithms.wca.alpaca_paper_broker import WcaAlpacaPaperBroker
from backend.app.algorithms.wca.configuration import WcaConfiguration, default_wca_configuration
from backend.app.algorithms.wca.contracts import (
    WcaBrokerReconciliationResult,
    WcaCandle,
    WcaConfidenceCalibrationBin,
    WcaConfidenceCalibrationTable,
    WcaDecision,
    WcaMarketSnapshot,
    WcaQuote,
    WcaRuntimeMode,
    WcaSide,
    WcaStrategyEvaluation,
)
from backend.app.algorithms.wca.execution_pipeline import run_wca_paper_pipeline_adapter
from backend.app.algorithms.wca.paper_account import (
    WCA_ALPACA_PAPER_ACCOUNT_ID,
    WCA_ALPACA_PAPER_API_KEY_ID,
    WCA_ALPACA_PAPER_API_SECRET_KEY,
    WCA_ALPACA_PAPER_BASE_URL,
    WCA_AUTOMATIC_PAPER_ENABLED,
    WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
)
from backend.app.algorithms.wca.repository import WcaInventoryLedgerEvent, WcaSqliteRepository
from backend.app.algorithms.wca.rollout import WCA_PAPER_EXECUTION_ENABLED
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WcaRuntimeSettings, WcaRuntimeSupervisor
from backend.app.algorithms.wca.service import WcaService
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.tests.test_wca_step20_rollout import complete_evidence


ACCOUNT_ID = "wca-paper-acceptance"
SYMBOL = "SPY"
SESSION_START = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
DECISION_TIME = datetime(2026, 7, 20, 14, 49, tzinfo=UTC)
ENTRY_PRICE = 131.65


def test_wca_automatic_paper_acceptance_scenario_with_fake_alpaca_http_transport(monkeypatch) -> None:
    clock = {"now": DECISION_TIME + timedelta(seconds=2)}
    repository = acceptance_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(
            account_id=ACCOUNT_ID,
            symbol=SYMBOL,
            runtime_mode=WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER,
            max_state_age_seconds=999_999_999,
            max_authoritative_account_state_age_seconds=999_999_999,
            max_reconciliation_age_seconds=999_999_999,
            max_lag_seconds=120,
            poll_seconds=0.01,
        ),
        owner_id="phase15-acceptance-runtime",
    )
    service = WcaService(repository=repository)
    fake_http = FakeAlpacaPaperHttpTransport(account_id=ACCOUNT_ID, clock=lambda: clock["now"])
    real_broker_from_env = WcaAlpacaPaperBroker.from_env

    monkeypatch.setattr("backend.app.algorithms.wca.runtime_supervisor._utc_now", lambda: clock["now"])
    monkeypatch.setattr("backend.app.algorithms.wca.runtime_repository._utc_now", lambda: clock["now"])
    env = {
        WCA_AUTOMATIC_PAPER_ENABLED: "true",
        WCA_PAPER_EXECUTION_ENABLED: "true",
        WCA_ALPACA_PAPER_ACCOUNT_ID: ACCOUNT_ID,
        WCA_ALPACA_PAPER_API_KEY_ID: "wca-acceptance-key",
        WCA_ALPACA_PAPER_API_SECRET_KEY: "wca-acceptance-secret",
        WCA_ALPACA_PAPER_BASE_URL: WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
    }

    def fake_broker_from_env(*, account_id: str, environ=None, http_client=None):
        assert account_id == ACCOUNT_ID
        return real_broker_from_env(account_id=account_id, environ=env, http_client=fake_http)

    with patch.dict(os.environ, env, clear=False), patch(
        "backend.app.algorithms.wca.runtime_supervisor.WcaAlpacaPaperBroker.from_env",
        side_effect=fake_broker_from_env,
    ), patch(
        "backend.app.algorithms.wca.runtime_supervisor.run_wca_paper_pipeline_adapter",
        side_effect=acceptance_pipeline,
    ):
        default_control = repository.read_runtime_control(broker_account_id=ACCOUNT_ID, symbol=SYMBOL)
        assert default_control.paper_trading_requested is False
        assert default_control.effective_automatic_entries_enabled is False

        startup = supervisor.run_once()
        assert startup["workers"]["broker_reconciliation_worker"]["status"] == "completed"
        assert "wca.runtime.broker_reconciliation.completed" in startup["workers"]["broker_reconciliation_worker"]["reasonCodes"]
        assert repository.reconciliation_blocks_new_entries(account_id=ACCOUNT_ID, symbol=SYMBOL) is False, json.dumps(latest_reconciliation_debug(repository), sort_keys=True)
        assert repository.read_active_configuration() is not None
        assert repository.read_active_weights(as_of=clock["now"]) is not None
        assert len(repository.read_active_confidence_calibrations(symbol=SYMBOL, as_of=clock["now"])) >= 3

        paper_on = service.enqueue_automatic_paper_control(
            enabled=True,
            actor="phase15.acceptance",
            reason="wca.phase15.paper_requested_on",
            account_id=ACCOUNT_ID,
            symbol=SYMBOL,
        )
        assert paper_on["queued"] is True
        requested = supervisor.run_once()
        requested_control = repository.read_runtime_control(broker_account_id=ACCOUNT_ID, symbol=SYMBOL)
        assert requested["workers"]["runtime_control_worker"]["status"] == "completed"
        assert requested_control.paper_trading_requested is True
        assert requested_control.effective_automatic_entries_enabled is False
        assert "wca.runtime_control.market_data_stale" in requested_control.reason_codes

        event = finalized_event("phase15-bar-1", snapshot=acceptance_snapshot())
        accepted = runtime_repository.publish_finalized_bar_event(event, now=event.publication_timestamp)
        duplicate = runtime_repository.publish_finalized_bar_event(event, now=event.publication_timestamp)
        assert accepted.accepted is True
        assert duplicate.accepted is False
        assert duplicate.status == "duplicate"

        first_bar = supervisor.run_once()
        effective_control = repository.read_runtime_control(broker_account_id=ACCOUNT_ID, symbol=SYMBOL)
        assert first_bar["workers"]["decision_worker"]["status"] == "completed", first_bar["workers"]["decision_worker"].get("reasonCodes")
        decision_id = first_bar["workers"]["decision_worker"]["decisionId"]
        decision = persisted_decision(repository, decision_id)
        assert decision is not None
        assert first_bar["workers"]["global_risk_request_worker"]["status"] == "completed", "\n".join(
            (
                "decision_reasons:",
                *decision.reason_codes,
                "aggregation_reasons:",
                    *decision.aggregation.reason_codes,
                    "sizing_reasons:",
                    *decision.sizing.reason_codes,
                    "gate_statuses:",
                    *(f"{gate.gate_id}:{gate.status}:{gate.blocks_entry}:{gate.reason_codes}" for gate in decision.local_gates),
                )
            )
        assert first_bar["workers"]["execution_outbox_worker"]["status"] == "completed", first_bar["workers"]["execution_outbox_worker"].get("reasonCodes")
        assert effective_control.effective_paper_trading_enabled is True
        assert effective_control.effective_automatic_entries_enabled is True
        assert effective_control.rollout_stage == "LIMITED_AUTOMATIC_PAPER"

        approval = repository.read_global_risk_approval(decision_id=decision_id)
        outbox = repository.list_execution_outbox_records(account_id=ACCOUNT_ID)
        assert decision.proposed_order is not None
        assert decision.aggregation.signal == WcaSide.BUY
        assert "wca.aggregation.calculated" in decision.aggregation.reason_codes
        assert decision.cost_estimate is not None
        assert decision.sizing.final_quantity > 0
        assert approval is not None
        assert approval.entry_permitted is True
        assert len(outbox) == 1
        assert outbox[0].global_risk_decision_id == approval.global_risk_decision_id
        assert outbox[0].runtime_control_revision == effective_control.control_revision

        assert outbox[0].status in {"SUBMITTED", "PARTIALLY_FILLED", "ACCEPTED_FOR_PAPER"}, outbox_debug(repository, outbox[0].outbox_id)
        entry_posts = fake_http.entry_order_posts()
        assert len(entry_posts) == 1
        assert entry_posts[0]["client_order_id"].startswith("wca-")
        projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=ACCOUNT_ID, symbol=SYMBOL)
        assert projection.open_quantity == 2
        assert fake_http.position_quantity == 2
        assert len(fake_http.active_protective_orders()) == 2
        assert {int(float(order["qty"])) for order in fake_http.active_protective_orders()} == {2}

        fake_http.fill_remaining_entry()
        clock["now"] = DECISION_TIME + timedelta(seconds=12)
        runtime_repository.enqueue_command(
            runtime_command(
                WcaRuntimeCommandType.BROKER_RECONCILIATION,
                account_id=ACCOUNT_ID,
                symbol=SYMBOL,
                decision_id=decision_id,
                run_id="phase15-after-remaining-fill",
                reason_codes=("wca.phase15.remaining_fill_poll",),
            )
        )
        after_fill = supervisor.run_once()
        assert after_fill["workers"]["broker_reconciliation_worker"]["fillsProcessed"] == 1
        projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=ACCOUNT_ID, symbol=SYMBOL)
        assert projection.open_quantity == 5
        assert fake_http.position_quantity == 5
        assert len(fake_http.active_protective_orders()) == 2
        assert {int(float(order["qty"])) for order in fake_http.active_protective_orders()} == {5}
        assert fake_http.cancelled_protective_count >= 2
        assert repository.reconciliation_blocks_new_entries(account_id=ACCOUNT_ID, symbol=SYMBOL) is False, json.dumps(
                {**latest_reconciliation_debug(repository), "intents": order_intents_debug(repository), "fakeOrders": fake_http.open_order_debug()},
            sort_keys=True,
        )

        service.enqueue_automatic_paper_control(
            enabled=False,
            actor="phase15.acceptance",
            reason="wca.phase15.paper_requested_off",
            account_id=ACCOUNT_ID,
            symbol=SYMBOL,
        )
        paper_off = supervisor.run_once()
        off_control = repository.read_runtime_control(broker_account_id=ACCOUNT_ID, symbol=SYMBOL)
        assert paper_off["workers"]["runtime_control_worker"]["status"] == "completed"
        assert off_control.paper_trading_requested is False
        assert off_control.effective_automatic_entries_enabled is False
        assert len(fake_http.active_protective_orders()) == 2

        before_second_bar_entries = len(fake_http.entry_order_posts())
        clock["now"] = DECISION_TIME + timedelta(minutes=1, seconds=2)
        second_event = finalized_event("phase15-bar-2", snapshot=acceptance_snapshot(offset_minutes=1))
        assert runtime_repository.publish_finalized_bar_event(second_event, now=second_event.publication_timestamp).accepted is True
        second_bar = supervisor.run_once()
        assert second_bar["workers"]["execution_outbox_worker"]["status"] in {"blocked", "idle"}
        assert len(fake_http.entry_order_posts()) == before_second_bar_entries
        assert len(fake_http.active_protective_orders()) == 2
        pre_eos_projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=ACCOUNT_ID, symbol=SYMBOL)
        assert pre_eos_projection.open_quantity == 5

        clock["now"] = datetime(2026, 7, 20, 20, 56, tzinfo=UTC)
        runtime_repository.enqueue_command(
            runtime_command(
                WcaRuntimeCommandType.END_OF_SESSION,
                account_id=ACCOUNT_ID,
                symbol=SYMBOL,
                decision_id=decision_id,
                run_id="phase15-end-of-session",
                payload={"evaluated_at": clock["now"].isoformat()},
                reason_codes=("wca.phase15.end_of_session",),
            )
        )
        end_of_session = supervisor.run_once()
        eos = end_of_session["workers"]["end_of_session_worker"]
        assert eos["status"] == "completed"
        assert eos["verified"] is True
        assert "wca.runtime.end_of_session.verified_flat" in eos["reasonCodes"]

    final_projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=ACCOUNT_ID, symbol=SYMBOL)
    assert final_projection.open_quantity == 0
    assert final_projection.reserved_risk == 0
    assert fake_http.position_quantity == 0
    assert fake_http.active_protective_orders() == []
    assert len(fake_http.entry_order_posts()) == 1
    assert len({order["client_order_id"] for order in fake_http.orders.values()}) == len(fake_http.orders)
    assert all(str(order["client_order_id"]).startswith("wca-") for order in fake_http.orders.values())
    assert cross_algorithm_mutation_count(repository) == 0

def acceptance_pipeline(pipeline_input):
    return run_wca_paper_pipeline_adapter(pipeline_input, voters=ACCEPTANCE_VOTERS)


class AcceptanceVoter:
    def __init__(self, strategy_id: str, strategy_version: str, family: str, base_weight: float) -> None:
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.family = family
        self.base_weight = base_weight

    def evaluate(self, snapshot: WcaMarketSnapshot) -> WcaStrategyEvaluation:
        return WcaStrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            name=f"Acceptance {self.strategy_id}",
            status="ACTIVE",
            signal=WcaSide.BUY,
            confidence=0.90,
            raw_confidence=0.90,
            calibrated_confidence=0.90,
            direction=WcaSide.BUY,
            applicability="ACTIVE",
            evidence_strength=0.90,
            data_quality_status="ACTIVE",
            base_weight=self.base_weight,
            effective_weight=self.base_weight,
            contribution=self.base_weight * 0.90,
            reason_codes=(f"wca.phase15.acceptance_voter.{self.strategy_id}",),
            explanation="Acceptance scenario controlled voter.",
        )


ACCEPTANCE_VOTERS = (
    AcceptanceVoter("C1", "wca_moving_average_trend_v1", "trend", 0.10),
    AcceptanceVoter("C4", "wca_vwap_mean_reversion_v1", "mean_reversion", 0.08),
    AcceptanceVoter("C7", "wca_opening_range_breakout_v1", "breakout", 0.10),
)


class FakeAlpacaPaperHttpTransport:
    def __init__(self, *, account_id: str, clock) -> None:
        self.account_id = account_id
        self.clock = clock
        self.orders: dict[str, dict[str, object]] = {}
        self.activities: list[dict[str, object]] = []
        self.position_quantity = 0
        self.cancelled_protective_count = 0
        self._entry_client_order_id = ""

    def request(self, method: str, url: str, **kwargs):
        path = urlparse(url).path
        method = method.upper()
        params = kwargs.get("params") or {}
        if method == "GET" and path == "/v2/account":
            return self._response(url, {"id": self.account_id, "account_number": self.account_id, "equity": "100000", "buying_power": "100000", "realized_intraday_pl": "0"})
        if method == "GET" and path == "/v2/clock":
            return self._response(url, {"timestamp": self.clock().isoformat(), "is_open": True, "next_open": self.clock().isoformat(), "next_close": datetime(2026, 7, 20, 20, 0, tzinfo=UTC).isoformat()})
        if method == "GET" and path == "/v2/positions":
            if self.position_quantity == 0:
                return self._response(url, [])
            return self._response(
                url,
                [
                    {
                        "symbol": SYMBOL,
                        "qty": str(self.position_quantity),
                        "avg_entry_price": str(ENTRY_PRICE),
                        "current_price": str(ENTRY_PRICE + 0.25),
                        "client_order_id": self._entry_client_order_id,
                        "decision_id": "wca-decision-phase15-bar-1",
                        "order_intent_id": "wca-intent-phase15-bar-1",
                    }
                ],
            )
        if method == "GET" and path == "/v2/orders":
            status = str(params.get("status") or "open").lower()
            rows = list(self.orders.values())
            if status == "open":
                rows = [row for row in rows if str(row["status"]) in {"accepted", "new", "partially_filled"}]
            return self._response(url, rows)
        if method == "GET" and path == "/v2/orders:by_client_order_id":
            client_order_id = str(params.get("client_order_id") or "")
            order = self.orders.get(client_order_id)
            if order is None:
                return self._response(url, {"message": "not found"}, status_code=404)
            return self._response(url, order)
        if method == "GET" and path == "/v2/account/activities/FILL":
            activities = list(self.activities)
            self.activities.clear()
            return self._response(url, activities)
        if method == "POST" and path == "/v2/orders":
            return self._response(url, self._create_order(dict(kwargs.get("json") or {})), status_code=201)
        if method == "DELETE" and path.startswith("/v2/orders/"):
            broker_id = path.rsplit("/", 1)[-1]
            for order in self.orders.values():
                if order["id"] == broker_id:
                    if str(order["client_order_id"]).startswith("wca-protection-"):
                        self.cancelled_protective_count += 1
                    order["status"] = "canceled"
                    return self._response(url, {"id": broker_id, "status": "canceled"})
            return self._response(url, {"id": broker_id, "status": "missing"})
        return self._response(url, {"message": f"Unhandled fake Alpaca route {method} {path}"}, status_code=500)

    def fill_remaining_entry(self) -> None:
        order = self.orders[self._entry_client_order_id]
        remaining = int(float(order["qty"])) - int(float(order["filled_qty"]))
        assert remaining > 0
        order["filled_qty"] = order["qty"]
        order["status"] = "filled"
        self.position_quantity += remaining
        self.activities.append(
            {
                "id": f"activity-{order['id']}-remaining",
                "order_id": order["id"],
                "client_order_id": order["client_order_id"],
                "qty": str(remaining),
                "price": order["limit_price"],
                "transaction_time": self.clock().isoformat(),
            }
        )

    def entry_order_posts(self) -> list[dict[str, object]]:
        return [
            order
            for order in self.orders.values()
            if str(order["client_order_id"]).startswith("wca-")
            and not str(order["client_order_id"]).startswith("wca-protection-")
            and not str(order["client_order_id"]).startswith("wca-eos-")
        ]

    def active_protective_orders(self) -> list[dict[str, object]]:
        return [
            order
            for order in self.orders.values()
            if str(order["client_order_id"]).startswith("wca-protection-") and str(order["status"]) != "canceled"
        ]

    def open_order_debug(self) -> list[dict[str, object]]:
        return [
            {
                "client": str(order["client_order_id"]),
                "status": str(order["status"]),
                "qty": str(order["qty"]),
                "type": str(order["type"]),
                "orderIntent": str(order.get("order_intent_id") or ""),
            }
            for order in self.orders.values()
            if str(order["status"]) in {"accepted", "new", "partially_filled"}
        ]

    def _create_order(self, payload: dict[str, object]) -> dict[str, object]:
        client_order_id = str(payload["client_order_id"])
        quantity = int(float(payload["qty"]))
        broker_order_id = f"alpaca-{client_order_id}"
        order = {
            "id": broker_order_id,
            "client_order_id": client_order_id,
            "symbol": str(payload.get("symbol") or SYMBOL),
            "qty": str(quantity),
            "filled_qty": "0",
            "filled_avg_price": str(payload.get("limit_price") or ENTRY_PRICE),
            "limit_price": str(payload.get("limit_price") or ENTRY_PRICE),
            "stop_price": str(payload.get("stop_price") or ""),
            "side": str(payload.get("side") or "buy"),
            "type": str(payload.get("type") or "limit"),
            "time_in_force": str(payload.get("time_in_force") or "day"),
            "status": "accepted",
            "submitted_at": self.clock().isoformat(),
            "created_at": self.clock().isoformat(),
            "decision_id": "wca-decision-phase15-bar-1",
            "order_intent_id": "wca-intent-phase15-bar-1",
        }
        if client_order_id.startswith("wca-protection-"):
            pass
        elif client_order_id.startswith("wca-eos-"):
            order["filled_qty"] = str(quantity)
            order["status"] = "filled"
            self.position_quantity = max(0, self.position_quantity - quantity)
        else:
            partial = min(2, quantity)
            order["filled_qty"] = str(partial)
            order["status"] = "partially_filled"
            self.position_quantity += partial
            self._entry_client_order_id = client_order_id
        self.orders[client_order_id] = order
        return order

    def _response(self, url: str, payload, *, status_code: int = 200) -> httpx.Response:
        return httpx.Response(status_code=status_code, json=payload, request=httpx.Request("GET", url))


def acceptance_repository() -> WcaSqliteRepository:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    config = acceptance_configuration()
    repository.initialize_defaults(
        symbol=SYMBOL,
        configuration=config.model_dump(mode="json"),
        weight_snapshot=baseline_weight_snapshot(cutoff=DECISION_TIME, weight_version="phase15.acceptance.weights"),
        engine_version="phase15-acceptance",
    )
    for strategy_id, strategy_version in (
        ("C1", "wca_moving_average_trend_v1"),
        ("C4", "wca_vwap_mean_reversion_v1"),
        ("C7", "wca_opening_range_breakout_v1"),
    ):
        repository.save_confidence_calibration(
            acceptance_calibration(strategy_id, strategy_version),
            symbol=SYMBOL,
            configuration_version=config.configuration_version,
            engine_version="phase15-acceptance",
        )
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id="phase15-clean-install-inventory-state",
            event_type="DAILY_STATE_RESET",
            broker_account_id=ACCOUNT_ID,
            symbol=SYMBOL,
            event_timestamp=DECISION_TIME - timedelta(minutes=5),
            trade_date=DECISION_TIME.date().isoformat(),
            source_authority="phase15.acceptance",
            configuration_version=config.configuration_version,
            decision_id="phase15-install",
            run_id="phase15-install",
            payload={"reconciled": True, "isolated": True},
        )
    )
    seed_rollout_evidence(repository)
    repository.write_broker_reconciliation(
        WcaBrokerReconciliationResult(
            reconciliation_id="phase15-install-clean-reconciliation",
            account_id=ACCOUNT_ID,
            evaluated_at=DECISION_TIME - timedelta(seconds=30),
            intents_checked=0,
            broker_open_orders_checked=0,
            broker_positions_checked=0,
            reason_codes=("wca.phase15.install_reconciliation_seed",),
        )
    )
    return repository


def acceptance_configuration() -> WcaConfiguration:
    config = default_wca_configuration()
    payload = config.model_dump(mode="python")
    payload["content_hash"] = ""
    payload["runtime"] = {**payload["runtime"], "runtime_mode": WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER}
    payload["calibration"] = {**payload["calibration"], "enabled": True, "minimum_samples": 1}
    payload["aggregation"] = {
        **payload["aggregation"],
        "minimum_score": 0.0,
        "buy_threshold": 0.01,
        "strong_buy_threshold": 0.02,
        "minimum_active_strategies": 3,
        "minimum_directional_agreement": 0.0,
        "minimum_average_confidence": 0.0,
    }
    payload["risk"] = {**payload["risk"], "base_risk_percent": 0.1, "hard_max_risk_percent": 1.0}
    payload["sizing"] = {**payload["sizing"], "max_allowed_shares": 5, "hard_max_allowed_shares": 5}
    payload["execution"] = {
        **payload["execution"],
        "minimum_net_edge_per_share": 0.0,
        "max_spread_percent": 1.0,
        "entry_cutoff_minutes": 15 * 60 + 30,
    }
    payload["limited_automatic_paper"] = {
        **payload["limited_automatic_paper"],
        "broker_account_id": ACCOUNT_ID,
        "max_quantity": 5,
        "max_daily_trades": 3,
        "max_daily_loss_dollars": 100,
        "entry_windows": ("10:00-11:30 America/New_York", "13:30-15:30 America/New_York"),
        "permitted_strategy_ids": ("C1", "C4", "C7"),
        "rollout_stage": "LIMITED_AUTOMATIC_PAPER",
    }
    return WcaConfiguration.model_validate(payload)


def acceptance_calibration(strategy_id: str, strategy_version: str) -> WcaConfidenceCalibrationTable:
    return WcaConfidenceCalibrationTable(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        direction=WcaSide.BUY,
        regime="default",
        calibration_version=f"phase15.acceptance.calibration.{strategy_id}",
        created_at=DECISION_TIME - timedelta(days=1),
        outcome_cutoff_timestamp=DECISION_TIME - timedelta(days=1),
        minimum_samples=1,
        prior_success_rate=0.90,
        prior_strength=10,
        sample_count=100,
        bins=(
            WcaConfidenceCalibrationBin(
                lower_bound=0.0,
                upper_bound=1.0,
                sample_count=100,
                success_count=90,
                prior_success_rate=0.90,
                prior_strength=10,
                posterior_success_rate=0.90,
            ),
        ),
        reason_codes=("wca.phase15.acceptance_calibration",),
    )


def seed_rollout_evidence(repository: WcaSqliteRepository) -> None:
    evidence = complete_evidence()
    payload = evidence.model_dump()
    with repository.connect() as conn:
        for evidence_id in sorted(evidence.persisted_evidence_ids):
            row_payload = {**payload, "evidence_id": evidence_id}
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_rollout_evidence (
                    evidence_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, rollout_phase, payload_json
                )
                VALUES (?, 'wca', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    ACCOUNT_ID,
                    SYMBOL,
                    (DECISION_TIME - timedelta(days=1)).isoformat(),
                    "phase15.acceptance",
                    "phase15.acceptance",
                    f"phase15-{evidence_id}",
                    f"phase15-{evidence_id}",
                    "phase15-rollout",
                    "LIMITED_AUTOMATIC_PAPER",
                    json.dumps(row_payload, sort_keys=True),
                ),
            )


def acceptance_snapshot(*, offset_minutes: int = 0) -> WcaMarketSnapshot:
    base = SESSION_START + timedelta(minutes=offset_minutes)
    candles = []
    for index in range(80):
        price = 100.0 + 0.4 * index
        candles.append(
            WcaCandle(
                timestamp=base + timedelta(minutes=index),
                open=price,
                high=price + 0.08,
                low=price - 0.04,
                close=price + 0.05,
                volume=750_000 + index * 1_000,
            )
        )
    timestamp = candles[-1].timestamp
    return WcaMarketSnapshot(
        symbol=SYMBOL,
        data_timestamp=timestamp,
        decision_timestamp=timestamp,
        candles=tuple(candles),
        quote=WcaQuote(timestamp=timestamp, bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01),
        data_ready=True,
        source="phase15_fake_alpaca_market_data",
        reason_codes=("wca.phase15.acceptance_snapshot",),
    )


def finalized_event(event_id: str, *, snapshot: WcaMarketSnapshot) -> WcaFinalizedBarEvent:
    return WcaFinalizedBarEvent(
        event_id=event_id,
        symbol=SYMBOL,
        finalized_candle_timestamp=snapshot.candles[-1].timestamp,
        data_manifest_hash=f"{event_id}-manifest",
        publication_timestamp=snapshot.candles[-1].timestamp + timedelta(seconds=1),
        source="phase15.acceptance.finalized_bar_publisher",
        snapshot=snapshot,
    )


def persisted_decision(repository: WcaSqliteRepository, decision_id: str) -> WcaDecision:
    with sqlite3.connect(repository.path) as conn:
        row = conn.execute("SELECT payload_json FROM wca_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
    assert row is not None
    return WcaDecision.model_validate(json.loads(row[0]))


def outbox_debug(repository: WcaSqliteRepository, outbox_id: str) -> dict[str, object]:
    with sqlite3.connect(repository.path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(wca_execution_outbox)").fetchall()}
        selected = [column for column in ("status", "error_payload_json", "response_payload_json") if column in columns]
        row = conn.execute(
            f"SELECT {', '.join(selected)} FROM wca_execution_outbox WHERE outbox_id = ?",
            (outbox_id,),
        ).fetchone()
    assert row is not None
    debug = {key: json.loads(row[key] or "{}") if key.endswith("_json") else row[key] for key in row.keys()}
    error = debug.get("error_payload_json")
    if isinstance(error, dict):
        return {"status": debug.get("status"), "reason_codes": error.get("reason_codes") or error.get("reasonCodes")}
    return debug


def latest_reconciliation_debug(repository: WcaSqliteRepository) -> dict[str, object]:
    with sqlite3.connect(repository.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT reconciliation_id, hard_operational_warning, discrepancy_count, payload_json
            FROM wca_broker_reconciliations
            WHERE account_id = ? AND symbol = ?
            ORDER BY timestamp DESC, created_at DESC
            LIMIT 1
            """,
            (ACCOUNT_ID, SYMBOL),
        ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"] or "{}")
    discrepancies = payload.get("discrepancies") or []
    compact_discrepancies = [
        {
            "type": item.get("discrepancy_type") or item.get("discrepancyType"),
            "reason": item.get("reason_code") or item.get("reasonCode"),
            "brokerQuantity": item.get("broker_quantity") or item.get("brokerQuantity"),
            "backendQuantity": item.get("backend_quantity") or item.get("backendQuantity"),
            "clientOrderId": (item.get("attribution") or {}).get("clientOrderId"),
            "orderIntentId": (item.get("attribution") or {}).get("orderIntentId"),
        }
        for item in discrepancies
    ]
    return {
        "reconciliation_id": row["reconciliation_id"],
        "hard_operational_warning": row["hard_operational_warning"],
        "discrepancy_count": row["discrepancy_count"],
        "reason_codes": payload.get("reason_codes") or payload.get("reasonCodes"),
        "discrepancies": compact_discrepancies,
    }


def order_intents_debug(repository: WcaSqliteRepository) -> list[dict[str, object]]:
    with sqlite3.connect(repository.path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT order_intent_id, side, quantity, payload_json
            FROM wca_order_intents
            WHERE account_id = ?
            ORDER BY created_at, order_intent_id
            """,
            (ACCOUNT_ID,),
        ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        result.append(
            {
                "intent": row["order_intent_id"],
                "side": row["side"],
                "quantity": row["quantity"],
                "client": payload.get("client_order_id") or payload.get("clientOrderId"),
                "status": payload.get("status"),
            }
        )
    return result


def cross_algorithm_mutation_count(repository: WcaSqliteRepository) -> int:
    with sqlite3.connect(repository.path) as conn:
        total = 0
        for table in (
            "wca_inventory_ledger",
            "wca_owned_lots",
            "wca_execution_outbox",
            "wca_attributed_orders",
            "wca_broker_orders",
            "wca_attributed_fills",
        ):
            total += int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE algorithm_id <> 'wca'").fetchone()[0])
    return total


def temp_db_path() -> Path:
    root = Path.cwd() / "backend" / "data" / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"wca-phase15-{uuid4().hex}.sqlite"

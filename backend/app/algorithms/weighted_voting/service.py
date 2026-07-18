"""Application service boundary for Weighted Voting."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.backtest.engine import WeightedBacktestEngineConfig, WeightedBacktestResult, backtest_engine_status, run_weighted_voting_backtest
from backend.app.algorithms.weighted_voting.backtest.execution_simulator import simulator_status
from backend.app.algorithms.weighted_voting.backtest.walk_forward import walk_forward_status
from backend.app.algorithms.weighted_voting.catalog import weighted_voting_dedicated_strategy_inventory
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.decision_gates import WeightedFiveMinuteAlignment, WeightedVotingLocalGateInputs, evaluate_local_decision_gates
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope, default_hard_limits, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.final_acceptance import build_weighted_voting_final_acceptance_report
from backend.app.algorithms.weighted_voting.global_interface import build_global_order_proposal_from_weighted_voting_proposal, apply_global_response_to_weighted_voting_proposal
from backend.app.algorithms.weighted_voting.identity import (
    WEIGHTED_VOTING_ALGORITHM_ID,
    WEIGHTED_VOTING_SERVICE_VERSION,
    weighted_voting_exclusion_inventory,
    weighted_voting_reason_code,
    weighted_voting_service_boundary,
    weighted_voting_shared_service_boundary,
)
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot, build_weighted_voting_market_snapshot
from backend.app.algorithms.weighted_voting.migration import migration_status
from backend.app.algorithms.weighted_voting.models import WeightedCandle, WeightedSide, WeightedStrategyOutcome, WeightedVotingDecision, WeightedVotingSignal, WeightedWeightState
from backend.app.algorithms.weighted_voting.observability import DECISION_OBSERVABILITY_PREFIX, observability_status, record_decision_observability
from backend.app.algorithms.weighted_voting.order_proposal import build_weighted_voting_order_proposal
from backend.app.algorithms.weighted_voting.persistence import (
    WEIGHTED_VOTING_SETTINGS_KEY,
    WeightedVotingFilesystemStateStore,
    WeightedVotingStateStore,
    load_effective_settings,
    persist_effective_settings,
)
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingContext, calculate_weighted_voting_position_size
from backend.app.algorithms.weighted_voting.rollout import rollout_status
from backend.app.algorithms.weighted_voting.scheduler import ACTIVE_WEIGHT_STATE_KEY, WeightedVotingDailySchedulerConfig, run_after_market_daily_weight_update
from backend.app.algorithms.weighted_voting.signal_engine import evaluate_signals
from backend.app.algorithms.weighted_voting.strategies.common import average_true_range, average_volume
from backend.app.algorithms.weighted_voting.weight_engine import append_weight_history, create_unseeded_equal_weight_state, rollback_weight_state, update_performance_weight_state, weight_engine_status
from backend.app.gates import GlobalGateResponse


class WeightedVotingService:
    """Thin orchestrator for future backend-authoritative Weighted Voting."""

    version = WEIGHTED_VOTING_SERVICE_VERSION

    def __init__(self, config: WeightedVotingConfig | None = None, store: WeightedVotingStateStore | None = None) -> None:
        self.config = config or WeightedVotingConfig()
        self.store = store or WeightedVotingFilesystemStateStore()

    def aggregate_signals(self, signals: list[WeightedVotingSignal]) -> WeightedVotingDecision:
        return aggregate_weighted_signals(signals, config=self.config)

    def status(self) -> dict[str, Any]:
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "serviceVersion": self.version,
            "serviceBoundary": asdict(weighted_voting_service_boundary()),
            "excludedComponents": weighted_voting_exclusion_inventory(),
            "sharedServiceBoundary": weighted_voting_shared_service_boundary(),
            "baselineConfiguration": self.config.baseline_configuration(),
            "strategyInventory": [asdict(item) for item in weighted_voting_dedicated_strategy_inventory()],
            "weightEngine": asdict(weight_engine_status(self.config)),
            "backtesting": {
                "engine": backtest_engine_status(),
                "executionSimulator": simulator_status(),
                "walkForward": walk_forward_status(),
            },
            "observability": observability_status(),
            "migration": migration_status(),
            "status": "ready",
            "mode": "backtesting_and_paper_trading_only",
            "isolated": True,
            "rollout": rollout_status(),
            "finalAcceptance": build_weighted_voting_final_acceptance_report(),
            "reasonCodes": (weighted_voting_reason_code("api.ready"),),
            "explanation": "Weighted Voting API is backend-authoritative and isolated from other algorithms.",
        }

    def get_config(self) -> dict[str, Any]:
        snapshot = _read_optional(self.store, WEIGHTED_VOTING_SETTINGS_KEY)
        if snapshot:
            return {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "configuration": snapshot,
                "source": "backend_store",
            }
        effective = resolve_effective_settings(timestamp=_now())
        persist_effective_settings(self.store, effective)
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "configuration": effective.model_dump(mode="json"),
            "source": "backend_default",
        }

    def put_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = default_weighted_settings(timestamp=_now())
        envelope = default_dynamic_envelope(timestamp=_now())
        limits = default_hard_limits(timestamp=_now())
        allowed_values = {field: value for field, value in payload.items() if hasattr(defaults, field)}
        effective = resolve_effective_settings(
            default_settings=defaults.model_copy(update=allowed_values),
            dynamic_envelope=envelope,
            hard_limits=limits,
            timestamp=_now(),
            configuration_version="weighted_voting_api_config_v1",
        )
        persist_effective_settings(self.store, effective)
        if isinstance(self.store, WeightedVotingFilesystemStateStore):
            self.store.write_artifact(
                "configurations",
                effective.settings_version,
                effective.model_dump(mode="json"),
                run_id="weighted_voting_config",
                data_hash="",
                config_hash=effective.configuration_hash,
                weight_version=self.active_weight_state().weight_version,
                created_at=effective.settings_timestamp,
            )
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "configuration": effective.model_dump(mode="json"),
            "reasonCodes": (weighted_voting_reason_code("config.updated"),),
        }

    def active_weight_state(self) -> WeightedWeightState:
        snapshot = _read_optional(self.store, ACTIVE_WEIGHT_STATE_KEY)
        if snapshot:
            return WeightedWeightState.model_validate(snapshot)
        state = create_unseeded_equal_weight_state(timestamp=_now())
        self.store.write_snapshot(ACTIVE_WEIGHT_STATE_KEY, state.model_dump(mode="json"))
        return state

    def weights_active(self) -> dict[str, Any]:
        state = self.active_weight_state()
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "weightState": state.model_dump(mode="json"),
        }

    def weights_history(self) -> dict[str, Any]:
        history = _read_optional(self.store, "weighted_voting.weights.history") or {"items": self._snapshots_with_prefix("weighted_voting.weights.history.")}
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "history": history.get("items", []),
        }

    def weights_recalculate(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        current = self.active_weight_state()
        outcomes = tuple(WeightedStrategyOutcome.model_validate(row) for row in payload.get("outcomes", ()))
        timestamp = _parse_datetime(payload.get("update_timestamp") or payload.get("updateTimestamp") or _now().isoformat())
        state = update_performance_weight_state(
            current,
            outcomes,
            update_timestamp=timestamp,
            data_timestamp=timestamp,
            session_date=payload.get("session_date") or payload.get("sessionDate"),
            config=self.config,
            regime_label=payload.get("regime_label") or payload.get("regimeLabel"),
        )
        history = tuple(WeightedWeightState.model_validate(row) for row in self.weights_history()["history"])
        updated_history = append_weight_history(history, state)
        self.store.write_snapshot(ACTIVE_WEIGHT_STATE_KEY, state.model_dump(mode="json"))
        self.store.write_snapshot("weighted_voting.weights.history", {"algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID, "items": [item.model_dump(mode="json") for item in updated_history]})
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "weightState": state.model_dump(mode="json"),
            "reasonCodes": state.reason_codes,
        }

    def weights_rollback(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        target = str(payload.get("target_weight_version") or payload.get("targetWeightVersion") or "")
        if not target:
            raise ValueError("target_weight_version is required")
        timestamp = _parse_datetime(payload.get("rollback_timestamp") or payload.get("rollbackTimestamp") or _now().isoformat())
        current = self.active_weight_state()
        history = tuple(WeightedWeightState.model_validate(row) for row in self.weights_history()["history"])
        state = rollback_weight_state(current, history, target_weight_version=target, rollback_timestamp=timestamp)
        self.store.write_snapshot(ACTIVE_WEIGHT_STATE_KEY, state.model_dump(mode="json"))
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "weightState": state.model_dump(mode="json"),
            "reasonCodes": state.reason_codes,
        }

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = build_weighted_voting_market_snapshot(payload)
        active_weight_state = self.active_weight_state()
        condition = classify_market_condition(snapshot, config=self.config)
        weighted_signals = tuple(evaluate_signals(snapshot, self.config, active_weight_state, condition))
        decision = aggregate_weighted_signals(list(weighted_signals), config=self.config, decision_timestamp=snapshot.data_timestamp)
        effective = load_effective_settings(self.store) if _read_optional(self.store, WEIGHTED_VOTING_SETTINGS_KEY) else resolve_effective_settings(timestamp=snapshot.data_timestamp)
        decision = decision.model_copy(
            update={
                "weight_version": active_weight_state.weight_version,
                "settings_version": effective.settings_version,
                "data_manifest_hash": snapshot.data_manifest_hash,
            }
        )
        gate_result = evaluate_local_decision_gates(
            WeightedVotingLocalGateInputs(
                decision=decision,
                signals=weighted_signals,
                market_snapshot=snapshot,
                five_minute_alignment=WeightedFiveMinuteAlignment.POSITIVE if decision.signal in (WeightedSide.BUY.value, WeightedSide.SELL.value) else WeightedFiveMinuteAlignment.UNAVAILABLE,
                expected_value_after_costs=_expected_value_after_costs(weighted_signals, decision, snapshot),
                spread_cost=_spread(snapshot),
                slippage_cost=0.02,
                fee_cost=0.01,
                atr_percent=_atr_percent(snapshot),
                entry_quality=decision.vote_scores.winner_score,
                session_allowed=True,
                weighted_daily_loss_percent=0.0,
                weighted_daily_trade_count=0,
                capital_available=float(payload.get("capital_available", 100_000.0)),
                current_position=None,
                data_timestamp=snapshot.data_timestamp,
            ),
            config=self.config,
        )
        sizing = calculate_weighted_voting_position_size(
            WeightedVotingSizingContext(
                decision=decision,
                effective_settings=effective,
                market_snapshot=snapshot,
                account_equity=float(payload.get("account_equity", 100_000.0)),
                available_buying_power=float(payload.get("available_buying_power", 100_000.0)),
                remaining_weighted_daily_risk=float(payload.get("remaining_weighted_daily_risk", 1_000.0)),
                remaining_weighted_capital_partition=float(payload.get("remaining_weighted_capital_partition", 30_000.0)),
                global_available_risk=float(payload.get("global_available_risk", 1_000.0)),
                global_max_shares=int(payload.get("global_max_shares", 2_147_483_647)),
                structural_invalidation_price=_structural_invalidation(weighted_signals, decision.proposed_side),
                atr=average_true_range(snapshot.one_minute_candles, 14),
                slippage_per_share=float(payload.get("slippage_per_share", 0.01)),
                current_one_minute_volume=snapshot.one_minute_candles[-1].volume,
                average_one_minute_volume=average_volume(snapshot.one_minute_candles, 20),
                local_gate_result=gate_result,
            )
        )
        trigger_price = _proposal_entry_price(snapshot, decision.proposed_side)
        stop_price = _proposal_stop_price(trigger_price, sizing.stop_distance, decision.proposed_side)
        target_price = _proposal_target_price(trigger_price, sizing.stop_distance, effective.target_r, decision.proposed_side)
        order_proposal = build_weighted_voting_order_proposal(
            decision=decision,
            sizing=sizing,
            effective_settings=effective,
            market_snapshot=snapshot,
            signals=weighted_signals,
            trigger_price=trigger_price,
            limit_price=trigger_price,
            stop_price=stop_price,
            target_price=target_price,
            created_at=snapshot.data_timestamp,
        )
        global_proposal = build_global_order_proposal_from_weighted_voting_proposal(
            proposal=order_proposal,
            decision=decision,
            sizing=sizing,
            effective_settings=effective,
        )
        global_response = _global_response_from_payload(payload, global_proposal, sizing)
        global_application = apply_global_response_to_weighted_voting_proposal(global_proposal, global_response)
        decision = decision.model_copy(
            update={
                "proposed_quantity": sizing.quantity,
                "gate_results": gate_result.gate_results,
            }
        )
        decision_payload = decision.model_dump(mode="json")
        self.store.write_snapshot(f"weighted_voting.decisions.{decision.decision_id}", decision_payload)
        self.store.write_snapshot(
            f"weighted_voting.signals.{decision.decision_id}",
            {
                "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                "decision_id": decision.decision_id,
                "signals": [signal.model_dump(mode="json") for signal in weighted_signals],
            },
        )
        self.store.write_snapshot(f"weighted_voting.order_proposals.{order_proposal.proposal_id}", order_proposal.as_dict())
        self.store.write_snapshot(f"weighted_voting.global_gate_applications.{decision.decision_id}", global_application.model_dump(mode="json"))
        observability_snapshot = record_decision_observability(
            store=self.store,
            market_snapshot=snapshot,
            signals=weighted_signals,
            active_weight_state=active_weight_state,
            decision=decision,
            market_condition=condition,
            effective_settings=effective,
            local_gate_result=gate_result,
            sizing_result=sizing,
            global_order_proposal=global_proposal,
            global_gate_response=global_response,
            global_gate_application=global_application,
            recorded_at=snapshot.data_timestamp,
        )
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "serviceVersion": self.version,
            "decision": decision_payload,
            "signals": [signal.model_dump(mode="json") for signal in weighted_signals],
            "marketCondition": condition.model_dump(mode="json"),
            "gateResult": _json_ready(gate_result),
            "sizingResult": _json_ready(sizing),
            "orderProposal": order_proposal.as_dict(),
            "globalOrderProposal": global_proposal.model_dump(mode="json"),
            "globalGateResponse": global_response.model_dump(mode="json"),
            "globalGateApplication": global_application.model_dump(mode="json"),
            "observabilitySnapshot": {
                "decisionId": observability_snapshot["decisionId"],
                "snapshotHash": observability_snapshot["snapshotHash"],
                "key": f"weighted_voting.observability.decisions.{observability_snapshot['decisionId']}",
            },
            "reasonCodes": ("weighted_voting.evaluate.completed",),
        }

    def create_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(payload.get("run_id") or f"weighted-voting-backtest-{_now().strftime('%Y%m%dT%H%M%S')}")
        symbol = str(payload.get("symbol") or "SPY")
        candles = _candles_from_payload(payload)
        result = run_weighted_voting_backtest(
            candles=candles,
            config=WeightedBacktestEngineConfig(symbol=symbol, run_id=run_id, source="weighted_voting_api_backtest"),
            created_at=_now(),
        )
        self._persist_backtest_result(result)
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runId": run_id,
            "result": _backtest_summary(result),
            "reasonCodes": ("weighted_voting.backtest.created",),
        }

    def get_backtest(self, run_id: str) -> dict[str, Any]:
        return self._read_backtest_payload(run_id)

    def get_backtest_collection(self, run_id: str, collection: str) -> dict[str, Any]:
        payload = self._read_backtest_payload(run_id)
        if collection == "equity":
            algorithm_results = payload.get("algorithmResults", {})
            return {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "runId": run_id,
                "equity": algorithm_results.get("equity_curve", algorithm_results.get("equityCurve", [])),
            }
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runId": run_id,
            collection: payload.get(collection, []),
        }

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "decision": self._read_required(f"weighted_voting.decisions.{decision_id}"),
        }

    def get_signals(self, decision_id: str) -> dict[str, Any]:
        snapshot = _read_optional(self.store, f"weighted_voting.signals.{decision_id}")
        if snapshot:
            signals = snapshot.get("signals", [])
        else:
            observability = self._read_required(f"{DECISION_OBSERVABILITY_PREFIX}{decision_id}")
            signals = observability.get("strategySignals", [])
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "decisionId": decision_id,
            "signals": signals,
        }

    def performance(self) -> dict[str, Any]:
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "performance": _read_optional(self.store, "weighted_voting.performance.latest") or {},
        }

    def performance_strategies(self) -> dict[str, Any]:
        latest = _read_optional(self.store, "weighted_voting.performance.latest") or {}
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "strategies": latest.get("strategyLevel", latest.get("strategy_level", self._snapshots_with_prefix("weighted_voting.performance.strategy."))),
        }

    def performance_market_conditions(self) -> dict[str, Any]:
        latest = _read_optional(self.store, "weighted_voting.performance.latest") or {}
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "marketConditions": latest.get("marketConditionLevel", latest.get("market_condition_level", self._snapshots_with_prefix("weighted_voting.performance.market_condition."))),
        }

    def positions(self) -> dict[str, Any]:
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "positions": self._collection("weighted_voting.positions.index", "weighted_voting.positions."),
        }

    def trades(self) -> dict[str, Any]:
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "trades": self._collection("weighted_voting.trades.index", "weighted_voting.trades."),
        }

    def observability(self, decision_id: str) -> dict[str, Any]:
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "observability": self._read_required(f"{DECISION_OBSERVABILITY_PREFIX}{decision_id}"),
        }

    def daily_update_status(self) -> dict[str, Any]:
        latest = _read_optional(self.store, "weighted_voting.daily_update.latest")
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "dailyUpdate": latest or {"status": "never_run", "reasonCodes": ("weighted_voting.daily_update.not_run",)},
        }

    def run_daily_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_date = date.fromisoformat(str(payload["session_date"]))
        symbol = str(payload.get("symbol") or "SPY")
        candles = _candles_from_payload(payload)
        provider = _StaticDatasetProvider(candles)
        result = run_after_market_daily_weight_update(
            session_date=session_date,
            store=self.store,
            dataset_provider=provider,
            completed_at=_parse_datetime(payload.get("completed_at") or _now().isoformat()),
            config=WeightedVotingDailySchedulerConfig(symbol=symbol),
        )
        payload_result = _json_ready(result)
        self.store.write_snapshot("weighted_voting.daily_update.latest", payload_result)
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "dailyUpdate": payload_result,
        }

    def _persist_backtest_result(self, result: WeightedBacktestResult) -> None:
        payload = _backtest_payload(result)
        self.store.write_snapshot(f"weighted_voting.backtests.{result.run.run_id}", payload)
        if isinstance(self.store, WeightedVotingFilesystemStateStore):
            self.store.write_artifact(
                "backtest_runs",
                result.run.run_id,
                payload,
                run_id=result.run.run_id,
                data_hash=result.manifest.data_hash,
                config_hash=result.run.configuration_version,
                weight_version=result.run.weight_version,
                created_at=result.run.started_at,
            )

    def _read_backtest_payload(self, run_id: str) -> dict[str, Any]:
        snapshot = _read_optional(self.store, f"weighted_voting.backtests.{run_id}")
        if not snapshot:
            raise KeyError(run_id)
        return snapshot

    def _read_required(self, key: str) -> dict[str, Any]:
        snapshot = _read_optional(self.store, key)
        if snapshot is None:
            raise KeyError(key)
        return snapshot

    def _collection(self, index_key: str, prefix: str) -> list[dict[str, Any]]:
        index = _read_optional(self.store, index_key)
        if isinstance(index, dict):
            items = index.get("items")
            if isinstance(items, list):
                return items
            ids = index.get("ids") or index.get("record_ids")
            if isinstance(ids, list):
                return [item for item in (_read_optional(self.store, f"{prefix}{record_id}") for record_id in ids) if item is not None]
        return self._snapshots_with_prefix(prefix, exclude=(index_key,))

    def _snapshots_with_prefix(self, prefix: str, *, exclude: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        snapshots = getattr(self.store, "snapshots", None)
        if not isinstance(snapshots, dict):
            return []
        return [
            _json_ready(value)
            for key, value in sorted(snapshots.items())
            if str(key).startswith(prefix) and str(key) not in exclude
        ]


def _candles_from_payload(payload: dict[str, Any]) -> tuple[WeightedCandle, ...]:
    rows = payload.get("candles") or ()
    if not isinstance(rows, (list, tuple)):
        raise ValueError("candles must be a list")
    candles = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each candle must be an object")
        values = dict(row)
        timestamp = values.get("timestamp")
        if isinstance(timestamp, str):
            values["timestamp"] = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        candles.append(WeightedCandle(**values))
    return tuple(candles)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("datetime value must be an ISO-8601 string")


class _StaticDatasetProvider:
    def __init__(self, candles: tuple[WeightedCandle, ...]) -> None:
        self.candles = candles

    def candles_for_session(self, session_date: date) -> tuple[WeightedCandle, ...]:
        return self.candles


def _expected_value_after_costs(signals: tuple[WeightedVotingSignal, ...], decision: WeightedVotingDecision, snapshot: WeightedVotingMarketSnapshot) -> float:
    values = [signal.expected_return_after_costs for signal in signals if signal.signal == decision.proposed_side]
    cost = _spread(snapshot) / snapshot.one_minute_candles[-1].close if snapshot.one_minute_candles[-1].close > 0 else 0.0
    return (max(values) if values else 0.0) - cost


def _spread(snapshot: WeightedVotingMarketSnapshot) -> float:
    if snapshot.bid is None or snapshot.ask is None:
        return 0.0
    return max(0.0, snapshot.ask - snapshot.bid)


def _atr_percent(snapshot: WeightedVotingMarketSnapshot) -> float | None:
    atr = average_true_range(snapshot.one_minute_candles, 14)
    latest = snapshot.one_minute_candles[-1]
    return atr / latest.close if atr is not None and latest.close > 0 else None


def _structural_invalidation(signals: tuple[WeightedVotingSignal, ...], side: str) -> float | None:
    levels = [signal.invalidation_level for signal in signals if signal.signal == side and signal.invalidation_level is not None]
    if not levels:
        return None
    return max(levels) if side == WeightedSide.BUY.value else min(levels)


def _proposal_entry_price(snapshot: WeightedVotingMarketSnapshot, side: str) -> float | None:
    if side == WeightedSide.BUY.value:
        return snapshot.ask
    if side == WeightedSide.SELL.value:
        return snapshot.bid
    return None


def _proposal_stop_price(entry_price: float | None, stop_distance: float, side: str) -> float | None:
    if entry_price is None or stop_distance <= 0:
        return None
    if side == WeightedSide.BUY.value:
        return max(0.01, round(entry_price - stop_distance, 4))
    if side == WeightedSide.SELL.value:
        return round(entry_price + stop_distance, 4)
    return None


def _proposal_target_price(entry_price: float | None, stop_distance: float, target_r: float, side: str) -> float | None:
    if entry_price is None or stop_distance <= 0 or target_r <= 0:
        return None
    target_distance = stop_distance * target_r
    if side == WeightedSide.BUY.value:
        return round(entry_price + target_distance, 4)
    if side == WeightedSide.SELL.value:
        return max(0.01, round(entry_price - target_distance, 4))
    return None


def _global_response_from_payload(payload: dict[str, Any], proposal, sizing) -> GlobalGateResponse:
    response = payload.get("global_gate_response") or payload.get("globalGateResponse") or {}
    if not isinstance(response, dict):
        raise ValueError("global gate response must be an object")
    values = {
        "action": response.get("action", "ALLOW"),
        "maximumAllowedQuantity": response.get("maximumAllowedQuantity", response.get("maximum_allowed_quantity", sizing.quantity)),
        "maximumAdditionalRiskDollars": response.get("maximumAdditionalRiskDollars", response.get("maximum_additional_risk_dollars", sizing.effective_risk_dollars)),
        "rejectionReasons": tuple(response.get("rejectionReasons", response.get("rejection_reasons", ()))),
        "emergencyAction": response.get("emergencyAction", response.get("emergency_action")),
        "evaluatedAt": response.get("evaluatedAt", response.get("evaluated_at", proposal.proposedAt)),
        "configurationHash": response.get("configurationHash", response.get("configuration_hash", "global_gate_response_default_allow")),
    }
    return GlobalGateResponse(**values)


def _backtest_summary(result: WeightedBacktestResult) -> dict[str, Any]:
    return {
        "run": result.run.model_dump(mode="json"),
        "manifest": _json_ready(result.manifest),
        "configurationManifest": _json_ready(result.configuration_manifest),
        "reproducibilityHash": result.reproducibility_hash,
        "algorithmResults": _json_ready(result.algorithm_results),
        "tradeCount": len(result.trades),
        "decisionCount": len(result.decisions),
        "strategyPerformance": {key: _json_ready(value) for key, value in result.strategy_results.items()},
    }


def _backtest_payload(result: WeightedBacktestResult) -> dict[str, Any]:
    return {
        **_backtest_summary(result),
        "trades": [_json_ready(trade) for trade in result.trades],
        "decisions": [_json_ready(decision) for decision in result.decisions],
        "strategyPerformance": {key: _json_ready(value) for key, value in result.strategy_results.items()},
    }


def _read_optional(store: WeightedVotingStateStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _json_ready(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)

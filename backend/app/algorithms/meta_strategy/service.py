"""Application-service boundary for the Meta-Strategy algorithm."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from backend.app.algorithms.meta_strategy.backtest import MetaStrategyBacktestRequest, run_meta_strategy_backtest
from backend.app.algorithms.meta_strategy.execution_pipeline import (
    META_STRATEGY_EXECUTION_PIPELINE_STAGES,
    MetaStrategyExecutionPipelineRequest,
    pipeline_modes_using_authoritative_sequence,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.feature_schema import meta_strategy_feature_schema_hash
from backend.app.algorithms.meta_strategy.final_acceptance import build_meta_strategy_final_acceptance_report
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID, ALGORITHM_NAME
from backend.app.algorithms.meta_strategy.market_snapshot import MetaStrategyMarketSnapshotRequest
from backend.app.algorithms.meta_strategy.models import load_runtime_model_artifact, load_runtime_model_artifact_data
from backend.app.algorithms.meta_strategy.promotion import (
    build_meta_strategy_promotion_evidence,
    evaluate_meta_strategy_promotion_policy,
    validate_meta_strategy_paper_stability,
)
from backend.app.algorithms.meta_strategy.training import train_and_validate_meta_model_v2
from backend.app.algorithms.meta_strategy.versions import meta_strategy_version_identifiers


ServiceStatus = Literal["OK", "REQUIRES_INPUT", "REJECTED"]


@dataclass(frozen=True)
class MetaStrategyServiceResult:
    algorithmId: str
    operation: str
    status: ServiceStatus
    payload: Mapping[str, Any]
    reasonCodes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithmId,
            "operation": self.operation,
            "status": self.status,
            "payload": _plain(self.payload),
            "reasonCodes": list(self.reasonCodes),
        }


class MetaStrategyApplicationService:
    """Thin orchestration layer over the authoritative Meta-Strategy package."""

    def status(self) -> dict[str, Any]:
        diagnostics = self.diagnostics()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="status",
            status="OK",
            payload={
                "algorithmName": ALGORITHM_NAME,
                "router": "backend.app.algorithms.meta_strategy.api",
                "packageBoundary": "dedicated",
                "modelStatus": {
                    "mode": "OFF",
                    "status": "not_loaded",
                    "reasonCodes": ("meta_strategy.model.off_by_default",),
                },
                "diagnostics": diagnostics["payload"],
            },
            reasonCodes=("meta_strategy.service.status_ready",),
        ).to_dict()

    def configuration(self) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="configuration",
            status="OK",
            payload={
                "versions": meta_strategy_version_identifiers(),
                "baselineImmutable": True,
                "effectiveProfileDoesNotOverwriteDefaults": True,
            },
            reasonCodes=("meta_strategy.service.configuration_ready",),
        ).to_dict()

    def evaluate(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._run_pipeline("evaluation", "EVALUATION", payload).to_dict()

    def predict(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = self._run_pipeline("prediction", "EVALUATION", payload).to_dict()
        result["payload"]["orderSubmissionAllowed"] = False
        result["payload"]["approvedSubmissionEndpointRequired"] = True
        result["reasonCodes"] = [*result["reasonCodes"], "meta_strategy.prediction.no_order_submission"]
        return result

    def shadow_evaluate(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._run_pipeline("shadow_evaluation", "SHADOW", payload).to_dict()

    def paper_evaluate(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._run_pipeline("paper_evaluation", "PAPER", payload).to_dict()

    def deterministic_activation(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.shadow_evaluate(payload)

    def ml_filter_rollout(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.shadow_evaluate(payload)

    def dynamic_policy_shadow(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.shadow_evaluate(payload)

    def dynamic_policy_activation(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.paper_evaluate(payload)

    def ml_risk_modifier_experiment(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self.shadow_evaluate(payload)
        response["payload"]["riskModifierAppliedToOrders"] = False
        response["reasonCodes"] = [*response["reasonCodes"], "meta_strategy.ml_risk_modifier.experiment_no_order_submission"]
        return response

    def train(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        training_arguments = data.get("trainingArguments") or data.get("training_arguments")
        if not isinstance(training_arguments, Mapping):
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="training",
                status="REQUIRES_INPUT",
                payload={
                    "authoritativeEntrypoint": "backend.app.algorithms.meta_strategy.training.train_and_validate_meta_model_v2",
                    "requiredInput": "trainingArguments",
                },
                reasonCodes=("meta_strategy.service.training_arguments_required",),
            ).to_dict()
        result = train_and_validate_meta_model_v2(**dict(training_arguments))
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="training",
            status="OK",
            payload={"trainingResult": _plain(result)},
            reasonCodes=("meta_strategy.service.training_dispatched",),
        ).to_dict()

    def load_artifact(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        artifact = data.get("artifact") or data.get("modelArtifact")
        artifact_path = data.get("artifactPath") or data.get("path")
        expected_hash = str(data.get("expectedFeatureSchemaHash") or data.get("expected_feature_schema_hash") or "")
        if isinstance(artifact, Mapping):
            expected_hash = expected_hash or str(artifact.get("featureSchemaHash") or meta_strategy_feature_schema_hash())
            loaded = load_runtime_model_artifact_data(dict(artifact), expected_feature_schema_hash=expected_hash)
        elif artifact_path:
            loaded = load_runtime_model_artifact(Path(str(artifact_path)), expected_feature_schema_hash=expected_hash or meta_strategy_feature_schema_hash())
        else:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="artifact_loading",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "artifact or artifactPath"},
                reasonCodes=("meta_strategy.service.artifact_required",),
            ).to_dict()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="artifact_loading",
            status="OK",
            payload={
                "artifactId": loaded.artifactId,
                "artifactHash": loaded.artifactHash,
                "modelVersion": loaded.modelVersion,
                "featureSchemaHash": loaded.featureSchemaHash,
                "promotionStatus": loaded.promotionStatus,
            },
            reasonCodes=("meta_strategy.service.artifact_loaded",),
        ).to_dict()

    def backtest(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        snapshot_payloads = data.get("decisionRequests") or data.get("decision_requests")
        if not isinstance(snapshot_payloads, Sequence) or isinstance(snapshot_payloads, str | bytes):
            single = data.get("snapshotRequest") or data.get("snapshot_request")
            snapshot_payloads = (single,) if single is not None else ()
        if not snapshot_payloads:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="backtesting",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "decisionRequests"},
                reasonCodes=("meta_strategy.service.backtest_decision_requests_required",),
            ).to_dict()
        request = MetaStrategyBacktestRequest(
            decision_requests=tuple(_snapshot_request(row) for row in snapshot_payloads),
            model_artifacts=tuple(dict(row) for row in data.get("modelArtifacts", ())),
        )
        result = run_meta_strategy_backtest(request)
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="backtesting",
            status="OK",
            payload={
                "decisionCount": len(result.decisions),
                "metrics": result.metrics,
                "diagnostics": result.diagnostics,
                "runtimeParity": result.runtime_parity,
                "report": result.report,
            },
            reasonCodes=("meta_strategy.service.backtest_completed",),
        ).to_dict()

    def promote(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        candidate_artifact = data.get("candidateArtifact") or data.get("candidate_artifact") or data.get("artifact")
        evidence_payload = data.get("evidence") or {}
        if not isinstance(candidate_artifact, Mapping):
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="promotion",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "candidateArtifact"},
                reasonCodes=("meta_strategy.service.candidate_artifact_required",),
            ).to_dict()
        evidence = build_meta_strategy_promotion_evidence(candidate_artifact=candidate_artifact, **dict(evidence_payload))
        decision = evaluate_meta_strategy_promotion_policy(evidence, candidate_artifact=candidate_artifact)
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="promotion",
            status="OK" if decision.promoted else "REJECTED",
            payload={"decision": decision},
            reasonCodes=decision.reason_codes,
        ).to_dict()

    def validate_paper_stability(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        candidate_artifact = data.get("candidateArtifact") or data.get("candidate_artifact") or data.get("artifact")
        observations = data.get("observations") or ()
        if not isinstance(candidate_artifact, Mapping):
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="paper_stability",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "candidateArtifact"},
                reasonCodes=("meta_strategy.service.candidate_artifact_required",),
            ).to_dict()
        evidence = validate_meta_strategy_paper_stability(
            candidate_artifact=candidate_artifact,
            observations=tuple(dict(row) for row in observations),
        )
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="paper_stability",
            status="OK" if evidence.stable else "REJECTED",
            payload={"evidence": evidence},
            reasonCodes=evidence.reason_codes,
        ).to_dict()

    def final_acceptance(self) -> dict[str, Any]:
        report = build_meta_strategy_final_acceptance_report()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="final_acceptance",
            status="OK" if report["complete"] else "REJECTED",
            payload=report,
            reasonCodes=("meta_strategy.service.final_acceptance_ready",),
        ).to_dict()

    def diagnostics(self) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="diagnostics",
            status="OK",
            payload={
                "algorithmName": ALGORITHM_NAME,
                "versions": meta_strategy_version_identifiers(),
                "authoritativePipelineStages": META_STRATEGY_EXECUTION_PIPELINE_STAGES,
                "pipelineModes": pipeline_modes_using_authoritative_sequence(),
                "serviceOperations": (
                    "evaluation",
                    "training",
                    "artifact_loading",
                    "backtesting",
                    "shadow_evaluation",
                    "paper_evaluation",
                    "promotion",
                    "paper_stability",
                    "diagnostics",
                ),
            },
            reasonCodes=("meta_strategy.service.diagnostics_ready",),
        ).to_dict()

    def _run_pipeline(
        self,
        operation: str,
        mode: Literal["EVALUATION", "SHADOW", "PAPER"],
        payload: Mapping[str, Any] | None,
    ) -> MetaStrategyServiceResult:
        data = dict(payload or {})
        snapshot_payload = data.get("snapshotRequest") or data.get("snapshot_request")
        if snapshot_payload is None:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation=operation,
                status="REQUIRES_INPUT",
                payload={"requiredInput": "snapshotRequest"},
                reasonCodes=("meta_strategy.service.snapshot_request_required",),
            )
        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(
                mode=mode,
                snapshot_request=_snapshot_request(snapshot_payload),
                model_artifact=_optional_mapping(data.get("modelArtifact") or data.get("model_artifact")),
                account_equity=_optional_float(data, "accountEquity", "account_equity"),
                available_buying_power=_optional_float(data, "availableBuyingPower", "available_buying_power"),
                remaining_algorithm_risk=_optional_float(data, "remainingAlgorithmRisk", "remaining_algorithm_risk"),
                global_available_risk=_optional_float(data, "globalAvailableRisk", "global_available_risk"),
                global_quantity_cap=_optional_int(data, "globalQuantityCap", "global_quantity_cap"),
                realized_daily_pnl=float(data.get("realizedDailyPnl", data.get("realized_daily_pnl", 0.0)) or 0.0),
                daily_trade_count=int(data.get("dailyTradeCount", data.get("daily_trade_count", 0)) or 0),
                paper_trading_permission=bool(data.get("paperTradingPermission", data.get("paper_trading_permission", True))),
                live_trading_permission=bool(data.get("liveTradingPermission", data.get("live_trading_permission", False))),
                event_blackout=bool(data.get("eventBlackout", data.get("event_blackout", False))),
                session_allowed=bool(data.get("sessionAllowed", data.get("session_allowed", True))),
                broker_quantity=int(data.get("brokerQuantity", data.get("broker_quantity", 0)) or 0),
                duplicate_order_intent_ids=tuple(data.get("duplicateOrderIntentIds", data.get("duplicate_order_intent_ids", ())) or ()),
                existing_position_symbols=tuple(data.get("existingPositionSymbols", data.get("existing_position_symbols", ())) or ()),
                max_quote_age_seconds=int(data.get("maxQuoteAgeSeconds", data.get("max_quote_age_seconds", 60)) or 60),
            )
        )
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation=operation,
            status="OK",
            payload=_pipeline_summary(result),
            reasonCodes=result.reason_codes,
        )


def _snapshot_request(value: Any) -> MetaStrategyMarketSnapshotRequest:
    if isinstance(value, MetaStrategyMarketSnapshotRequest):
        return value
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    return MetaStrategyMarketSnapshotRequest.model_validate(value)


def _optional_mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _optional_float(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            return float(payload[key])
    return None


def _optional_int(payload: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            return int(payload[key])
    return None


def _pipeline_summary(result: Any) -> dict[str, Any]:
    return {
        "mode": result.mode,
        "decisionId": result.snapshot.decision_id,
        "snapshotId": result.snapshot.snapshot_id,
        "symbol": result.snapshot.symbol,
        "stageSequence": result.stage_sequence,
        "deterministicCandidate": {
            "direction": result.deterministic_candidate.direction,
            "confidence": result.deterministic_candidate.deterministic_confidence,
            "winningScore": result.deterministic_candidate.winning_score,
            "opposingScore": result.deterministic_candidate.opposing_score,
            "edge": result.deterministic_candidate.edge,
        },
        "geometry": result.geometry,
        "inference": result.inference,
        "localGates": result.local_gates,
        "dynamicProfile": result.dynamic_profile,
        "sizing": result.sizing,
        "orderIntent": result.order_intent,
        "globalRisk": result.global_risk,
        "orderValidation": result.order_validation,
        "brokerResult": result.broker_result,
        "persistenceResult": result.persistence_result,
        "reconciliation": result.reconciliation,
        "finalValid": result.final_valid,
    }


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_plain(item) for item in value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


__all__ = [
    "MetaStrategyApplicationService",
    "MetaStrategyServiceResult",
]

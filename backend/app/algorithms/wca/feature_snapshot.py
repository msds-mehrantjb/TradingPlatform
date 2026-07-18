"""Read-only WCA feature snapshots for external analytics and ML consumers."""

from __future__ import annotations

from backend.app.algorithms.wca.contracts import WCA_FEATURE_SNAPSHOT_SCHEMA_VERSION, WcaDecision, WcaDynamicProfile, WcaReadOnlyFeatureSnapshot, WcaSide

WCA_FEATURE_SNAPSHOT_VERSION = WCA_FEATURE_SNAPSHOT_SCHEMA_VERSION


def build_wca_feature_snapshot(
    decision: WcaDecision,
    *,
    snapshot_id: str | None = None,
    dynamic_profile: WcaDynamicProfile | None = None,
) -> WcaReadOnlyFeatureSnapshot:
    aggregation = decision.aggregation
    final_side = aggregation.post_local_gate_decision or aggregation.signal or WcaSide.HOLD
    agreement = aggregation.buy_agreement if final_side == WcaSide.BUY.value else aggregation.sell_agreement if final_side == WcaSide.SELL.value else max(aggregation.buy_agreement, aggregation.sell_agreement)
    return WcaReadOnlyFeatureSnapshot(
        snapshot_id=snapshot_id or f"wca-feature-{decision.decision_id}",
        decision_id=decision.decision_id,
        strategy_signals=aggregation.strategy_evaluations,
        strategy_calibrated_confidences={row.strategy_id: row.calibrated_confidence for row in aggregation.strategy_evaluations},
        effective_weights={row.strategy_id: row.effective_weight for row in aggregation.strategy_evaluations},
        family_contributions=aggregation.family_contributions,
        buy_score=aggregation.buy_score,
        sell_score=aggregation.sell_score,
        normalized_score=aggregation.normalized_net_score,
        agreement=agreement,
        score_edge=aggregation.winner_edge,
        market_status=decision.market_status,
        dynamic_profile=dynamic_profile,
        local_gate_results=decision.local_gates,
        final_wca_decision=final_side,
        reason_codes=(WCA_FEATURE_SNAPSHOT_VERSION, "wca.feature_snapshot.read_only"),
    )


__all__ = ["WCA_FEATURE_SNAPSHOT_VERSION", "build_wca_feature_snapshot"]

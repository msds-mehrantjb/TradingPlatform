"""Meta-Strategy promotion and rollout governance."""

from backend.app.algorithms.meta_strategy.promotion.evidence import (
    META_STRATEGY_PROMOTION_EVIDENCE_FIELDS,
    MetaStrategyPromotionEvidence,
    PromotionEvidenceSourceError,
    build_meta_strategy_promotion_evidence,
    evidence_matches_candidate_artifact,
)
from backend.app.algorithms.meta_strategy.promotion.paper_stability import (
    MetaStrategyPaperStabilityConfig,
    MetaStrategyPaperStabilityEvidence,
    paper_stability_matches_candidate_artifact,
    validate_meta_strategy_paper_stability,
)
from backend.app.algorithms.meta_strategy.promotion.policy import (
    MetaStrategyPromotionDecision,
    MetaStrategyPromotionPolicy,
    evaluate_meta_strategy_promotion_policy,
)
from backend.app.algorithms.meta_strategy.promotion.rollout import (
    META_STRATEGY_LIVE_ROLLOUT_STAGES,
    META_STRATEGY_ROLLOUT_STAGES,
    MetaStrategyManualApproval,
    MetaStrategyRolloutDecision,
    MetaStrategyRolloutState,
    advance_meta_strategy_rollout,
    initial_meta_strategy_rollout_state,
    rollback_meta_strategy_rollout,
)

__all__ = [
    "META_STRATEGY_LIVE_ROLLOUT_STAGES",
    "META_STRATEGY_PROMOTION_EVIDENCE_FIELDS",
    "META_STRATEGY_ROLLOUT_STAGES",
    "MetaStrategyManualApproval",
    "MetaStrategyPaperStabilityConfig",
    "MetaStrategyPaperStabilityEvidence",
    "MetaStrategyPromotionDecision",
    "MetaStrategyPromotionEvidence",
    "MetaStrategyPromotionPolicy",
    "MetaStrategyRolloutDecision",
    "MetaStrategyRolloutState",
    "PromotionEvidenceSourceError",
    "advance_meta_strategy_rollout",
    "build_meta_strategy_promotion_evidence",
    "evaluate_meta_strategy_promotion_policy",
    "evidence_matches_candidate_artifact",
    "initial_meta_strategy_rollout_state",
    "paper_stability_matches_candidate_artifact",
    "rollback_meta_strategy_rollout",
    "validate_meta_strategy_paper_stability",
]

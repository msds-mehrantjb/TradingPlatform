from __future__ import annotations

from pydantic import Field

from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot
from backend.app.algorithms.voting_ensemble.strategies.base import StrategyEvaluationContext, hold_signal as context_hold_signal
from backend.app.algorithms.voting_ensemble.strategies.directional.signal_contract import (
    DirectionalStrategyParameters,
    DirectionalStrategySignal,
    directional_signal,
    hold_signal,
)
from backend.app.algorithms.voting_ensemble.strategies.directional.snapshot_helpers import lower_wick_ratio, spy_candles, upper_wick_ratio
from backend.app.algorithms.voting_ensemble.strategies.registry import resolve_strategy


class AtrOverextensionReversionParameters(DirectionalStrategyParameters):
    minExtensionAtr: float = Field(default=1.25, ge=0.1)
    minRejectionWickRatio: float = Field(default=0.20, ge=0.0, le=1.0)


class AtrOverextensionReversionStrategy:
    strategyId = "atr_overextension_reversion"
    strategyName = "ATR Overextension Reversion"
    strategyVersion = "atr_overextension_reversion_v1"
    family = "mean_reversion"

    def __init__(self, parameters: AtrOverextensionReversionParameters | None = None) -> None:
        self.parameters = parameters or AtrOverextensionReversionParameters()

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, correlation_id: str) -> DirectionalStrategySignal:
        candles = spy_candles(snapshot)
        latest = candles[-1] if candles else None
        atr = snapshot.features.atr
        anchor = snapshot.features.vwap or snapshot.features.bollingerMiddle
        if latest is None or atr is None or atr <= 0 or anchor is None:
            return self._hold(snapshot, correlation_id, "ATR or reversion anchor is unavailable.", "atr_overextension_reversion.data_unavailable", data_ready=False)
        extension_atr = (latest.close - anchor) / atr
        if extension_atr <= -self.parameters.minExtensionAtr and lower_wick_ratio(latest) >= self.parameters.minRejectionWickRatio:
            return directional_signal(
                strategy_id=self.strategyId,
                strategy_name=self.strategyName,
                strategy_version=self.strategyVersion,
                family=self.family,
                signal="Buy",
                confidence=min(0.9, 0.35 + abs(extension_atr) / 4),
                evaluated_at=snapshot.evaluationTimestamp,
                correlation_id=correlation_id,
                evidence=(f"Close is {extension_atr:.2f} ATR below the reversion anchor {anchor:.2f} with lower-wick rejection.",),
                reason_codes=("atr_overextension_reversion.buy_extension",),
                features={"extensionAtr": round(extension_atr, 4), "anchor": round(anchor, 4)},
            )
        if extension_atr >= self.parameters.minExtensionAtr and upper_wick_ratio(latest) >= self.parameters.minRejectionWickRatio:
            return directional_signal(
                strategy_id=self.strategyId,
                strategy_name=self.strategyName,
                strategy_version=self.strategyVersion,
                family=self.family,
                signal="Sell",
                confidence=min(0.9, 0.35 + abs(extension_atr) / 4),
                evaluated_at=snapshot.evaluationTimestamp,
                correlation_id=correlation_id,
                evidence=(f"Close is {extension_atr:.2f} ATR above the reversion anchor {anchor:.2f} with upper-wick rejection.",),
                reason_codes=("atr_overextension_reversion.sell_extension",),
                features={"extensionAtr": round(extension_atr, 4), "anchor": round(anchor, 4)},
            )
        return self._hold(snapshot, correlation_id, "No ATR overextension with rejection evidence.", "atr_overextension_reversion.no_extension")

    def _hold(self, snapshot: VotingEnsembleEvaluationSnapshot, correlation_id: str, reason: str, code: str, *, data_ready: bool = True) -> DirectionalStrategySignal:
        return hold_signal(
            strategy_id=self.strategyId,
            strategy_name=self.strategyName,
            strategy_version=self.strategyVersion,
            family=self.family,
            evaluated_at=snapshot.evaluationTimestamp,
            correlation_id=correlation_id,
            reason=reason,
            reason_code=code,
            data_ready=data_ready,
        )


class AtrOverextensionReplayCompatibilityStrategy:
    registryEntry = resolve_strategy("atr_overextension_reversion")

    def evaluate(self, context: StrategyEvaluationContext):
        return context_hold_signal(
            context,
            confidence=0.10,
            setupDetected=False,
            regimeFit=1.0,
            reliability=0.5,
            reasonCodes=["atr_overextension_reversion.snapshot_runtime_required"],
            explanation="ATR overextension is evaluated by the immutable-snapshot Voting Ensemble runtime; the legacy replay component is hold-only.",
            featureNames=("atr", "session_vwap", "spy_1m_candles"),
        )

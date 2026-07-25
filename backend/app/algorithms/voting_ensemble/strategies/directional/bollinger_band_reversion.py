from __future__ import annotations

from pydantic import Field

from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot
from backend.app.algorithms.voting_ensemble.strategies.directional.signal_contract import (
    DirectionalStrategyParameters,
    DirectionalStrategySignal,
    directional_signal,
    hold_signal,
)
from backend.app.algorithms.voting_ensemble.strategies.directional.snapshot_helpers import latest_close, lower_wick_ratio, spy_candles, upper_wick_ratio


class BollingerBandReversionParameters(DirectionalStrategyParameters):
    minBandExtensionPercent: float = Field(default=0.0005, ge=0.0)
    minRejectionWickRatio: float = Field(default=0.20, ge=0.0, le=1.0)


class BollingerBandReversionStrategy:
    strategyId = "bollinger_band_reversion"
    strategyName = "Bollinger Band Reversion"
    strategyVersion = "bollinger_band_reversion_v1"
    family = "mean_reversion"

    def __init__(self, parameters: BollingerBandReversionParameters | None = None) -> None:
        self.parameters = parameters or BollingerBandReversionParameters()

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, correlation_id: str) -> DirectionalStrategySignal:
        candles = spy_candles(snapshot)
        latest = candles[-1] if candles else None
        upper = snapshot.features.bollingerUpper
        lower = snapshot.features.bollingerLower
        middle = snapshot.features.bollingerMiddle
        if latest is None or upper is None or lower is None or middle is None:
            return self._hold(snapshot, correlation_id, "Bollinger bands or finalised candles are unavailable.", "bollinger_band_reversion.data_unavailable", data_ready=False)
        close = latest_close(snapshot)
        lower_extension = max(0.0, lower - latest.low) / max(close, 0.01)
        upper_extension = max(0.0, latest.high - upper) / max(close, 0.01)
        if lower_extension >= self.parameters.minBandExtensionPercent and latest.close >= lower and lower_wick_ratio(latest) >= self.parameters.minRejectionWickRatio:
            return directional_signal(
                strategy_id=self.strategyId,
                strategy_name=self.strategyName,
                strategy_version=self.strategyVersion,
                family=self.family,
                signal="Buy",
                confidence=min(0.9, 0.45 + lower_extension * 200),
                evaluated_at=snapshot.evaluationTimestamp,
                correlation_id=correlation_id,
                evidence=(f"Price rejected below lower band {lower:.2f} and closed back inside toward middle {middle:.2f}.",),
                reason_codes=("bollinger_band_reversion.buy_reentry",),
                features={"bandPosition": "lower_reentry", "lowerExtensionPercent": round(lower_extension, 6), "lowerWickRatio": round(lower_wick_ratio(latest), 4)},
            )
        if upper_extension >= self.parameters.minBandExtensionPercent and latest.close <= upper and upper_wick_ratio(latest) >= self.parameters.minRejectionWickRatio:
            return directional_signal(
                strategy_id=self.strategyId,
                strategy_name=self.strategyName,
                strategy_version=self.strategyVersion,
                family=self.family,
                signal="Sell",
                confidence=min(0.9, 0.45 + upper_extension * 200),
                evaluated_at=snapshot.evaluationTimestamp,
                correlation_id=correlation_id,
                evidence=(f"Price rejected above upper band {upper:.2f} and closed back inside toward middle {middle:.2f}.",),
                reason_codes=("bollinger_band_reversion.sell_reentry",),
                features={"bandPosition": "upper_reentry", "upperExtensionPercent": round(upper_extension, 6), "upperWickRatio": round(upper_wick_ratio(latest), 4)},
            )
        return self._hold(snapshot, correlation_id, "No completed Bollinger band re-entry evidence.", "bollinger_band_reversion.no_reentry")

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

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.algorithms.voting_ensemble.models import VotingCandle
from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot
from backend.app.algorithms.voting_ensemble.strategies.directional.signal_contract import (
    DirectionalStrategySignal,
    directional_signal,
    hold_signal,
)
from backend.app.algorithms.voting_ensemble.strategies.directional.snapshot_helpers import close_location, spy_candles
from backend.app.domain.models import RegimeState


GapOutcome = Literal["bullish_gap_continuation", "bearish_gap_continuation", "bullish_gap_fade", "bearish_gap_fade"]
GapSide = Literal["gap_up", "gap_down"]


class GapContinuationFadeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "gap_continuation_fade_v1"
    minConfirmationBar: int = Field(default=3, ge=1, le=60)
    maxConfirmationBar: int = Field(default=45, ge=1, le=180)
    openingRangeBars: int = Field(default=3, ge=1, le=30)
    minGapPercent: float = Field(default=0.0010, ge=0.0, le=0.20)
    minGapAtr: float = Field(default=0.20, ge=0.0, le=10.0)
    maxGapAtr: float = Field(default=3.00, ge=0.1, le=20.0)
    minContinuationBeyondOpeningRangeAtr: float = Field(default=0.05, ge=0.0, le=5.0)
    minFadeIntoGapFraction: float = Field(default=0.35, ge=0.0, le=1.0)
    maxPartialFillFractionForContinuation: float = Field(default=0.20, ge=0.0, le=1.0)
    minConfirmationBodyAtr: float = Field(default=0.05, ge=0.0, le=5.0)
    minOpeningRelativeVolume: float = Field(default=0.70, ge=0.0, le=10.0)
    maxSpreadBasisPoints: float = Field(default=12.0, ge=0.0)
    minDisplayedLiquidityShares: float = Field(default=500.0, ge=0.0)
    minRegimeFit: float = Field(default=0.15, ge=0.0, le=1.0)
    stalePriorCloseMaxDays: int = Field(default=4, ge=1, le=10)

    @model_validator(mode="after")
    def validate_window_and_gap_bounds(self) -> "GapContinuationFadeConfig":
        if self.maxConfirmationBar < self.minConfirmationBar:
            raise ValueError("maxConfirmationBar must be >= minConfirmationBar")
        if self.maxGapAtr < self.minGapAtr:
            raise ValueError("maxGapAtr must be >= minGapAtr")
        return self

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


class GapContinuationFadeStrategy:
    strategyId = "gap_continuation_fade"
    strategyName = "Gap Continuation / Fade"
    strategyVersion = "gap_continuation_fade_snapshot_v1"
    family = "gap_session"

    def __init__(self, config: GapContinuationFadeConfig | None = None) -> None:
        self.config = config or GapContinuationFadeConfig()

    def evaluate(
        self,
        snapshot: VotingEnsembleEvaluationSnapshot,
        *,
        correlation_id: str,
        regime_state: RegimeState | None = None,
    ) -> DirectionalStrategySignal:
        candles = spy_candles(snapshot)
        event_id = self._event_correlation_id(snapshot)
        if len(candles) <= self.config.openingRangeBars:
            return self._hold(snapshot, event_id, "Gap/session setup requires completed opening-range and confirmation candles.", "gap_continuation_fade.insufficient_data", data_ready=False)
        session_bar = len(candles) - 1
        if session_bar < self.config.minConfirmationBar or session_bar > self.config.maxConfirmationBar:
            return self._hold(snapshot, event_id, "Gap/session strategy is outside its configured session window.", "gap_continuation_fade.outside_session_window", features={"sessionBar": session_bar})
        if snapshot.nbbo is None:
            return self._hold(snapshot, event_id, "NBBO spread and displayed liquidity are mandatory.", "gap_continuation_fade.missing_nbbo", data_ready=False)
        prior_close = snapshot.priorDayLevels.close
        if prior_close is None or prior_close <= 0:
            return self._hold(snapshot, event_id, "Prior close is mandatory for opening-gap classification.", "gap_continuation_fade.missing_prior_close", data_ready=False)
        if self._prior_close_is_stale(snapshot):
            return self._hold(snapshot, event_id, "Prior-close timestamp is stale or future-dated.", "gap_continuation_fade.stale_prior_close", data_ready=False)
        atr = snapshot.features.atr
        if atr is None or atr <= 0:
            return self._hold(snapshot, event_id, "ATR is mandatory for normalised gap classification.", "gap_continuation_fade.missing_atr", data_ready=False)
        vwap = snapshot.features.vwap
        if vwap is None or vwap <= 0:
            return self._hold(snapshot, event_id, "Session VWAP is mandatory for gap/session confirmation.", "gap_continuation_fade.missing_vwap", data_ready=False)
        if self._event_day_blocks(snapshot, regime_state):
            return self._hold(snapshot, event_id, "Event context blocks shadow gap/session eligibility.", "gap_continuation_fade.event_day")
        if self._regime_fit(regime_state) < self.config.minRegimeFit:
            return self._hold(snapshot, event_id, "Gap/session regime fit is too weak.", "gap_continuation_fade.regime_fit_too_low")
        if snapshot.nbbo.spreadBasisPoints > self.config.maxSpreadBasisPoints:
            return self._hold(snapshot, event_id, "Spread exceeds gap/session permission.", "gap_continuation_fade.spread_too_wide")
        displayed_liquidity = snapshot.nbbo.bidSize + snapshot.nbbo.askSize
        if displayed_liquidity < self.config.minDisplayedLiquidityShares:
            return self._hold(snapshot, event_id, "Displayed liquidity is insufficient.", "gap_continuation_fade.insufficient_liquidity")

        first = candles[0]
        latest = candles[-1]
        previous = candles[-2]
        gap = first.open - prior_close
        gap_percent = gap / prior_close
        gap_atr = abs(gap) / atr
        if abs(gap_percent) < self.config.minGapPercent or gap_atr < self.config.minGapAtr:
            return self._hold(snapshot, event_id, "Opening gap is too small for gap/session classification.", "gap_continuation_fade.small_or_no_gap", features=self._base_features(snapshot, gap_percent, gap_atr))
        if gap_atr > self.config.maxGapAtr:
            return self._hold(snapshot, event_id, "Opening gap is too large for configured one-minute gap/session automation.", "gap_continuation_fade.large_gap", features=self._base_features(snapshot, gap_percent, gap_atr))

        gap_side: GapSide = "gap_up" if gap > 0 else "gap_down"
        opening_range = candles[: self.config.openingRangeBars]
        opening_high = max(candle.high for candle in opening_range)
        opening_low = min(candle.low for candle in opening_range)
        fill_fraction = self._fill_fraction(gap_side, latest.close, first.open, prior_close)
        relative_volume = self._relative_opening_volume(candles)
        if relative_volume < self.config.minOpeningRelativeVolume:
            return self._hold(snapshot, event_id, "Opening volume behaviour is below the continuation/fade evidence floor.", "gap_continuation_fade.opening_volume_weak", features={"relativeVolume": round(relative_volume, 4)})

        continuation = self._continuation(
            gap_side=gap_side,
            latest=latest,
            previous=previous,
            opening_high=opening_high,
            opening_low=opening_low,
            fill_fraction=fill_fraction,
            vwap=vwap,
            atr=atr,
        )
        fade = self._fade(
            snapshot=snapshot,
            gap_side=gap_side,
            latest=latest,
            previous=previous,
            opening_high=opening_high,
            opening_low=opening_low,
            fill_fraction=fill_fraction,
            vwap=vwap,
            atr=atr,
        )
        if continuation and fade:
            return self._hold(snapshot, event_id, "Gap continuation and fade conditions are simultaneously present, so the setup fails closed.", "gap_continuation_fade.mutual_exclusion")
        if not continuation and not fade:
            return self._hold(
                snapshot,
                event_id,
                "Gap is classified, but explicit invalidation and confirmation rules have not selected continuation or fade.",
                "gap_continuation_fade.no_confirmed_setup",
                features={**self._base_features(snapshot, gap_percent, gap_atr), "fillFraction": round(fill_fraction, 4)},
            )

        if continuation and gap_side == "gap_up":
            return self._signal(snapshot, event_id, "Buy", "bullish_gap_continuation", gap_percent, gap_atr, fill_fraction, relative_volume, displayed_liquidity)
        if continuation and gap_side == "gap_down":
            return self._signal(snapshot, event_id, "Sell", "bearish_gap_continuation", gap_percent, gap_atr, fill_fraction, relative_volume, displayed_liquidity)
        if fade and gap_side == "gap_down":
            return self._signal(snapshot, event_id, "Buy", "bullish_gap_fade", gap_percent, gap_atr, fill_fraction, relative_volume, displayed_liquidity)
        return self._signal(snapshot, event_id, "Sell", "bearish_gap_fade", gap_percent, gap_atr, fill_fraction, relative_volume, displayed_liquidity)

    def _signal(
        self,
        snapshot: VotingEnsembleEvaluationSnapshot,
        event_id: str,
        side: Literal["Buy", "Sell"],
        outcome: GapOutcome,
        gap_percent: float,
        gap_atr: float,
        fill_fraction: float,
        relative_volume: float,
        displayed_liquidity: float,
    ) -> DirectionalStrategySignal:
        confidence = min(0.82, 0.48 + min(0.16, gap_atr * 0.08) + min(0.10, max(0.0, relative_volume - 1.0) * 0.08) + min(0.08, abs(fill_fraction - 0.5) * 0.16))
        return directional_signal(
            strategy_id=self.strategyId,
            strategy_name=self.strategyName,
            strategy_version=self.strategyVersion,
            family=self.family,
            signal=side,
            confidence=round(confidence, 4),
            evaluated_at=snapshot.evaluationTimestamp,
            correlation_id=event_id,
            evidence=(f"{outcome.replace('_', ' ')} confirmed with gap {gap_percent:.2%}, {gap_atr:.2f} ATR, fill fraction {fill_fraction:.2f}.",),
            reason_codes=(f"gap_continuation_fade.{outcome}",),
            features={
                **self._base_features(snapshot, gap_percent, gap_atr),
                "setupOutcome": outcome,
                "fillFraction": round(fill_fraction, 4),
                "relativeVolume": round(relative_volume, 4),
                "displayedLiquidityShares": round(displayed_liquidity, 4),
                "eventCorrelationId": event_id,
                "gapSessionEventCorrelationId": event_id,
                "shadowOnly": True,
            },
        )

    def _hold(
        self,
        snapshot: VotingEnsembleEvaluationSnapshot,
        event_id: str,
        reason: str,
        code: str,
        *,
        data_ready: bool = True,
        features: dict[str, Any] | None = None,
    ) -> DirectionalStrategySignal:
        return hold_signal(
            strategy_id=self.strategyId,
            strategy_name=self.strategyName,
            strategy_version=self.strategyVersion,
            family=self.family,
            evaluated_at=snapshot.evaluationTimestamp,
            correlation_id=event_id,
            reason=reason,
            reason_code=code,
            data_ready=data_ready,
            features={"shadowOnly": True, "eventCorrelationId": event_id, **(features or {})},
        )

    def _continuation(
        self,
        *,
        gap_side: GapSide,
        latest: VotingCandle,
        previous: VotingCandle,
        opening_high: float,
        opening_low: float,
        fill_fraction: float,
        vwap: float,
        atr: float,
    ) -> bool:
        if fill_fraction > self.config.maxPartialFillFractionForContinuation:
            return False
        body_atr = abs(latest.close - latest.open) / atr
        if body_atr < self.config.minConfirmationBodyAtr:
            return False
        if gap_side == "gap_up":
            breakout = latest.close > opening_high + (atr * self.config.minContinuationBeyondOpeningRangeAtr)
            return breakout and latest.close > previous.high and latest.close > vwap and latest.close > latest.open and close_location(latest) >= 0.55
        breakout = latest.close < opening_low - (atr * self.config.minContinuationBeyondOpeningRangeAtr)
        return breakout and latest.close < previous.low and latest.close < vwap and latest.close < latest.open and close_location(latest) <= 0.45

    def _fade(
        self,
        *,
        snapshot: VotingEnsembleEvaluationSnapshot,
        gap_side: GapSide,
        latest: VotingCandle,
        previous: VotingCandle,
        opening_high: float,
        opening_low: float,
        fill_fraction: float,
        vwap: float,
        atr: float,
    ) -> bool:
        body_atr = abs(latest.close - latest.open) / atr
        if fill_fraction < self.config.minFadeIntoGapFraction or body_atr < self.config.minConfirmationBodyAtr:
            return False
        if gap_side == "gap_up":
            premarket_rejection = bool(snapshot.premarketLevels.high and latest.close < snapshot.premarketLevels.high and previous.high >= snapshot.premarketLevels.high)
            return latest.close < opening_low and latest.close < previous.low and latest.close < vwap and latest.close < latest.open and (premarket_rejection or fill_fraction >= self.config.minFadeIntoGapFraction)
        premarket_rejection = bool(snapshot.premarketLevels.low and latest.close > snapshot.premarketLevels.low and previous.low <= snapshot.premarketLevels.low)
        return latest.close > opening_high and latest.close > previous.high and latest.close > vwap and latest.close > latest.open and (premarket_rejection or fill_fraction >= self.config.minFadeIntoGapFraction)

    def _fill_fraction(self, gap_side: GapSide, latest_close: float, session_open: float, prior_close: float) -> float:
        gap_size = abs(session_open - prior_close)
        if gap_size <= 0:
            return 0.0
        if gap_side == "gap_up":
            return max(0.0, min(1.0, (session_open - latest_close) / gap_size))
        return max(0.0, min(1.0, (latest_close - session_open) / gap_size))

    def _relative_opening_volume(self, candles: tuple[VotingCandle, ...]) -> float:
        if len(candles) < 2:
            return 1.0
        baseline = mean(candle.volume for candle in candles[:-1])
        return candles[-1].volume / max(baseline, 1.0)

    def _event_day_blocks(self, snapshot: VotingEnsembleEvaluationSnapshot, regime_state: RegimeState | None) -> bool:
        event = snapshot.economicEventState.state
        if bool(event.get("eventBlackoutActive", False)):
            return True
        importance = str(event.get("importance") or event.get("eventImportance") or "").lower()
        state = str(event.get("state") or event.get("eventState") or "").lower()
        if importance in {"high", "critical"} and state in {"active", "imminent", "shock"}:
            return True
        regime_event = str((regime_state.features if regime_state else {}).get("eventRiskState") or "").lower()
        return regime_event in {"event_risk_active", "event_risk_imminent", "event_shock"}

    def _regime_fit(self, regime_state: RegimeState | None) -> float:
        if regime_state is None:
            return 1.0
        value = regime_state.features.get("gapSessionFit")
        if not isinstance(value, int | float):
            return 1.0
        return max(0.0, min(1.0, float(value)))

    def _prior_close_is_stale(self, snapshot: VotingEnsembleEvaluationSnapshot) -> bool:
        raw = (
            snapshot.sessionState.get("priorCloseTimestamp")
            or snapshot.sessionState.get("priorDayCloseTimestamp")
            or snapshot.operationalHealthSnapshot.get("priorCloseTimestamp")
        )
        if raw is None:
            return False
        timestamp = _timestamp(raw)
        if timestamp is None:
            return True
        evaluated = _utc(snapshot.evaluationTimestamp)
        return timestamp > evaluated or (evaluated - timestamp).days > self.config.stalePriorCloseMaxDays

    def _base_features(self, snapshot: VotingEnsembleEvaluationSnapshot, gap_percent: float, gap_atr: float) -> dict[str, float | str | bool]:
        first = spy_candles(snapshot)[0] if spy_candles(snapshot) else None
        premarket_direction = "unknown"
        if snapshot.premarketLevels.open and snapshot.premarketLevels.close:
            premarket_direction = "up" if snapshot.premarketLevels.close > snapshot.premarketLevels.open else "down" if snapshot.premarketLevels.close < snapshot.premarketLevels.open else "flat"
        return {
            "priorClose": round(float(snapshot.priorDayLevels.close or 0.0), 4),
            "sessionOpen": round(float(first.open if first else 0.0), 4),
            "premarketHigh": round(float(snapshot.premarketLevels.high or 0.0), 4),
            "premarketLow": round(float(snapshot.premarketLevels.low or 0.0), 4),
            "gapPercent": round(gap_percent, 6),
            "gapAtr": round(gap_atr, 4),
            "premarketDirection": premarket_direction,
        }

    def _event_correlation_id(self, snapshot: VotingEnsembleEvaluationSnapshot) -> str:
        candles = spy_candles(snapshot)
        first = candles[0] if candles else None
        payload = {
            "eventType": "gap_continuation_fade",
            "symbol": snapshot.symbol,
            "sessionOpen": first.open if first else None,
            "priorClose": snapshot.priorDayLevels.close,
            "sessionDate": snapshot.evaluationTimestamp.date().isoformat(),
            "settingsHash": snapshot.settingsHash,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        return f"gap-session-event-{digest}"


def _timestamp(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return _utc(raw)
    if isinstance(raw, str) and raw:
        try:
            return _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


__all__ = ["GapContinuationFadeConfig", "GapContinuationFadeStrategy"]

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot
from backend.app.algorithms.voting_ensemble.strategies.directional.signal_contract import (
    DirectionalStrategySignal,
    directional_signal,
    hold_signal,
)
from backend.app.algorithms.voting_ensemble.strategies.directional.snapshot_helpers import spy_candles
from backend.app.domain.models import RegimeState


BreakoutSide = Literal["Buy", "Sell"]


class OpeningRangeBreakoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "opening_range_breakout_v1"
    openingRangeMinutes: int = Field(default=15, ge=3, le=60)
    maxBreakoutAgeMinutes: int = Field(default=150, ge=1, le=390)
    minBreakoutAtr: float = Field(default=0.08, ge=0.0, le=5.0)
    minRelativeVolume: float = Field(default=1.05, ge=0.0)
    minRangeAtr: float = Field(default=0.20, ge=0.0)
    maxRangeAtr: float = Field(default=4.0, ge=0.0)
    maxSpreadBasisPoints: float = Field(default=12.0, ge=0.0)
    minDisplayedLiquidityShares: float = Field(default=500.0, ge=0.0)
    requireRetestHold: bool = False
    minRegimeFit: float = Field(default=0.15, ge=0.0, le=1.0)
    blockHighImpactEventRisk: bool = True
    allowedSessionPhases: tuple[str, ...] = ("regular", "regular_session", "market_open")
    blockedMarketStructureStates: tuple[str, ...] = ("halt", "auction", "illiquid", "dislocated", "event_shock")

    @model_validator(mode="after")
    def max_range_must_exceed_min_range(self) -> "OpeningRangeBreakoutConfig":
        if self.maxRangeAtr < self.minRangeAtr:
            raise ValueError("maxRangeAtr must be >= minRangeAtr")
        return self

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


class OpeningRangeBreakoutStrategy:
    strategyId = "opening_range_breakout"
    strategyName = "Opening Range Breakout"
    strategyVersion = "opening_range_breakout_snapshot_v1"
    family = "breakout"

    def __init__(self, config: OpeningRangeBreakoutConfig | None = None) -> None:
        self.config = config or OpeningRangeBreakoutConfig()

    def evaluate(
        self,
        snapshot: VotingEnsembleEvaluationSnapshot,
        *,
        correlation_id: str,
        regime_state: RegimeState | None = None,
    ) -> DirectionalStrategySignal:
        candles = spy_candles(snapshot)
        event_id = self._event_correlation_id(snapshot)
        if len(candles) <= self.config.openingRangeMinutes:
            return self._hold(snapshot, event_id, "Opening range is not complete.", "opening_range_breakout.range_incomplete", data_ready=False)
        if snapshot.nbbo is None:
            return self._hold(snapshot, event_id, "NBBO spread and displayed liquidity are mandatory.", "opening_range_breakout.missing_nbbo", data_ready=False)
        atr = snapshot.features.atr
        if atr is None or atr <= 0:
            return self._hold(snapshot, event_id, "ATR is mandatory for normalized breakout distance.", "opening_range_breakout.missing_atr", data_ready=False)
        if self._event_risk_blocks(snapshot, regime_state):
            return self._hold(snapshot, event_id, "Economic-event risk blocks shadow ORB eligibility.", "opening_range_breakout.event_blackout")
        if self._regime_fit(regime_state) < self.config.minRegimeFit:
            return self._hold(snapshot, event_id, "Regime fit does not support breakout-family promotion evidence.", "opening_range_breakout.regime_fit_too_low")
        if not self._time_of_day_allows(snapshot):
            return self._hold(snapshot, event_id, "Session phase is not valid for opening-range breakout evidence.", "opening_range_breakout.invalid_time_of_day")
        if not self._market_structure_allows(snapshot):
            return self._hold(snapshot, event_id, "Market structure does not permit opening-range breakout evidence.", "opening_range_breakout.market_structure_unsupported")

        opening = candles[: self.config.openingRangeMinutes]
        latest = candles[-1]
        range_high = max(candle.high for candle in opening)
        range_low = min(candle.low for candle in opening)
        range_width = range_high - range_low
        range_atr = range_width / atr
        if range_width <= 0:
            return self._hold(snapshot, event_id, "Opening range is malformed.", "opening_range_breakout.range_malformed", data_ready=False)
        if range_atr < self.config.minRangeAtr:
            return self._hold(snapshot, event_id, "Opening range is too narrow relative to ATR.", "opening_range_breakout.range_too_narrow", features={"rangeAtr": round(range_atr, 4)})
        if range_atr > self.config.maxRangeAtr:
            return self._hold(snapshot, event_id, "Opening range is too wide relative to ATR.", "opening_range_breakout.range_too_wide", features={"rangeAtr": round(range_atr, 4)})
        opening_end = opening[-1].timestamp
        if latest.timestamp <= opening_end:
            return self._hold(snapshot, event_id, "Latest candle is not after the completed opening range.", "opening_range_breakout.range_incomplete")
        if latest.timestamp > opening_end + timedelta(minutes=self.config.maxBreakoutAgeMinutes):
            return self._hold(snapshot, event_id, "Opening-range breakout opportunity is stale.", "opening_range_breakout.stale")
        if snapshot.nbbo.spreadBasisPoints > self.config.maxSpreadBasisPoints:
            return self._hold(snapshot, event_id, "Spread exceeds opening-range breakout permission.", "opening_range_breakout.spread_too_wide")
        displayed_liquidity = snapshot.nbbo.bidSize + snapshot.nbbo.askSize
        if displayed_liquidity < self.config.minDisplayedLiquidityShares:
            return self._hold(snapshot, event_id, "Displayed liquidity is insufficient for breakout permission.", "opening_range_breakout.insufficient_liquidity")

        baseline_volume = mean(candle.volume for candle in candles[: -1]) if len(candles) > 1 else latest.volume
        relative_volume = latest.volume / max(baseline_volume, 1.0)
        if relative_volume < self.config.minRelativeVolume:
            return self._hold(snapshot, event_id, "Breakout lacks volume or relative-volume confirmation.", "opening_range_breakout.volume_unconfirmed", features={"relativeVolume": round(relative_volume, 4)})

        long_distance = latest.close - range_high
        short_distance = range_low - latest.close
        long_breakout = long_distance > 0 and (long_distance / atr) >= self.config.minBreakoutAtr
        short_breakout = short_distance > 0 and (short_distance / atr) >= self.config.minBreakoutAtr
        if long_breakout and latest.low < range_high:
            return self._hold(snapshot, event_id, "Long breakout closed outside but immediately traded back inside the opening range.", "opening_range_breakout.close_back_inside")
        if short_breakout and latest.high > range_low:
            return self._hold(snapshot, event_id, "Short breakout closed outside but immediately traded back inside the opening range.", "opening_range_breakout.close_back_inside")
        if self.config.requireRetestHold and long_breakout and latest.low <= range_high:
            return self._hold(snapshot, event_id, "Long breakout has not held a retest above the opening range.", "opening_range_breakout.retest_hold_missing")
        if self.config.requireRetestHold and short_breakout and latest.high >= range_low:
            return self._hold(snapshot, event_id, "Short breakout has not held a retest below the opening range.", "opening_range_breakout.retest_hold_missing")

        if long_breakout:
            return self._signal(snapshot, event_id, "Buy", range_high, long_distance, atr, relative_volume, range_atr, displayed_liquidity)
        if short_breakout:
            return self._signal(snapshot, event_id, "Sell", range_low, short_distance, atr, relative_volume, range_atr, displayed_liquidity)
        return self._hold(
            snapshot,
            event_id,
            "Price has not broken beyond the correct opening-range boundary by the ATR-normalized threshold.",
            "opening_range_breakout.no_boundary_break",
            features={"rangeHigh": round(range_high, 4), "rangeLow": round(range_low, 4)},
        )

    def _signal(
        self,
        snapshot: VotingEnsembleEvaluationSnapshot,
        event_id: str,
        side: BreakoutSide,
        boundary: float,
        distance: float,
        atr: float,
        relative_volume: float,
        range_atr: float,
        displayed_liquidity: float,
    ) -> DirectionalStrategySignal:
        normalized_distance = distance / atr
        confidence = min(0.82, 0.50 + min(0.20, normalized_distance * 0.5) + min(0.12, (relative_volume - 1.0) * 0.12))
        code = "opening_range_breakout.buy_breakout" if side == "Buy" else "opening_range_breakout.sell_breakout"
        direction = "above" if side == "Buy" else "below"
        return directional_signal(
            strategy_id=self.strategyId,
            strategy_name=self.strategyName,
            strategy_version=self.strategyVersion,
            family=self.family,
            signal=side,
            confidence=round(confidence, 4),
            evaluated_at=snapshot.evaluationTimestamp,
            correlation_id=event_id,
            evidence=(f"{side} opening-range breakout closed {direction} {boundary:.2f} by {normalized_distance:.2f} ATR with relative volume {relative_volume:.2f}.",),
            reason_codes=(code,),
            features={
                "openingRangeBoundary": round(boundary, 4),
                "breakoutDistanceAtr": round(normalized_distance, 4),
                "relativeVolume": round(relative_volume, 4),
                "rangeAtr": round(range_atr, 4),
                "displayedLiquidityShares": round(displayed_liquidity, 4),
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
            features={"shadowOnly": True, **(features or {})},
        )

    def _event_correlation_id(self, snapshot: VotingEnsembleEvaluationSnapshot) -> str:
        candles = spy_candles(snapshot)
        opening = candles[: self.config.openingRangeMinutes]
        payload = {
            "strategyId": self.strategyId,
            "symbol": snapshot.symbol,
            "openingStart": opening[0].timestamp.isoformat() if opening else None,
            "openingEnd": opening[-1].timestamp.isoformat() if opening else None,
            "openingHigh": max((candle.high for candle in opening), default=None),
            "openingLow": min((candle.low for candle in opening), default=None),
            "settingsHash": snapshot.settingsHash,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        return f"opening-range-breakout-{digest}"

    def _event_risk_blocks(self, snapshot: VotingEnsembleEvaluationSnapshot, regime_state: RegimeState | None) -> bool:
        if not self.config.blockHighImpactEventRisk:
            return False
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
        value = regime_state.features.get("breakoutFit")
        if not isinstance(value, int | float):
            return 1.0
        return max(0.0, min(1.0, float(value)))

    def _time_of_day_allows(self, snapshot: VotingEnsembleEvaluationSnapshot) -> bool:
        phase = str(snapshot.sessionState.get("phase") or snapshot.sessionState.get("sessionPhase") or "").lower()
        return not phase or phase in set(self.config.allowedSessionPhases)

    def _market_structure_allows(self, snapshot: VotingEnsembleEvaluationSnapshot) -> bool:
        candidates = (
            snapshot.sessionState.get("marketStructure"),
            snapshot.sessionState.get("marketStructureState"),
            snapshot.operationalHealthSnapshot.get("marketStructure"),
            snapshot.operationalHealthSnapshot.get("marketStructureState"),
        )
        blocked = set(self.config.blockedMarketStructureStates)
        return all(str(candidate).lower() not in blocked for candidate in candidates if candidate is not None)


__all__ = ["OpeningRangeBreakoutConfig", "OpeningRangeBreakoutStrategy"]

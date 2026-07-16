"""WCA-owned market-status resolver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from backend.app.algorithms.wca.contracts import (
    WcaAlgorithmRiskStatus,
    WcaDataQualityStatus,
    WcaEvaluationStatus,
    WcaEventRiskStatus,
    WcaLiquidityStatus,
    WcaMarketSnapshot,
    WcaMarketStatus,
    WcaSessionStatus,
    WcaTrendStatus,
    WcaVolatilityStatus,
)
from backend.app.algorithms.wca.strategies.indicators import atr, average_volume, completed_candles, eastern_minutes, sma


@dataclass(frozen=True)
class WcaMarketStatusConfig:
    stale_after_seconds: int = 120
    minimum_completed_candles: int = 20
    improvement_confirmation_candles: int = 3
    minimum_profile_hold_seconds: int = 300
    profile_ttl_seconds: int = 900
    weak_trend_threshold: float = 0.001
    strong_trend_threshold: float = 0.004
    very_low_volatility_atr_pct: float = 0.001
    low_volatility_atr_pct: float = 0.0025
    high_volatility_atr_pct: float = 0.006
    extreme_volatility_atr_pct: float = 0.012
    deep_volume_threshold: float = 250000
    thin_volume_threshold: float = 50000
    unsafe_volume_threshold: float = 10000
    deep_spread_pct: float = 0.0002
    thin_spread_pct: float = 0.0008
    unsafe_spread_pct: float = 0.002


_RISK_RANK = {
    WcaAlgorithmRiskStatus.NORMAL.value: 0,
    WcaAlgorithmRiskStatus.REDUCED.value: 1,
    WcaAlgorithmRiskStatus.DEFENSIVE.value: 2,
    WcaAlgorithmRiskStatus.DAILY_STOP.value: 3,
}


def unavailable_market_status(snapshot: WcaMarketSnapshot, reason: str) -> WcaMarketStatus:
    return WcaMarketStatus(
        status=WcaEvaluationStatus.INVALID,
        event_risk=WcaEventRiskStatus.BLOCKED,
        data_quality=WcaDataQualityStatus.INVALID,
        algorithm_risk=WcaAlgorithmRiskStatus.DAILY_STOP,
        classification_confidence=0,
        input_timestamp=snapshot.data_timestamp,
        profile_expiration=snapshot.decision_timestamp,
        data_quality_flags=(reason,),
        reason_codes=(reason,),
        explanation="WCA market status is unavailable until the engine is implemented.",
    )


def resolve_market_status(
    snapshot: WcaMarketSnapshot,
    *,
    previous_status: WcaMarketStatus | None = None,
    confirmation_count: int = 0,
    config: WcaMarketStatusConfig = WcaMarketStatusConfig(),
) -> WcaMarketStatus:
    candles = completed_candles(snapshot)
    flags = _data_quality_flags(snapshot, candles, config)
    if flags:
        return WcaMarketStatus(
            status=WcaEvaluationStatus.INVALID,
            trend=WcaTrendStatus.RANGE,
            volatility=WcaVolatilityStatus.EXTREME if "wca.market.extreme_risk" in flags else WcaVolatilityStatus.NORMAL,
            liquidity=WcaLiquidityStatus.UNSAFE,
            session=_classify_session(snapshot),
            event_risk=WcaEventRiskStatus.BLOCKED,
            data_quality=WcaDataQualityStatus.INVALID,
            algorithm_risk=WcaAlgorithmRiskStatus.DAILY_STOP,
            classification_confidence=0,
            input_timestamp=snapshot.data_timestamp,
            data_quality_flags=flags,
            profile_expiration=snapshot.decision_timestamp,
            reason_codes=flags,
            explanation="Invalid, stale, halted, or blocked WCA market data prevents a favorable status.",
        )

    trend = _classify_trend(candles, config)
    volatility = _classify_volatility(candles, config)
    liquidity = _classify_liquidity(snapshot, candles, config)
    session = _classify_session(snapshot)
    event_risk = _classify_event_risk(snapshot)
    data_quality = _classify_data_quality(snapshot, candles, config)
    risk = _classify_algorithm_risk(volatility, liquidity, event_risk, data_quality)
    risk, hysteresis_reasons = _apply_hysteresis(
        raw_risk=risk,
        snapshot=snapshot,
        previous_status=previous_status,
        confirmation_count=confirmation_count,
        config=config,
    )
    status = WcaEvaluationStatus.ACTIVE if data_quality == WcaDataQualityStatus.HEALTHY else WcaEvaluationStatus.DEGRADED
    if risk == WcaAlgorithmRiskStatus.DAILY_STOP:
        status = WcaEvaluationStatus.INVALID if data_quality == WcaDataQualityStatus.INVALID else WcaEvaluationStatus.DEGRADED
    confidence = _classification_confidence(snapshot, candles, data_quality, liquidity, volatility, config)
    reasons = (
        "wca.market_status.resolved",
        f"wca.market.trend.{trend.value}",
        f"wca.market.volatility.{volatility.value}",
        f"wca.market.liquidity.{liquidity.value}",
        f"wca.market.risk.{risk.value}",
        *hysteresis_reasons,
    )
    return WcaMarketStatus(
        status=status,
        trend=trend,
        volatility=volatility,
        liquidity=liquidity,
        session=session,
        event_risk=event_risk,
        data_quality=data_quality,
        algorithm_risk=risk,
        classification_confidence=confidence,
        input_timestamp=snapshot.data_timestamp,
        data_quality_flags=(),
        profile_expiration=snapshot.decision_timestamp + timedelta(seconds=config.profile_ttl_seconds),
        reason_codes=reasons,
        explanation="WCA market status resolved from WCA-owned snapshot inputs.",
    )


def _data_quality_flags(snapshot: WcaMarketSnapshot, candles: tuple, config: WcaMarketStatusConfig) -> tuple[str, ...]:
    flags: list[str] = []
    if not snapshot.data_ready:
        flags.append("wca.market.data_not_ready")
    if not candles:
        flags.append("wca.market.missing_candles")
    if snapshot.decision_timestamp < snapshot.data_timestamp:
        flags.append("wca.market.timestamp_inverted")
    if (snapshot.decision_timestamp - snapshot.data_timestamp).total_seconds() > config.stale_after_seconds:
        flags.append("wca.market.stale_snapshot")
    if len(candles) < 2:
        flags.append("wca.market.insufficient_candles")
    if any(c.close <= 0 or c.high < c.low or c.volume < 0 for c in candles):
        flags.append("wca.market.invalid_candle")
    reason_text = " ".join(snapshot.reason_codes).lower()
    if "halt" in reason_text or "luld" in reason_text or "blocked" in reason_text or "daily_stop" in reason_text:
        flags.append("wca.market.extreme_risk")
    return tuple(flags)


def _classify_trend(candles: tuple, config: WcaMarketStatusConfig) -> WcaTrendStatus:
    if len(candles) < config.minimum_completed_candles:
        return WcaTrendStatus.RANGE
    close = candles[-1].close
    spread = (sma(candles, 10) - sma(candles, 20)) / close
    if spread >= config.strong_trend_threshold:
        return WcaTrendStatus.STRONG_UPTREND
    if spread >= config.weak_trend_threshold:
        return WcaTrendStatus.WEAK_UPTREND
    if spread <= -config.strong_trend_threshold:
        return WcaTrendStatus.STRONG_DOWNTREND
    if spread <= -config.weak_trend_threshold:
        return WcaTrendStatus.WEAK_DOWNTREND
    return WcaTrendStatus.RANGE


def _classify_volatility(candles: tuple, config: WcaMarketStatusConfig) -> WcaVolatilityStatus:
    if len(candles) < 15:
        return WcaVolatilityStatus.HIGH
    atr_pct = atr(candles, 14) / max(candles[-1].close, 0.01)
    if atr_pct < config.very_low_volatility_atr_pct:
        return WcaVolatilityStatus.VERY_LOW
    if atr_pct < config.low_volatility_atr_pct:
        return WcaVolatilityStatus.LOW
    if atr_pct < config.high_volatility_atr_pct:
        return WcaVolatilityStatus.NORMAL
    if atr_pct < config.extreme_volatility_atr_pct:
        return WcaVolatilityStatus.HIGH
    return WcaVolatilityStatus.EXTREME


def _classify_liquidity(snapshot: WcaMarketSnapshot, candles: tuple, config: WcaMarketStatusConfig) -> WcaLiquidityStatus:
    avg_volume = average_volume(candles, min(20, len(candles)))
    spread_pct = 0.0
    if snapshot.quote is not None:
        midpoint = max((snapshot.quote.bid + snapshot.quote.ask) / 2, 0.01)
        spread_pct = (snapshot.quote.ask - snapshot.quote.bid) / midpoint
    if avg_volume < config.unsafe_volume_threshold or spread_pct >= config.unsafe_spread_pct:
        return WcaLiquidityStatus.UNSAFE
    if avg_volume < config.thin_volume_threshold or spread_pct >= config.thin_spread_pct:
        return WcaLiquidityStatus.THIN
    if avg_volume >= config.deep_volume_threshold and spread_pct <= config.deep_spread_pct:
        return WcaLiquidityStatus.DEEP
    return WcaLiquidityStatus.NORMAL


def _classify_session(snapshot: WcaMarketSnapshot) -> WcaSessionStatus:
    minutes = eastern_minutes(snapshot.data_timestamp)
    if minutes < 10 * 60:
        return WcaSessionStatus.OPENING
    if minutes < 11 * 60 + 30:
        return WcaSessionStatus.MORNING
    if minutes < 13 * 60 + 30:
        return WcaSessionStatus.MIDDAY
    if minutes < 15 * 60 + 30:
        return WcaSessionStatus.AFTERNOON
    return WcaSessionStatus.CLOSING


def _classify_event_risk(snapshot: WcaMarketSnapshot) -> WcaEventRiskStatus:
    reason_text = " ".join(snapshot.reason_codes).lower()
    if "event_block" in reason_text or "blocked_event" in reason_text:
        return WcaEventRiskStatus.BLOCKED
    if "event_elevated" in reason_text or "news_elevated" in reason_text:
        return WcaEventRiskStatus.ELEVATED
    return WcaEventRiskStatus.NORMAL


def _classify_data_quality(snapshot: WcaMarketSnapshot, candles: tuple, config: WcaMarketStatusConfig) -> WcaDataQualityStatus:
    age_seconds = (snapshot.decision_timestamp - snapshot.data_timestamp).total_seconds()
    if len(candles) < config.minimum_completed_candles or age_seconds > config.stale_after_seconds / 2:
        return WcaDataQualityStatus.DEGRADED
    return WcaDataQualityStatus.HEALTHY


def _classify_algorithm_risk(
    volatility: WcaVolatilityStatus,
    liquidity: WcaLiquidityStatus,
    event_risk: WcaEventRiskStatus,
    data_quality: WcaDataQualityStatus,
) -> WcaAlgorithmRiskStatus:
    if data_quality == WcaDataQualityStatus.INVALID or volatility == WcaVolatilityStatus.EXTREME or liquidity == WcaLiquidityStatus.UNSAFE or event_risk == WcaEventRiskStatus.BLOCKED:
        return WcaAlgorithmRiskStatus.DAILY_STOP
    if volatility == WcaVolatilityStatus.HIGH or liquidity == WcaLiquidityStatus.THIN or event_risk == WcaEventRiskStatus.ELEVATED:
        return WcaAlgorithmRiskStatus.DEFENSIVE
    if data_quality == WcaDataQualityStatus.DEGRADED or volatility in {WcaVolatilityStatus.VERY_LOW, WcaVolatilityStatus.LOW}:
        return WcaAlgorithmRiskStatus.REDUCED
    return WcaAlgorithmRiskStatus.NORMAL


def _apply_hysteresis(
    *,
    raw_risk: WcaAlgorithmRiskStatus,
    snapshot: WcaMarketSnapshot,
    previous_status: WcaMarketStatus | None,
    confirmation_count: int,
    config: WcaMarketStatusConfig,
) -> tuple[WcaAlgorithmRiskStatus, tuple[str, ...]]:
    if previous_status is None:
        return raw_risk, ()
    previous_risk = (
        previous_status.algorithm_risk.value
        if isinstance(previous_status.algorithm_risk, WcaAlgorithmRiskStatus)
        else str(previous_status.algorithm_risk)
    )
    raw_rank = _RISK_RANK[raw_risk.value]
    previous_rank = _RISK_RANK.get(previous_risk, 0)
    if raw_rank > previous_rank:
        return raw_risk, ("wca.market.hysteresis.defensive_immediate",)
    if raw_rank == previous_rank:
        return raw_risk, ()
    elapsed_seconds = (
        (snapshot.decision_timestamp - (previous_status.input_timestamp or snapshot.decision_timestamp)).total_seconds()
        if previous_status.input_timestamp is not None
        else config.minimum_profile_hold_seconds
    )
    held_seconds = max(elapsed_seconds, confirmation_count * 60)
    if confirmation_count >= config.improvement_confirmation_candles and held_seconds >= config.minimum_profile_hold_seconds:
        return raw_risk, ("wca.market.hysteresis.improvement_confirmed",)
    return WcaAlgorithmRiskStatus(previous_risk), ("wca.market.hysteresis.improvement_held",)


def _classification_confidence(
    snapshot: WcaMarketSnapshot,
    candles: tuple,
    data_quality: WcaDataQualityStatus,
    liquidity: WcaLiquidityStatus,
    volatility: WcaVolatilityStatus,
    config: WcaMarketStatusConfig,
) -> float:
    confidence = 0.95
    if data_quality == WcaDataQualityStatus.DEGRADED:
        confidence -= 0.20
    if len(candles) < config.minimum_completed_candles * 2:
        confidence -= 0.10
    if snapshot.quote is None:
        confidence -= 0.08
    if liquidity in {WcaLiquidityStatus.THIN, WcaLiquidityStatus.UNSAFE}:
        confidence -= 0.15
    if volatility == WcaVolatilityStatus.EXTREME:
        confidence -= 0.15
    return round(max(0, min(1, confidence)), 4)

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.feature_engine import FeatureQuality
from backend.app.domain.models import ContextSignal, Direction, Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


class EconomicEventPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    eventFamily: str = Field(min_length=1)
    eventType: str = Field(min_length=1)
    preEventCautionWindowMinutes: int = Field(ge=0)
    preEventBlackoutWindowMinutes: int = Field(ge=0)
    releaseFreezeDurationMinutes: int = Field(ge=0)
    postEventStabilizationWindowMinutes: int = Field(ge=0)
    minimumLiquidityShares: float | None = Field(default=None, ge=0)
    maximumSpreadBps: float | None = Field(default=None, ge=0)
    minimumNetEdgeBps: float = Field(ge=0)
    maximumPositionRiskMultiplier: float = Field(ge=0, le=1)


class EconomicEventContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "economic_event_context_v1"
    eventWindowMinutes: int = Field(default=30, ge=1, le=240)
    highImportanceRiskCap: float = Field(default=0.35, ge=0, le=1)
    mediumImportanceRiskCap: float = Field(default=0.65, ge=0, le=1)
    lowImportanceRiskCap: float = Field(default=1.0, ge=0, le=1)
    volatilityShockThreshold: float = Field(default=1.8, gt=0)
    spreadShockBasisPoints: float = Field(default=8.0, ge=0)
    shockRiskCap: float = Field(default=0.5, ge=0, le=1)
    shockSizeMultiplier: float = Field(default=0.5, ge=0, le=1)
    shockMinimumEdgeMultiplier: float = Field(default=1.5, ge=1)
    shockCooldownMinutes: int = Field(default=5, ge=0, le=120)
    feedUnavailableRiskCap: float = Field(default=0.0, ge=0, le=1)
    degradedFeedRiskCap: float = Field(default=0.5, ge=0, le=1)
    requiredSafetyMarginBps: float = Field(default=2.0, ge=0)
    minimumEdgeToCostRatio: float = Field(default=1.25, ge=0)
    eventPolicies: dict[str, EconomicEventPolicy] = Field(default_factory=lambda: DEFAULT_EVENT_POLICIES)
    eventTypeAliases: dict[str, str] = Field(default_factory=lambda: DEFAULT_EVENT_TYPE_ALIASES)
    maxConfidenceAdjustment: float = Field(default=0.12, ge=0, le=0.25)

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


def _policy(
    event_family: str,
    event_type: str,
    *,
    caution: int,
    blackout: int,
    freeze: int,
    stabilization: int,
    min_liquidity: float | None,
    max_spread: float | None,
    min_net_edge: float,
    max_risk: float,
) -> EconomicEventPolicy:
    return EconomicEventPolicy(
        eventFamily=event_family,
        eventType=event_type,
        preEventCautionWindowMinutes=caution,
        preEventBlackoutWindowMinutes=blackout,
        releaseFreezeDurationMinutes=freeze,
        postEventStabilizationWindowMinutes=stabilization,
        minimumLiquidityShares=min_liquidity,
        maximumSpreadBps=max_spread,
        minimumNetEdgeBps=min_net_edge,
        maximumPositionRiskMultiplier=max_risk,
    )


DEFAULT_EVENT_POLICIES: dict[str, EconomicEventPolicy] = {
    "fomc_statement": _policy("Federal Reserve", "FOMC statement", caution=60, blackout=15, freeze=3, stabilization=30, min_liquidity=75_000, max_spread=4.0, min_net_edge=6.0, max_risk=0.0),
    "rate_decision": _policy("Federal Reserve", "Rate decision", caution=60, blackout=15, freeze=3, stabilization=30, min_liquidity=75_000, max_spread=4.0, min_net_edge=6.0, max_risk=0.0),
    "press_conference": _policy("Federal Reserve", "Press conference", caution=45, blackout=10, freeze=2, stabilization=30, min_liquidity=75_000, max_spread=4.0, min_net_edge=6.0, max_risk=0.0),
    "minutes": _policy("Federal Reserve", "Minutes", caution=30, blackout=10, freeze=2, stabilization=20, min_liquidity=60_000, max_spread=5.0, min_net_edge=5.0, max_risk=0.35),
    "chair_speech": _policy("Federal Reserve", "Chair speech", caution=30, blackout=10, freeze=2, stabilization=20, min_liquidity=60_000, max_spread=5.0, min_net_edge=5.0, max_risk=0.35),
    "governor_speech": _policy("Federal Reserve", "Governor speech", caution=20, blackout=5, freeze=1, stabilization=15, min_liquidity=50_000, max_spread=6.0, min_net_edge=4.0, max_risk=0.5),
    "cpi": _policy("Inflation", "CPI", caution=45, blackout=15, freeze=3, stabilization=25, min_liquidity=75_000, max_spread=4.0, min_net_edge=6.0, max_risk=0.0),
    "core_cpi": _policy("Inflation", "Core CPI", caution=45, blackout=15, freeze=3, stabilization=25, min_liquidity=75_000, max_spread=4.0, min_net_edge=6.0, max_risk=0.0),
    "pce": _policy("Inflation", "PCE", caution=30, blackout=10, freeze=2, stabilization=20, min_liquidity=60_000, max_spread=5.0, min_net_edge=5.0, max_risk=0.25),
    "ppi": _policy("Inflation", "PPI", caution=30, blackout=10, freeze=2, stabilization=20, min_liquidity=60_000, max_spread=5.0, min_net_edge=5.0, max_risk=0.35),
    "nonfarm_payrolls": _policy("Labor", "Nonfarm payrolls", caution=45, blackout=15, freeze=3, stabilization=25, min_liquidity=75_000, max_spread=4.0, min_net_edge=6.0, max_risk=0.0),
    "unemployment_rate": _policy("Labor", "Unemployment rate", caution=30, blackout=10, freeze=2, stabilization=20, min_liquidity=60_000, max_spread=5.0, min_net_edge=5.0, max_risk=0.35),
    "average_hourly_earnings": _policy("Labor", "Average hourly earnings", caution=30, blackout=10, freeze=2, stabilization=20, min_liquidity=60_000, max_spread=5.0, min_net_edge=5.0, max_risk=0.35),
    "initial_jobless_claims": _policy("Labor", "Initial jobless claims", caution=15, blackout=5, freeze=1, stabilization=10, min_liquidity=40_000, max_spread=6.0, min_net_edge=4.0, max_risk=0.5),
    "gdp": _policy("Growth and activity", "GDP", caution=20, blackout=5, freeze=1, stabilization=15, min_liquidity=50_000, max_spread=6.0, min_net_edge=4.0, max_risk=0.5),
    "retail_sales": _policy("Growth and activity", "Retail sales", caution=20, blackout=5, freeze=1, stabilization=15, min_liquidity=50_000, max_spread=6.0, min_net_edge=4.0, max_risk=0.5),
    "ism_manufacturing": _policy("Growth and activity", "ISM manufacturing", caution=20, blackout=5, freeze=1, stabilization=15, min_liquidity=50_000, max_spread=6.0, min_net_edge=4.0, max_risk=0.5),
    "ism_services": _policy("Growth and activity", "ISM services", caution=20, blackout=5, freeze=1, stabilization=15, min_liquidity=50_000, max_spread=6.0, min_net_edge=4.0, max_risk=0.5),
    "consumer_sentiment": _policy("Growth and activity", "Consumer sentiment", caution=15, blackout=5, freeze=1, stabilization=10, min_liquidity=40_000, max_spread=7.0, min_net_edge=3.0, max_risk=0.65),
    "trading_halt": _policy("Market-structure events", "Trading halt", caution=0, blackout=390, freeze=30, stabilization=60, min_liquidity=None, max_spread=0.0, min_net_edge=999.0, max_risk=0.0),
    "luld_event": _policy("Market-structure events", "LULD event", caution=0, blackout=390, freeze=30, stabilization=60, min_liquidity=None, max_spread=0.0, min_net_edge=999.0, max_risk=0.0),
    "circuit_breaker": _policy("Market-structure events", "Circuit breaker", caution=0, blackout=390, freeze=30, stabilization=60, min_liquidity=None, max_spread=0.0, min_net_edge=999.0, max_risk=0.0),
    "early_close": _policy("Market-structure events", "Early close", caution=60, blackout=15, freeze=0, stabilization=0, min_liquidity=50_000, max_spread=6.0, min_net_edge=4.0, max_risk=0.5),
    "options_expiration": _policy("Market-structure events", "Options expiration", caution=60, blackout=15, freeze=0, stabilization=30, min_liquidity=75_000, max_spread=5.0, min_net_edge=5.0, max_risk=0.35),
    "index_rebalance": _policy("Market-structure events", "Index rebalance", caution=60, blackout=15, freeze=0, stabilization=30, min_liquidity=75_000, max_spread=5.0, min_net_edge=5.0, max_risk=0.25),
    "moc_imbalance": _policy("Market-structure events", "Market-on-close imbalance period", caution=30, blackout=10, freeze=0, stabilization=15, min_liquidity=75_000, max_spread=5.0, min_net_edge=5.0, max_risk=0.25),
    "geopolitical_news": _policy("Unscheduled shock events", "Geopolitical news", caution=0, blackout=30, freeze=5, stabilization=60, min_liquidity=100_000, max_spread=4.0, min_net_edge=8.0, max_risk=0.0),
    "emergency_central_bank_announcement": _policy("Unscheduled shock events", "Emergency central-bank announcement", caution=0, blackout=30, freeze=5, stabilization=60, min_liquidity=100_000, max_spread=4.0, min_net_edge=8.0, max_risk=0.0),
    "financial_system_incident": _policy("Unscheduled shock events", "Major financial-system incident", caution=0, blackout=30, freeze=5, stabilization=60, min_liquidity=100_000, max_spread=4.0, min_net_edge=8.0, max_risk=0.0),
    "exchange_outage": _policy("Unscheduled shock events", "Exchange outage", caution=0, blackout=390, freeze=30, stabilization=60, min_liquidity=None, max_spread=0.0, min_net_edge=999.0, max_risk=0.0),
    "data_feed_outage": _policy("Unscheduled shock events", "Data-feed outage", caution=0, blackout=390, freeze=30, stabilization=60, min_liquidity=None, max_spread=0.0, min_net_edge=999.0, max_risk=0.0),
    "unknown": _policy("Unknown", "Unknown event", caution=60, blackout=30, freeze=5, stabilization=60, min_liquidity=100_000, max_spread=4.0, min_net_edge=8.0, max_risk=0.0),
}


DEFAULT_EVENT_TYPE_ALIASES: dict[str, str] = {
    "fomc": "fomc_statement",
    "fomc statement": "fomc_statement",
    "fomc_statement": "fomc_statement",
    "rate decision": "rate_decision",
    "rate_decision": "rate_decision",
    "press conference": "press_conference",
    "press_conference": "press_conference",
    "fomc minutes": "minutes",
    "minutes": "minutes",
    "chair speech": "chair_speech",
    "powell speech": "chair_speech",
    "governor speech": "governor_speech",
    "cpi": "cpi",
    "core cpi": "core_cpi",
    "core_cpi": "core_cpi",
    "pce": "pce",
    "ppi": "ppi",
    "nfp": "nonfarm_payrolls",
    "nonfarm payrolls": "nonfarm_payrolls",
    "nonfarm_payrolls": "nonfarm_payrolls",
    "unemployment rate": "unemployment_rate",
    "average hourly earnings": "average_hourly_earnings",
    "initial jobless claims": "initial_jobless_claims",
    "gdp": "gdp",
    "retail sales": "retail_sales",
    "ism manufacturing": "ism_manufacturing",
    "ism services": "ism_services",
    "consumer sentiment": "consumer_sentiment",
    "trading halt": "trading_halt",
    "halt": "trading_halt",
    "luld": "luld_event",
    "luld event": "luld_event",
    "circuit breaker": "circuit_breaker",
    "early close": "early_close",
    "opex": "options_expiration",
    "options expiration": "options_expiration",
    "index rebalance": "index_rebalance",
    "moc imbalance": "moc_imbalance",
    "market-on-close imbalance": "moc_imbalance",
    "geopolitical news": "geopolitical_news",
    "emergency central-bank announcement": "emergency_central_bank_announcement",
    "financial-system incident": "financial_system_incident",
    "financial system incident": "financial_system_incident",
    "exchange outage": "exchange_outage",
    "data-feed outage": "data_feed_outage",
    "data feed outage": "data_feed_outage",
}


@dataclass(frozen=True)
class EconomicEventEvidence:
    dataReady: bool
    eventId: str | None
    eventType: str | None
    eventCategory: str | None
    eventPolicyKey: str
    eventPolicy: dict[str, Any]
    provider: str | None
    providerTimestamp: str | None
    receivedAt: str | None
    feedHealth: str
    eventImportance: str
    eventPhase: str
    minutesUntilEvent: float | None
    minutesSinceEvent: float | None
    eventState: str
    actual: float | str | None
    forecast: float | str | None
    previous: float | str | None
    revisedPrevious: float | str | None
    surpriseRaw: float | None
    surprisePct: float | None
    surpriseZscore: float | None
    affectedSymbols: list[str]
    directionalReaction: str
    volatilityShock: float | None
    spreadShock: float | None
    allowNewEntries: bool
    allowPositionIncrease: bool
    eventBlackout: bool
    recommendedRiskCap: float
    recommendedSizeMultiplier: float
    minimumEdgeMultiplier: float
    executionMode: str
    cooldownUntil: str | None
    eventReactionAllowed: bool
    requiredSafetyMarginBps: float
    minimumEdgeToCostRatio: float
    identityStateIndicators: dict[str, Any]
    surpriseIndicators: dict[str, Any]
    marketReactionIndicators: dict[str, Any]
    latencyDataHealthIndicators: dict[str, Any]
    transactionCostTradabilityIndicators: dict[str, Any]
    contextEffect: str
    reasonCodes: list[str]


class EconomicEventContext:
    registryEntry = resolve_strategy("economic_event_context")

    def __init__(self, config: EconomicEventContextConfig | None = None) -> None:
        self.config = config or EconomicEventContextConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> ContextSignal:
        if self.registryEntry.collection != StrategyCollection.CONTEXT.value:
            raise ValueError("Economic Event Context must be registered as context")
        evidence = self._evidence(context)
        return ContextSignal(
            contextId=self.registryEntry.strategyId,
            signal=Signal.HOLD,
            direction=Direction.FLAT,
            confidence=self._confidence(evidence),
            dataReady=evidence.dataReady,
            explanation=self._explanation(evidence),
            features={
                "eventId": evidence.eventId,
                "eventType": evidence.eventType,
                "eventCategory": evidence.eventCategory,
                "eventPolicyKey": evidence.eventPolicyKey,
                "eventPolicy": evidence.eventPolicy,
                "provider": evidence.provider,
                "providerTimestamp": evidence.providerTimestamp,
                "receivedAt": evidence.receivedAt,
                "feedHealth": evidence.feedHealth,
                "eventImportance": evidence.eventImportance,
                "eventPhase": evidence.eventPhase,
                "event_phase": evidence.eventPhase,
                "minutesUntilEvent": evidence.minutesUntilEvent,
                "minutes_to_event": evidence.minutesUntilEvent,
                "minutesSinceEvent": evidence.minutesSinceEvent,
                "minutes_after_event": evidence.minutesSinceEvent,
                "eventState": evidence.eventState,
                "actual": evidence.actual,
                "forecast": evidence.forecast,
                "previous": evidence.previous,
                "revisedPrevious": evidence.revisedPrevious,
                "surpriseRaw": evidence.surpriseRaw,
                "surprisePct": evidence.surprisePct,
                "surpriseZscore": evidence.surpriseZscore,
                "affectedSymbols": evidence.affectedSymbols,
                "directionalReaction": evidence.directionalReaction,
                "volatilityShock": evidence.volatilityShock,
                "spreadShock": evidence.spreadShock,
                "allow_new_entries": evidence.allowNewEntries,
                "allowNewEntries": evidence.allowNewEntries,
                "allow_position_increase": evidence.allowPositionIncrease,
                "allowPositionIncrease": evidence.allowPositionIncrease,
                "event_blackout": evidence.eventBlackout,
                "eventBlackout": evidence.eventBlackout,
                "recommendedRiskCap": evidence.recommendedRiskCap,
                "recommended_risk_cap": evidence.recommendedRiskCap,
                "recommended_size_multiplier": evidence.recommendedSizeMultiplier,
                "recommendedSizeMultiplier": evidence.recommendedSizeMultiplier,
                "minimum_edge_multiplier": evidence.minimumEdgeMultiplier,
                "minimumEdgeMultiplier": evidence.minimumEdgeMultiplier,
                "execution_mode": evidence.executionMode,
                "executionMode": evidence.executionMode,
                "cooldown_until": evidence.cooldownUntil,
                "cooldownUntil": evidence.cooldownUntil,
                "event_reaction_allowed": evidence.eventReactionAllowed,
                "eventReactionAllowed": evidence.eventReactionAllowed,
                "required_safety_margin_bps": evidence.requiredSafetyMarginBps,
                "requiredSafetyMarginBps": evidence.requiredSafetyMarginBps,
                "minimum_edge_to_cost_ratio": evidence.minimumEdgeToCostRatio,
                "minimumEdgeToCostRatio": evidence.minimumEdgeToCostRatio,
                "identityStateIndicators": evidence.identityStateIndicators,
                "surpriseIndicators": evidence.surpriseIndicators,
                "marketReactionIndicators": evidence.marketReactionIndicators,
                "latencyDataHealthIndicators": evidence.latencyDataHealthIndicators,
                "transactionCostTradabilityIndicators": evidence.transactionCostTradabilityIndicators,
                **evidence.identityStateIndicators,
                **evidence.surpriseIndicators,
                **evidence.marketReactionIndicators,
                **evidence.latencyDataHealthIndicators,
                **evidence.transactionCostTradabilityIndicators,
                "maxConfidenceAdjustment": self.config.maxConfidenceAdjustment,
                "contextEffect": evidence.contextEffect,
                "reasonCodes": evidence.reasonCodes,
            },
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            configurationHash=context.configurationHash,
        )

    def _evidence(self, context: StrategyEvaluationContext) -> EconomicEventEvidence:
        feature = context.featureSnapshot.features.get("economicEventState")
        if not feature or not isinstance(feature.value, dict):
            return _missing(["economic_event.missing_event_state"])
        candidate_events = self._candidate_events(feature.value)
        event = self._dominant_event_from_candidates(candidate_events, context.evaluatedAt)
        if not event:
            return _missing(["economic_event.empty_event_state"])

        timestamp = _event_timestamp(event)
        minutes_until = ((timestamp - context.evaluatedAt).total_seconds() / 60) if timestamp else None
        minutes_since = ((context.evaluatedAt - timestamp).total_seconds() / 60) if timestamp else None
        importance = _importance(event)
        event_state = _event_state(event, minutes_until, minutes_since, self.config.eventWindowMinutes)
        policy_key, policy = self._policy_for(event)
        event_phase = _event_phase(event, event_state, minutes_until, minutes_since, policy)
        base_risk_cap = self._risk_cap(importance, event_state)
        risk_cap = self._phase_adjusted_risk_cap(base_risk_cap, event_state, event_phase, policy)
        candles = _candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [])
        reaction = _observable_reaction(candles, timestamp, context.evaluatedAt) if timestamp else "none_observable"
        volatility_shock = _volatility_shock(candles)
        spread_bps = _number(context.featureSnapshot.features.get("spreadBasisPoints").value if context.featureSnapshot.features.get("spreadBasisPoints") else None)
        spread_shock = None if spread_bps is None else spread_bps / max(self.config.spreadShockBasisPoints, 0.01)
        surprise = _surprise_indicators(event)
        market_reaction = _market_reaction_indicators(event, candles, timestamp, context.evaluatedAt, spread_bps)
        latency = _latency_indicators(event, context, feature.sourceTimestamp)
        required_safety_margin_bps = self._required_safety_margin_bps(candidate_events)
        economics = _execution_economics_indicators(event, spread_bps, required_safety_margin_bps)
        shock = (volatility_shock is not None and volatility_shock >= self.config.volatilityShockThreshold) or (spread_shock is not None and spread_shock >= 1.0)
        unexpected_breaking = bool(event.get("unexpected_breaking_event") or event.get("unexpectedBreakingEvent"))
        malformed = bool(event.get("malformed") or (event_state == "released" and event.get("actual") is None))
        event_blackout = event_state == "blackout" or event_phase in {"PRE_EVENT_BLACKOUT", "RELEASE_FREEZE"} or (importance in {"high", "unknown"} and event_state == "active")
        controls = self._controls(event_state, risk_cap, shock, event_blackout, context.evaluatedAt, event, economics, spread_bps, market_reaction, policy, malformed=malformed, unexpected_breaking=unexpected_breaking)
        identity = _identity_state_indicators(event, importance, event_phase, minutes_until, minutes_since, policy_key)
        simultaneous_controls = None
        if len(candidate_events) > 1:
            simultaneous_controls = self._most_restrictive_simultaneous_controls(candidate_events, context, spread_bps, shock)
            controls = simultaneous_controls["controls"]
            identity.update(
                {
                    "simultaneous_event_count": len(candidate_events),
                    "simultaneous_event_policy_keys": simultaneous_controls["policyKeys"],
                    "simultaneous_event_execution_modes": simultaneous_controls["executionModes"],
                    "simultaneous_event_required_safety_margin_bps": required_safety_margin_bps,
                }
            )
        effect = "reduce_risk" if controls["recommendedRiskCap"] < 1.0 or shock or not controls["allowNewEntries"] else "neutral"
        reasons = [f"economic_event.{effect}", "economic_event.candidate_side_not_replaced"]
        if feature.quality != FeatureQuality.READY.value:
            reasons.append(f"economic_event.feature_quality_{str(feature.quality).lower()}")
        if shock:
            reasons.append("economic_event.enforceable_shock_controls")
        if event_blackout:
            reasons.append("economic_event.blackout_controls")
        if malformed:
            reasons.append("economic_event.malformed_release_block")
        if unexpected_breaking:
            reasons.append("economic_event.unexpected_breaking_defensive")
        if not controls["eventReactionAllowed"]:
            reasons.append("economic_event.event_reaction_blocked")
        if event.get("duplicate_of") or event.get("duplicateOf") or event.get("revision_number") or event.get("revisionNumber"):
            reasons.append("economic_event.duplicate_or_revision_recomputed")
        if feature.value is not event and (feature.value.get("simultaneous_events") or feature.value.get("simultaneousEvents")):
            reasons.append("economic_event.simultaneous_events_selected_dominant")
        if simultaneous_controls:
            reasons.append("economic_event.simultaneous_events_most_restrictive_controls")
        return EconomicEventEvidence(
            dataReady=feature.quality == FeatureQuality.READY.value,
            eventId=_string_or_none(event.get("event_id") or event.get("eventId")),
            eventType=_string_or_none(event.get("event_type") or event.get("eventType")),
            eventCategory=_string_or_none(event.get("event_category") or event.get("eventCategory") or event.get("category")),
            eventPolicyKey=policy_key,
            eventPolicy=policy.model_dump(mode="json"),
            provider=_string_or_none(event.get("provider")),
            providerTimestamp=_string_or_none(event.get("provider_timestamp") or event.get("providerTimestamp")),
            receivedAt=_string_or_none(event.get("received_at") or event.get("receivedAt")),
            feedHealth=str(event.get("feed_health") or event.get("feedHealth") or "unknown"),
            eventImportance=importance,
            eventPhase=event_phase,
            minutesUntilEvent=round(minutes_until, 2) if minutes_until is not None else None,
            minutesSinceEvent=round(minutes_since, 2) if minutes_since is not None else None,
            eventState=event_state,
            actual=event.get("actual"),
            forecast=event.get("forecast"),
            previous=event.get("previous"),
            revisedPrevious=event.get("revised_previous") or event.get("revisedPrevious"),
            surpriseRaw=_number(surprise.get("actual_minus_forecast")),
            surprisePct=_number(surprise.get("percentage_surprise")),
            surpriseZscore=_number(surprise.get("standardized_surprise_zscore")),
            affectedSymbols=list(event.get("affected_symbols") or event.get("affectedSymbols") or []),
            directionalReaction=reaction,
            volatilityShock=round(volatility_shock, 4) if volatility_shock is not None else None,
            spreadShock=round(spread_shock, 4) if spread_shock is not None else None,
            allowNewEntries=controls["allowNewEntries"],
            allowPositionIncrease=controls["allowPositionIncrease"],
            eventBlackout=controls["eventBlackout"],
            recommendedRiskCap=controls["recommendedRiskCap"],
            recommendedSizeMultiplier=controls["recommendedSizeMultiplier"],
            minimumEdgeMultiplier=controls["minimumEdgeMultiplier"],
            executionMode=controls["executionMode"],
            cooldownUntil=controls["cooldownUntil"],
            eventReactionAllowed=controls["eventReactionAllowed"],
            requiredSafetyMarginBps=required_safety_margin_bps,
            minimumEdgeToCostRatio=self.config.minimumEdgeToCostRatio,
            identityStateIndicators=identity,
            surpriseIndicators=surprise,
            marketReactionIndicators=market_reaction,
            latencyDataHealthIndicators=latency,
            transactionCostTradabilityIndicators=economics,
            contextEffect=effect,
            reasonCodes=reasons,
        )

    def _risk_cap(self, importance: str, event_state: str) -> float:
        if event_state == "none":
            return 1.0
        if importance in {"high", "unknown"}:
            return self.config.highImportanceRiskCap
        if importance == "medium":
            return self.config.mediumImportanceRiskCap
        return self.config.lowImportanceRiskCap

    def _phase_adjusted_risk_cap(self, base_risk_cap: float, event_state: str, event_phase: str, policy: EconomicEventPolicy) -> float:
        if event_state == "none":
            return 1.0
        if event_phase in {"NORMAL", "NORMALIZED"} and event_state not in {"active", "blackout", "upcoming"}:
            return 1.0
        return min(base_risk_cap, policy.maximumPositionRiskMultiplier)

    def _policy_for(self, event: dict[str, Any]) -> tuple[str, EconomicEventPolicy]:
        event_type = str(event.get("event_type") or event.get("eventType") or event.get("type") or "").strip().lower()
        event_category = str(event.get("event_category") or event.get("eventCategory") or event.get("category") or "").strip().lower()
        key = self.config.eventTypeAliases.get(event_type) or self.config.eventTypeAliases.get(event_category) or _slug(event_type) or _slug(event_category) or "unknown"
        if key not in self.config.eventPolicies:
            key = "unknown"
        return key, self.config.eventPolicies[key]

    def _candidate_events(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = [event, *list(event.get("simultaneous_events") or event.get("simultaneousEvents") or [])]
        return [candidate for candidate in candidates if isinstance(candidate, dict) and candidate]

    def _dominant_event(self, event: dict[str, Any], evaluated_at: datetime) -> dict[str, Any]:
        return self._dominant_event_from_candidates(self._candidate_events(event), evaluated_at)

    def _dominant_event_from_candidates(self, normalized: list[dict[str, Any]], evaluated_at: datetime) -> dict[str, Any]:
        if not normalized:
            return {}

        def rank(candidate: dict[str, Any]) -> tuple[float, int, int, float]:
            timestamp = _event_timestamp(candidate)
            minutes_until = ((timestamp - evaluated_at).total_seconds() / 60) if timestamp else None
            minutes_since = ((evaluated_at - timestamp).total_seconds() / 60) if timestamp else None
            event_state = _event_state(candidate, minutes_until, minutes_since, self.config.eventWindowMinutes)
            _, policy = self._policy_for(candidate)
            phase = _event_phase(candidate, event_state, minutes_until, minutes_since, policy)
            blackout_rank = 1 if phase in {"PRE_EVENT_BLACKOUT", "RELEASE_FREEZE", "FEED_UNAVAILABLE"} or event_state == "blackout" else 0
            importance_rank = {"unknown": 3, "high": 3, "medium": 2, "low": 1}.get(_importance(candidate), 3)
            proximity = abs(minutes_until if minutes_until is not None else minutes_since if minutes_since is not None else 9_999.0)
            return (1.0 - policy.maximumPositionRiskMultiplier, blackout_rank, importance_rank, -proximity)

        return max(normalized, key=rank)

    def _required_safety_margin_bps(self, events: list[dict[str, Any]]) -> float:
        margins = [self.config.requiredSafetyMarginBps]
        for event in events:
            _, policy = self._policy_for(event)
            margins.append(policy.minimumNetEdgeBps)
        return max(margins)

    def _most_restrictive_simultaneous_controls(self, events: list[dict[str, Any]], context: StrategyEvaluationContext, spread_bps: float | None, shock: bool) -> dict[str, Any]:
        controls: list[dict[str, Any]] = []
        policy_keys: list[str] = []
        execution_modes: list[str] = []
        for event in events:
            timestamp = _event_timestamp(event)
            minutes_until = ((timestamp - context.evaluatedAt).total_seconds() / 60) if timestamp else None
            minutes_since = ((context.evaluatedAt - timestamp).total_seconds() / 60) if timestamp else None
            importance = _importance(event)
            event_state = _event_state(event, minutes_until, minutes_since, self.config.eventWindowMinutes)
            policy_key, policy = self._policy_for(event)
            phase = _event_phase(event, event_state, minutes_until, minutes_since, policy)
            base_risk_cap = self._risk_cap(importance, event_state)
            risk_cap = self._phase_adjusted_risk_cap(base_risk_cap, event_state, phase, policy)
            market_reaction = _market_reaction_indicators(event, _candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or []), timestamp, context.evaluatedAt, spread_bps)
            economics = _execution_economics_indicators(event, spread_bps, max(self.config.requiredSafetyMarginBps, policy.minimumNetEdgeBps))
            malformed = bool(event.get("malformed") or (event_state == "released" and event.get("actual") is None))
            unexpected_breaking = bool(event.get("unexpected_breaking_event") or event.get("unexpectedBreakingEvent"))
            event_blackout = event_state == "blackout" or phase in {"PRE_EVENT_BLACKOUT", "RELEASE_FREEZE"} or (importance in {"high", "unknown"} and event_state == "active")
            item = self._controls(event_state, risk_cap, shock, event_blackout, context.evaluatedAt, event, economics, spread_bps, market_reaction, policy, malformed=malformed, unexpected_breaking=unexpected_breaking)
            controls.append(item)
            policy_keys.append(policy_key)
            execution_modes.append(str(item["executionMode"]))
        return {
            "controls": _aggregate_controls(controls),
            "policyKeys": policy_keys,
            "executionModes": execution_modes,
        }

    def _controls(self, event_state: str, risk_cap: float, shock: bool, event_blackout: bool, evaluated_at: datetime, event: dict[str, Any], economics: dict[str, Any], spread_bps: float | None, market_reaction: dict[str, Any], policy: EconomicEventPolicy, *, malformed: bool, unexpected_breaking: bool) -> dict[str, Any]:
        if event_state == "none":
            return {
                "allowNewEntries": True,
                "allowPositionIncrease": True,
                "eventBlackout": False,
                "recommendedRiskCap": 1.0,
                "recommendedSizeMultiplier": 1.0,
                "minimumEdgeMultiplier": 1.0,
                "executionMode": "normal",
                "cooldownUntil": None,
                "eventReactionAllowed": False,
            }
        feed_health = str(event.get("feed_health") or event.get("feedHealth") or "unknown")
        feed_unavailable = feed_health in {"stale", "unavailable", "unknown"}
        degraded_feed = feed_health == "degraded"
        economics_block = _economics_block(economics, self.config.minimumEdgeToCostRatio)
        spread_block = bool(policy.maximumSpreadBps is not None and (spread_bps is None or spread_bps > policy.maximumSpreadBps))
        liquidity_block = _liquidity_block(policy, economics, market_reaction)
        defensive_block = event_blackout or malformed or unexpected_breaking or feed_unavailable or economics_block or spread_block or liquidity_block
        enforceable_cap = min(risk_cap, self.config.shockRiskCap if shock else 1.0, 0.0 if event_blackout else 1.0)
        if feed_unavailable or malformed or unexpected_breaking or economics_block:
            enforceable_cap = min(enforceable_cap, self.config.feedUnavailableRiskCap)
        elif degraded_feed:
            enforceable_cap = min(enforceable_cap, self.config.degradedFeedRiskCap)
        size_multiplier = min(enforceable_cap, self.config.shockSizeMultiplier if shock else 1.0)
        cooldown_until = None
        if (shock or unexpected_breaking) and self.config.shockCooldownMinutes > 0:
            cooldown_until = (evaluated_at + timedelta(minutes=self.config.shockCooldownMinutes)).isoformat().replace("+00:00", "Z")
        if event_blackout:
            execution_mode = "blackout"
        elif malformed:
            execution_mode = "malformed_release_block"
        elif unexpected_breaking:
            execution_mode = "defensive_shock"
        elif feed_unavailable:
            execution_mode = "feed_unavailable"
        elif economics_block:
            execution_mode = "net_edge_insufficient"
        elif spread_block:
            execution_mode = "event_spread_limit_exceeded"
        elif liquidity_block:
            execution_mode = "event_liquidity_insufficient"
        elif not bool(enforceable_cap):
            execution_mode = "no_new_entries"
        elif shock or enforceable_cap < 1.0:
            execution_mode = "reduce_only"
        else:
            execution_mode = "normal"
        return {
            "allowNewEntries": bool(enforceable_cap > 0.0 and not defensive_block),
            "allowPositionIncrease": bool(enforceable_cap >= 1.0 and not shock and not defensive_block),
            "eventBlackout": event_blackout,
            "recommendedRiskCap": round(max(0.0, min(1.0, enforceable_cap)), 4),
            "recommendedSizeMultiplier": round(max(0.0, min(1.0, size_multiplier)), 4),
            "minimumEdgeMultiplier": round(self.config.shockMinimumEdgeMultiplier if shock else 1.0, 4),
            "executionMode": execution_mode,
            "cooldownUntil": cooldown_until,
            "eventReactionAllowed": bool(not defensive_block and not shock and event_state not in {"cancelled", "postponed"}),
        }

    def _confidence(self, evidence: EconomicEventEvidence) -> float:
        if not evidence.dataReady:
            return 0.0
        risk_score = 1.0 - evidence.recommendedRiskCap
        shock_score = max((evidence.volatilityShock or 0) / max(self.config.volatilityShockThreshold * 2, 0.01), (evidence.spreadShock or 0) / 2)
        return round(max(0.05, min(1.0, (0.65 * risk_score) + (0.35 * min(1.0, shock_score)))), 4)

    def _explanation(self, evidence: EconomicEventEvidence) -> str:
        if not evidence.dataReady:
            return f"HOLD context because economic-event inputs are unavailable: {', '.join(evidence.reasonCodes)}."
        return (
            "HOLD context only: Economic Event Context "
            f"{evidence.eventImportance} {evidence.eventState}, reaction {evidence.directionalReaction}, "
            f"risk cap {evidence.recommendedRiskCap:.2f}; candidate side is not replaced."
        )


def _missing(reason_codes: list[str]) -> EconomicEventEvidence:
    return EconomicEventEvidence(
        dataReady=False,
        eventId=None,
        eventType=None,
        eventCategory=None,
        eventPolicyKey="unknown",
        eventPolicy=DEFAULT_EVENT_POLICIES["unknown"].model_dump(mode="json"),
        provider=None,
        providerTimestamp=None,
        receivedAt=None,
        feedHealth="unknown",
        eventImportance="unknown",
        eventPhase="FEED_UNAVAILABLE",
        minutesUntilEvent=None,
        minutesSinceEvent=None,
        eventState="missing",
        actual=None,
        forecast=None,
        previous=None,
        revisedPrevious=None,
        surpriseRaw=None,
        surprisePct=None,
        surpriseZscore=None,
        affectedSymbols=[],
        directionalReaction="none_observable",
        volatilityShock=None,
        spreadShock=None,
        allowNewEntries=False,
        allowPositionIncrease=False,
        eventBlackout=False,
        recommendedRiskCap=0.0,
        recommendedSizeMultiplier=0.0,
        minimumEdgeMultiplier=2.0,
        executionMode="event_data_unavailable",
        cooldownUntil=None,
        eventReactionAllowed=False,
        requiredSafetyMarginBps=2.0,
        minimumEdgeToCostRatio=1.25,
        identityStateIndicators={"event_phase": "FEED_UNAVAILABLE", "status": "missing", "importance": "unknown"},
        surpriseIndicators=_empty_surprise_indicators(),
        marketReactionIndicators=_empty_market_reaction_indicators(),
        latencyDataHealthIndicators=_empty_latency_indicators(),
        transactionCostTradabilityIndicators=_empty_economics_indicators(),
        contextEffect="reduce_risk",
        reasonCodes=reason_codes,
    )


def _importance(event: dict[str, Any]) -> str:
    raw = str(event.get("importance") or event.get("impact") or event.get("severity") or "unknown").lower()
    return raw if raw in {"low", "medium", "high"} else "unknown"


def _event_state(event: dict[str, Any], minutes_until: float | None, minutes_since: float | None, window: int) -> str:
    status = str(event.get("status") or event.get("state") or "").lower()
    category = str(event.get("event_category") or event.get("eventCategory") or event.get("category") or "").lower()
    if status in {"none", "no_event"} or category in {"none", "no_event"}:
        return "none"
    if status in {"cancelled", "postponed", "blackout"}:
        return status
    if event.get("active") is True:
        return "active"
    if minutes_until is not None and 0 <= minutes_until <= window:
        return "upcoming"
    if minutes_since is not None and 0 <= minutes_since <= window:
        return "recent"
    return "outside_window"


def _event_phase(event: dict[str, Any], event_state: str, minutes_until: float | None, minutes_since: float | None, policy: EconomicEventPolicy) -> str:
    explicit = str(event.get("event_phase") or event.get("eventPhase") or "").upper()
    if explicit:
        return explicit
    if event_state == "none":
        return "NORMAL"
    if str(event.get("feed_health") or event.get("feedHealth") or "unknown").lower() in {"stale", "unavailable", "unknown"}:
        return "FEED_UNAVAILABLE"
    if event_state in {"cancelled", "postponed"}:
        return "NORMALIZED"
    if event_state == "blackout":
        return "PRE_EVENT_BLACKOUT"
    if minutes_until is not None:
        if 0 <= minutes_until <= policy.preEventBlackoutWindowMinutes:
            return "PRE_EVENT_BLACKOUT"
        if 0 <= minutes_until <= policy.preEventCautionWindowMinutes:
            return "PRE_EVENT_CAUTION"
    if minutes_since is not None:
        if 0 <= minutes_since <= policy.releaseFreezeDurationMinutes:
            return "RELEASE_FREEZE"
        if policy.releaseFreezeDurationMinutes < minutes_since <= max(policy.releaseFreezeDurationMinutes, 5):
            return "POST_EVENT_DISCOVERY"
        if 5 < minutes_since <= policy.postEventStabilizationWindowMinutes:
            return "POST_EVENT_STABILIZATION"
    if event_state == "outside_window" and minutes_until is not None and minutes_until > 0:
        return "NORMAL"
    return "NORMALIZED" if event_state in {"released", "recent", "outside_window"} else "NORMAL"


def _identity_state_indicators(event: dict[str, Any], importance: str, event_phase: str, minutes_until: float | None, minutes_since: float | None, policy_key: str) -> dict[str, Any]:
    return {
        "event_type": event.get("event_type") or event.get("eventType"),
        "event_category": event.get("event_category") or event.get("eventCategory") or event.get("category"),
        "event_id": event.get("event_id") or event.get("eventId"),
        "importance": importance,
        "scheduled_at": event.get("scheduled_at") or event.get("scheduledAt"),
        "released_at": event.get("released_at") or event.get("releasedAt"),
        "event_phase": event_phase,
        "minutes_to_event": round(minutes_until, 2) if minutes_until is not None else None,
        "minutes_after_event": round(minutes_since, 2) if minutes_since is not None else None,
        "status": event.get("status") or event.get("state"),
        "affected_assets": list(event.get("affected_symbols") or event.get("affectedSymbols") or []),
        "event_policy_key": policy_key,
        "duplicate_of": event.get("duplicate_of") or event.get("duplicateOf"),
        "revision_number": event.get("revision_number") or event.get("revisionNumber"),
    }


def _surprise_indicators(event: dict[str, Any]) -> dict[str, Any]:
    actual = _number(event.get("actual"))
    forecast = _number(event.get("forecast"))
    previous = _number(event.get("previous"))
    revised_previous = _number(event.get("revised_previous") or event.get("revisedPrevious"))
    raw = _number(event.get("surprise_raw") or event.get("surpriseRaw"))
    if raw is None and actual is not None and forecast is not None:
        raw = actual - forecast
    pct = _number(event.get("surprise_pct") or event.get("surprisePct"))
    if pct is None and raw is not None and forecast not in {None, 0.0}:
        pct = (raw / abs(float(forecast))) * 100.0
    prior_revision = None if previous is None or revised_previous is None else revised_previous - previous
    zscore = _number(event.get("surprise_zscore") or event.get("surpriseZscore"))
    baseline_mean = _number(event.get("surprise_mean") or event.get("surpriseMean") or _nested(event, "surprise_baseline", "mean") or _nested(event, "surpriseBaseline", "mean"))
    baseline_stddev = _number(
        event.get("surprise_stddev")
        or event.get("surpriseStddev")
        or event.get("surprise_standard_deviation")
        or event.get("surpriseStandardDeviation")
        or _nested(event, "surprise_baseline", "stddev")
        or _nested(event, "surpriseBaseline", "stddev")
    )
    baseline_sample_count = _number(event.get("surprise_sample_count") or event.get("surpriseSampleCount") or _nested(event, "surprise_baseline", "sampleCount") or _nested(event, "surpriseBaseline", "sampleCount"))
    if zscore is None and raw is not None and baseline_stddev is not None and baseline_stddev > 0:
        zscore = (raw - (baseline_mean or 0.0)) / baseline_stddev
    return {
        "actual_minus_forecast": round(raw, 6) if raw is not None else None,
        "percentage_surprise": round(pct, 6) if pct is not None else None,
        "standardized_surprise_zscore": round(zscore, 6) if zscore is not None else None,
        "surprise_baseline_mean": round(baseline_mean, 6) if baseline_mean is not None else None,
        "surprise_baseline_stddev": round(baseline_stddev, 6) if baseline_stddev is not None else None,
        "surprise_baseline_sample_count": int(baseline_sample_count) if baseline_sample_count is not None else None,
        "prior_revision": round(prior_revision, 6) if prior_revision is not None else None,
        "directional_interpretation": _directional_interpretation(event, raw),
        "cross_series_confirmation": event.get("cross_series_confirmation") or event.get("crossSeriesConfirmation"),
    }


def _market_reaction_indicators(event: dict[str, Any], candles: list[dict[str, Any]], event_at: datetime | None, evaluated_at: datetime, spread_bps: float | None) -> dict[str, Any]:
    supplied = dict(event.get("market_reaction") or event.get("marketReaction") or {})
    ranges = [float(row["high"]) - float(row["low"]) for row in candles[-11:-1]]
    latest_range = (float(candles[-1]["high"]) - float(candles[-1]["low"])) if candles else None
    baseline_range = mean(ranges) if ranges else None
    volume_baseline = mean(float(row["volume"]) for row in candles[-11:-1]) if len(candles) >= 12 else None
    latest_volume = float(candles[-1]["volume"]) if candles else None
    latest_trade_count = _number(candles[-1].get("tradeCount")) if candles else None
    prior_trade_counts = [float(row["tradeCount"]) for row in candles[-11:-1] if row.get("tradeCount") is not None]
    prior_trade_count = mean(prior_trade_counts) if prior_trade_counts else None
    result = {
        "return_15s": _supplied_number(supplied, "return_15s", "return15s"),
        "return_30s": _supplied_number(supplied, "return_30s", "return30s"),
        "return_1m": _return_after(candles, event_at, evaluated_at, 60),
        "return_3m": _return_after(candles, event_at, evaluated_at, 180),
        "return_5m": _return_after(candles, event_at, evaluated_at, 300),
        "return_15m": _return_after(candles, event_at, evaluated_at, 900),
        "range_expansion_ratio": (latest_range / baseline_range) if latest_range is not None and baseline_range else None,
        "realized_volatility_ratio": _realized_volatility_ratio(candles),
        "volume_shock_ratio": (latest_volume / volume_baseline) if latest_volume is not None and volume_baseline else None,
        "trade_count_shock": (latest_trade_count / prior_trade_count) if latest_trade_count is not None and prior_trade_count else None,
        "signed_volume_delta": _signed_volume_delta(candles),
        "spread_percentile": _supplied_number(supplied, "spread_percentile", "spreadPercentile"),
        "spread_change_velocity": _supplied_number(supplied, "spread_change_velocity", "spreadChangeVelocity"),
        "quote_update_rate": _supplied_number(supplied, "quote_update_rate", "quoteUpdateRate"),
        "order_book_imbalance": _supplied_number(supplied, "order_book_imbalance", "orderBookImbalance"),
        "available_depth": _supplied_number(supplied, "available_depth", "availableDepth"),
        "price_impact": _supplied_number(supplied, "price_impact", "priceImpact"),
        "reversal_from_initial_impulse": supplied.get("reversal_from_initial_impulse") or supplied.get("reversalFromInitialImpulse"),
        "reaction_persistence": supplied.get("reaction_persistence") or supplied.get("reactionPersistence"),
        "baseline_normalization": {
            "time_of_day": supplied.get("time_of_day") or supplied.get("timeOfDay"),
            "day_type": event.get("day_type") or event.get("dayType"),
            "event_type": event.get("event_type") or event.get("eventType"),
            "volatility_regime": event.get("volatility_regime") or event.get("volatilityRegime"),
            "recent_spy_liquidity_conditions": supplied.get("recent_spy_liquidity_conditions") or supplied.get("recentSpyLiquidityConditions"),
            "baseline_version": event.get("baseline_version") or event.get("baselineVersion"),
        },
        "current_spread_bps": spread_bps,
    }
    return {key: _rounded(value) for key, value in result.items()}


def _latency_indicators(event: dict[str, Any], context: StrategyEvaluationContext, source_timestamp: datetime | None) -> dict[str, Any]:
    latency = dict(event.get("latency") or {})
    raw = context.featureSnapshot.rawInputs
    quote_timestamp = _raw_timestamp((raw.get("quote") or {}).get("timestamp")) if isinstance(raw.get("quote"), dict) else None
    market_timestamp = context.featureSnapshot.anchorTimestamp
    provider_timestamp = source_timestamp or _raw_timestamp(event.get("provider_timestamp") or event.get("providerTimestamp"))
    return {
        "event_provider_age_ms": _age_ms(context.evaluatedAt, provider_timestamp),
        "market_data_age_ms": _age_ms(context.evaluatedAt, market_timestamp),
        "quote_age_ms": _age_ms(context.evaluatedAt, quote_timestamp),
        "last_trade_age_ms": _supplied_number(latency, "last_trade_age_ms", "lastTradeAgeMs"),
        "decision_age_ms": _supplied_number(latency, "decision_age_ms", "decisionAgeMs"),
        "feature_compute_duration_ms": _supplied_number(latency, "feature_compute_duration_ms", "featureComputeDurationMs"),
        "routing_duration_ms": _supplied_number(latency, "routing_duration_ms", "routingDurationMs"),
        "acknowledgment_latency_ms": _supplied_number(latency, "acknowledgment_latency_ms", "acknowledgmentLatencyMs"),
        "clock_skew_ms": _clock_skew_ms(event),
        "sequence_gap_count": _supplied_number(latency, "sequence_gap_count", "sequenceGapCount"),
        "dropped_message_count": _supplied_number(latency, "dropped_message_count", "droppedMessageCount"),
        "feed_health": event.get("feed_health") or event.get("feedHealth") or "unknown",
    }


def _execution_economics_indicators(event: dict[str, Any], spread_bps: float | None, required_safety_margin_bps: float) -> dict[str, Any]:
    economics = dict(event.get("execution_economics") or event.get("executionEconomics") or {})
    spread_cost = _supplied_number(economics, "expected_spread_cost_bps", "expectedSpreadCostBps")
    if spread_cost is None:
        spread_cost = spread_bps
    slippage = _supplied_number(economics, "expected_slippage_bps", "expectedSlippageBps")
    fees = _supplied_number(economics, "fees_bps", "feesBps")
    impact = _supplied_number(economics, "market_impact_bps", "marketImpactBps")
    total = _supplied_number(economics, "expected_total_cost_bps", "expectedTotalCostBps")
    known_costs = [value for value in (spread_cost, slippage, fees, impact) if value is not None]
    if total is None and len(known_costs) == 4:
        total = sum(known_costs)
    gross = _supplied_number(economics, "predicted_gross_edge_bps", "predictedGrossEdgeBps")
    net = _supplied_number(economics, "predicted_net_edge_bps", "predictedNetEdgeBps")
    if net is None and gross is not None and total is not None:
        net = gross - total
    edge_ratio = _supplied_number(economics, "edge_to_cost_ratio", "edgeToCostRatio")
    if edge_ratio is None and gross is not None and total and total > 0:
        edge_ratio = gross / total
    return {
        "expected_spread_cost_bps": _rounded(spread_cost),
        "expected_slippage_bps": _rounded(slippage),
        "fees_bps": _rounded(fees),
        "market_impact_bps": _rounded(impact),
        "expected_total_cost_bps": _rounded(total),
        "predicted_gross_edge_bps": _rounded(gross),
        "predicted_net_edge_bps": _rounded(net),
        "edge_to_cost_ratio": _rounded(edge_ratio),
        "fillable_quantity": _supplied_number(economics, "fillable_quantity", "fillableQuantity"),
        "participation_rate": _supplied_number(economics, "participation_rate", "participationRate"),
        "adverse_selection_risk": _supplied_number(economics, "adverse_selection_risk", "adverseSelectionRisk"),
        "required_safety_margin_bps": required_safety_margin_bps,
    }


def _economics_block(economics: dict[str, Any], minimum_edge_to_cost_ratio: float) -> bool:
    net = _number(economics.get("predicted_net_edge_bps"))
    margin = _number(economics.get("required_safety_margin_bps")) or 0.0
    ratio = _number(economics.get("edge_to_cost_ratio"))
    if net is not None and net <= margin:
        return True
    if ratio is not None and ratio < minimum_edge_to_cost_ratio:
        return True
    return False


def _liquidity_block(policy: EconomicEventPolicy, economics: dict[str, Any], market_reaction: dict[str, Any]) -> bool:
    if policy.minimumLiquidityShares is None:
        return False
    fillable = _number(economics.get("fillable_quantity"))
    depth = _number(market_reaction.get("available_depth"))
    observed = fillable if fillable is not None else depth
    return observed is None or observed < policy.minimumLiquidityShares


def _aggregate_controls(controls: list[dict[str, Any]]) -> dict[str, Any]:
    if not controls:
        return {
            "allowNewEntries": False,
            "allowPositionIncrease": False,
            "eventBlackout": False,
            "recommendedRiskCap": 0.0,
            "recommendedSizeMultiplier": 0.0,
            "minimumEdgeMultiplier": 2.0,
            "executionMode": "event_data_unavailable",
            "cooldownUntil": None,
            "eventReactionAllowed": False,
        }
    return {
        "allowNewEntries": all(bool(item["allowNewEntries"]) for item in controls),
        "allowPositionIncrease": all(bool(item["allowPositionIncrease"]) for item in controls),
        "eventBlackout": any(bool(item["eventBlackout"]) for item in controls),
        "recommendedRiskCap": min(float(item["recommendedRiskCap"]) for item in controls),
        "recommendedSizeMultiplier": min(float(item["recommendedSizeMultiplier"]) for item in controls),
        "minimumEdgeMultiplier": max(float(item["minimumEdgeMultiplier"]) for item in controls),
        "executionMode": _most_restrictive_execution_mode([str(item["executionMode"]) for item in controls]),
        "cooldownUntil": _latest_cooldown([item.get("cooldownUntil") for item in controls]),
        "eventReactionAllowed": all(bool(item["eventReactionAllowed"]) for item in controls),
    }


def _most_restrictive_execution_mode(modes: list[str]) -> str:
    priority = {
        "blackout": 100,
        "malformed_release_block": 95,
        "defensive_shock": 90,
        "feed_unavailable": 85,
        "net_edge_insufficient": 80,
        "event_spread_limit_exceeded": 75,
        "event_liquidity_insufficient": 70,
        "no_new_entries": 65,
        "reduce_only": 50,
        "normal": 0,
    }
    return max(modes or ["normal"], key=lambda mode: priority.get(mode, 60))


def _latest_cooldown(values: list[Any]) -> str | None:
    timestamps = [_raw_timestamp(value) for value in values if value]
    ready = [timestamp for timestamp in timestamps if timestamp is not None]
    if not ready:
        return None
    return max(ready).isoformat().replace("+00:00", "Z")


def _nested(payload: dict[str, Any], outer: str, inner: str) -> Any:
    value = payload.get(outer)
    return value.get(inner) if isinstance(value, dict) else None


def _slug(value: str) -> str:
    return "_".join(part for part in value.replace("-", " ").replace("/", " ").split() if part)


def _event_timestamp(event: dict[str, Any]) -> datetime | None:
    for key in ("scheduled_at", "scheduledAt", "released_at", "releasedAt", "eventTimestamp", "eventTime", "timestamp"):
        if event.get(key):
            return _timestamp(event[key])
    return None


def _observable_reaction(candles: list[dict[str, Any]], event_at: datetime, evaluated_at: datetime) -> str:
    before = _latest_at_or_before(candles, event_at)
    latest = _latest_at_or_before(candles, evaluated_at)
    if not before or not latest:
        return "none_observable"
    change = (float(latest["close"]) - float(before["close"])) / float(before["close"])
    if change > 0.001:
        return "up"
    if change < -0.001:
        return "down"
    return "flat"


def _volatility_shock(candles: list[dict[str, Any]]) -> float | None:
    if len(candles) < 12:
        return None
    ranges = [float(row["high"]) - float(row["low"]) for row in candles[-11:-1]]
    baseline = mean(ranges)
    latest = float(candles[-1]["high"]) - float(candles[-1]["low"])
    return latest / baseline if baseline > 0 else None


def _return_after(candles: list[dict[str, Any]], event_at: datetime | None, evaluated_at: datetime, seconds: int) -> float | None:
    if event_at is None:
        return None
    before = _latest_at_or_before(candles, event_at)
    after_timestamp = event_at + timedelta(seconds=seconds)
    if after_timestamp > evaluated_at:
        return None
    after = _latest_at_or_before(candles, after_timestamp)
    if not before or not after or float(before["close"]) <= 0:
        return None
    return round(((float(after["close"]) - float(before["close"])) / float(before["close"])) * 10_000, 6)


def _realized_volatility_ratio(candles: list[dict[str, Any]]) -> float | None:
    if len(candles) < 22:
        return None
    recent = _return_std(candles[-6:])
    baseline = _return_std(candles[-21:-6])
    return recent / baseline if recent is not None and baseline and baseline > 0 else None


def _return_std(candles: list[dict[str, Any]]) -> float | None:
    returns = [
        (float(candles[index]["close"]) - float(candles[index - 1]["close"])) / float(candles[index - 1]["close"])
        for index in range(1, len(candles))
        if float(candles[index - 1]["close"]) > 0
    ]
    if len(returns) < 2:
        return None
    avg = mean(returns)
    variance = mean([(value - avg) ** 2 for value in returns])
    return variance ** 0.5


def _signed_volume_delta(candles: list[dict[str, Any]]) -> float | None:
    if len(candles) < 2:
        return None
    latest = candles[-1]
    prior_volume = mean(float(row["volume"]) for row in candles[-11:-1]) if len(candles) >= 12 else float(candles[-2]["volume"])
    sign = 1.0 if float(latest["close"]) >= float(latest["open"]) else -1.0
    return sign * (float(latest["volume"]) - prior_volume)


def _directional_interpretation(event: dict[str, Any], surprise_raw: float | None) -> str:
    explicit = event.get("directional_interpretation") or event.get("directionalInterpretation")
    if explicit:
        return str(explicit)
    if surprise_raw is None:
        return "unknown"
    category = str(event.get("event_category") or event.get("eventCategory") or event.get("category") or "").lower()
    hot_is_risk_off = category in {"inflation", "jobs", "wages", "fed"}
    if surprise_raw > 0:
        return "risk_off" if hot_is_risk_off else "risk_on"
    if surprise_raw < 0:
        return "risk_on" if hot_is_risk_off else "risk_off"
    return "neutral"


def _candles(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(raw, key=lambda row: _timestamp(row["timestamp"]))


def _latest_at_or_before(candles: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any] | None:
    candidates = [row for row in candles if _timestamp(row["timestamp"]) <= timestamp]
    return max(candidates, key=lambda row: _timestamp(row["timestamp"])) if candidates else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _supplied_number(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(payload.get(key))
        if value is not None:
            return value
    return None


def _rounded(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    number = _number(value)
    return round(number, 6) if number is not None else value


def _raw_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return _timestamp(value)
    except ValueError:
        return None


def _age_ms(evaluated_at: datetime, timestamp: datetime | None) -> float | None:
    return round((evaluated_at - timestamp).total_seconds() * 1000, 3) if timestamp else None


def _clock_skew_ms(event: dict[str, Any]) -> float | None:
    provider = _raw_timestamp(event.get("provider_timestamp") or event.get("providerTimestamp"))
    received = _raw_timestamp(event.get("received_at") or event.get("receivedAt"))
    if not provider or not received:
        return None
    return round((received - provider).total_seconds() * 1000, 3)


def _empty_surprise_indicators() -> dict[str, Any]:
    return {
        "actual_minus_forecast": None,
        "percentage_surprise": None,
        "standardized_surprise_zscore": None,
        "surprise_baseline_mean": None,
        "surprise_baseline_stddev": None,
        "surprise_baseline_sample_count": None,
        "prior_revision": None,
        "directional_interpretation": "unknown",
        "cross_series_confirmation": None,
    }


def _empty_market_reaction_indicators() -> dict[str, Any]:
    return {
        "return_15s": None,
        "return_30s": None,
        "return_1m": None,
        "return_3m": None,
        "return_5m": None,
        "return_15m": None,
        "range_expansion_ratio": None,
        "realized_volatility_ratio": None,
        "volume_shock_ratio": None,
        "trade_count_shock": None,
        "signed_volume_delta": None,
        "spread_percentile": None,
        "spread_change_velocity": None,
        "quote_update_rate": None,
        "order_book_imbalance": None,
        "available_depth": None,
        "price_impact": None,
        "reversal_from_initial_impulse": None,
        "reaction_persistence": None,
    }


def _empty_latency_indicators() -> dict[str, Any]:
    return {
        "event_provider_age_ms": None,
        "market_data_age_ms": None,
        "quote_age_ms": None,
        "last_trade_age_ms": None,
        "decision_age_ms": None,
        "feature_compute_duration_ms": None,
        "routing_duration_ms": None,
        "acknowledgment_latency_ms": None,
        "clock_skew_ms": None,
        "sequence_gap_count": None,
        "dropped_message_count": None,
        "feed_health": "unknown",
    }


def _empty_economics_indicators() -> dict[str, Any]:
    return {
        "expected_spread_cost_bps": None,
        "expected_slippage_bps": None,
        "fees_bps": None,
        "market_impact_bps": None,
        "expected_total_cost_bps": None,
        "predicted_gross_edge_bps": None,
        "predicted_net_edge_bps": None,
        "edge_to_cost_ratio": None,
        "fillable_quantity": None,
        "participation_rate": None,
        "adverse_selection_risk": None,
        "required_safety_margin_bps": None,
    }


def _string_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None

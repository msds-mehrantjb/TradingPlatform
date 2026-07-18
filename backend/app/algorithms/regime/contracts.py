"""Backend-owned Regime contracts.

These contracts intentionally live in Python so the backend runtime is the
source of truth for Regime classification, decisions, orders, and backtests.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, field
from typing import Any, Literal


REGIME_ALGORITHM_ID = "regime"
REGIME_ALGORITHM_VERSION = "regime_algorithm_v3_backend_authoritative"
REGIME_SETTINGS_VERSION = "regime_base_settings_v2"
REGIME_STRATEGY_CATALOG_VERSION = "regime_strategy_catalog_v3_backend"
REGIME_PROFILE_VERSION = "regime_profile_matrix_v2_backend"

RegimeSignal = Literal["Buy", "Sell", "Hold"]
StrategyRole = Literal["directional", "confirmation", "regime_context", "safety_gate"]
MarketRegimeId = Literal[
    "strong_uptrend",
    "weak_uptrend",
    "strong_downtrend",
    "weak_downtrend",
    "range_bound",
    "sideways_range",
    "choppy_mixed",
    "opening_breakout",
    "intraday_expansion",
    "high_volatility_trend",
    "low_volatility_quiet",
    "failed_breakout_reversal",
    "gap_session",
    "event_risk",
    "liquidity_stress",
    "extreme_volatility_no_trade",
]


CANONICAL_MARKET_REGIMES: tuple[str, ...] = (
    "strong_uptrend",
    "weak_uptrend",
    "strong_downtrend",
    "weak_downtrend",
    "range_bound",
    "sideways_range",
    "choppy_mixed",
    "opening_breakout",
    "intraday_expansion",
    "high_volatility_trend",
    "low_volatility_quiet",
    "failed_breakout_reversal",
    "gap_session",
    "event_risk",
    "liquidity_stress",
    "extreme_volatility_no_trade",
)
LEGACY_REGIME_ALIASES: tuple[str, ...] = (
    "low_volatility",
    "normal_volatility",
    "high_volatility",
    "trend_continuation",
    "bullish_breakout",
    "bearish_breakout",
    "bullish_reversal_risk",
    "bearish_reversal_risk",
    "mean_reversion",
)
REGIME_OPPORTUNITY_TAGS: tuple[str, ...] = (
    "trend_continuation",
    "bullish_breakout",
    "bearish_breakout",
    "bullish_reversal_risk",
    "bearish_reversal_risk",
    "mean_reversion",
    "no_trade",
)


@dataclass(frozen=True)
class RegimeCandle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None


@dataclass(frozen=True)
class RegimeMarketSnapshot:
    symbol: str
    candles: tuple[RegimeCandle, ...]
    one_minute_candles: tuple[RegimeCandle, ...]
    five_minute_candles: tuple[RegimeCandle, ...]
    context_feeds: dict[str, Any]

    @property
    def latest(self) -> RegimeCandle:
        return self.candles[-1]


@dataclass(frozen=True)
class RegimeAxes:
    direction: str
    volatility: str
    structure: str
    liquidity: str
    session: str
    event_risk: str


@dataclass(frozen=True)
class RegimeClassification:
    raw_regime: str
    axes: RegimeAxes
    confidence: float
    features: dict[str, Any]
    evidence: dict[str, Any]
    missing_inputs: tuple[str, ...]
    no_trade_reasons: tuple[str, ...]
    timestamp: str


@dataclass(frozen=True)
class RegimeHysteresisState:
    confirmed_regime: str
    previous_regime: str | None
    candidate_regime: str | None
    candidate_confirmation_count: int
    regime_start_time: str
    transition_confidence: float
    transition_reason: str
    transition_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegimeStrategyEvaluation:
    strategy_id: str
    name: str
    family: str
    role: StrategyRole
    signal: RegimeSignal
    confidence: float
    weight: float
    eligible: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegimeDecision:
    algorithm_id: str
    algorithm_version: str
    settings_version: str
    strategy_catalog_version: str
    profile_version: str
    decision_id: str
    symbol: str
    signal: RegimeSignal
    aggregate_signal: str
    trade_allowed: bool
    trade_blockers: tuple[str, ...]
    raw_classification: RegimeClassification
    confirmed_state: RegimeHysteresisState
    strategy_outputs: tuple[RegimeStrategyEvaluation, ...]
    family_scores: dict[str, float]
    effective_settings: dict[str, Any]
    score: float
    confidence: float


@dataclass(frozen=True)
class RegimeSizingResult:
    quantity: int
    risk_dollars: float
    stop_distance: float
    stop_price: float | None
    target_price: float | None
    limiting_factor: str
    quantity_caps: tuple[dict[str, Any], ...]
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegimeOrderIntent:
    algorithm_id: str
    algorithm_version: str
    settings_version: str
    decision_id: str
    order_intent_id: str
    symbol: str
    side: RegimeSignal
    position_effect: str
    quantity: int
    entry_price: float
    stop_price: float | None
    target_price: float | None
    risk_dollars: float
    regime: str
    confidence: float


def to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {item.name: to_dict(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, tuple):
        return [to_dict(item) for item in value]
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value

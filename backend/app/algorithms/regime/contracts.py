"""Backend-owned Regime contracts.

These contracts intentionally live in Python so the backend runtime is the
source of truth for Regime classification, decisions, orders, and backtests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, field
from enum import Enum
from typing import Any, Literal


REGIME_ALGORITHM_ID = "regime"
REGIME_ALGORITHM_VERSION = "regime_algorithm_v3_backend_authoritative"
REGIME_SETTINGS_VERSION = "regime_base_settings_v2"
REGIME_STRATEGY_CATALOG_VERSION = "regime_strategy_catalog_v3_backend"
REGIME_PROFILE_VERSION = "regime_profile_matrix_v3_backend"
REGIME_DEFAULT_SHADOW_ALGORITHM_INSTANCE_ID = "regime-default"
REGIME_DEFAULT_SHADOW_ACCOUNT_ID = "default"
REGIME_DEFAULT_PAPER_ALGORITHM_INSTANCE_ID = "regime-paper-default"
REGIME_DEFAULT_LOCAL_PAPER_ALGORITHM_INSTANCE_ID = "regime-local-paper-default"
REGIME_DEFAULT_LOCAL_PAPER_ACCOUNT_ID = "regime-local-paper-account"
REGIME_UNCONFIGURED_PAPER_ACCOUNT_ID = "regime-paper-account-unconfigured"
REGIME_PAPER_ALGORITHM_INSTANCE_ID_ENV = "REGIME_PAPER_ALGORITHM_INSTANCE_ID"
REGIME_ALPACA_PAPER_ACCOUNT_ID_ENV = "REGIME_ALPACA_PAPER_ACCOUNT_ID"
REGIME_PAPER_ACCOUNT_ID_ENV = "REGIME_PAPER_ACCOUNT_ID"
GENERIC_ALPACA_PAPER_ACCOUNT_ID_ENV = "ALPACA_PAPER_ACCOUNT_ID"


class RegimeRuntimeMode(str, Enum):
    SHADOW = "shadow"
    PAPER = "paper"
    LOCAL_PAPER = "local_paper"
    BACKTEST = "backtest"
    REPLAY = "replay"


REGIME_ALLOWED_RUNTIME_MODE_VALUES: tuple[str, ...] = tuple(mode.value for mode in RegimeRuntimeMode)


def normalize_regime_runtime_mode(value: Any | None, *, default: RegimeRuntimeMode = RegimeRuntimeMode.SHADOW) -> RegimeRuntimeMode:
    if isinstance(value, RegimeRuntimeMode):
        return value
    raw = str(value if value not in {None, ""} else default.value).strip().lower()
    if raw == "live":
        raise ValueError("Regime live runtime mode is disabled and must fail closed")
    try:
        return RegimeRuntimeMode(raw)
    except ValueError as exc:
        allowed = ", ".join(REGIME_ALLOWED_RUNTIME_MODE_VALUES)
        raise ValueError(f"Unsupported Regime runtime mode '{raw}'. Allowed modes: {allowed}") from exc


def default_regime_algorithm_instance_id(runtime_mode: str | RegimeRuntimeMode | None = None) -> str:
    mode = normalize_regime_runtime_mode(runtime_mode).value if runtime_mode not in {None, ""} else RegimeRuntimeMode.SHADOW.value
    if mode == RegimeRuntimeMode.PAPER.value:
        return _clean_env(REGIME_PAPER_ALGORITHM_INSTANCE_ID_ENV) or REGIME_DEFAULT_PAPER_ALGORITHM_INSTANCE_ID
    if mode == RegimeRuntimeMode.LOCAL_PAPER.value:
        return _clean_env("REGIME_LOCAL_PAPER_ALGORITHM_INSTANCE_ID") or REGIME_DEFAULT_LOCAL_PAPER_ALGORITHM_INSTANCE_ID
    return REGIME_DEFAULT_SHADOW_ALGORITHM_INSTANCE_ID


def configured_regime_paper_account_id() -> str:
    return (
        _clean_env(REGIME_ALPACA_PAPER_ACCOUNT_ID_ENV)
        or _clean_env(REGIME_PAPER_ACCOUNT_ID_ENV)
        or _clean_env(GENERIC_ALPACA_PAPER_ACCOUNT_ID_ENV)
        or REGIME_UNCONFIGURED_PAPER_ACCOUNT_ID
    )


def default_regime_account_id(runtime_mode: str | RegimeRuntimeMode | None = None) -> str:
    mode = normalize_regime_runtime_mode(runtime_mode).value if runtime_mode not in {None, ""} else RegimeRuntimeMode.SHADOW.value
    if mode == RegimeRuntimeMode.PAPER.value:
        return configured_regime_paper_account_id()
    if mode == RegimeRuntimeMode.LOCAL_PAPER.value:
        return _clean_env("REGIME_LOCAL_PAPER_ACCOUNT_ID") or REGIME_DEFAULT_LOCAL_PAPER_ACCOUNT_ID
    return REGIME_DEFAULT_SHADOW_ACCOUNT_ID


def _clean_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None

RegimeSignal = Literal["Buy", "Sell", "Hold"]
StrategyRole = Literal["directional", "confirmation", "regime_context", "safety_gate"]
MarketRegimeId = Literal[
    "strong_uptrend",
    "weak_uptrend",
    "strong_downtrend",
    "weak_downtrend",
    "range_bound",
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
    "unknown",
]


CANONICAL_MARKET_REGIMES: tuple[str, ...] = (
    "strong_uptrend",
    "weak_uptrend",
    "strong_downtrend",
    "weak_downtrend",
    "range_bound",
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
    "unknown",
)
LEGACY_REGIME_ALIASES: tuple[str, ...] = (
    "sideways_range",
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
    fifteen_minute_candles: tuple[RegimeCandle, ...] = ()

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
    trend_strength: str = "unknown"
    data_quality: str = "unknown"


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
    candidate_start_time: str | None = None
    regime_confidence: float = 0.0
    last_transition_time: str | None = None
    bars_in_current_regime: int = 1
    state_version: int = 1


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
    strategy_version: str = "unknown"
    lifecycle_status: str = "active"
    expected_gross_edge_bps: float = 0.0
    entry_reference: dict[str, Any] | None = None
    stop_reference: dict[str, Any] | None = None
    target_reference: dict[str, Any] | None = None
    valid_until: str | None = None
    setup_id: str | None = None
    reason_codes: tuple[str, ...] = ()
    data_ready: bool = True


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

"""Authoritative Weighted Voting strategy catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_STRATEGY_VERSION
from backend.app.algorithms.weighted_voting.models import WeightedVotingStrategyFamily


WEIGHTED_VOTING_CATALOG_VERSION = WEIGHTED_VOTING_STRATEGY_VERSION
WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT = 0.25
WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT = 0.02
WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT = 0.25
WEIGHTED_VOTING_DEFAULT_ELIGIBLE_MARKET_CONDITIONS = (
    "clean",
    "mixed",
    "strategy_specific_permissions",
)


@dataclass(frozen=True)
class WeightedVotingStrategyCatalogEntry:
    strategy_id: str
    name: str
    family: WeightedVotingStrategyFamily
    module_name: str
    purpose: str
    required_data: tuple[str, ...]
    optional_data: tuple[str, ...]
    valid_session_window: str
    minimum_warmup: int
    invalid_market_conditions: tuple[str, ...]
    buy_rule: str
    sell_rule: str
    hold_rule: str
    confidence_components: tuple[str, ...]
    invalidation_condition: str
    data_quality_classification: str
    version: str
    enabled: bool = True
    baseline_weight: float = WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT
    minimum_weight: float = WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT
    maximum_weight: float = WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT
    eligible_market_conditions: tuple[str, ...] = WEIGHTED_VOTING_DEFAULT_ELIGIBLE_MARKET_CONDITIONS
    long_allowed: bool = True
    short_allowed: bool = True

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def eligible_sessions(self) -> tuple[str, ...]:
        return (self.valid_session_window,)

    @property
    def strategy_implementation_version(self) -> str:
        return self.version

    @property
    def dedicated_file(self) -> str:
        return f"backend/app/algorithms/weighted_voting/strategies/{self.module_name}.py"


@dataclass(frozen=True)
class WeightedVotingDedicatedStrategyInventoryItem:
    enabled: bool
    strategy_id: str
    name: str
    display_name: str
    family: WeightedVotingStrategyFamily
    baseline_weight: float
    minimum_weight: float
    maximum_weight: float
    required_data: tuple[str, ...]
    eligible_sessions: tuple[str, ...]
    eligible_market_conditions: tuple[str, ...]
    long_allowed: bool
    short_allowed: bool
    module_name: str
    class_name: str
    implementation_module: str
    implementation_path: str
    version: str
    strategy_implementation_version: str
    dedicated_file: str
    required_indicators: tuple[str, ...]
    required_candle_history: str
    data_readiness_checks: tuple[str, ...]
    market_condition_permissions: tuple[str, ...]
    entry_conditions: tuple[str, ...]
    buy_conditions: tuple[str, ...]
    sell_conditions: tuple[str, ...]
    hold_conditions: tuple[str, ...]
    confidence_calculation: tuple[str, ...]
    expected_return_estimate: str
    invalidation_level: str
    stop_reference: str
    target_reference: str
    reason_codes: tuple[str, ...]
    explanation: str
    performance_history: str
    state_namespace: str


WeightedVotingModuleLifecycleStatus = Literal["active", "shadow", "disabled", "unavailable", "not_data_ready", "deprecated_alias"]


@dataclass(frozen=True)
class WeightedVotingModuleStatus:
    id: str
    status: WeightedVotingModuleLifecycleStatus


@dataclass(frozen=True)
class WeightedVotingModuleInventory:
    algorithm_id: str
    catalog_version: str
    directional: tuple[WeightedVotingModuleStatus, ...]
    context: tuple[WeightedVotingModuleStatus, ...] = ()
    regime: tuple[WeightedVotingModuleStatus, ...] = ()
    safety: tuple[WeightedVotingModuleStatus, ...] = ()


WEIGHTED_VOTING_STRATEGY_CATALOG: tuple[WeightedVotingStrategyCatalogEntry, ...] = (
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S2",
        name="First Pullback After Open",
        family=WeightedVotingStrategyFamily.TREND,
        module_name="first_pullback_after_open",
        purpose="Join the first controlled pullback after an early directional impulse.",
        required_data=("1m OHLCV candles", "regular-session clock", "opening impulse", "VWAP", "recent swing high/low"),
        optional_data=("5m confirmation candles", "ATR"),
        valid_session_window="09:45-11:30 America/New_York",
        minimum_warmup=20,
        invalid_market_conditions=("no early impulse", "choppy VWAP rotation", "volume below configured minimum", "stale candles"),
        buy_rule="Buy when an uptrend impulse pulls back toward VWAP or prior support and resumes upward.",
        sell_rule="Sell when a downtrend impulse pulls back toward VWAP or prior resistance and resumes downward.",
        hold_rule="Hold when the first pullback is absent, too deep, or not followed by continuation.",
        confidence_components=("impulse strength", "pullback depth", "VWAP respect", "resumption candle", "5m alignment"),
        invalidation_condition="Invalidate if the pullback breaks the impulse origin or flips through VWAP against the setup.",
        data_quality_classification="requires clean 1m OHLCV, VWAP, and session sequencing",
        version="weighted_strategy_S2_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S5",
        name="Failed Breakout Reversal",
        family=WeightedVotingStrategyFamily.REVERSAL,
        module_name="failed_breakout_reversal",
        purpose="Reverse after a breakout attempt fails and price returns through the breakout level.",
        required_data=("1m OHLCV candles", "prior range high/low", "breakout attempt", "re-entry close", "current volume"),
        optional_data=("opening-range levels", "5m confirmation candles"),
        valid_session_window="10:00-15:30 America/New_York",
        minimum_warmup=30,
        invalid_market_conditions=("no defined range", "confirmed trend continuation", "thin volume", "stale candles"),
        buy_rule="Buy when a downside break fails and price closes back above the broken range low with reversal momentum.",
        sell_rule="Sell when an upside break fails and price closes back below the broken range high with reversal momentum.",
        hold_rule="Hold when breakout failure is unconfirmed or price remains outside the prior range.",
        confidence_components=("breakout excess", "failed-break re-entry", "volume fade", "range clarity", "5m non-confirmation of breakout"),
        invalidation_condition="Invalidate if price resumes in the breakout direction beyond the failed-break extreme.",
        data_quality_classification="requires clean 1m OHLCV and deterministic prior range levels",
        version="weighted_strategy_S5_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S6",
        name="Liquidity Sweep Reversal",
        family=WeightedVotingStrategyFamily.REVERSAL,
        module_name="liquidity_sweep_reversal",
        purpose="Reverse after a stop-run sweep of a recent swing level rejects back inside the range.",
        required_data=("1m OHLCV candles", "recent swing high/low", "sweep wick", "close back inside level", "current volume"),
        optional_data=("spread quote", "5m confirmation candles"),
        valid_session_window="09:45-15:30 America/New_York",
        minimum_warmup=25,
        invalid_market_conditions=("no recent swing level", "wide spread", "sweep candle is stale", "halted or malformed candles"),
        buy_rule="Buy when price sweeps below a recent swing low and closes back above it with rejection evidence.",
        sell_rule="Sell when price sweeps above a recent swing high and closes back below it with rejection evidence.",
        hold_rule="Hold when the sweep does not reclaim the level or rejection quality is insufficient.",
        confidence_components=("wick rejection", "level significance", "volume burst", "reclaim close", "spread quality"),
        invalidation_condition="Invalidate if price closes beyond the sweep extreme after the reclaim attempt.",
        data_quality_classification="requires clean 1m OHLCV and reliable swing-level construction",
        version="weighted_strategy_S6_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S7",
        name="Bollinger/ATR Reversion",
        family=WeightedVotingStrategyFamily.MEAN_REVERSION,
        module_name="bollinger_atr_reversion",
        purpose="Fade statistically large Bollinger/ATR extensions when volatility is not expanding directionally.",
        required_data=("1m OHLCV candles", "Bollinger bands", "ATR", "current close", "recent candle body"),
        optional_data=("VWAP", "spread quote"),
        valid_session_window="10:00-15:15 America/New_York",
        minimum_warmup=50,
        invalid_market_conditions=("volatility breakout", "band walk trend", "extreme spread", "stale candles"),
        buy_rule="Buy when price extends below the lower band by an ATR-normalized amount and starts reverting upward.",
        sell_rule="Sell when price extends above the upper band by an ATR-normalized amount and starts reverting downward.",
        hold_rule="Hold when bands are walking directionally or extension is not statistically meaningful.",
        confidence_components=("band z-score", "ATR extension", "reversal candle", "VWAP distance", "volatility stability"),
        invalidation_condition="Invalidate if price continues to close outside the band with expanding ATR.",
        data_quality_classification="requires clean 1m OHLCV plus Bollinger and ATR warm-up",
        version="weighted_strategy_S7_v1",
    ),
)


_STRATEGY_CLASS_NAMES = {
    "first_pullback_after_open": "FirstPullbackAfterOpenStrategy",
    "failed_breakout_reversal": "FailedBreakoutReversalStrategy",
    "liquidity_sweep_reversal": "LiquiditySweepReversalStrategy",
    "bollinger_atr_reversion": "BollingerAtrReversionStrategy",
}


_STRATEGY_OWNERSHIP = {
    "S2": {
        "required_indicators": ("opening_impulse", "session_vwap", "pullback_depth", "recent_swing_high_low", "trend_return"),
        "data_readiness_checks": ("minimum 20 completed regular-session candles", "opening impulse exists", "VWAP is computable", "fresh 1m candle", "valid pullback sequence"),
        "market_condition_permissions": ("regular session 09:45-11:30 America/New_York", "no choppy VWAP rotation", "volume above local minimum"),
        "entry_conditions": ("early impulse established", "first controlled pullback formed", "resumption candle confirms trend"),
        "buy_conditions": ("uptrend impulse", "pullback holds near VWAP/support", "latest candle resumes upward"),
        "sell_conditions": ("downtrend impulse", "pullback rejects near VWAP/resistance", "latest candle resumes downward"),
        "hold_conditions": ("no impulse", "pullback too deep", "resumption absent", "session too early or stale"),
        "expected_return_estimate": "Absolute opening trend return adjusted by pullback quality through directional_signal expected_return.",
        "invalidation_level": "Pullback swing low for longs and pullback swing high for shorts.",
        "stop_reference": "First-pullback swing extreme used as structural stop reference.",
        "target_reference": "Impulse continuation distance and active Weighted Voting target-R settings.",
        "reason_codes": ("weighted_voting.s2.first_pullback_buy", "weighted_voting.s2.first_pullback_sell", "weighted_voting.s2.first_pullback_hold"),
    },
    "S5": {
        "required_indicators": ("prior_range_high", "prior_range_low", "breakout_attempt", "reentry_close", "current_volume"),
        "data_readiness_checks": ("minimum 30 completed candles", "prior range is defined", "attempt candle exists", "fresh 1m candle", "volume context available"),
        "market_condition_permissions": ("regular session 10:00-15:30 America/New_York", "confirmed trend continuation blocked", "thin volume blocked"),
        "entry_conditions": ("breakout attempt beyond prior range", "close back inside failed level", "reversal momentum after failure"),
        "buy_conditions": ("downside break fails", "close back above prior range low", "bullish re-entry momentum"),
        "sell_conditions": ("upside break fails", "close back below prior range high", "bearish re-entry momentum"),
        "hold_conditions": ("failure unconfirmed", "price remains outside range", "prior range unavailable", "volume context weak"),
        "expected_return_estimate": "Failed-break depth and re-entry distance scaled through directional_signal expected_return.",
        "invalidation_level": "Failed-break extreme: previous low for longs and previous high for shorts.",
        "stop_reference": "Failed-break extreme beyond the rejected level.",
        "target_reference": "Return toward prior range midpoint/opposite boundary plus active target-R settings.",
        "reason_codes": ("weighted_voting.s5.failed_breakout_buy", "weighted_voting.s5.failed_breakout_sell", "weighted_voting.s5.failed_breakout_hold"),
    },
    "S6": {
        "required_indicators": ("recent_swing_high", "recent_swing_low", "sweep_wick", "reclaim_close", "current_volume"),
        "data_readiness_checks": ("minimum 25 completed candles", "recent swing level exists", "sweep wick is measurable", "fresh 1m candle", "reclaim close confirmed"),
        "market_condition_permissions": ("regular session 09:45-15:30 America/New_York", "wide spread blocked", "halted or malformed candle blocked"),
        "entry_conditions": ("stop-run sweep through swing level", "close reclaims level", "wick rejection quality is sufficient"),
        "buy_conditions": ("sweep below swing low", "close back above swing low", "lower wick rejection"),
        "sell_conditions": ("sweep above swing high", "close back below swing high", "upper wick rejection"),
        "hold_conditions": ("no reclaim", "wick too small", "level significance insufficient", "swing unavailable"),
        "expected_return_estimate": "Sweep depth and wick rejection quality scaled through directional_signal expected_return.",
        "invalidation_level": "Sweep candle low for longs and sweep candle high for shorts.",
        "stop_reference": "Sweep extreme beyond liquidity level.",
        "target_reference": "Range reversion from sweep level plus active Weighted Voting target-R settings.",
        "reason_codes": ("weighted_voting.s6.liquidity_sweep_buy", "weighted_voting.s6.liquidity_sweep_sell", "weighted_voting.s6.liquidity_sweep_hold"),
    },
    "S7": {
        "required_indicators": ("bollinger_upper", "bollinger_middle", "bollinger_lower", "atr", "reversal_candle"),
        "data_readiness_checks": ("minimum 50 completed candles", "Bollinger bands are available", "ATR warmup is available", "fresh 1m candle", "volatility stability is acceptable"),
        "market_condition_permissions": ("regular session 10:00-15:15 America/New_York", "volatility breakout blocked", "band-walk trend blocked", "extreme spread blocked"),
        "entry_conditions": ("statistical band extension", "ATR-normalized excess", "reversal candle confirms reversion"),
        "buy_conditions": ("close below lower band", "lower-band ATR extension", "upward reversal confirmation"),
        "sell_conditions": ("close above upper band", "upper-band ATR extension", "downward reversal confirmation"),
        "hold_conditions": ("extension not meaningful", "band-walk trend", "ATR unavailable", "reversal absent"),
        "expected_return_estimate": "Distance back toward Bollinger middle band scaled by ATR and costs.",
        "invalidation_level": "Latest candle low for long reversion and latest candle high for short reversion.",
        "stop_reference": "Reversion candle extreme outside the band.",
        "target_reference": "Bollinger middle band plus active Weighted Voting target-R settings.",
        "reason_codes": ("weighted_voting.s7.bollinger_atr_reversion_buy", "weighted_voting.s7.bollinger_atr_reversion_sell", "weighted_voting.s7.bollinger_atr_reversion_hold"),
    },
}


def weighted_voting_dedicated_strategy_inventory() -> tuple[WeightedVotingDedicatedStrategyInventoryItem, ...]:
    return tuple(
        WeightedVotingDedicatedStrategyInventoryItem(
            enabled=entry.enabled,
            strategy_id=entry.strategy_id,
            name=entry.name,
            display_name=entry.display_name,
            family=entry.family,
            baseline_weight=entry.baseline_weight,
            minimum_weight=entry.minimum_weight,
            maximum_weight=entry.maximum_weight,
            required_data=entry.required_data,
            eligible_sessions=entry.eligible_sessions,
            eligible_market_conditions=entry.eligible_market_conditions,
            long_allowed=entry.long_allowed,
            short_allowed=entry.short_allowed,
            module_name=entry.module_name,
            class_name=_STRATEGY_CLASS_NAMES[entry.module_name],
            implementation_module=f"backend.app.algorithms.weighted_voting.strategies.{entry.module_name}",
            implementation_path=entry.dedicated_file,
            version=entry.version,
            strategy_implementation_version=entry.strategy_implementation_version,
            dedicated_file=entry.dedicated_file,
            required_indicators=_STRATEGY_OWNERSHIP[entry.strategy_id]["required_indicators"],
            required_candle_history=f"{entry.minimum_warmup} completed 1-minute candles minimum",
            data_readiness_checks=_STRATEGY_OWNERSHIP[entry.strategy_id]["data_readiness_checks"],
            market_condition_permissions=_STRATEGY_OWNERSHIP[entry.strategy_id]["market_condition_permissions"],
            entry_conditions=_STRATEGY_OWNERSHIP[entry.strategy_id]["entry_conditions"],
            buy_conditions=_STRATEGY_OWNERSHIP[entry.strategy_id]["buy_conditions"],
            sell_conditions=_STRATEGY_OWNERSHIP[entry.strategy_id]["sell_conditions"],
            hold_conditions=_STRATEGY_OWNERSHIP[entry.strategy_id]["hold_conditions"],
            confidence_calculation=entry.confidence_components,
            expected_return_estimate=_STRATEGY_OWNERSHIP[entry.strategy_id]["expected_return_estimate"],
            invalidation_level=_STRATEGY_OWNERSHIP[entry.strategy_id]["invalidation_level"],
            stop_reference=_STRATEGY_OWNERSHIP[entry.strategy_id]["stop_reference"],
            target_reference=_STRATEGY_OWNERSHIP[entry.strategy_id]["target_reference"],
            reason_codes=_STRATEGY_OWNERSHIP[entry.strategy_id]["reason_codes"],
            explanation=f"{entry.name} is owned by Weighted Voting strategy {entry.strategy_id} in its dedicated module and may evolve without changing similarly named strategies in other algorithms.",
            performance_history=f"backend/app/algorithms/weighted_voting/performance_tracker.py and WeightedWeightState.performance_metrics scoped by strategy_id={entry.strategy_id}",
            state_namespace=f"weighted_voting.strategies.{entry.strategy_id}",
        )
        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG
    )


def weighted_voting_enabled_strategy_catalog() -> tuple[WeightedVotingStrategyCatalogEntry, ...]:
    return tuple(entry for entry in WEIGHTED_VOTING_STRATEGY_CATALOG if entry.enabled)


def _module_status(item: WeightedVotingDedicatedStrategyInventoryItem) -> WeightedVotingModuleStatus:
    return WeightedVotingModuleStatus(id=item.strategy_id, status="active" if item.enabled else "disabled")


def weighted_voting_module_inventory() -> WeightedVotingModuleInventory:
    return WeightedVotingModuleInventory(
        algorithm_id="weighted_voting",
        catalog_version=WEIGHTED_VOTING_CATALOG_VERSION,
        directional=tuple(_module_status(item) for item in weighted_voting_dedicated_strategy_inventory()),
    )


WEIGHTED_VOTING_MODULE_INVENTORY = weighted_voting_module_inventory()


__all__ = [
    "WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT",
    "WEIGHTED_VOTING_CATALOG_VERSION",
    "WEIGHTED_VOTING_DEFAULT_ELIGIBLE_MARKET_CONDITIONS",
    "WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT",
    "WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT",
    "WEIGHTED_VOTING_MODULE_INVENTORY",
    "WEIGHTED_VOTING_STRATEGY_CATALOG",
    "WeightedVotingDedicatedStrategyInventoryItem",
    "WeightedVotingModuleInventory",
    "WeightedVotingModuleLifecycleStatus",
    "WeightedVotingModuleStatus",
    "WeightedVotingStrategyCatalogEntry",
    "weighted_voting_dedicated_strategy_inventory",
    "weighted_voting_enabled_strategy_catalog",
    "weighted_voting_module_inventory",
]

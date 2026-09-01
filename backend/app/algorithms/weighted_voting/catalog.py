"""Authoritative Weighted Voting strategy catalog."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_STRATEGY_VERSION
from backend.app.algorithms.weighted_voting.models import WeightedVotingStrategyFamily


WEIGHTED_VOTING_CATALOG_VERSION = WEIGHTED_VOTING_STRATEGY_VERSION
# Placeholder only. Every entry's real baseline is the equal share of the vote among
# the strategies that actually vote, assigned in one place once the catalogue below is
# complete -- see WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT.
_UNASSIGNED_BASELINE_WEIGHT = 0.0
WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT = 0.02
WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT = 0.35
WeightedVotingStrategyLifecycleStatus = Literal["active", "shadow", "disabled", "not_data_ready", "retired"]
WEIGHTED_VOTING_PAIRWISE_SIGNAL_CORRELATION_NAMESPACE = "weighted_voting.performance_tracker.pairwise_signal_correlation"
WEIGHTED_VOTING_PAIRWISE_RETURN_CORRELATION_NAMESPACE = "weighted_voting.performance_tracker.pairwise_return_correlation"


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
    lifecycle: WeightedVotingStrategyLifecycleStatus
    lifecycle_reason: str
    enabled: bool = True
    baseline_weight: float = _UNASSIGNED_BASELINE_WEIGHT
    minimum_weight: float = WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT
    maximum_weight: float = WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT
    long_allowed: bool = True
    short_allowed: bool = True

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def eligible_sessions(self) -> tuple[str, ...]:
        return (self.valid_session_window,)

    @property
    def session_window_bounds(self) -> tuple[str, str]:
        """The declared window as the (start, end) pair the strategies gate on."""
        start, _, end = self.valid_session_window.split(" ", 1)[0].partition("-")
        return start, end

    @property
    def strategy_implementation_version(self) -> str:
        return self.version

    @property
    def executes(self) -> bool:
        return self.enabled and self.lifecycle in ("active", "shadow")

    @property
    def contributes_to_vote(self) -> bool:
        return self.enabled and self.lifecycle == "active"

    @property
    def shadow_records_only(self) -> bool:
        return self.lifecycle == "shadow"

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
    optional_data: tuple[str, ...]
    eligible_sessions: tuple[str, ...]
    invalid_market_conditions: tuple[str, ...]
    data_quality_classification: str
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
    lifecycle: WeightedVotingStrategyLifecycleStatus
    lifecycle_reason: str
    executes: bool
    voting_influence: float
    shadow_performance_state: str
    signal_correlation_state: str
    return_correlation_state: str


WeightedVotingModuleLifecycleStatus = WeightedVotingStrategyLifecycleStatus


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


_DECLARED_STRATEGY_CATALOG: tuple[WeightedVotingStrategyCatalogEntry, ...] = (
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S1",
        name="Opening Range Breakout",
        family=WeightedVotingStrategyFamily.BREAKOUT,
        module_name="opening_range_breakout",
        purpose="Trade the first confirmed break of the opening range while the extension away from it is still tradable.",
        required_data=("1m OHLCV candles", "opening-range high/low", "ATR", "current volume", "20-candle average volume"),
        optional_data=("5m confirmation candles", "spread quote"),
        valid_session_window="09:45-11:00 America/New_York",
        minimum_warmup=15,
        invalid_market_conditions=("opening range undefined", "volume below the configured ratio", "extension beyond the maximum opening ATR", "stale candles"),
        buy_rule="Buy when price closes above the opening-range high by the configured distance with volume confirmation and an acceptable ATR extension.",
        sell_rule="Sell when price closes below the opening-range low by the configured distance with volume confirmation and an acceptable ATR extension.",
        hold_rule="Hold when breakout distance, volume confirmation, or ATR extension is missing.",
        confidence_components=("breakout distance from the range boundary", "volume ratio against the 20-candle average", "ATR extension from the range"),
        invalidation_condition="Invalidate if price closes back inside the opening range.",
        data_quality_classification="requires clean 1m OHLCV, deterministic opening-range construction, and ATR warm-up",
        version="weighted_strategy_S1_v1",
        lifecycle="shadow",
        lifecycle_reason="Implemented and unit-tested but never registered; runs in shadow to produce the breakout-family evidence promotion requires.",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S2",
        name="First Pullback After Open",
        family=WeightedVotingStrategyFamily.TREND,
        module_name="first_pullback_after_open",
        purpose="Join the first controlled pullback after an early directional impulse.",
        required_data=("1m OHLCV candles", "regular-session clock", "opening impulse", "VWAP", "recent swing high/low"),
        optional_data=("5m confirmation candles", "ATR"),
        valid_session_window="09:45-11:30 America/New_York",
        minimum_warmup=25,
        invalid_market_conditions=("no early impulse", "choppy VWAP rotation", "volume below configured minimum", "stale candles"),
        buy_rule="Buy when an uptrend impulse pulls back toward VWAP or prior support and resumes upward.",
        sell_rule="Sell when a downtrend impulse pulls back toward VWAP or prior resistance and resumes downward.",
        hold_rule="Hold when the first pullback is absent, too deep, or not followed by continuation.",
        confidence_components=("impulse strength", "pullback depth", "VWAP respect", "resumption candle", "5m alignment"),
        invalidation_condition="Invalidate if the pullback breaks the impulse origin or flips through VWAP against the setup.",
        data_quality_classification="requires clean 1m OHLCV, VWAP, and session sequencing",
        version="weighted_strategy_S2_v1",
        lifecycle="active",
        lifecycle_reason="Initial validated active Weighted Voting strategy.",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S3",
        name="VWAP Trend Continuation",
        family=WeightedVotingStrategyFamily.TREND,
        module_name="vwap_trend_continuation",
        purpose="Continue an established VWAP-aligned trend after a pullback to VWAP or a fresh continuation close.",
        required_data=("1m OHLCV candles", "session VWAP", "8/21 moving averages", "VWAP slope", "recent six-candle window"),
        optional_data=("5m confirmation candles", "ATR"),
        valid_session_window="10:00-15:30 America/New_York",
        minimum_warmup=50,
        invalid_market_conditions=("flat VWAP slope", "moving averages not aligned", "no pullback or continuation close", "stale candles"),
        buy_rule="Buy when price holds above a rising VWAP with the fast moving average above the slow one and a VWAP touch or continuation high.",
        sell_rule="Sell when price holds below a falling VWAP with the fast moving average below the slow one and a VWAP touch or continuation low.",
        hold_rule="Hold when VWAP slope, moving-average alignment, or the continuation setup is missing.",
        confidence_components=("close slope over twelve candles", "VWAP touch versus continuation close", "distance from VWAP"),
        invalidation_condition="Invalidate if price closes back through VWAP against the trend.",
        data_quality_classification="requires clean 1m OHLCV plus VWAP and 21-period moving-average warm-up",
        version="weighted_strategy_S3_v1",
        lifecycle="shadow",
        lifecycle_reason="Implemented and unit-tested but never registered; shares the trend family with active S2, so correlation evidence is required before promotion.",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S4",
        name="VWAP Mean Reversion",
        family=WeightedVotingStrategyFamily.MEAN_REVERSION,
        module_name="vwap_mean_reversion",
        purpose="Fade a stretched distance from VWAP once price stops accelerating away and prints a reversal candle.",
        required_data=("1m OHLCV candles", "session VWAP", "ATR", "trend strength", "previous candle close"),
        optional_data=("5m confirmation candles", "spread quote"),
        valid_session_window="10:00-15:15 America/New_York",
        minimum_warmup=30,
        invalid_market_conditions=("strong trend environment", "price accelerating away from VWAP", "VWAP or ATR unavailable", "stale candles"),
        buy_rule="Buy when price sits the configured distance below VWAP, is no longer accelerating away, and prints an upward reversal candle.",
        sell_rule="Sell when price sits the configured distance above VWAP, is no longer accelerating away, and prints a downward reversal candle.",
        hold_rule="Hold when VWAP distance is insufficient, the trend is strong, or reversal confirmation is absent.",
        confidence_components=("distance from VWAP", "ATR relative to price", "reversal candle confirmation"),
        invalidation_condition="Invalidate if price resumes away from VWAP beyond the reversal candle extreme.",
        data_quality_classification="requires clean 1m OHLCV plus VWAP and ATR warm-up",
        version="weighted_strategy_S4_v1",
        lifecycle="shadow",
        lifecycle_reason="Implemented and unit-tested but never registered; shares the mean-reversion family with active S7, so correlation evidence is required before promotion.",
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
        lifecycle="active",
        lifecycle_reason="Initial validated active Weighted Voting strategy.",
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
        lifecycle="active",
        lifecycle_reason="Initial validated active Weighted Voting strategy.",
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
        lifecycle="active",
        lifecycle_reason="Initial validated active Weighted Voting strategy.",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S8",
        name="Volatility Breakout",
        family=WeightedVotingStrategyFamily.BREAKOUT,
        module_name="volatility_breakout",
        purpose="Trade a compression-to-expansion break in the part of the session that the opening-range window does not cover.",
        required_data=("1m OHLCV candles", "ATR", "20-candle compression window", "10-candle expansion window", "20-candle average volume"),
        optional_data=("5m confirmation candles", "spread quote"),
        valid_session_window="11:00-15:30 America/New_York",
        minimum_warmup=50,
        invalid_market_conditions=("compression or expansion window incomplete", "range never compressed", "no volatility expansion", "volume below the configured ratio", "stale candles"),
        buy_rule="Buy when a compressed range expands and price closes above the compression high by the configured distance with volume confirmation.",
        sell_rule="Sell when a compressed range expands and price closes below the compression low by the configured distance with volume confirmation.",
        hold_rule="Hold when compression, expansion, volume confirmation, or breakout quality is missing.",
        confidence_components=("breakout distance from the compression boundary", "expansion range", "volume ratio against the 20-candle average"),
        invalidation_condition="Invalidate if price closes back inside the compression range.",
        data_quality_classification="requires clean 1m OHLCV plus ATR warm-up and 31 completed candles for the compression and expansion windows",
        version="weighted_strategy_S8_v1",
        lifecycle="shadow",
        lifecycle_reason="Implemented and unit-tested but never registered; runs in shadow to produce the breakout-family evidence promotion requires.",
    ),
)


def _equal_voting_share(entries: tuple[WeightedVotingStrategyCatalogEntry, ...]) -> float:
    """Each voting strategy's share of the vote before performance adjusts it.

    Deriving this from the roster is what keeps a promotion coherent. The share used to
    be the literal 0.25, which is 1/4 only because four strategies voted; promoting a
    fifth would have published baselines summing to 1.25 while the weight engine used
    1/5, so the two halves of the system would have disagreed about the same promotion.
    """
    voters = tuple(entry for entry in entries if entry.contributes_to_vote)
    return 1.0 / len(voters) if voters else 0.0


WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT = _equal_voting_share(_DECLARED_STRATEGY_CATALOG)
WEIGHTED_VOTING_STRATEGY_CATALOG: tuple[WeightedVotingStrategyCatalogEntry, ...] = tuple(
    replace(entry, baseline_weight=WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT) for entry in _DECLARED_STRATEGY_CATALOG
)
# Lifecycle is the only place a strategy's voting status is stated. These follow it, so
# a promotion cannot leave the signal engine and the weight engine disagreeing.
WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS: tuple[str, ...] = tuple(
    entry.strategy_id for entry in WEIGHTED_VOTING_STRATEGY_CATALOG if entry.contributes_to_vote
)
WEIGHTED_VOTING_SHADOW_STRATEGY_IDS: tuple[str, ...] = tuple(
    entry.strategy_id for entry in WEIGHTED_VOTING_STRATEGY_CATALOG if entry.shadow_records_only
)


_STRATEGY_CLASS_NAMES = {
    "opening_range_breakout": "OpeningRangeBreakoutStrategy",
    "first_pullback_after_open": "FirstPullbackAfterOpenStrategy",
    "vwap_trend_continuation": "VwapTrendContinuationStrategy",
    "vwap_mean_reversion": "VwapMeanReversionStrategy",
    "failed_breakout_reversal": "FailedBreakoutReversalStrategy",
    "liquidity_sweep_reversal": "LiquiditySweepReversalStrategy",
    "bollinger_atr_reversion": "BollingerAtrReversionStrategy",
    "volatility_breakout": "VolatilityBreakoutStrategy",
}


_STRATEGY_OWNERSHIP = {
    "S1": {
        "required_indicators": ("opening_range_high", "opening_range_low", "atr", "current_volume", "average_volume_20"),
        "data_readiness_checks": ("minimum 15 completed regular-session candles", "opening range is constructible", "ATR warmup is available", "fresh 1m candle", "20-candle volume baseline exists"),
        "market_condition_permissions": ("regular session 09:45-11:00 America/New_York", "volume below the configured ratio blocked", "extension beyond the maximum opening ATR blocked"),
        "entry_conditions": ("close beyond the opening range by the configured distance", "volume confirms the break", "extension still within the maximum opening ATR"),
        "buy_conditions": ("close above opening-range high", "breakout distance met", "volume ratio confirms"),
        "sell_conditions": ("close below opening-range low", "breakout distance met", "volume ratio confirms"),
        "hold_conditions": ("breakout distance insufficient", "volume unconfirmed", "extension too far", "opening range unavailable"),
        "expected_return_estimate": "Breakout distance beyond the opening-range boundary scaled through directional_signal expected_return.",
        "invalidation_level": "Opening-range high for shorts and opening-range low for longs; the broken boundary itself.",
        "stop_reference": "Opposite side of the opening range boundary that was broken.",
        "target_reference": "Opening-range height projected from the break plus active Weighted Voting target-R settings.",
        "reason_codes": ("weighted_voting.s1.opening_range_breakout_buy", "weighted_voting.s1.opening_range_breakout_sell", "weighted_voting.s1.no_confirmed_opening_breakout"),
    },
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
    "S3": {
        "required_indicators": ("session_vwap", "vwap_slope", "sma_8", "sma_21", "close_slope_12"),
        "data_readiness_checks": ("minimum 50 completed candles", "VWAP is computable", "8 and 21 period moving averages are available", "fresh 1m candle", "six-candle continuation window exists"),
        "market_condition_permissions": ("regular session 10:00-15:30 America/New_York", "flat VWAP slope blocked", "unaligned moving averages blocked"),
        "entry_conditions": ("price on the trend side of VWAP", "moving averages aligned with the trend", "VWAP touch or continuation close"),
        "buy_conditions": ("close above rising VWAP", "fast moving average above slow", "VWAP touch or continuation high"),
        "sell_conditions": ("close below falling VWAP", "fast moving average below slow", "VWAP touch or continuation low"),
        "hold_conditions": ("VWAP slope too flat", "moving averages unaligned", "no touch or continuation", "VWAP or moving-average warmup missing"),
        "expected_return_estimate": "Distance from VWAP scaled by close slope through directional_signal expected_return.",
        "invalidation_level": "Session VWAP for both directions.",
        "stop_reference": "Session VWAP as the trend-side structural stop reference.",
        "target_reference": "Trend continuation distance plus active Weighted Voting target-R settings.",
        "reason_codes": ("weighted_voting.s3.vwap_trend_continuation_buy", "weighted_voting.s3.vwap_trend_continuation_sell", "weighted_voting.s3.no_vwap_continuation"),
    },
    "S4": {
        "required_indicators": ("session_vwap", "atr", "trend_strength", "vwap_distance", "previous_close"),
        "data_readiness_checks": ("minimum 30 completed candles", "VWAP is computable", "ATR warmup is available", "fresh 1m candle", "previous candle is present"),
        "market_condition_permissions": ("regular session 10:00-15:15 America/New_York", "strong trend environment blocked", "acceleration away from VWAP blocked"),
        "entry_conditions": ("distance from VWAP beyond the configured minimum", "price no longer accelerating away", "reversal candle confirms"),
        "buy_conditions": ("close below VWAP by the configured distance", "up candle", "close above the previous close"),
        "sell_conditions": ("close above VWAP by the configured distance", "down candle", "close below the previous close"),
        "hold_conditions": ("VWAP distance insufficient", "strong trend", "accelerating away from VWAP", "reversal confirmation absent"),
        "expected_return_estimate": "Distance back toward VWAP scaled by ATR through directional_signal expected_return.",
        "invalidation_level": "Reversal candle low for longs and reversal candle high for shorts.",
        "stop_reference": "Reversal candle extreme on the stretched side of VWAP.",
        "target_reference": "Session VWAP plus active Weighted Voting target-R settings.",
        "reason_codes": ("weighted_voting.s4.vwap_reversion_buy", "weighted_voting.s4.vwap_reversion_sell", "weighted_voting.s4.no_vwap_reversion"),
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
    "S8": {
        "required_indicators": ("atr", "compression_range", "expansion_range", "compression_high_low", "average_volume_20"),
        "data_readiness_checks": ("minimum 50 completed candles", "ATR warmup is available", "20-candle compression window is complete", "10-candle expansion window is complete", "fresh 1m candle"),
        "market_condition_permissions": ("regular session 11:00-15:30 America/New_York", "uncompressed range blocked", "volume below the configured ratio blocked"),
        "entry_conditions": ("prior range compressed below the configured percent", "range expanded beyond the configured percent", "close beyond the compression boundary with volume confirmation"),
        "buy_conditions": ("close above compression high by the breakout distance", "up candle", "volume ratio confirms"),
        "sell_conditions": ("close below compression low by the breakout distance", "down candle", "volume ratio confirms"),
        "hold_conditions": ("no compression", "no expansion", "volume unconfirmed", "compression or expansion window incomplete"),
        "expected_return_estimate": "Breakout distance beyond the compression boundary scaled through directional_signal expected_return.",
        "invalidation_level": "Compression high for shorts and compression low for longs; the broken boundary itself.",
        "stop_reference": "Opposite side of the compression range that was broken.",
        "target_reference": "Compression range height projected from the break plus active Weighted Voting target-R settings.",
        "reason_codes": ("weighted_voting.s8.volatility_breakout_buy", "weighted_voting.s8.volatility_breakout_sell", "weighted_voting.s8.no_volatility_expansion_breakout"),
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
            optional_data=entry.optional_data,
            eligible_sessions=entry.eligible_sessions,
            invalid_market_conditions=entry.invalid_market_conditions,
            data_quality_classification=entry.data_quality_classification,
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
            lifecycle=entry.lifecycle,
            lifecycle_reason=entry.lifecycle_reason,
            executes=entry.executes,
            voting_influence=entry.baseline_weight if entry.contributes_to_vote else 0.0,
            shadow_performance_state=(
                f"weighted_voting.strategies.{entry.strategy_id}.shadow_performance"
                if entry.shadow_records_only
                else f"weighted_voting.strategies.{entry.strategy_id}.active_performance"
            ),
            signal_correlation_state=f"{WEIGHTED_VOTING_PAIRWISE_SIGNAL_CORRELATION_NAMESPACE}.{entry.strategy_id}",
            return_correlation_state=f"{WEIGHTED_VOTING_PAIRWISE_RETURN_CORRELATION_NAMESPACE}.{entry.strategy_id}",
        )
        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG
    )


def weighted_voting_catalog_entry(strategy_id: str) -> WeightedVotingStrategyCatalogEntry:
    """The catalogue entry a strategy module reads its own limits from."""
    for entry in WEIGHTED_VOTING_STRATEGY_CATALOG:
        if entry.strategy_id == strategy_id:
            return entry
    raise KeyError(f"{strategy_id} is not a Weighted Voting catalogue strategy")


def weighted_voting_enabled_strategy_catalog() -> tuple[WeightedVotingStrategyCatalogEntry, ...]:
    return tuple(entry for entry in WEIGHTED_VOTING_STRATEGY_CATALOG if entry.executes)


def weighted_voting_active_strategy_catalog() -> tuple[WeightedVotingStrategyCatalogEntry, ...]:
    return tuple(entry for entry in WEIGHTED_VOTING_STRATEGY_CATALOG if entry.contributes_to_vote)


def _module_status(item: WeightedVotingDedicatedStrategyInventoryItem) -> WeightedVotingModuleStatus:
    return WeightedVotingModuleStatus(id=item.strategy_id, status=item.lifecycle)


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
    "WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT",
    "WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT",
    "WEIGHTED_VOTING_MODULE_INVENTORY",
    "WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS",
    "WEIGHTED_VOTING_PAIRWISE_RETURN_CORRELATION_NAMESPACE",
    "WEIGHTED_VOTING_PAIRWISE_SIGNAL_CORRELATION_NAMESPACE",
    "WEIGHTED_VOTING_SHADOW_STRATEGY_IDS",
    "WEIGHTED_VOTING_STRATEGY_CATALOG",
    "WeightedVotingDedicatedStrategyInventoryItem",
    "WeightedVotingModuleInventory",
    "WeightedVotingModuleLifecycleStatus",
    "WeightedVotingModuleStatus",
    "WeightedVotingStrategyCatalogEntry",
    "WeightedVotingStrategyLifecycleStatus",
    "weighted_voting_active_strategy_catalog",
    "weighted_voting_catalog_entry",
    "weighted_voting_dedicated_strategy_inventory",
    "weighted_voting_enabled_strategy_catalog",
    "weighted_voting_module_inventory",
]

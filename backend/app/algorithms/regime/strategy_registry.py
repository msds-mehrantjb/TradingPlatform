"""Backend-authoritative Regime strategy catalog."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeMarketSnapshot, RegimeStrategyEvaluation
from backend.app.algorithms.regime.strategies.base import RegimeStrategyDefinition, evaluate_definition
from backend.app.algorithms.regime.strategies.confirmation import adx_trend_strength, volume_confirmation
from backend.app.algorithms.regime.strategies.context import atr_volatility_regime, vwap_position
from backend.app.algorithms.regime.strategies.directional import (
    bollinger_band_mean_reversion,
    failed_breakout_reversal,
    gap_continuation_fade,
    intraday_breakout,
    liquidity_sweep_reversal,
    macd_momentum,
    market_structure,
    moving_average_trend,
    opening_range_breakout,
    rsi_mean_reversion,
    trend_pullback,
    volatility_breakout,
    vwap_mean_reversion,
    vwap_trend_continuation,
)
from backend.app.algorithms.regime.strategies.safety import (
    cash_avoid_filter,
    circuit_breaker,
    event_blackout,
    excessive_spread,
    extreme_volatility,
    halt_luld,
    insufficient_liquidity,
    missing_critical_data,
    stale_data,
    unsupported_session,
)


RegimeModuleLifecycleStatus = Literal["active", "shadow", "disabled", "unavailable", "not_data_ready", "deprecated_alias"]
REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES = frozenset({"trend", "breakout", "mean_reversion", "reversal"})
REGIME_MINIMUM_INDEPENDENT_DIRECTIONAL_FAMILIES = 2
REGIME_NO_TRADE_REGIMES = frozenset({"event_risk", "liquidity_stress", "extreme_volatility_no_trade", "unknown"})
REGIME_RANGE_REGIMES = frozenset({"range_bound", "sideways_range", "choppy_mixed", "low_volatility_quiet"})
REGIME_TREND_REGIMES = frozenset({"strong_uptrend", "weak_uptrend", "strong_downtrend", "weak_downtrend", "high_volatility_trend"})
REGIME_BREAKOUT_REGIMES = frozenset({"opening_breakout", "intraday_expansion"})
REGIME_GAP_REGIMES = frozenset({"gap_session"})
REGIME_ALL_TRADE_REGIMES = REGIME_RANGE_REGIMES | REGIME_TREND_REGIMES | REGIME_BREAKOUT_REGIMES | REGIME_GAP_REGIMES


@dataclass(frozen=True)
class RegimeModuleStatus:
    id: str
    status: RegimeModuleLifecycleStatus
    strategy_version: str
    role: str
    family: str
    data_requirements: tuple[str, ...]
    compatible_regimes: tuple[str, ...]
    lifecycle_status: RegimeModuleLifecycleStatus
    activation_evidence: tuple[str, ...]
    can_affect_orders: bool
    implementation_identity: str
    canonical_id: str


@dataclass(frozen=True)
class RegimeStrategyMetadata:
    data_requirements: tuple[str, ...]
    compatible_regimes: tuple[str, ...]
    activation_evidence: tuple[str, ...]
    can_affect_orders: bool
    implementation_identity: str


@dataclass(frozen=True)
class RegimeModuleInventory:
    algorithm_id: str
    catalog_version: str
    directional: tuple[RegimeModuleStatus, ...]
    context: tuple[RegimeModuleStatus, ...]
    regime: tuple[RegimeModuleStatus, ...]
    safety: tuple[RegimeModuleStatus, ...]


def _evaluate_adx_atr_regime_classifier(
    snapshot: RegimeMarketSnapshot,
    classification: RegimeClassification,
) -> tuple[str, float, str, dict]:
    axes = classification.axes
    return (
        "Hold",
        classification.confidence,
        f"regime.classifier.{classification.raw_regime}",
        {
            "rawRegime": classification.raw_regime,
            "trendStrength": axes.direction,
            "volatilityLevel": axes.volatility,
            "structure": axes.structure,
            "liquidity": axes.liquidity,
            "session": axes.session,
            "eventRisk": axes.event_risk,
            "evidenceAxes": ("Trend strength", "Volatility level", "Structure", "Liquidity", "Session", "Event risk"),
            "features": classification.features,
            "classifierEvidence": classification.evidence,
        },
    )


REGIME_STRATEGY_DEFINITIONS: tuple[RegimeStrategyDefinition, ...] = (
    RegimeStrategyDefinition("moving_average_trend", "Moving Average Trend", "trend", "directional", 0.09, 20, moving_average_trend.evaluate, "moving_average_trend_v1", "shadow"),
    RegimeStrategyDefinition("trend_pullback", "Trend Pullback", "trend", "directional", 0.08, 20, trend_pullback.evaluate, "trend_pullback_v2", "active"),
    RegimeStrategyDefinition("rsi_mean_reversion", "RSI Mean Reversion", "mean_reversion", "directional", 0.07, 20, rsi_mean_reversion.evaluate, "rsi_mean_reversion_v1", "shadow"),
    RegimeStrategyDefinition("bollinger_band_mean_reversion", "Bollinger Band Mean Reversion", "mean_reversion", "directional", 0.07, 20, bollinger_band_mean_reversion.evaluate, "bollinger_band_mean_reversion_v2", "active"),
    RegimeStrategyDefinition("opening_range_breakout", "Opening Range Breakout", "breakout", "directional", 0.08, 20, opening_range_breakout.evaluate, "opening_range_breakout_v2", "active"),
    RegimeStrategyDefinition("intraday_breakout", "Intraday Breakout", "breakout", "directional", 0.08, 20, intraday_breakout.evaluate, "intraday_breakout_v1", "shadow"),
    RegimeStrategyDefinition("macd_momentum", "MACD Momentum", "momentum", "directional", 0.07, 20, macd_momentum.evaluate, "macd_momentum_v1", "shadow"),
    RegimeStrategyDefinition("market_structure", "Market Structure", "structure", "directional", 0.07, 20, market_structure.evaluate, "market_structure_v1", "shadow"),
    RegimeStrategyDefinition("gap_continuation_fade", "Gap Continuation/Fade", "event", "directional", 0.06, 20, gap_continuation_fade.evaluate, "gap_continuation_fade_v2", "active"),
    RegimeStrategyDefinition("vwap_trend_continuation", "VWAP Trend Continuation", "vwap", "directional", 0.08, 20, vwap_trend_continuation.evaluate, "vwap_trend_continuation_v2", "active"),
    RegimeStrategyDefinition("vwap_mean_reversion", "VWAP Mean Reversion", "vwap", "directional", 0.08, 20, vwap_mean_reversion.evaluate, "vwap_mean_reversion_v2", "active"),
    RegimeStrategyDefinition("failed_breakout_reversal", "Failed Breakout Reversal", "reversal", "directional", 0.06, 20, failed_breakout_reversal.evaluate, "failed_breakout_reversal_v2", "active"),
    RegimeStrategyDefinition("liquidity_sweep_reversal", "Liquidity Sweep Reversal", "reversal", "directional", 0.06, 20, liquidity_sweep_reversal.evaluate, "liquidity_sweep_reversal_v1_microstructure_required", "not_data_ready"),
    RegimeStrategyDefinition("volatility_breakout", "Volatility Breakout", "breakout", "directional", 0.07, 20, volatility_breakout.evaluate, "volatility_breakout_v2", "active"),
    RegimeStrategyDefinition("volume_confirmation", "Volume Confirmation", "confirmation", "confirmation", 0.0, 20, volume_confirmation.evaluate),
    RegimeStrategyDefinition("adx_trend_strength", "ADX Trend Strength", "confirmation", "confirmation", 0.0, 20, adx_trend_strength.evaluate),
    RegimeStrategyDefinition("vwap_position", "VWAP Position", "regime_context", "regime_context", 0.0, 20, vwap_position.evaluate),
    RegimeStrategyDefinition("atr_volatility_regime", "ATR Volatility Regime", "regime_context", "regime_context", 0.0, 20, atr_volatility_regime.evaluate),
    RegimeStrategyDefinition("cash_avoid_filter", "Cash/Avoid Trading", "safety", "safety_gate", 0.0, 1, cash_avoid_filter.evaluate),
    RegimeStrategyDefinition("missing_critical_data", "Missing Critical Data", "safety", "safety_gate", 0.0, 1, missing_critical_data.evaluate),
    RegimeStrategyDefinition("stale_data", "Stale Data", "safety", "safety_gate", 0.0, 1, stale_data.evaluate),
    RegimeStrategyDefinition("extreme_volatility", "Extreme Volatility", "safety", "safety_gate", 0.0, 1, extreme_volatility.evaluate),
    RegimeStrategyDefinition("excessive_spread", "Excessive Spread", "safety", "safety_gate", 0.0, 1, excessive_spread.evaluate),
    RegimeStrategyDefinition("insufficient_liquidity", "Insufficient Liquidity", "safety", "safety_gate", 0.0, 1, insufficient_liquidity.evaluate),
    RegimeStrategyDefinition("event_blackout", "Event Blackout", "safety", "safety_gate", 0.0, 1, event_blackout.evaluate),
    RegimeStrategyDefinition("halt_luld", "Halt/LULD", "safety", "safety_gate", 0.0, 1, halt_luld.evaluate),
    RegimeStrategyDefinition("circuit_breaker", "Circuit Breaker", "safety", "safety_gate", 0.0, 1, circuit_breaker.evaluate),
    RegimeStrategyDefinition("unsupported_session", "Unsupported Session", "safety", "safety_gate", 0.0, 1, unsupported_session.evaluate),
)

REGIME_STRATEGY_ALIASES = {
    "adx_trend_strength_regime": "adx_trend_strength",
    "adx_atr_regime_classifier": "adx_trend_strength",
    "first_pullback_after_open": "trend_pullback",
    "bollinger_atr_reversion": "bollinger_band_mean_reversion",
    "failed_breakout_strategy": "failed_breakout_reversal",
    "cash_avoid_trading_filter": "cash_avoid_filter",
}

REGIME_STRATEGY_METADATA: dict[str, RegimeStrategyMetadata] = {
    "moving_average_trend": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "ema_20_50", "adx", "efficiency_ratio"),
        tuple(sorted(REGIME_TREND_REGIMES | REGIME_BREAKOUT_REGIMES)),
        ("phase7_shadow_duplicate_of_trend_pullback",),
        False,
        "directional.moving_average_trend.ema_alignment_v1",
    ),
    "trend_pullback": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "derived_5m_finalized", "derived_15m_finalized", "ema", "vwap", "atr", "adx"),
        tuple(sorted(REGIME_TREND_REGIMES)),
        ("phase7_independent_strategy_review", "focused_behavioral_tests"),
        True,
        "directional.trend_pullback.pullback_reclaim_v2",
    ),
    "rsi_mean_reversion": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "rsi", "atr", "vwap"),
        tuple(sorted(REGIME_RANGE_REGIMES)),
        ("phase7_shadow_until_independent_from_other_mean_reversion",),
        False,
        "directional.rsi_mean_reversion.rsi_recovery_v1",
    ),
    "bollinger_band_mean_reversion": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "bollinger_bands", "rsi", "macd", "atr"),
        tuple(sorted(REGIME_RANGE_REGIMES)),
        ("phase7_independent_strategy_review", "focused_behavioral_tests"),
        True,
        "directional.bollinger_band_mean_reversion.band_reentry_v2",
    ),
    "opening_range_breakout": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "opening_range", "atr", "spread", "relative_volume"),
        tuple(sorted(REGIME_BREAKOUT_REGIMES | REGIME_GAP_REGIMES)),
        ("phase7_independent_strategy_review", "focused_behavioral_tests"),
        True,
        "directional.opening_range_breakout.opening_range_acceptance_v2",
    ),
    "intraday_breakout": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "rolling_range", "compression", "relative_volume"),
        tuple(sorted(REGIME_BREAKOUT_REGIMES | REGIME_TREND_REGIMES)),
        ("phase7_shadow_duplicate_of_orb_and_volatility_breakout",),
        False,
        "directional.intraday_breakout.rolling_range_v1",
    ),
    "macd_momentum": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "macd_histogram", "adx"),
        tuple(sorted(REGIME_TREND_REGIMES | REGIME_BREAKOUT_REGIMES)),
        ("phase7_shadow_confirmation_only_until_validated",),
        False,
        "directional.macd_momentum.histogram_acceleration_v1",
    ),
    "market_structure": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "swing_structure"),
        tuple(sorted(REGIME_ALL_TRADE_REGIMES)),
        ("phase7_shadow_context_only_until_directionally_validated",),
        False,
        "directional.market_structure.swing_break_v1",
    ),
    "gap_continuation_fade": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "previous_regular_close", "premarket_levels", "opening_range", "vwap", "atr"),
        tuple(sorted(REGIME_GAP_REGIMES | REGIME_BREAKOUT_REGIMES)),
        ("phase7_independent_strategy_review", "focused_behavioral_tests"),
        True,
        "directional.gap_continuation_fade.gap_resolution_v2",
    ),
    "vwap_trend_continuation": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "vwap", "vwap_slope", "atr", "adx", "relative_volume"),
        tuple(sorted(REGIME_TREND_REGIMES | REGIME_BREAKOUT_REGIMES)),
        ("phase7_independent_strategy_review", "focused_behavioral_tests"),
        True,
        "directional.vwap_trend_continuation.vwap_reclaim_v2",
    ),
    "vwap_mean_reversion": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "vwap", "atr", "rsi", "estimated_cost_bps"),
        tuple(sorted(REGIME_RANGE_REGIMES)),
        ("phase7_independent_strategy_review", "focused_behavioral_tests"),
        True,
        "directional.vwap_mean_reversion.vwap_excursion_v2",
    ),
    "failed_breakout_reversal": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "opening_range", "rolling_reference", "premarket_levels", "vwap", "atr"),
        tuple(sorted(REGIME_RANGE_REGIMES | REGIME_BREAKOUT_REGIMES | REGIME_GAP_REGIMES)),
        ("phase7_independent_strategy_review", "focused_behavioral_tests"),
        True,
        "directional.failed_breakout_reversal.failed_acceptance_v2",
    ),
    "liquidity_sweep_reversal": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "trusted_quote_or_trade_microstructure", "liquidity_levels"),
        tuple(sorted(REGIME_RANGE_REGIMES | REGIME_BREAKOUT_REGIMES | REGIME_GAP_REGIMES)),
        ("phase7_not_data_ready_without_microstructure",),
        False,
        "directional.liquidity_sweep_reversal.microstructure_sweep_v1",
    ),
    "volatility_breakout": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "compression", "bollinger_width", "range_expansion", "relative_volume", "atr"),
        tuple(sorted(REGIME_BREAKOUT_REGIMES | {"high_volatility_trend"})),
        ("phase7_independent_strategy_review", "focused_behavioral_tests"),
        True,
        "directional.volatility_breakout.compression_expansion_v2",
    ),
    "volume_confirmation": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "relative_volume"),
        tuple(sorted(REGIME_ALL_TRADE_REGIMES)),
        ("confirmation_module_existing_tests",),
        True,
        "confirmation.volume_confirmation.relative_volume_v1",
    ),
    "adx_trend_strength": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "adx", "directional_movement"),
        tuple(sorted(REGIME_ALL_TRADE_REGIMES)),
        ("confirmation_module_existing_tests",),
        True,
        "confirmation.adx_trend_strength.directional_movement_v1",
    ),
    "vwap_position": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "vwap"),
        tuple(sorted(REGIME_ALL_TRADE_REGIMES)),
        ("context_module_existing_tests",),
        True,
        "context.vwap_position.distance_v1",
    ),
    "atr_volatility_regime": RegimeStrategyMetadata(
        ("finalized_1m_ohlcv", "atr"),
        tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)),
        ("context_module_existing_tests",),
        True,
        "context.atr_volatility_regime.atr_percent_v1",
    ),
    "cash_avoid_filter": RegimeStrategyMetadata(("market_clock",), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.cash_avoid_filter.session_v1"),
    "missing_critical_data": RegimeStrategyMetadata(("market_data_quality",), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.missing_critical_data.v1"),
    "stale_data": RegimeStrategyMetadata(("market_data_quality",), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.stale_data.v1"),
    "extreme_volatility": RegimeStrategyMetadata(("atr", "volatility_axis"), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.extreme_volatility.v1"),
    "excessive_spread": RegimeStrategyMetadata(("quote_spread",), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.excessive_spread.v1"),
    "insufficient_liquidity": RegimeStrategyMetadata(("volume", "liquidity_axis"), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.insufficient_liquidity.v1"),
    "event_blackout": RegimeStrategyMetadata(("economic_event_calendar",), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.event_blackout.v1"),
    "halt_luld": RegimeStrategyMetadata(("halt_luld_feed",), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.halt_luld.v1"),
    "circuit_breaker": RegimeStrategyMetadata(("market_wide_circuit_breaker_feed",), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.circuit_breaker.v1"),
    "unsupported_session": RegimeStrategyMetadata(("market_clock",), tuple(sorted(REGIME_ALL_TRADE_REGIMES | REGIME_NO_TRADE_REGIMES)), ("safety_gate_existing_tests",), True, "safety.unsupported_session.v1"),
}


def _focused_test_path(definition: RegimeStrategyDefinition) -> Path:
    role_folder = {
        "directional": "directional",
        "confirmation": "confirmation",
        "regime_context": "context",
        "safety_gate": "safety",
    }[definition.role]
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "backend" / "tests" / "regime" / "strategies" / role_folder / f"test_{definition.strategy_id}.py"


def resolve_regime_strategy_alias(strategy_id: str, aliases: dict[str, str] | None = None) -> str:
    alias_map = aliases if aliases is not None else REGIME_STRATEGY_ALIASES
    seen: set[str] = set()
    path: list[str] = []
    current = strategy_id
    while current in alias_map:
        if current in seen:
            cycle = " -> ".join((*path, current))
            raise RuntimeError(f"Regime strategy alias cycle detected: {cycle}")
        seen.add(current)
        path.append(current)
        current = alias_map[current]
    return current


def regime_strategy_metadata(strategy_id: str) -> RegimeStrategyMetadata:
    canonical = resolve_regime_strategy_alias(strategy_id)
    try:
        return REGIME_STRATEGY_METADATA[canonical]
    except KeyError as exc:
        raise KeyError(f"Missing Regime strategy metadata for {canonical}") from exc


def regime_strategy_can_route(definition: RegimeStrategyDefinition, regime: str, profile: dict | None = None) -> bool:
    profile = profile or {}
    if definition.role != "directional":
        return True
    if profile.get("noNewEntries") or regime in REGIME_NO_TRADE_REGIMES:
        return False
    metadata = regime_strategy_metadata(definition.strategy_id)
    if regime not in set(metadata.compatible_regimes):
        return False
    disabled = set(profile.get("disabledStrategyFamilies", ()))
    if definition.family in disabled:
        return False
    allowed = set(profile.get("allowedStrategyFamilies", ()))
    if allowed and definition.family not in allowed:
        return False
    return True


def validate_regime_strategy_registry(definitions: tuple[RegimeStrategyDefinition, ...] = REGIME_STRATEGY_DEFINITIONS) -> dict[str, Any]:
    directional = tuple(definition for definition in definitions if definition.role == "directional")
    active_directional = tuple(definition for definition in directional if definition.lifecycle_status == "active")
    if not active_directional:
        raise RuntimeError("Regime strategy registry has zero active directional strategies")
    families = {definition.family for definition in active_directional}
    if len(families) < REGIME_MINIMUM_INDEPENDENT_DIRECTIONAL_FAMILIES:
        raise RuntimeError("Regime strategy registry requires at least two active independent directional families")
    missing_initial = REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES - families
    if missing_initial:
        raise RuntimeError(f"Regime strategy registry missing required active initial families: {sorted(missing_initial)}")
    definition_ids = [definition.strategy_id for definition in definitions]
    duplicates = sorted({strategy_id for strategy_id in definition_ids if definition_ids.count(strategy_id) > 1})
    if duplicates:
        raise RuntimeError(f"Regime strategy registry has duplicate strategy IDs: {duplicates}")
    for alias in REGIME_STRATEGY_ALIASES:
        resolve_regime_strategy_alias(alias)
    aliases_as_definitions = sorted(set(REGIME_STRATEGY_ALIASES) & set(definition_ids))
    if aliases_as_definitions:
        raise RuntimeError(f"Regime strategy aliases cannot be registered as production definitions: {aliases_as_definitions}")
    unresolved_aliases = sorted(alias for alias in REGIME_STRATEGY_ALIASES if resolve_regime_strategy_alias(alias) not in set(definition_ids))
    if unresolved_aliases:
        raise RuntimeError(f"Regime strategy aliases resolve to missing canonical strategies: {unresolved_aliases}")
    missing_metadata = sorted(definition.strategy_id for definition in definitions if definition.strategy_id not in REGIME_STRATEGY_METADATA)
    if missing_metadata:
        raise RuntimeError(f"Regime strategy registry missing metadata: {missing_metadata}")
    missing_evaluators = sorted(definition.strategy_id for definition in definitions if not callable(definition.evaluator))
    if missing_evaluators:
        raise RuntimeError(f"Regime strategy registry missing evaluators: {missing_evaluators}")
    missing_tests = sorted(definition.strategy_id for definition in definitions if not _focused_test_path(definition).exists())
    if missing_tests:
        raise RuntimeError(f"Regime strategy registry missing focused tests: {missing_tests}")
    active_missing_tests = sorted(definition.strategy_id for definition in active_directional if not _focused_test_path(definition).exists())
    if active_missing_tests:
        raise RuntimeError(f"Active Regime strategies lack focused tests: {active_missing_tests}")
    active_missing_versions = sorted(definition.strategy_id for definition in active_directional if not str(definition.strategy_version or "").strip() or definition.strategy_version == "unknown")
    if active_missing_versions:
        raise RuntimeError(f"Active Regime strategies lack version metadata: {active_missing_versions}")
    active_missing_data = sorted(definition.strategy_id for definition in active_directional if not regime_strategy_metadata(definition.strategy_id).data_requirements)
    if active_missing_data:
        raise RuntimeError(f"Active Regime strategies lack data requirements: {active_missing_data}")
    active_no_trade_routes = sorted(
        definition.strategy_id
        for definition in active_directional
        if set(regime_strategy_metadata(definition.strategy_id).compatible_regimes) & set(REGIME_NO_TRADE_REGIMES)
    )
    if active_no_trade_routes:
        raise RuntimeError(f"Active Regime directional strategies route no-trade regimes: {active_no_trade_routes}")
    active_identities: dict[str, list[str]] = {}
    for definition in active_directional:
        identity = regime_strategy_metadata(definition.strategy_id).implementation_identity
        active_identities.setdefault(identity, []).append(definition.strategy_id)
    duplicate_identities = {identity: ids for identity, ids in active_identities.items() if len(ids) > 1}
    if duplicate_identities:
        raise RuntimeError(f"Active Regime strategies share canonical implementation identity: {duplicate_identities}")
    return {
        "algorithmId": "regime",
        "validated": True,
        "directionalCount": len(directional),
        "activeDirectionalCount": len(active_directional),
        "independentDirectionalFamilies": tuple(sorted(families)),
        "requiredInitialFamilies": tuple(sorted(REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES)),
        "aliasCount": len(REGIME_STRATEGY_ALIASES),
        "startupAssertion": "fail_closed",
    }


REGIME_STRATEGY_REGISTRY_VALIDATION = validate_regime_strategy_registry()


def _module_status(definition: RegimeStrategyDefinition) -> RegimeModuleStatus:
    metadata = regime_strategy_metadata(definition.strategy_id)
    status = definition.lifecycle_status if definition.lifecycle_status in {"active", "shadow", "disabled", "unavailable", "not_data_ready"} else "unavailable"
    return RegimeModuleStatus(
        id=definition.strategy_id,
        status=status,  # type: ignore[arg-type]
        strategy_version=definition.strategy_version,
        role=definition.role,
        family=definition.family,
        data_requirements=metadata.data_requirements,
        compatible_regimes=metadata.compatible_regimes,
        lifecycle_status=status,  # type: ignore[arg-type]
        activation_evidence=metadata.activation_evidence,
        can_affect_orders=metadata.can_affect_orders and definition.lifecycle_status == "active",
        implementation_identity=metadata.implementation_identity,
        canonical_id=definition.strategy_id,
    )


REGIME_MODULE_INVENTORY = RegimeModuleInventory(
    algorithm_id="regime",
    catalog_version="regime_strategy_catalog_v3_backend",
    directional=tuple(_module_status(strategy) for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "directional"),
    context=tuple(_module_status(strategy) for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "confirmation"),
    regime=tuple(_module_status(strategy) for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "regime_context"),
    safety=tuple(_module_status(strategy) for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "safety_gate"),
)


def evaluate_strategy(
    strategy_id: str,
    snapshot: RegimeMarketSnapshot,
    classification: RegimeClassification,
    strategy_settings: dict[str, Any] | None = None,
) -> RegimeStrategyEvaluation:
    canonical = REGIME_STRATEGY_ALIASES.get(strategy_id, strategy_id)
    for definition in REGIME_STRATEGY_DEFINITIONS:
        if definition.strategy_id == canonical:
            return evaluate_definition(definition, snapshot, classification, strategy_settings)
    raise KeyError(f"Unknown Regime strategy: {strategy_id}")


def regime_strategy_inventory() -> dict[str, object]:
    alias_inventory = tuple(
        {
            "id": alias,
            "status": "deprecated_alias",
            "lifecycleStatus": "deprecated_alias",
            "canonicalId": resolve_regime_strategy_alias(alias),
            "canAffectOrders": False,
        }
        for alias in sorted(REGIME_STRATEGY_ALIASES)
    )
    return {
        "algorithmId": "regime",
        "catalogVersion": "regime_strategy_catalog_v3_backend",
        "strategyCount": len(REGIME_STRATEGY_DEFINITIONS),
        "directionalCount": sum(1 for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "directional"),
        "confirmationCount": sum(1 for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "confirmation"),
        "contextCount": sum(1 for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "regime_context"),
        "safetyCount": sum(1 for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "safety_gate"),
        "registryValidation": REGIME_STRATEGY_REGISTRY_VALIDATION,
        "moduleInventory": asdict(REGIME_MODULE_INVENTORY),
        "aliasInventory": alias_inventory,
        "aliases": REGIME_STRATEGY_ALIASES,
    }


__all__ = [
    "REGIME_MODULE_INVENTORY",
    "REGIME_MINIMUM_INDEPENDENT_DIRECTIONAL_FAMILIES",
    "REGIME_NO_TRADE_REGIMES",
    "REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES",
    "REGIME_STRATEGY_ALIASES",
    "REGIME_STRATEGY_DEFINITIONS",
    "REGIME_STRATEGY_METADATA",
    "REGIME_STRATEGY_REGISTRY_VALIDATION",
    "RegimeModuleInventory",
    "RegimeModuleLifecycleStatus",
    "RegimeModuleStatus",
    "RegimeStrategyMetadata",
    "evaluate_strategy",
    "regime_strategy_inventory",
    "regime_strategy_can_route",
    "regime_strategy_metadata",
    "resolve_regime_strategy_alias",
    "validate_regime_strategy_registry",
]

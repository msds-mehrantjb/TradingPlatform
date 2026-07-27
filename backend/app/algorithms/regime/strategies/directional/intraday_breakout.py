from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import clamp01, compression, cost_bps, expected_edge_bps, range_expansion, relative_vol, rolling_reference, settings_payload


@dataclass(frozen=True)
class IntradayBreakoutSettings:
    reference_lookback: int = 24
    maximum_compression_ratio: float = 0.85
    minimum_range_expansion: float = 1.15
    minimum_relative_volume: float = 1.1
    minimum_net_edge_bps: float = 4.0


DEFAULT_SETTINGS = IntradayBreakoutSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    reference = rolling_reference(snapshot, settings.reference_lookback)
    comp = compression(snapshot)
    expansion = range_expansion(snapshot)
    rv = relative_vol(snapshot)
    close = snapshot.latest.close
    signal = "Hold"
    edge_bps = 0.0
    if reference["high"] is not None and close > reference["high"]:
        signal = "Buy"
        edge_bps = expected_edge_bps(close - reference["high"], close)
    elif reference["low"] is not None and close < reference["low"]:
        signal = "Sell"
        edge_bps = expected_edge_bps(reference["low"] - close, close)
    net_edge = edge_bps - cost_bps(snapshot, classification)
    evidence = {
        "reference": reference,
        "compressionRatio": comp,
        "rangeExpansion": expansion,
        "relativeVolume": rv,
        "edgeBps": edge_bps,
        "netEdgeBps": net_edge,
        "settings": settings_payload(settings),
    }
    missing = [name for name, value in {"priorLevel": reference["high"] or reference["low"], "compressionRatio": comp, "rangeExpansion": expansion}.items() if value is None]
    if missing:
        return "Hold", 0.0, "regime.strategy.intraday_breakout.missing_inputs", {**evidence, "missingInputReasons": tuple(missing)}
    if comp > settings.maximum_compression_ratio:
        return "Hold", 0.40, "regime.strategy.intraday_breakout.compression_required", evidence
    if signal == "Hold":
        return "Hold", 0.42, "regime.strategy.intraday_breakout.no_level_break", evidence
    if expansion < settings.minimum_range_expansion or rv < settings.minimum_relative_volume:
        return "Hold", 0.45, "regime.strategy.intraday_breakout.expansion_unconfirmed", evidence
    if net_edge < settings.minimum_net_edge_bps:
        return "Hold", 0.34, "regime.strategy.intraday_breakout.edge_after_costs_insufficient", evidence
    return signal, clamp01(0.55 + min(net_edge / 100, 0.20) + min(rv / 12, 0.12)), "regime.strategy.intraday_breakout.confirmed", evidence

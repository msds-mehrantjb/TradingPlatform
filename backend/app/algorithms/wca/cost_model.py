"""WCA adapter around a neutral transaction-cost estimate."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.wca.contracts import WcaCostEstimate, WcaEffectiveSettings, WcaMarketSnapshot, WcaSide


WCA_COST_MODEL_ADAPTER_VERSION = "wca_neutral_cost_model_adapter_v1"


@dataclass(frozen=True)
class WcaCostModelInput:
    snapshot: WcaMarketSnapshot
    effective_settings: WcaEffectiveSettings
    side: WcaSide | str
    gross_edge_per_share: float
    average_one_minute_volume: float
    expected_quantity: int = 1


def estimate_wca_round_trip_cost(model_input: WcaCostModelInput) -> WcaCostEstimate:
    """Estimate conservative round-trip cost from quote, liquidity, configured fees, and WCA slippage stats."""

    snapshot = model_input.snapshot
    quote = snapshot.quote
    if quote is None:
        price = snapshot.candles[-1].close
        spread = 0.0
        reasons = (WCA_COST_MODEL_ADAPTER_VERSION, "wca.cost_model.missing_quote")
    else:
        price = quote.ask if _side_value(model_input.side) == WcaSide.BUY.value else quote.bid
        spread = max(0.0, quote.ask - quote.bid)
        reasons = (WCA_COST_MODEL_ADAPTER_VERSION, "wca.cost_model.estimated")

    baseline = model_input.effective_settings.baseline
    participation = model_input.expected_quantity / max(1.0, model_input.average_one_minute_volume)
    impact = _bps(price, baseline.market_impact_bps) * (1.0 + max(0.0, participation))
    adverse_selection = _bps(price, baseline.adverse_selection_bps)
    replacement = _bps(price, baseline.replacement_cost_bps)
    fees = baseline.configured_fee_per_share * 2.0
    observed_slippage = baseline.observed_slippage_per_share
    conservative_round_trip = max(
        0.0,
        (spread / 2.0)
        + (spread / 2.0)
        + impact
        + adverse_selection
        + replacement
        + fees
        + observed_slippage
        + model_input.effective_settings.final_assumed_slippage_per_share,
    )
    uncertainty = model_input.effective_settings.final_uncertainty_buffer_per_share
    net_edge = model_input.gross_edge_per_share - conservative_round_trip - uncertainty
    minimum = model_input.effective_settings.final_minimum_net_edge_per_share
    allowed = net_edge > minimum
    return WcaCostEstimate(
        symbol=snapshot.symbol,
        side=model_input.side,
        entry_price=max(price, 0.000001),
        spread=spread,
        half_spread_entry=spread / 2.0,
        half_spread_exit=spread / 2.0,
        market_impact_per_share=impact,
        adverse_selection_per_share=adverse_selection,
        nonfill_or_replacement_per_share=replacement,
        fees_per_share=fees,
        observed_wca_slippage_per_share=observed_slippage,
        conservative_round_trip_cost_per_share=conservative_round_trip,
        uncertainty_buffer_per_share=uncertainty,
        conservative_gross_edge_per_share=max(0.0, model_input.gross_edge_per_share),
        conservative_net_edge_per_share=net_edge,
        minimum_required_net_edge_per_share=minimum,
        entry_allowed=allowed,
        reason_codes=(*reasons, "wca.cost_model.entry_edge_ok" if allowed else "wca.cost_model.entry_edge_not_met"),
    )


def _bps(price: float, bps: float) -> float:
    return max(0.0, price) * max(0.0, bps) / 10_000.0


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


__all__ = [
    "WCA_COST_MODEL_ADAPTER_VERSION",
    "WcaCostModelInput",
    "estimate_wca_round_trip_cost",
]

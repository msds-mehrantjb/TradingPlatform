from __future__ import annotations

from backend.app.algorithms.regime.contracts import RegimeAxes, RegimeClassification


def classification(
    *,
    raw_regime: str = "strong_uptrend",
    direction: str = "strong_up",
    volatility: str = "normal",
    structure: str = "trend",
    liquidity: str = "good",
    session: str = "midday",
    event_risk: str = "none",
    confidence: float = 0.8,
    features: dict | None = None,
    missing_inputs: tuple[str, ...] = (),
    no_trade_reasons: tuple[str, ...] = (),
) -> RegimeClassification:
    return RegimeClassification(
        raw_regime=raw_regime,
        axes=RegimeAxes(direction, volatility, structure, liquidity, session, event_risk),
        confidence=confidence,
        features={
            "bullScore": 5,
            "bearScore": 0,
            "rsi": 50,
            "vwap": 100,
            "relativeVolume": 1.0,
            "atr": 0.5,
            "atrPercent": 0.005,
            "macdHistogram": 0.1,
            **(features or {}),
        },
        evidence={"close": 101.0},
        missing_inputs=missing_inputs,
        no_trade_reasons=no_trade_reasons,
        timestamp="2026-07-18T15:30:00Z",
    )


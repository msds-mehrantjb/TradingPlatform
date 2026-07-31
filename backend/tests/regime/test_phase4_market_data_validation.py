from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.service import RegimeApplicationService


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase4_market_data"


def test_phase4_future_or_malformed_bar_fails_closed_to_hold_and_persists_timestamps() -> None:
    service = RegimeApplicationService(_repository())
    payload = _payload(count=6)
    payload["marketData"]["observedAt"] = "2026-07-23T14:35:00Z"
    payload["marketData"]["primaryCandles"][-1]["timestamp"] = "2026-07-23T14:36:00Z"
    payload["marketData"]["oneMinuteCandles"][-1]["timestamp"] = "2026-07-23T14:36:00Z"
    payload["marketData"]["primaryCandles"][-1]["high"] = 99.0
    payload["marketData"]["oneMinuteCandles"][-1]["high"] = 99.0

    result = service.evaluate(payload)

    assert result["decision"]["signal"] == "Hold"
    assert result["orderProposal"] is None
    assert "regime.market_data.future_dated_bar" in result["decision"]["trade_blockers"]
    assert "regime.market_data.ohlc_inconsistent" in result["decision"]["trade_blockers"]
    assert result["dataTimestamp"] == "2026-07-23T14:36:00Z"
    assert result["featureTimestamp"] <= result["dataTimestamp"]
    records = service.repository.read_owned_records("regime_decisions", _identity())
    assert records[0]["decision"]["dataTimestamp"] == "2026-07-23T14:36:00Z"
    assert records[0]["decision"]["featureTimestamp"] == result["featureTimestamp"]


def test_phase4_duplicate_out_of_order_and_missing_bars_are_fail_closed_reasons() -> None:
    service = RegimeApplicationService(_repository())
    payload = _payload(count=8)
    rows = payload["marketData"]["oneMinuteCandles"]
    rows[3]["timestamp"] = rows[2]["timestamp"]
    rows[5]["timestamp"], rows[6]["timestamp"] = rows[6]["timestamp"], rows[5]["timestamp"]

    result = service.evaluate(payload)
    reasons = result["marketDataValidation"]["reasonCodes"]

    assert result["decision"]["signal"] == "Hold"
    assert "regime.market_data.duplicate_timestamp" in reasons
    assert "regime.market_data.out_of_order" in reasons
    assert "regime.market_data.missing_bars" in reasons


def test_phase4_higher_timeframes_are_derived_point_in_time_from_finalized_one_minute_bars() -> None:
    supplied_future_five = [{"timestamp": "2026-07-23T15:30:00Z", "open": 1, "high": 999, "low": 1, "close": 999, "volume": 1}]
    payload = _payload(count=15)
    payload["marketData"]["fiveMinuteCandles"] = supplied_future_five
    payload["marketData"]["fifteenMinuteCandles"] = supplied_future_five

    snapshot = build_regime_market_snapshot(payload["marketData"])

    assert snapshot.five_minute_candles[-1].timestamp == payload["marketData"]["oneMinuteCandles"][14]["timestamp"]
    assert snapshot.fifteen_minute_candles[-1].timestamp == payload["marketData"]["oneMinuteCandles"][14]["timestamp"]
    assert snapshot.five_minute_candles[-1].close != 999
    assert snapshot.context_feeds["marketDataSource"]["higherTimeframePolicy"] == "derived_point_in_time_from_finalized_one_minute"


def _repository() -> RegimeRepository:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")


def _identity() -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "phase4-market-data",
        "accountId": "paper-account-phase4",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }


def _payload(*, count: int = 20) -> dict:
    candles = []
    price = 100.0
    for index in range(count):
        price += 0.03
        candles.append(
            {
                "timestamp": f"2026-07-23T14:{30 + index:02d}:00Z",
                "open": round(price - 0.02, 4),
                "high": round(price + 0.08, 4),
                "low": round(price - 0.08, 4),
                "close": round(price, 4),
                "volume": 150_000 + index,
                "finalized": True,
            }
        )
    return {
        **_identity(),
        "marketData": {
            "symbol": "SPY",
            "timeframe": "1Min",
            "primaryCandles": list(candles),
            "oneMinuteCandles": list(candles),
            "contextFeeds": {
                "quoteFreshness": {"status": "fresh", "ageMs": 500, "bid": 100.0, "ask": 100.02, "spreadBps": 2.0, "expectedFillQuantity": 100},
                "scheduledEconomicEvent": {"state": "none", "minutesUntilEvent": 999},
            },
        },
    }

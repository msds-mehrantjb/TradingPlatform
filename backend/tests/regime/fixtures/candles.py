from __future__ import annotations

from datetime import UTC, datetime, timedelta


def candles(count: int = 70, *, trend: str = "up", start: float = 100.0, volume: float = 120_000.0, hour: int = 15) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    price = start
    start_time = datetime(2026, 7, 23, hour, 0, tzinfo=UTC)
    for index in range(count):
        if trend == "up":
            price += 0.12
        elif trend == "down":
            price -= 0.12
        elif trend == "breakout" and index == count - 1:
            price += 2.0
        else:
            price += 0.01 if index % 2 == 0 else -0.01
        timestamp = (start_time + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        rows.append(
            {
                "timestamp": timestamp,
                "open": price - 0.05,
                "high": price + 0.10,
                "low": price - 0.10,
                "close": price,
                "volume": volume,
            }
        )
    return rows

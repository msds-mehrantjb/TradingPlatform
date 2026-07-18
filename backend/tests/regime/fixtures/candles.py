from __future__ import annotations


def candles(count: int = 70, *, trend: str = "up", start: float = 100.0, volume: float = 120_000.0, hour: int = 15) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    price = start
    for index in range(count):
        if trend == "up":
            price += 0.12
        elif trend == "down":
            price -= 0.12
        elif trend == "breakout" and index == count - 1:
            price += 2.0
        else:
            price += 0.01 if index % 2 == 0 else -0.01
        minute = index % 60
        rows.append(
            {
                "timestamp": f"2026-07-18T{hour:02d}:{minute:02d}:00Z",
                "open": price - 0.05,
                "high": price + 0.10,
                "low": price - 0.10,
                "close": price,
                "volume": volume,
            }
        )
    return rows


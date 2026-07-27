"""Regime chronological walk-forward validation."""

from __future__ import annotations

from typing import Any


def walk_forward_summary(
    candles: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    folds: int = 2,
    holdout_fraction: float = 0.2,
    minimum_fold_net_profit: float | None = None,
    minimum_holdout_net_profit: float | None = None,
) -> dict[str, Any]:
    if not candles:
        return {
            "accepted": False,
            "status": "INSUFFICIENT_EVIDENCE",
            "folds": 0,
            "foldResults": [],
            "walkForwardStable": False,
            "holdoutUntouched": True,
            "holdout": {"accepted": False, "status": "NOT_RUN", "untouched": True, "tradeCount": 0, "netProfit": 0.0},
            "splitIndex": 0,
            "tradeCount": len(trades),
            "reasonCodes": ["regime.backtest.walk_forward.insufficient_candles"],
        }
    folds = max(1, int(folds))
    holdout_fraction = max(0.05, min(0.5, float(holdout_fraction)))
    holdout_start = int(len(candles) * (1.0 - holdout_fraction)) if candles else 0
    train_end = max(0, holdout_start)
    fold_size = max(1, train_end // folds) if train_end else 1
    fold_results = []
    for fold in range(folds):
        start = fold * fold_size
        end = train_end if fold == folds - 1 else min(train_end, (fold + 1) * fold_size)
        fold_trades = _trades_between(candles, trades, start, end)
        net_profit = _net_profit(fold_trades)
        threshold = 0.0 if minimum_fold_net_profit is None else float(minimum_fold_net_profit)
        status = "PASS" if fold_trades and net_profit >= threshold else "FAIL" if fold_trades else "INSUFFICIENT_EVIDENCE"
        fold_results.append(
            {
                "fold": fold + 1,
                "startIndex": start,
                "endIndex": end,
                "tradeCount": len(fold_trades),
                "netProfit": net_profit,
                "minimumNetProfit": threshold,
                "status": status,
                "accepted": status == "PASS",
            }
        )
    holdout_trades = _trades_between(candles, trades, holdout_start, len(candles))
    holdout_threshold = 0.0 if minimum_holdout_net_profit is None else float(minimum_holdout_net_profit)
    holdout_status = "PASS" if holdout_trades and _net_profit(holdout_trades) >= holdout_threshold else "FAIL" if holdout_trades else "INSUFFICIENT_EVIDENCE"
    holdout = {
        "startIndex": holdout_start,
        "endIndex": len(candles),
        "tradeCount": len(holdout_trades),
        "netProfit": _net_profit(holdout_trades),
        "minimumNetProfit": holdout_threshold,
        "status": holdout_status,
        "accepted": holdout_status == "PASS",
        "untouched": True,
    }
    accepted = all(item["accepted"] for item in fold_results) and bool(holdout["accepted"])
    midpoint = len(candles) // 2
    return {
        "accepted": accepted,
        "status": "PASS" if accepted else "FAIL",
        "folds": len(fold_results),
        "foldResults": fold_results,
        "walkForwardStable": all(item["accepted"] for item in fold_results),
        "holdoutUntouched": True,
        "holdout": holdout,
        "splitIndex": midpoint,
        "tradeCount": len(trades),
        "reasonCodes": _reason_codes(fold_results, holdout),
    }


def _trades_between(candles: list[dict[str, Any]], trades: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    timestamps = [str(candle.get("timestamp") or "") for candle in candles]
    start_ts = timestamps[start] if 0 <= start < len(timestamps) else ""
    end_ts = timestamps[end - 1] if 0 <= end - 1 < len(timestamps) else "~~~~"
    return [trade for trade in trades if start_ts <= str(trade.get("exitAt") or "") <= end_ts]


def _net_profit(trades: list[dict[str, Any]]) -> float:
    return sum(float(trade.get("netPnl", trade.get("pnl") or 0.0)) for trade in trades)


def _reason_codes(fold_results: list[dict[str, Any]], holdout: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if any(item.get("status") == "INSUFFICIENT_EVIDENCE" for item in fold_results):
        reasons.append("regime.backtest.walk_forward.insufficient_fold_evidence")
    if any(item.get("status") == "FAIL" for item in fold_results):
        reasons.append("regime.backtest.walk_forward.fold_failed")
    if holdout.get("status") == "INSUFFICIENT_EVIDENCE":
        reasons.append("regime.backtest.holdout.insufficient_evidence")
    if holdout.get("status") == "FAIL":
        reasons.append("regime.backtest.holdout.failed")
    return reasons

"""Run the dedicated Voting Ensemble replay against a prepared dataset, into the served cache.

    python scripts/run_voting_ensemble_dedicated_replay.py <manifest.json> [1Min|5Min] [summary.json]

Uses main.py's own cache function, so the result lands next to the manifest under the name
`/api/voting-ensemble/backtest` looks for, keyed by the manifest's requested range. The
daily artifact job produces the same file; this is for producing it on demand. Expect about
0.35 s per one-minute bar on real data, so roughly 85 minutes for a 33-session dataset.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.app import main  # noqa: E402

SUMMARY_KEYS = (
    "engine",
    "matchesLiveAlgorithm",
    "timeframe",
    "requestedStartDate",
    "effectiveStartDate",
    "effectiveStartReason",
    "barsExcludedBeforeContext",
    "bars",
    "sessions",
    "decisionCount",
    "totalTrades",
    "winners",
    "losers",
    "netTotalPnl",
    "grossTotalPnl",
    "totalExpenses",
    "profitFactor",
    "expectancy",
    "maxDrawdownPercent",
    "startingCapital",
    "finalEquity",
    "historyWindows",
    "liveSettingsConfigurationHash",
    "dataQuality",
    "netPerformanceByFamily",
    "netPerformanceByRegime",
    "stageResultCount",
    "cachedAt",
)


def run(manifest_path: Path, timeframe: str, summary_path: Path | None) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest"] = str(manifest_path)
    start_date = str(manifest["requestedStartDate"])
    end_date = str(manifest["requestedEndDate"])
    files = manifest["files"]
    data_path = Path(files["continuous5mJsonl"] if timeframe == "5Min" else files["continuous1mJsonl"])

    started = time.perf_counter()
    result = main.cached_voting_ensemble_backtest(
        data_path=data_path,
        manifest=manifest,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
    )
    summary = {key: result.get(key) for key in SUMMARY_KEYS}
    summary["elapsedSeconds"] = round(time.perf_counter() - started, 1)
    summary["trades"] = [
        {k: trade.get(k) for k in ("side", "entryAt", "exitAt", "entryPrice", "exitPrice", "quantity", "netPnl", "exitReason", "family", "regime")}
        for trade in result.get("trades", [])
    ]
    if summary_path is not None:
        summary_path.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    manifest_arg = Path(sys.argv[1])
    timeframe_arg = sys.argv[2] if len(sys.argv) > 2 else "1Min"
    summary_arg = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    if timeframe_arg not in {"1Min", "5Min"}:
        raise SystemExit("timeframe must be 1Min or 5Min")
    outcome = run(manifest_arg, timeframe_arg, summary_arg)
    print(json.dumps({k: outcome[k] for k in ("timeframe", "effectiveStartDate", "sessions", "decisionCount", "totalTrades", "netTotalPnl", "elapsedSeconds")}))

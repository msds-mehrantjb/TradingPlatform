from __future__ import annotations

import argparse
import json
import traceback
from datetime import UTC, datetime

from .main import artifact_job_path, write_artifact_job_status
from .train_market_forecast import (
    DEFAULT_ATR_LOOKBACK_MINUTES,
    DEFAULT_EMBARGO_MINUTES,
    DEFAULT_MAX_ROWS,
    DEFAULT_MIN_STOP_PCT,
    DEFAULT_MIN_TARGET_PCT,
    DEFAULT_PROFIT_TARGET,
    DEFAULT_STOP_ATR_MULTIPLIER,
    DEFAULT_STOP_LOSS,
    DEFAULT_TARGET_ATR_MULTIPLIER,
    DEFAULT_TRAINING_COST,
    DEFAULT_WALK_FORWARD_FOLDS,
    train_market_forecast_model,
)


def load_job(job_id: str) -> dict:
    path = artifact_job_path(job_id)
    if not path.exists():
        return {"jobId": job_id}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the isolated 5-minute future market forecast model.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--feed", default="iex")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--model-kind", choices=["xgboost", "logistic"], default="xgboost")
    args = parser.parse_args()

    write_artifact_job_status(
        args.job_id,
        {
            **load_job(args.job_id),
            "status": "running",
            "startedAt": datetime.now(UTC).isoformat(),
            "message": "Future market forecast retraining is running.",
            "error": None,
        },
    )

    try:
        summary = train_market_forecast_model(
            symbol=args.symbol.upper(),
            feed=args.feed,
            start_date=args.start_date,
            end_date=args.end_date,
            profit_target=DEFAULT_PROFIT_TARGET,
            stop_loss=DEFAULT_STOP_LOSS,
            min_target_pct=DEFAULT_MIN_TARGET_PCT,
            min_stop_pct=DEFAULT_MIN_STOP_PCT,
            target_atr_multiplier=DEFAULT_TARGET_ATR_MULTIPLIER,
            stop_atr_multiplier=DEFAULT_STOP_ATR_MULTIPLIER,
            atr_lookback_minutes=DEFAULT_ATR_LOOKBACK_MINUTES,
            walk_forward_folds=DEFAULT_WALK_FORWARD_FOLDS,
            embargo_minutes=DEFAULT_EMBARGO_MINUTES,
            training_cost=DEFAULT_TRAINING_COST,
            max_rows=DEFAULT_MAX_ROWS,
            model_kind=args.model_kind,
        )
        write_artifact_job_status(
            args.job_id,
            {
                **load_job(args.job_id),
                "status": "ready",
                "completedAt": datetime.now(UTC).isoformat(),
                "artifactPath": summary.get("artifact"),
                "summary": summary,
                "message": "Future market forecast retrained.",
                "error": None,
            },
        )
        return 0
    except Exception as exc:  # pragma: no cover - worker failure capture
        write_artifact_job_status(
            args.job_id,
            {
                **load_job(args.job_id),
                "status": "error",
                "completedAt": datetime.now(UTC).isoformat(),
                "message": f"Future market forecast retraining failed: {exc}",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

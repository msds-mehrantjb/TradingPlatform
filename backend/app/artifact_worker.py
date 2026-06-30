from __future__ import annotations

import argparse
import json
import traceback
from datetime import UTC, datetime
from pathlib import Path

from .main import (
    artifact_job_path,
    regenerate_backtest_ml_artifacts,
    write_artifact_job_status,
)


def load_job(job_id: str) -> dict:
    path = artifact_job_path(job_id)
    if not path.exists():
        return {"jobId": job_id}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate durable ML/backtest artifacts.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()

    job = load_job(args.job_id)
    write_artifact_job_status(
        args.job_id,
        {
            **job,
            "status": "running",
            "startedAt": datetime.now(UTC).isoformat(),
            "message": "ML artifact regeneration is running.",
            "error": None,
        },
    )

    try:
        manifest_path = Path(args.manifest)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["manifest"] = str(manifest_path.resolve())

        def progress(stage: str) -> None:
            write_artifact_job_status(
                args.job_id,
                {
                    **load_job(args.job_id),
                    "status": "running",
                    "stage": stage,
                    "updatedAt": datetime.now(UTC).isoformat(),
                    "message": f"ML artifact regeneration is running: {stage}.",
                },
            )

        artifacts = regenerate_backtest_ml_artifacts(
            manifest=manifest,
            symbol=args.symbol.upper(),
            start_date=args.start_date,
            end_date=args.end_date,
            progress_callback=progress,
        )
        write_artifact_job_status(
            args.job_id,
            {
                **load_job(args.job_id),
                "status": "ready",
                "completedAt": datetime.now(UTC).isoformat(),
                "message": "ML artifacts regenerated.",
                "artifacts": artifacts,
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
                "message": f"ML artifact regeneration failed: {exc}",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

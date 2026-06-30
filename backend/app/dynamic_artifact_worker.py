from __future__ import annotations

import argparse
import json
import traceback
from datetime import UTC, datetime

from .main import artifact_job_path, build_dynamic_trading_artifact, write_artifact_job_status


def load_job(job_id: str) -> dict:
    path = artifact_job_path(job_id)
    if not path.exists():
        return {"jobId": job_id}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a dynamic Trading Settings artifact.")
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()

    job = load_job(args.job_id)
    write_artifact_job_status(
        args.job_id,
        {
            **job,
            "status": "running",
            "startedAt": job.get("startedAt") or datetime.now(UTC).isoformat(),
            "updatedAt": datetime.now(UTC).isoformat(),
            "message": "Dynamic Trading Settings artifact is running.",
            "error": None,
        },
    )

    try:
        payload = load_job(args.job_id).get("payload") or {}
        artifact = build_dynamic_trading_artifact(payload)
        write_artifact_job_status(
            args.job_id,
            {
                **load_job(args.job_id),
                "status": "ready",
                "completedAt": datetime.now(UTC).isoformat(),
                "updatedAt": datetime.now(UTC).isoformat(),
                "message": "Dynamic Trading Settings artifact is ready.",
                "artifactPath": artifact.get("artifactPath"),
                "artifactId": artifact.get("artifactId"),
                "configHash": artifact.get("configHash"),
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
                "updatedAt": datetime.now(UTC).isoformat(),
                "message": f"Dynamic Trading Settings artifact failed: {exc}",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Executable entry point for the standalone WCA research worker process."""

from __future__ import annotations

import argparse
import json
import time
from typing import Sequence

from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.research_repository import WcaResearchRepository
from backend.app.algorithms.wca.research_worker import WCA_RESEARCH_WORKER_REQUIRES_OS_PROCESS, WcaResearchWorker, WcaResearchWorkerSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the standalone WCA research worker process.")
    parser.add_argument("--database-url", default=None, help="SQLite database URL. Defaults to application settings.")
    parser.add_argument("--once", action="store_true", help="Run one job claim/execution pass and exit.")
    parser.add_argument("--max-iterations", type=int, default=None, help="Stop after this many polling iterations.")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Sleep interval between idle polls.")
    parser.add_argument("--lease-seconds", type=int, default=900, help="Research job lease duration.")
    parser.add_argument("--owner-id", default=None, help="Stable research-worker lease owner identifier.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = WcaSqliteRepository(args.database_url)
    worker = WcaResearchWorker(
        repository=repository,
        research_repository=WcaResearchRepository(repository),
        settings=WcaResearchWorkerSettings(lease_seconds=args.lease_seconds, poll_seconds=args.poll_seconds),
        owner_id=args.owner_id,
    )
    if args.once:
        result = worker.run_once()
        print(json.dumps({"researchWorkerProcess": WCA_RESEARCH_WORKER_REQUIRES_OS_PROCESS, "result": result}, sort_keys=True))
        return 0
    iterations = 0
    while args.max_iterations is None or iterations < args.max_iterations:
        worker.run_once()
        iterations += 1
        time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

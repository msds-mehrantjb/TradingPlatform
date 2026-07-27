"""Command-line entry point for one Meta-Strategy durable worker."""

from __future__ import annotations

import argparse
import time

from backend.app.algorithms.meta_strategy.jobs import META_STRATEGY_JOB_QUEUES, MetaStrategyJobRepository, MetaStrategyWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one durable Meta-Strategy worker queue.")
    parser.add_argument("--queue", required=True, choices=sorted(META_STRATEGY_JOB_QUEUES))
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    repository = MetaStrategyJobRepository(args.database_url)
    worker = MetaStrategyWorker(repository=repository, queue_name=args.queue, worker_id=args.worker_id)
    while True:
        worker.run_once()
        if args.once:
            return
        time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    main()

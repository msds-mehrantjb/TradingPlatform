# WCA Research Worker

Step 8 introduces a second WCA-owned background process:

```bash
python -m backend.app.algorithms.wca.research_worker_main
```

This worker is separate from FastAPI and separate from the latency-sensitive WCA runtime process. The runtime process has priority over research work; research jobs must not delay one-minute decisions, position protection, broker reconciliation, or execution outbox processing.

## Durable Job Queue

Research jobs are stored in `wca_background_jobs` with lifecycle state, lease owner, lease expiration, retry policy, progress, cancellation flag, logs, error details, and result references.

Supported job types are:

- backtests
- backtest mode suites
- walk-forward runs
- holdout runs
- historical replay
- confidence calibration
- performance-statistics updates
- weight-candidate calculation
- correlation analysis
- strategy-health analysis
- shadow comparisons
- paper-stability reports
- export and report generation

The lifecycle is:

`QUEUED -> CLAIMED -> RUNNING -> SUCCEEDED`

Terminal alternatives are:

`FAILED`, `CANCELLED`, `EXPIRED`, and `QUARANTINED`.

## API Boundary

Heavy WCA API calls enqueue research jobs and return a job ID. They do not run the expensive work synchronously in request handlers.

## Candidate Promotion

Confidence calibration and weight-candidate jobs write versioned records to `wca_research_candidates`. These records are not active runtime state. They remain `pending_promotion` until a separate promotion action validates and atomically activates them.

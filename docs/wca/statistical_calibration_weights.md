# WCA Statistical Calibration and Weights

Step 12 activates deterministic, WCA-owned statistical confidence calibration and performance-derived weight candidates without adding machine learning.

## Confidence Calibration

Runtime decisions consume immutable calibration tables from `wca_confidence_calibrations` using an as-of decision timestamp. A table is usable only when its creation time and outcome cutoff are not later than the decision timestamp and it is not stale.

Calibration tables are strategy-specific and strategy-version-specific. Direction-specific tables are produced only when the direction has enough prior samples. Regime-specific tables are produced only when the regime has enough prior samples. All bins are beta-binomial shrunk toward a conservative prior.

If no active table is available, the pipeline builds versioned conservative fallback tables instead of passing an empty table set. Raw strategy confidence remains stored separately from modifier-adjusted and calibrated confidence, and insufficient data emits explicit reason codes.

## Weight Candidates

The WCA weight engine starts from the catalog baseline and uses only prior `WcaStrategyPerformanceRecord` outcomes. It includes sample-size reliability, shrinkage toward baseline, bounded time decay, expectancy, average R, downside deviation, drawdown, loss streak, sufficiently sampled regime applicability, correlation penalties, strategy caps, family concentration caps, nonnegative weights, and final normalization to 1.00.

Runtime decisions read active weight snapshots from `wca_weight_snapshots` as of the decision timestamp. Future snapshots and future performance outcomes are ignored.

## Research Worker

`confidence_calibration` and `weight_candidate_calculation` jobs now perform real deterministic calculations in the WCA research worker. They write versioned candidate payloads to `wca_research_candidates` with `promotion_status = "pending_promotion"`.

These research jobs do not modify active runtime calibration or weight state. Promotion remains a separate action.

## Leakage Protection

Step 12 tests prove:

- future calibration tables are ignored by earlier decisions;
- future weight snapshots are ignored by earlier decisions;
- future performance outcomes do not affect candidate weights at an earlier cutoff;
- research candidates do not activate runtime state directly;
- WCA calibration and weight modules do not import sibling algorithm mutable state.

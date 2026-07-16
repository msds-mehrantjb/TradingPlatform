"""WCA automated-test coverage metadata.

This module is intentionally static. It lets CI report which WCA Step 19
coverage categories are mandatory without importing or executing trading
logic.
"""

from __future__ import annotations

from dataclasses import dataclass


WCA_STEP19_COVERAGE_VERSION = "wca_step19_comprehensive_tests_v1"


@dataclass(frozen=True)
class WcaCoverageCategory:
    category_id: str
    description: str
    mandatory_ci: bool = True


WCA_STEP19_COVERAGE_CATEGORIES: tuple[WcaCoverageCategory, ...] = (
    WcaCoverageCategory("strategy_unit", "Every WCA primary strategy has directional, hold, applicability, invalid, history, session, and boundary tests."),
    WcaCoverageCategory("modifiers", "WCA modifiers are bounded, neutral when auxiliary data is missing, and never cast independent votes."),
    WcaCoverageCategory("aggregation", "Aggregation covers normalization, caps, ties, edge, exclusions, calibration, and correlation penalties."),
    WcaCoverageCategory("dynamic_profile", "Dynamic profile covers unchanged baseline, defensive transitions, hysteresis, risk ceilings, blocks, and expiration."),
    WcaCoverageCategory("global_gate", "Global gates cover account, broker, data, duplicate, conflict, exposure, buying-power, session, and emergency cases."),
    WcaCoverageCategory("backtest_leakage", "Backtest tests prevent future bars, future outcomes, calibration leakage, same-bar fills, holdout access, and disorder."),
    WcaCoverageCategory("failure_injection", "Failure tests cover broker, market-data, persistence, retry, ordering, stale quote, and clock failures."),
    WcaCoverageCategory("ci_guardrails", "Critical risk tests are mandatory CI checks and production execution remains disabled without passing tests."),
    WcaCoverageCategory("golden_parity", "Golden parity fixtures remain available until legacy WCA removal."),
)


def wca_step19_coverage_report() -> dict[str, object]:
    return {
        "algorithm": "wca",
        "coverageVersion": WCA_STEP19_COVERAGE_VERSION,
        "categories": tuple(category.__dict__ for category in WCA_STEP19_COVERAGE_CATEGORIES),
    }


__all__ = [
    "WCA_STEP19_COVERAGE_CATEGORIES",
    "WCA_STEP19_COVERAGE_VERSION",
    "WcaCoverageCategory",
    "wca_step19_coverage_report",
]


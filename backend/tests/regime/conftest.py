from __future__ import annotations

import pytest


FORBIDDEN_OUTCOMES = {"skip", "skipif", "xfail"}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    forbidden = []
    for item in items:
        for marker in item.iter_markers():
            if marker.name in FORBIDDEN_OUTCOMES:
                forbidden.append(f"{item.nodeid} uses @{marker.name}")
    if forbidden:
        raise pytest.UsageError("Regime focused tests may not be disabled:\n" + "\n".join(sorted(forbidden)))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]):
    outcome = yield
    report = outcome.get_result()
    if report.skipped:
        reason = getattr(report, "longrepr", "skipped")
        report.outcome = "failed"
        report.longrepr = f"Regime focused tests may not be skipped or expected-failed: {reason}"

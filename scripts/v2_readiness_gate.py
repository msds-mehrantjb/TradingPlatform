from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.validation import build_v2_completion_readiness_report


def main() -> int:
    report = build_v2_completion_readiness_report()
    if "--json" in sys.argv:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0 if report.complete else 1

    print(
        "Voting Ensemble V2 readiness: "
        f"complete={report.complete} "
        f"passed={report.passedCount} "
        f"failed={report.failedCount} "
        f"version={report.readinessVersion}"
    )
    for condition in report.conditions:
        if not condition.passed:
            missing = ", ".join(condition.missingEvidence)
            print(f"FAILED {condition.id}: {condition.description}; missing: {missing}")
    return 0 if report.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())

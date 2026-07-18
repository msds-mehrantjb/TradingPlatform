from __future__ import annotations


def account(**overrides):
    return {
        "availableBuyingPower": 25_000.0,
        "remainingAlgorithmRiskDollars": 500.0,
        "globalRiskCapacityQuantity": 1_000,
        **overrides,
    }


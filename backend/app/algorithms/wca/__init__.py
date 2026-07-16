"""Weighted Confidence Aggregation backend package boundary."""

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WCA_CONTRACT_VERSION

WCA_PACKAGE_VERSION = "wca_backend_structure_v1"

__all__ = ["WCA_ALGORITHM_ID", "WCA_CONTRACT_VERSION", "WCA_PACKAGE_VERSION"]

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend" / "src" / "components" / "V2DecisionPanel.ts"
MAIN = ROOT / "frontend" / "src" / "main.ts"
STYLES = ROOT / "frontend" / "src" / "styles.css"


class FrontendV2DecisionPresentationTest(unittest.TestCase):
    def test_v2_panel_has_required_canonical_sections(self) -> None:
        source = COMPONENT.read_text(encoding="utf-8")

        for section in (
            "Directional strategies",
            "Context",
            "Regime and safety",
            "Ensemble",
            "ML",
            "Dynamic policy",
            "Global gates",
        ):
            self.assertIn(section, source)

    def test_v2_directional_and_context_catalogs_are_displayed_separately(self) -> None:
        source = COMPONENT.read_text(encoding="utf-8")

        for strategy_id in (
            "multi_timeframe_trend_alignment",
            "first_pullback_after_open",
            "vwap_trend_continuation",
            "opening_range_breakout",
            "volatility_breakout",
            "failed_breakout_reversal",
            "liquidity_sweep_reversal",
            "vwap_mean_reversion",
            "bollinger_atr_reversion",
            "gap_continuation_gap_fade",
        ):
            self.assertIn(strategy_id, source)

        for context_id in (
            "relative_strength_qqq_iwm",
            "market_breadth_momentum",
            "economic_event_context",
            "market_structure_context",
            "volume_confirmation",
            "vwap_position_context",
        ):
            self.assertIn(context_id, source)

        self.assertNotIn("10 active strategies", source)

    def test_v2_panel_makes_missing_data_and_gate_execution_state_visible(self) -> None:
        source = COMPONENT.read_text(encoding="utf-8")

        for required_text in (
            "Missing",
            "Not evaluated",
            "Hard blocker",
            "Caution",
            "Information",
            "Backend did not return this canonical directional strategy.",
        ):
            self.assertIn(required_text, source)

    def test_main_mounts_v2_panel_and_uses_backend_decision_endpoint(self) -> None:
        source = MAIN.read_text(encoding="utf-8")

        self.assertIn("evaluatePaperDecisionV2", source)
        self.assertIn("renderV2DecisionPanel", source)
        self.assertIn("ensembleV2Shell", source)
        self.assertIn("refreshEnsembleV2Decision", source)
        self.assertIn("breadthComponents: {}", source)
        self.assertIn("economicEventState", source)

    def test_v2_styles_distinguish_panel_and_gate_statuses(self) -> None:
        source = STYLES.read_text(encoding="utf-8")

        for css in (
            ".v2-decision-shell",
            ".v2-decision-panel",
            'data-status="hard-blocker"',
            'data-status="caution"',
            'data-status="information"',
            'data-status="not-evaluated"',
        ):
            self.assertIn(css, source)


if __name__ == "__main__":
    unittest.main()

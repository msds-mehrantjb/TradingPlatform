from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WCA_FEATURE_DIR = ROOT / "frontend" / "src" / "features" / "wca"


REQUIRED_WCA_FRONTEND_FILES = {
    "api.ts",
    "types.ts",
    "state.ts",
    "formatters.ts",
    "WcaPanel.ts",
    "WcaStrategyTable.ts",
    "WcaSettingsPanel.ts",
    "WcaDynamicProfilePanel.ts",
    "WcaGatePanel.ts",
    "WcaOrderPanel.ts",
    "WcaBacktestPanel.ts",
}


def _read_feature_sources() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in WCA_FEATURE_DIR.glob("*.ts")}


def test_wca_frontend_feature_files_exist() -> None:
    assert WCA_FEATURE_DIR.exists()
    assert REQUIRED_WCA_FRONTEND_FILES <= {path.name for path in WCA_FEATURE_DIR.glob("*.ts")}


def test_wca_feature_layer_does_not_reimplement_authoritative_logic() -> None:
    sources = _read_feature_sources()
    forbidden_fragments = [
        "calculateConfidenceAggregation",
        "calculateConfidenceAggregationFromMarket",
        "confidencePositionSizing",
        "confidenceHardFilters",
        "confidenceSystemWeightMultiplier",
        "forecastBuySafety",
        "forecastStopOverride",
        "localStorage.setItem",
        "/api/meta",
        "/api/weighted-voting",
    ]
    for filename, source in sources.items():
        for fragment in forbidden_fragments:
            assert fragment not in source, f"{filename} must remain display/API-only and not use {fragment}"


def test_wca_frontend_api_uses_dedicated_backend_routes() -> None:
    api_source = (WCA_FEATURE_DIR / "api.ts").read_text(encoding="utf-8")
    assert "/api/wca/status" in api_source
    assert "/api/wca/configuration" in api_source
    assert "/api/wca/config/baseline" in api_source
    assert "/api/wca/backtests" in api_source
    assert "method: \"PUT\"" in api_source
    assert "updateWcaConfiguration" in api_source


def test_wca_panels_label_readonly_effective_settings_and_gate_boundaries() -> None:
    settings_source = (WCA_FEATURE_DIR / "WcaSettingsPanel.ts").read_text(encoding="utf-8")
    dynamic_source = (WCA_FEATURE_DIR / "WcaDynamicProfilePanel.ts").read_text(encoding="utf-8")
    gate_source = (WCA_FEATURE_DIR / "WcaGatePanel.ts").read_text(encoding="utf-8")
    assert "Effective settings are read-only" in settings_source
    assert "PUT /api/wca/configuration" in settings_source
    assert "Baseline risk:" in dynamic_source
    assert "Effective risk:" in dynamic_source
    assert "WCA-local block" in gate_source
    assert "Global account block" in gate_source
    assert "ML/Meta result" in gate_source


def test_wca_presentation_panel_is_mounted_from_main_without_replacing_other_tabs() -> None:
    main_source = (ROOT / "frontend" / "src" / "main.ts").read_text(encoding="utf-8")
    assert "wcaPresentationPanel" in main_source
    assert "./features/wca/WcaPanel" in main_source
    assert "fetchWcaStatus" in main_source
    assert "fetchWcaConfiguration" in main_source
    assert "fetchWcaBaselineSettings" in main_source
    assert "algoVotingEnsemblePanel.hidden" in main_source
    assert "algoWeightedVotingPanel.hidden" in main_source
    assert "algoRegimeSelectionPanel.hidden" in main_source
    assert "algoMetaStrategyPanel.hidden" in main_source


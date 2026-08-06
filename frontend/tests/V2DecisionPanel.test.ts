import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const ROOT = join(fileURLToPath(new URL("..", import.meta.url)), "..");

function read(path: string): string {
  return readFileSync(join(ROOT, path), "utf8");
}

function walk(path: string): string[] {
  const absolute = join(ROOT, path);
  if (!existsSync(absolute)) {
    return [];
  }
  return readdirSync(absolute).flatMap((entry) => {
    const child = join(absolute, entry);
    const stat = statSync(child);
    const relative = join(path, entry).replaceAll("\\", "/");
    return stat.isDirectory() ? walk(relative) : [relative];
  });
}

test("frontend Regime panel consumes the backend authoritative runtime", () => {
  const main = read("frontend/src/main.ts");
  const api = read("frontend/src/features/regime/api.ts");

  assert.match(main, /readLatestRegimeDecisionFromBackend/);
  assert.match(api, /\/api\/regime\/runtime\/latest-decision/);
  assert.match(api, /\/api\/regime\/evaluate/);
  assert.match(api, /\/api\/regime\/backtests\/run/);
  assert.doesNotMatch(main, /\bcalculateRegimeDecision\(/);
  assert.doesNotMatch(main, /\bbuildRegimeMarketContext\(/);
  assert.doesNotMatch(main, /\bbuildRegimeTargetOrder\(/);
});

test("frontend no longer contains an executable Regime algorithm implementation", () => {
  const files = walk("frontend/src/algorithms/regime");

  assert.deepEqual(files, []);
});

test("chart candle normalization dedupes equivalent timestamp formats by parsed time", () => {
  const main = read("frontend/src/main.ts");
  const normalizeStart = main.indexOf("function normalizeCandles");
  const normalizeEnd = main.indexOf("function resetHoverState");
  const normalize = main.slice(normalizeStart, normalizeEnd);

  assert.match(normalize, /new Date\(candle\.timestamp\)\.getTime\(\)/);
  assert.match(normalize, /byTimestamp\.set\(String\(time\)/);
  assert.match(normalize, /canonicalCandleTimestamp/);
  assert.doesNotMatch(normalize, /byTimestamp\.set\(candle\.timestamp/);
});

test("Meta-Strategy readiness sections are collapsed below trading settings", () => {
  const main = read("frontend/src/main.ts");
  const tradingSettingsIndex = main.indexOf('id="metaTradingSettingsMount"');
  const runtimeToggleIndex = main.indexOf('id="metaRuntimeReadinessToggle"');
  const mlToggleIndex = main.indexOf('id="metaMlReadinessToggle"');

  assert.ok(tradingSettingsIndex > -1);
  assert.ok(runtimeToggleIndex > tradingSettingsIndex);
  assert.ok(mlToggleIndex > runtimeToggleIndex);
  assert.match(main, /metaRuntimeReadinessExpanded: persistedUiState\.metaRuntimeReadinessExpanded \?\? false/);
  assert.match(main, /metaMlReadinessExpanded: persistedUiState\.metaMlReadinessExpanded \?\? false/);
  assert.match(main, /aria-expanded="false" aria-controls="metaRuntimeReadinessPanel"/);
  assert.match(main, /aria-expanded="false" aria-controls="metaMlReadinessPanel"/);
});

test("backend Regime paths are the only authoritative Regime runtime paths", () => {
  const backendApi = read("backend/app/algorithms/regime/api.py");
  const backendBacktest = read("backend/app/algorithms/regime/backtest/engine.py");
  const backendPipeline = read("backend/app/algorithms/regime/execution_pipeline.py");
  const backendStateful = read("backend/app/algorithms/regime/stateful_core.py");
  const backendFamilyAggregation = read("backend/app/algorithms/regime/family_aggregation.py");
  const backendSizing = read("backend/app/algorithms/regime/sizing.py");
  const backendExecutionGateway = read("backend/app/algorithms/regime/execution_gateway.py");
  const backendRepository = read("backend/app/algorithms/regime/repository.py");
  const backendPersistence = read("backend/app/algorithms/regime/persistence.py");
  const frontendFiles = walk("frontend/src").filter((path) => path.endsWith(".ts") || path.endsWith(".tsx"));
  const frontendText = frontendFiles.map(read).join("\n");

  assert.match(backendApi, /backend\.app\.algorithms\.regime\.execution_pipeline/);
  assert.match(backendApi, /backend\.app\.algorithms\.regime\.backtest\.engine/);
  assert.match(backendBacktest, /execute_regime_pipeline/);
  assert.match(backendPipeline, /execute_regime_pipeline/);
  assert.match(backendStateful, /process_regime_bar/);
  assert.match(backendFamilyAggregation, /aggregate_directional_strategies/);
  assert.match(backendSizing, /calculate_regime_position_size/);
  assert.match(backendExecutionGateway, /submit_regime_outbox_record/);
  assert.match(backendRepository, /RegimeRepository/);
  assert.match(backendPersistence, /RegimeSqliteRepository/);
  assert.doesNotMatch(frontendText, /frontend\/src\/algorithms\/regime\/backtest\/engine\.ts/);
  assert.doesNotMatch(frontendText, /runRegimeBacktest\(/);
});

test("Regime frontend API rejects authoritative runtime payloads recursively", () => {
  const api = read("frontend/src/features/regime/api.ts");
  const backendApi = read("backend/app/algorithms/regime/api.py");

  for (const key of [
    "regimeClassification",
    "strategyOutputs",
    "strategyWeights",
    "familyAggregation",
    "finalTradeDecision",
    "sizingResult",
    "orderQuantity",
    "orderIntent",
    "inventorySnapshot",
    "brokerSubmission",
    "submittedOrder",
    "paperOrder",
  ]) {
    assert.match(api, new RegExp(`"${key}"`));
    assert.match(backendApi, new RegExp(`"${key}"`));
  }
  assert.match(api, /findRegimeForbiddenPayloadPath/);
  assert.match(api, /payload cannot submit authoritative \$\{forbiddenPath\}/);
  assert.match(api, /cannot submit authoritative \$\{authoritativePath\}/);
  assert.match(backendApi, /_forbidden_payload_paths/);
});

test("Regime frontend order surface is display-only and cannot submit paper orders", () => {
  const main = read("frontend/src/main.ts");
  const autoSubmitStart = main.indexOf("function maybeAutoSubmitRegimeTargetOrder");
  const autoSubmitEnd = main.indexOf("function maybeAutoSubmitAllAlgorithms");
  const buildStart = main.indexOf("function buildBackendRegimeOrderRecommendation");
  const buildEnd = main.indexOf("function regimeTargetOrderFailedGates");
  const renderStart = main.indexOf("function renderConfidenceTargetOrderSettings");
  const renderEnd = main.indexOf("function renderConfidenceTargetSettingInput");
  const autoSubmit = main.slice(autoSubmitStart, autoSubmitEnd);
  const builder = main.slice(buildStart, buildEnd);
  const renderer = main.slice(renderStart, renderEnd);

  assert.match(autoSubmit, /return;/);
  assert.match(builder, /const eligible = false/);
  assert.match(builder, /const quantity = 0/);
  assert.match(builder, /Regime UI is display-only; backend workers own order intents and paper execution/);
  assert.match(builder, /Backend Regime order intent \$\{intent\.order_intent_id\} is displayed for diagnostics only/);
  assert.match(renderer, /Displayed from backend Regime state only; order intents and paper execution are backend-controlled/);
});

test("frontend does not own Economic Event trading decisions", () => {
  const main = read("frontend/src/main.ts");
  const backendContext = read("backend/app/strategies/context/economic_event_context.py");
  const backendPolicy = read("backend/app/trading_policy/risk_caps.py");

  assert.match(backendContext, /class EconomicEventContext/);
  assert.match(backendContext, /class EconomicEventPolicy/);
  assert.match(backendPolicy, /_event_cap/);
  assert.doesNotMatch(main, /\beventModeGate\(/);
  assert.doesNotMatch(main, /directionalSignal\(context\.event/);
  assert.doesNotMatch(main, /eventSignal !== intendedSide/);
  assert.doesNotMatch(main, /eventActive \? \["Event"\]/);
  assert.match(main, /backendEventContextDisplayGate/);
  assert.match(main, /frontend displays backend context only/);
});

test("Market Forecast dashboard uses backend actionable direction labels", () => {
  const main = read("frontend/src/main.ts");
  const css = read("frontend/src/styles.css");

  assert.match(main, /function marketForecastDirectionLabel/);
  assert.match(main, /Flat \/ No edge/);
  assert.match(main, /function marketForecastDirectionImpact/);
  assert.match(main, /function marketForecastHorizonImpact/);
  assert.match(main, /forecast\.futurePricePrediction\?\.direction \?\? "flat"/);
  assert.match(main, /marketForecastDirectionLabel\(horizon\.predictedDirection\)/);
  assert.match(main, /marketForecastHorizonImpact\(horizon\)/);
  assert.match(main, /P\(flat\/no edge\)/);
  assert.match(main, /Predicted move/);
  assert.doesNotMatch(main, /P\(buy success\).*need/);
  assert.doesNotMatch(main, /New entry/);
  assert.doesNotMatch(main, /horizon\.primaryDecisionGate \|\| horizon\.advice\.entryGate/);
  const renderStrip = main.slice(main.indexOf("function renderMultiHorizonForecastStrip"), main.indexOf("function thresholdImpact"));
  assert.doesNotMatch(renderStrip, /marketForecastDirectionLabel\(horizon\.predictedChangeDollars/);
  assert.doesNotMatch(renderStrip, /marketForecastDirectionImpact\(horizon\.predictedChangeDollars/);
  assert.doesNotMatch(renderStrip, /advice\.newLongEntry/);
  assert.doesNotMatch(main, /predictedDirection:\s*isPrimary\s*\?[^;\n]*predictedChange/);
  assert.match(css, /\.market-forecast-horizon-card\[data-impact="neutral"\]/);
});

test("WCA frontend displays backend runtime-control state and fails closed", () => {
  const api = read("frontend/src/features/wca/api.ts");
  const types = read("frontend/src/features/wca/types.ts");
  const state = read("frontend/src/features/wca/state.ts");
  const panel = read("frontend/src/features/wca/WcaPanel.ts");
  const main = read("frontend/src/main.ts");
  const wcaFeatureText = walk("frontend/src/features/wca").map(read).join("\n");

  assert.match(api, /fetchWcaRuntimeControl/);
  assert.match(api, /updateWcaRuntimeControl/);
  assert.match(api, /pauseWcaRuntimeEntries/);
  assert.match(api, /resumeWcaRuntimeEntries/);
  assert.match(api, /requestWcaEmergencyRiskReduction/);
  assert.match(api, /\/api\/wca\/runtime\/control/);
  assert.match(api, /\/api\/wca\/runtime\/emergency-risk-reduction/);
  assert.match(types, /paperTradingRequested/);
  assert.match(types, /effectivePaperTradingEnabled/);
  assert.match(types, /paperAccountVerified/);
  assert.match(state, /failClosedWcaRuntimeControl/);
  assert.match(state, /wca\.frontend\.backend_unreachable_fail_closed/);
  assert.doesNotMatch(wcaFeatureText, /localStorage\.setItem/);
  for (const label of [
    "Requested Paper",
    "Effective Paper",
    "Automatic entries",
    "Rollout stage",
    "Broker paper verification",
    "Reconciliation status",
    "Runtime health",
    "Last finalized bar",
    "Last decision",
    "Last order",
    "Last fill",
    "Current WCA position",
    "Active blocking reasons",
  ]) {
    assert.match(panel, new RegExp(label));
  }
  assert.match(main, /syncWcaAutomaticPaperControl/);
  assert.match(main, /Paper Effective/);
  assert.match(main, /Paper ON but blocked/);
  assert.doesNotMatch(main, /tradeToggleButton\.textContent[^;\n]*Paper On/);
});

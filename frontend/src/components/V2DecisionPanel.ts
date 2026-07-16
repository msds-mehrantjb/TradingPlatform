import type { PaperDecisionPayload } from "../api/v2";
import type { ContextSignal, FamilyScore, GateResult, StrategySignal } from "../domain/models";

export type V2DecisionPanelState = {
  status: "idle" | "loading" | "ready" | "error";
  decision: PaperDecisionPayload | null;
  error?: string;
  updatedAt?: string;
  configurationHash?: string;
};

const DIRECTIONAL_STRATEGIES = [
  ["multi_timeframe_trend_alignment", "Multi-Timeframe Trend Alignment"],
  ["first_pullback_after_open", "First Pullback After Open"],
  ["vwap_trend_continuation", "VWAP Trend Continuation"],
  ["opening_range_breakout", "Opening Range Breakout"],
  ["volatility_breakout", "Volatility Breakout"],
  ["failed_breakout_reversal", "Failed Breakout Reversal"],
  ["liquidity_sweep_reversal", "Liquidity Sweep Reversal"],
  ["vwap_mean_reversion", "VWAP Mean Reversion"],
  ["bollinger_atr_reversion", "Bollinger/ATR Reversion"],
  ["gap_continuation_gap_fade", "Gap Continuation / Gap Fade"],
] as const;

const CONTEXT_MODULES = [
  ["relative_strength_qqq_iwm", "Relative Strength"],
  ["market_breadth_momentum", "Breadth"],
  ["economic_event_context", "Economic Event"],
  ["market_structure_context", "Market Structure"],
  ["volume_confirmation", "Volume"],
  ["vwap_position_context", "VWAP Position"],
] as const;

export function renderV2DecisionPanel(state: V2DecisionPanelState): string {
  const decision = state.decision;
  return `
    <section class="v2-decision-panel" data-status="${escapeHtml(state.status)}" aria-label="Voting Ensemble V2 backend decision">
      <div class="v2-panel-header">
        <div>
          <span>Voting Ensemble V2</span>
          <strong>${decision ? escapeHtml(decision.familyEnsemble.signal) : state.status === "loading" ? "Evaluating" : "No backend decision"}</strong>
        </div>
        <small>${escapeHtml(v2StatusLabel(state))}</small>
      </div>
      ${
        state.status === "error"
          ? `<div class="v2-empty" data-state="error">${escapeHtml(state.error || "Backend V2 decision unavailable")}</div>`
          : state.status === "idle" || !decision
            ? `<div class="v2-empty">Waiting for a backend V2 paper decision. V1 displays remain separate.</div>`
            : renderReadyDecision(decision, state)
      }
    </section>
  `;
}

function renderReadyDecision(decision: PaperDecisionPayload, state: V2DecisionPanelState) {
  return `
    <div class="v2-version-row">
      <span>V2 backend result</span>
      <b>${escapeHtml(state.configurationHash || "configuration hash unavailable")}</b>
    </div>
    ${renderDirectionalStrategies(decision.strategyOutputs)}
    ${renderContext(decision.contextOutputs)}
    ${renderRegimeAndSafety(decision)}
    ${renderEnsemble(decision)}
    ${renderMl(decision)}
    ${renderDynamicPolicy(decision)}
    ${renderGlobalGates(decision.gateResults)}
  `;
}

function renderDirectionalStrategies(strategies: StrategySignal[]) {
  const byId = new Map(strategies.map((strategy) => [strategy.strategyId, strategy]));
  return renderSection(
    "Directional strategies",
    `<div class="v2-table-wrap">
      <table class="v2-table">
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Signal</th>
            <th>Conf</th>
            <th>Family</th>
            <th>Eligible</th>
            <th>Data</th>
            <th>Regime</th>
            <th>Rel</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          ${DIRECTIONAL_STRATEGIES.map(([strategyId, name]) => renderStrategyRow(byId.get(strategyId), name)).join("")}
        </tbody>
      </table>
    </div>`,
  );
}

function renderStrategyRow(strategy: StrategySignal | undefined, fallbackName: string) {
  if (!strategy) {
    return `
      <tr data-state="missing">
        <td>${escapeHtml(fallbackName)}</td>
        <td>Missing</td>
        <td>--</td>
        <td>--</td>
        <td>Missing</td>
        <td>Missing</td>
        <td>--</td>
        <td>--</td>
        <td>Backend did not return this canonical directional strategy.</td>
      </tr>
    `;
  }
  return `
    <tr data-signal="${escapeHtml(strategy.signal.toLowerCase())}" data-ready="${String(strategy.dataReady)}">
      <td>${escapeHtml(strategy.strategyName)}</td>
      <td>${escapeHtml(strategy.signal)}</td>
      <td>${percent(strategy.confidence)}</td>
      <td>${escapeHtml(strategy.family)}</td>
      <td>${yesNo(strategy.eligible)}</td>
      <td>${readyLabel(strategy.dataReady)}</td>
      <td>${percent(strategy.regimeFit)}</td>
      <td>${percent(strategy.reliability)}</td>
      <td>${escapeHtml(firstReason(strategy.reasonCodes, strategy.explanation))}</td>
    </tr>
  `;
}

function renderContext(contexts: ContextSignal[]) {
  const byId = new Map(contexts.map((context) => [context.contextId, context]));
  return renderSection(
    "Context",
    `<div class="v2-card-grid">
      ${CONTEXT_MODULES.map(([contextId, label]) => renderContextCard(byId.get(contextId), label)).join("")}
    </div>`,
  );
}

function renderContextCard(context: ContextSignal | undefined, label: string) {
  if (!context) {
    return `
      <article class="v2-mini-card" data-state="missing">
        <span>${escapeHtml(label)}</span>
        <strong>Missing</strong>
        <small>Not returned by backend V2.</small>
      </article>
    `;
  }
  return `
    <article class="v2-mini-card" data-ready="${String(context.dataReady)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(context.signal)} / ${percent(context.confidence)}</strong>
      <small>${readyLabel(context.dataReady)} - ${escapeHtml(featureSummary(context.features) || context.explanation)}</small>
    </article>
  `;
}

function renderRegimeAndSafety(decision: PaperDecisionPayload) {
  const regime = decision.regime;
  const features = asRecord(regime.features);
  const cashGate = decision.gateResults.find((gate) => gate.gateId === "cash_avoid_trading_filter" || gate.gateName.toLowerCase().includes("cash"));
  const familyFits = [
    ["Trend", features.trendFit],
    ["Breakout", features.breakoutFit],
    ["Reversal", features.reversalFit],
    ["Mean reversion", features.meanReversionFit],
    ["Gap/session", features.gapSessionFit],
  ];
  return renderSection(
    "Regime and safety",
    `<div class="v2-card-grid">
      <article class="v2-mini-card">
        <span>Detected regime</span>
        <strong>${escapeHtml(regime.label)}</strong>
        <small>${escapeHtml(regime.volatility)} volatility - ${percent(regime.confidence)}</small>
      </article>
      <article class="v2-mini-card">
        <span>Family fits</span>
        <strong>${familyFits.map(([label, value]) => `${escapeHtml(label)} ${percentOrMissing(value)}`).join(" / ")}</strong>
        <small>Regime fit stays separate from strategy direction.</small>
      </article>
      <article class="v2-mini-card" data-status="${escapeHtml((cashGate?.status || "INFO").toLowerCase())}">
        <span>Cash filter</span>
        <strong>${escapeHtml(cashGate?.status || "Not evaluated")}</strong>
        <small>${escapeHtml(cashGate?.explanation || "No cash filter result returned.")}</small>
      </article>
      <article class="v2-mini-card" data-ready="${String(decision.familyEnsemble.dataReady)}">
        <span>Data quality</span>
        <strong>${readyLabel(decision.familyEnsemble.dataReady)}</strong>
        <small>${escapeHtml(decision.familyEnsemble.dataReady ? "Backend V2 data-ready flag is true." : "Missing or stale inputs are visible.")}</small>
      </article>
    </div>`,
  );
}

function renderEnsemble(decision: PaperDecisionPayload) {
  const ensemble = decision.familyEnsemble;
  return renderSection(
    "Ensemble",
    `<div class="v2-metric-grid">
      <span><small>Candidate signal</small><b>${escapeHtml(ensemble.signal)}</b></span>
      <span><small>Final score</small><b>${score(ensemble.finalScore)}</b></span>
      <span><small>Eligible directional</small><b>${ensemble.eligibleStrategyCount}</b></span>
      <span><small>Supporting families</small><b>${familyList(ensemble.supportingFamilies)}</b></span>
      <span><small>Opposing families</small><b>${familyList(ensemble.opposingFamilies)}</b></span>
    </div>
    <div class="v2-family-score-list">
      ${ensemble.familyScores.map(renderFamilyScore).join("") || `<span class="v2-empty-inline">No family scores returned.</span>`}
    </div>
    <p class="v2-explanation">${escapeHtml(firstReason(ensemble.reasonCodes, ensemble.explanation))}</p>`,
  );
}

function renderFamilyScore(scoreRow: FamilyScore) {
  return `
    <span>
      <small>${escapeHtml(scoreRow.family)}</small>
      <b>B ${percent(scoreRow.buyScore)} / S ${percent(scoreRow.sellScore)} / H ${percent(scoreRow.holdScore)}</b>
    </span>
  `;
}

function renderMl(decision: PaperDecisionPayload) {
  const ml = asRecord(decision.mlResult);
  return renderSection(
    "ML",
    `<div class="v2-metric-grid">
      <span><small>Operating mode</small><b>${valueLabel(ml.mode)}</b></span>
      <span><small>Success probability</small><b>${percentOrMissing(ml.successProbability ?? ml.probabilityCandidateSuccess)}</b></span>
      <span><small>Expected value</small><b>${numberOrMissing(ml.expectedValueAfterCosts ?? ml.expectedValue)}</b></span>
      <span><small>Uncertainty</small><b>${percentOrMissing(ml.uncertainty)}</b></span>
      <span><small>OOD status</small><b>${percentOrMissing(ml.outOfDistributionScore)}</b></span>
      <span><small>Model health</small><b>${modelHealthLabel(ml.modelHealth)}</b></span>
    </div>`,
  );
}

function renderDynamicPolicy(decision: PaperDecisionPayload) {
  const policy = decision.effectivePolicy;
  const order = decision.orderPlan;
  const baselineRisk = policy.accountRiskState.equity * policy.baselineSettings.baseRiskPercent;
  const effectiveRiskMultiplier = baselineRisk > 0 ? policy.riskDollars / baselineRisk : null;
  const orderFeatures = asRecord((order as unknown as { features?: unknown } | null)?.features);
  const capBreakdown = orderFeatures.capBreakdown ?? orderFeatures.caps;
  const limitingCap = valueLabel(asRecord(capBreakdown).limitingCap ?? asRecord(capBreakdown).limiting_cap ?? "Unavailable");
  return renderSection(
    "Dynamic policy",
    `<div class="v2-metric-grid">
      <span><small>Baseline risk</small><b>${currency(baselineRisk)}</b></span>
      <span><small>Effective risk multiplier</small><b>${percentOrMissing(effectiveRiskMultiplier)}</b></span>
      <span><small>Risk dollars</small><b>${currency(policy.riskDollars)}</b></span>
      <span><small>Quantity</small><b>${order?.quantity ?? policy.maxQuantity}</b></span>
      <span><small>Entry plan</small><b>${escapeHtml(order?.orderType || "NO_ORDER")}</b></span>
      <span><small>Stop</small><b>${priceOrMissing(order?.stopPrice)}</b></span>
      <span><small>Target</small><b>${priceOrMissing(order?.targetPrice)}</b></span>
      <span><small>Holding time</small><b>${order?.maximumHoldingMinutes ?? policy.baselineSettings.baseMaximumHoldingMinutes}m</b></span>
      <span><small>Limiting cap</small><b>${escapeHtml(limitingCap)}</b></span>
    </div>`,
  );
}

function renderGlobalGates(gates: GateResult[]) {
  const sorted = [...gates].sort((left, right) => left.gateName.localeCompare(right.gateName));
  return renderSection(
    "Global gates",
    `<div class="v2-gate-list">
      ${
        sorted.length
          ? sorted.map(renderGate).join("")
          : `<span class="v2-gate-chip" data-status="not-evaluated"><b>Not evaluated</b><small>No gate execution results returned.</small></span>`
      }
    </div>`,
  );
}

function renderGate(gate: GateResult) {
  const status = gate.blocksTrading ? "hard-blocker" : gate.status === "CAUTION" ? "caution" : gate.status === "INFO" ? "information" : "pass";
  return `
    <span class="v2-gate-chip" data-status="${status}">
      <b>${escapeHtml(gate.blocksTrading ? "Hard blocker" : gate.status === "CAUTION" ? "Caution" : gate.status === "INFO" ? "Information" : "Pass")}</b>
      <small>${escapeHtml(gate.gateName)} - ${escapeHtml(gate.explanation)}</small>
    </span>
  `;
}

function renderSection(title: string, body: string) {
  return `
    <section class="v2-section">
      <h3>${escapeHtml(title)}</h3>
      ${body}
    </section>
  `;
}

function v2StatusLabel(state: V2DecisionPanelState) {
  if (state.status === "ready") {
    return state.updatedAt ? `Backend V2 - ${state.updatedAt}` : "Backend V2 ready";
  }
  if (state.status === "loading") {
    return "Backend V2 evaluating";
  }
  if (state.status === "error") {
    return "Backend V2 unavailable";
  }
  return "Separate from V1";
}

function firstReason(reasonCodes: string[] | undefined, explanation: string | undefined) {
  return reasonCodes?.[0] || explanation || "No explanation returned.";
}

function featureSummary(features: Record<string, unknown>) {
  const interesting = Object.entries(features).filter(([, value]) => value !== null && value !== undefined).slice(0, 3);
  return interesting.map(([key, value]) => `${key}: ${valueLabel(value)}`).join(" / ");
}

function familyList(families: string[]) {
  return families.length ? families.join(", ") : "None";
}

function modelHealthLabel(value: unknown) {
  const health = asRecord(value);
  return valueLabel(health.status ?? health.score ?? value);
}

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === "object" ? (value as Record<string, any>) : {};
}

function readyLabel(value: boolean) {
  return value ? "Ready" : "Missing";
}

function yesNo(value: boolean) {
  return value ? "Yes" : "No";
}

function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function percentOrMissing(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? percent(value) : "Missing";
}

function numberOrMissing(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(3) : "Missing";
}

function priceOrMissing(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : "Missing";
}

function currency(value: number) {
  return Number.isFinite(value) ? `$${value.toFixed(2)}` : "Missing";
}

function score(value: number) {
  return Number.isFinite(value) ? value.toFixed(3) : "Missing";
}

function valueLabel(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "Missing";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(3);
  }
  if (typeof value === "boolean") {
    return yesNo(value);
  }
  if (Array.isArray(value)) {
    return value.length ? value.map(valueLabel).join(", ") : "None";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function escapeHtml(value: unknown) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

import { buildRawRegimeCondition, regimeConditionContextKey } from "../classifier.ts";
import { resolveRegimeHysteresisSettings } from "../config.ts";
import { buildRegimeMarketContext } from "../decision-engine.ts";
import type {
  ConfirmedRegimeState,
  MarketRegimeId,
  RegimeConditionSnapshot,
  RegimeHysteresisSettings,
  RegimeHysteresisSnapshot,
  RegimeMarketContext,
  RegimeNoTradeTag,
} from "../types.ts";
import { createConfirmedRegimeState } from "./confirmed-regime-state.ts";
import { evaluateRegimeDwellPolicy } from "./dwell-policy.ts";
import { recoverRegimeHysteresisState } from "./state-recovery.ts";
import { appendRegimeTransitionHistory } from "./transition-history.ts";

type RawCondition = ReturnType<typeof buildRawRegimeCondition>;

export function confirmedRegimeCondition(
  market: RegimeMarketContext,
  raw: RawCondition,
  previousHysteresis: RegimeHysteresisSnapshot,
  hysteresisSettings?: Partial<RegimeHysteresisSettings>,
): {
  condition: RegimeConditionSnapshot;
  confirmationCount: number;
  held: boolean;
  hysteresis: RegimeHysteresisSnapshot;
  state: ConfirmedRegimeState;
} {
  const settings = resolveRegimeHysteresisSettings(hysteresisSettings);
  const recovered = recoverRegimeHysteresisState(previousHysteresis, raw.contextKey);
  const previous = recovered.previous;
  const rawRegime = raw.rawRegime;
  const unknownRegimeCount = isUnknownLikeRawCondition(raw) ? recovered.unknownRegimeCount + 1 : 0;

  if (!previous || recovered.previousRegime === rawRegime) {
    const dwellBars = previous ? recovered.previousDwellBars + 1 : 1;
    const state = withTransitionHistory(previous, createConfirmedRegimeState({
      rawRegime,
      confirmedRegime: rawRegime,
      rawConfidence: raw.confidence,
      confirmedConfidence: raw.confidence,
      candidateRegime: null,
      candidateCount: 0,
      dwellBars,
      heldPreviousRegime: false,
      transitionReason: previous ? "Raw regime matches confirmed regime" : "Initial regime classification",
      timestamp: raw.timestamp,
      previousRegime: previous ? recovered.previousRegime : null,
      regimeStartTime: previous?.regimeStartTime ?? raw.timestamp,
      minimumDwellSatisfied: dwellBars >= settings.minimumDwellBars,
      unknownRegimeCount,
      transitionConfidence: raw.confidence,
      transitionEvidence: transitionEvidence(raw, recovered.previousRegime, dwellBars, 0, settings.minimumDwellBars, "stable"),
    }));
    const condition = conditionFromRaw(raw, state);
    return { condition, confirmationCount: settings.confirmationBars, held: false, hysteresis: condition, state };
  }

  const candidateCount = previous.candidateRegime === rawRegime ? (previous.candidateCount ?? 0) + 1 : 1;
  const dwell = evaluateRegimeDwellPolicy(recovered.previousDwellBars, settings);
  const riskOffTransition = isRiskOffRawCondition(raw);
  const riskOnRecovery = isRiskOffRegime(recovered.previousRegime) && !riskOffTransition;
  const candidatePersisted = candidateCount >= settings.confirmationBars;
  const confidenceBreakout =
    !riskOnRecovery &&
    raw.confidence >= settings.immediateConfidenceThreshold &&
    raw.confidence >= recovered.previousConfidence + settings.transitionConfidenceGap;
  const unknownTimeout =
    settings.maximumUnknownBars > 0 &&
    isUnknownLikeRawCondition(raw) &&
    unknownRegimeCount >= settings.maximumUnknownBars;
  const shouldTransition = riskOffTransition || (dwell.minimumDwellSatisfied && (candidatePersisted || confidenceBreakout || unknownTimeout));

  if (shouldTransition) {
    const reason = riskOffTransition
      ? "Risk-off condition requires immediate transition"
      : candidatePersisted
        ? `Candidate regime persisted for ${candidateCount} bars`
        : confidenceBreakout
          ? "Candidate confidence exceeded immediate threshold and transition gap"
          : `Unknown/mixed regime persisted for ${unknownRegimeCount} bars`;
    const state = withTransitionHistory(previous, createConfirmedRegimeState({
      rawRegime,
      confirmedRegime: rawRegime,
      rawConfidence: raw.confidence,
      confirmedConfidence: raw.confidence,
      candidateRegime: null,
      candidateCount: 0,
      dwellBars: 1,
      heldPreviousRegime: false,
      transitionReason: reason,
      timestamp: raw.timestamp,
      previousRegime: recovered.previousRegime,
      regimeStartTime: raw.timestamp,
      minimumDwellSatisfied: settings.minimumDwellBars <= 1,
      unknownRegimeCount,
      transitionConfidence: raw.confidence,
      transitionEvidence: transitionEvidence(raw, recovered.previousRegime, dwell.dwellBars, candidateCount, settings.minimumDwellBars, "accepted"),
    }));
    const condition = conditionFromRaw(raw, state);
    return { condition, confirmationCount: candidateCount, held: false, hysteresis: condition, state };
  }

  const heldRegime = recovered.previousRegime;
  const state = withTransitionHistory(previous, createConfirmedRegimeState({
    rawRegime,
    confirmedRegime: heldRegime,
    rawConfidence: raw.confidence,
    confirmedConfidence: recovered.previousConfidence,
    candidateRegime: rawRegime,
    candidateCount,
    dwellBars: dwell.dwellBars,
    heldPreviousRegime: true,
    transitionReason: riskOnRecovery
      ? `Risk-on candidate ${rawRegime} must confirm for ${settings.confirmationBars} bars`
      : `Candidate ${rawRegime} waiting for ${settings.confirmationBars} bars, ${probability(settings.immediateConfidenceThreshold)} confidence, or ${probability(settings.transitionConfidenceGap)} confidence gap`,
    timestamp: raw.timestamp,
    previousRegime: heldRegime,
    regimeStartTime: recovered.regimeStartTime ?? raw.timestamp,
    minimumDwellSatisfied: dwell.minimumDwellSatisfied,
    unknownRegimeCount,
    transitionConfidence: raw.confidence,
    transitionEvidence: transitionEvidence(raw, heldRegime, dwell.dwellBars, candidateCount, settings.minimumDwellBars, "held"),
  }));
  const condition = { ...previous, ...state, confidence: state.confirmedConfidence, key: heldRegime, contextKey: raw.contextKey };
  return { condition, confirmationCount: candidateCount, held: true, hysteresis: condition, state };
}

export function recentRegimeConditionKeys(market: RegimeMarketContext): string[] {
  const keys: string[] = [];
  const start = Math.max(0, market.candles.length - 3);
  for (let index = start; index < market.candles.length; index += 1) {
    const prefix = market.candles.slice(0, index + 1);
    const snapshot = buildRegimeMarketContext({
      symbol: market.latest.symbol,
      primaryCandles: prefix,
      allCandles: market.allCandles,
      oneMinuteCandles: market.oneMinuteCandles,
      fiveMinuteCandles: market.fiveMinuteCandles,
    });
    if (!snapshot) {
      continue;
    }
    keys.push(buildRawRegimeCondition(snapshot).rawRegime);
  }
  return keys;
}

export function emptyRegimeHysteresisForMarket(market: RegimeMarketContext): RegimeConditionSnapshot | null {
  const state = createConfirmedRegimeState({
    rawRegime: "extreme_volatility_no_trade",
    confirmedRegime: "extreme_volatility_no_trade",
    rawConfidence: 0,
    confirmedConfidence: 0,
    candidateRegime: null,
    candidateCount: 0,
    dwellBars: 0,
    heldPreviousRegime: false,
    transitionReason: "No confirmed regime yet",
    timestamp: market.latest.timestamp,
    previousRegime: null,
    regimeStartTime: market.latest.timestamp,
    minimumDwellSatisfied: false,
    unknownRegimeCount: 0,
    transitionConfidence: 0,
    transitionEvidence: {},
  });
  return {
    primaryTrend: "Sideways / range-bound",
    volatility: "Normal volatility",
    opportunity: "No-trade",
    confidence: 0,
    key: "extreme_volatility_no_trade",
    contextKey: regimeConditionContextKey(market),
    ...withTransitionHistory(null, state),
  };
}

function conditionFromRaw(raw: RawCondition, state: ConfirmedRegimeState): RegimeConditionSnapshot {
  return {
    primaryTrend: raw.primaryTrend,
    volatility: raw.volatility,
    opportunity: raw.opportunity,
    confidence: state.confirmedConfidence,
    key: state.confirmedRegime,
    contextKey: raw.contextKey,
    axes: raw.axes,
    ...state,
  };
}

function withTransitionHistory(previous: RegimeConditionSnapshot | null, state: ConfirmedRegimeState): ConfirmedRegimeState {
  return {
    ...state,
    transitionHistory: appendRegimeTransitionHistory(previous, state),
  };
}

function transitionEvidence(
  raw: RawCondition,
  previousRegime: MarketRegimeId,
  dwellBars: number,
  candidateCount: number,
  minimumDwellBars: number,
  transitionState: "stable" | "held" | "accepted",
): Record<string, number | string | boolean | null> {
  return {
    rawRegime: raw.rawRegime,
    previousRegime,
    rawConfidence: raw.confidence,
    dwellBars,
    candidateCount,
    minimumDwellBars,
    transitionState,
    volatilityAxis: raw.axes.volatility,
    liquidityAxis: raw.axes.liquidity,
    eventRiskAxis: raw.axes.eventRisk,
    missingInputCount: raw.classification.missingInputs.length,
  };
}

function isRiskOffRawCondition(raw: RawCondition): boolean {
  return (
    raw.rawRegime === "extreme_volatility_no_trade" ||
    raw.rawRegime === "liquidity_stress" ||
    raw.rawRegime === "event_risk" ||
    raw.axes.volatility === "extreme" ||
    raw.axes.liquidity === "poor" ||
    raw.axes.eventRisk === "blackout" ||
    raw.noTradeReasons.some((reason) => {
      const lower = reason.toLowerCase();
      return lower.includes("halt") || lower.includes("luld") || lower.includes("circuit") || lower.includes("stale") || lower.includes("spread") || lower.includes("event blackout") || lower.includes("volatility") || lower.includes("broker") || lower.includes("account");
    })
  );
}

function isRiskOffRegime(regime: MarketRegimeId | RegimeNoTradeTag | undefined): boolean {
  return regime === "extreme_volatility_no_trade" || regime === "liquidity_stress" || regime === "event_risk" || regime === "no_trade";
}

function isUnknownLikeRawCondition(raw: RawCondition): boolean {
  return raw.rawRegime === "choppy_mixed" || raw.axes.liquidity === "unknown";
}

function probability(value: number): string {
  return `${Math.round(value * 1000) / 10}%`;
}

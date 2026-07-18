import { buildRawRegimeCondition, regimeConditionContextKey } from "./classifier.ts";
import { resolveRegimeHysteresisSettings } from "./config.ts";
import { buildRegimeMarketContext } from "./decision-engine.ts";
import type {
  ConfirmedRegimeState,
  MarketRegimeId,
  RegimeConditionSnapshot,
  RegimeHysteresisSettings,
  RegimeHysteresisSnapshot,
  RegimeMarketContext,
  RegimeNoTradeTag,
} from "./types.ts";

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
  const previous = previousHysteresis?.contextKey === raw.contextKey ? previousHysteresis : null;
  const previousRegime = previous?.confirmedRegime ?? previous?.rawRegime ?? (previous?.key as MarketRegimeId | undefined) ?? "choppy_mixed";
  const previousConfidence = previous?.confirmedConfidence ?? previous?.confidence ?? 0;
  const previousDwellBars = previous?.dwellBars ?? 0;
  const rawRegime = raw.rawRegime;

  if (!previous || previousRegime === rawRegime) {
    const state = confirmedState(rawRegime, rawRegime, raw.confidence, raw.confidence, null, 0, previous ? previousDwellBars + 1 : 1, false, previous ? "Raw regime matches confirmed regime" : "Initial regime classification", raw.timestamp);
    const condition = conditionFromRaw(raw, state);
    return { condition, confirmationCount: settings.confirmationBars, held: false, hysteresis: condition, state };
  }

  const candidateCount = previous.candidateRegime === rawRegime ? (previous.candidateCount ?? 0) + 1 : 1;
  const dwellBars = previousDwellBars + 1;
  const riskOffTransition = isRiskOffRawCondition(raw);
  const riskOnRecovery = isRiskOffRegime(previousRegime) && !riskOffTransition;
  const minimumDwellSatisfied = dwellBars >= settings.minimumDwellBars;
  const candidatePersisted = candidateCount >= settings.confirmationBars;
  const confidenceBreakout =
    !riskOnRecovery &&
    raw.confidence >= settings.immediateConfidenceThreshold &&
    raw.confidence >= previousConfidence + settings.transitionConfidenceGap;
  const unknownTimeout =
    settings.maximumUnknownBars > 0 &&
    isUnknownLikeRawCondition(raw) &&
    candidateCount >= settings.maximumUnknownBars;
  const shouldTransition = riskOffTransition || (minimumDwellSatisfied && (candidatePersisted || confidenceBreakout || unknownTimeout));

  if (shouldTransition) {
    const reason = riskOffTransition
      ? "Risk-off condition requires immediate transition"
      : candidatePersisted
        ? `Candidate regime persisted for ${candidateCount} bars`
        : confidenceBreakout
          ? "Candidate confidence exceeded immediate threshold and transition gap"
          : `Unknown/mixed regime persisted for ${candidateCount} bars`;
    const state = confirmedState(rawRegime, rawRegime, raw.confidence, raw.confidence, null, 0, 1, false, reason, raw.timestamp);
    const condition = conditionFromRaw(raw, state);
    return { condition, confirmationCount: candidateCount, held: false, hysteresis: condition, state };
  }

  const heldRegime = previousRegime;
  const state = confirmedState(
    rawRegime,
    heldRegime,
    raw.confidence,
    previousConfidence,
    rawRegime,
    candidateCount,
    dwellBars,
    true,
    riskOnRecovery
      ? `Risk-on candidate ${rawRegime} must confirm for ${settings.confirmationBars} bars`
      : `Candidate ${rawRegime} waiting for ${settings.confirmationBars} bars, ${probability(settings.immediateConfidenceThreshold)} confidence, or ${probability(settings.transitionConfidenceGap)} confidence gap`,
    raw.timestamp,
  );
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
  const state = confirmedState("extreme_volatility_no_trade", "extreme_volatility_no_trade", 0, 0, null, 0, 0, false, "No confirmed regime yet", market.latest.timestamp);
  return {
    primaryTrend: "Sideways / range-bound",
    volatility: "Normal volatility",
    opportunity: "No-trade",
    confidence: 0,
    key: "extreme_volatility_no_trade",
    contextKey: regimeConditionContextKey(market),
    ...state,
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

function confirmedState(
  rawRegime: MarketRegimeId,
  confirmedRegime: MarketRegimeId,
  rawConfidence: number,
  confirmedConfidence: number,
  candidateRegime: MarketRegimeId | null,
  candidateCount: number,
  dwellBars: number,
  heldPreviousRegime: boolean,
  transitionReason: string,
  timestamp: string,
): ConfirmedRegimeState {
  return {
    rawRegime,
    confirmedRegime,
    rawConfidence,
    confirmedConfidence,
    candidateRegime,
    candidateCount,
    dwellBars,
    heldPreviousRegime,
    transitionReason,
    timestamp,
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

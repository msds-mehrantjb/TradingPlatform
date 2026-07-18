import {
  REGIME_MAX_ABSTENTION_RATE,
  REGIME_MIN_CONDITION_CONFIDENCE,
  REGIME_MIN_INDEPENDENT_FAMILIES,
  REGIME_MIN_WINNING_EDGE,
  REGIME_MIN_WINNING_SCORE,
} from "../config.ts";
import { roundNumber } from "../indicators.ts";
import type { MarketRegimeId, RegimeAggregationResult, RegimeTradingSettings } from "../types.ts";

export type RegimeDecisionGateSettings = {
  minimumWinningScore: number;
  minimumDirectionalEdge: number;
  minimumRegimeConfidence: number;
  minimumActiveStrategies: number;
  minimumIndependentFamilies: number;
  maximumAbstentionRate: number;
};

export function regimeTradeBlockers(
  aggregation: RegimeAggregationResult,
  conditionConfidence: number,
  opportunity: string,
  conditionHeld: boolean,
  settings: RegimeTradingSettings | undefined,
  confirmedRegime: MarketRegimeId,
): string[] {
  const gates = regimeDecisionGateSettings(settings);
  const blockers: string[] = [];
  if (conditionHeld) {
    blockers.push("Market condition switch is not confirmed yet");
  }
  if (aggregation.winningDirection !== "buy" && aggregation.winningDirection !== "sell") {
    blockers.push("Winning direction is missing");
  }
  if (aggregation.winningScore < gates.minimumWinningScore) {
    blockers.push(`Winning direction score ${probability(aggregation.winningScore)} is below ${probability(gates.minimumWinningScore)}`);
  }
  if (aggregation.directionalEdge < gates.minimumDirectionalEdge) {
    blockers.push(`Winning direction edge ${probability(aggregation.directionalEdge)} is below ${probability(gates.minimumDirectionalEdge)}`);
  }
  if (conditionConfidence < gates.minimumRegimeConfidence) {
    blockers.push(`Market condition confidence ${probability(conditionConfidence)} is below ${probability(gates.minimumRegimeConfidence)}`);
  }
  if (aggregation.activeStrategyCount < gates.minimumActiveStrategies) {
    blockers.push(`Strategy coverage ${aggregation.activeStrategyCount} active is below ${gates.minimumActiveStrategies}`);
  }
  if (aggregation.activeFamilyCount < gates.minimumIndependentFamilies) {
    blockers.push(`Independent family coverage ${aggregation.activeFamilyCount} is below ${gates.minimumIndependentFamilies}`);
  }
  if (aggregation.abstentionRate > gates.maximumAbstentionRate) {
    blockers.push(`Abstention rate ${probability(aggregation.abstentionRate)} is above ${probability(gates.maximumAbstentionRate)}`);
  }
  if (opportunity === "No-trade") {
    blockers.push("Opportunity state is No-trade");
  }
  if (confirmedRegime === "extreme_volatility_no_trade" || confirmedRegime === "event_risk" || confirmedRegime === "liquidity_stress") {
    blockers.push(`Dynamic Regime profile prohibits new entries for ${confirmedRegime}`);
  }
  return blockers;
}

export function regimeDecisionGateSettings(settings?: RegimeTradingSettings): RegimeDecisionGateSettings {
  return {
    minimumWinningScore: settings?.minimumWinningScore ?? settings?.minimumBuyScore ?? REGIME_MIN_WINNING_SCORE,
    minimumDirectionalEdge: settings?.minimumDirectionalEdge ?? settings?.minimumSignalEdge ?? REGIME_MIN_WINNING_EDGE,
    minimumRegimeConfidence: settings?.minimumRegimeConfidence ?? REGIME_MIN_CONDITION_CONFIDENCE,
    minimumActiveStrategies: Math.max(1, Math.floor(settings?.minimumActiveStrategies ?? 3)),
    minimumIndependentFamilies: Math.max(1, Math.floor(settings?.minimumIndependentFamilies ?? REGIME_MIN_INDEPENDENT_FAMILIES)),
    maximumAbstentionRate: Math.max(0, Math.min(1, settings?.maximumAbstentionRate ?? REGIME_MAX_ABSTENTION_RATE)),
  };
}

function probability(value: number): string {
  return `${roundNumber(value * 100, 1)}%`;
}

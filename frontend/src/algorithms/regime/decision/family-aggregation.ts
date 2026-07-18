import { roundNumber } from "../indicators.ts";
import { regimeStrategyAggregationFamily } from "../router.ts";
import { activeDirectionalRegimeOutputs, regimeAbstentionRate, votingDirectionalRegimeOutputs } from "./abstention-policy.ts";
import { applyRegimeFamilyContributionCap, cappedRegimeStrategyContribution } from "./contribution-caps.ts";
import type {
  RegimeAggregationFamily,
  RegimeAggregationResult,
  RegimeFamilyScore,
  RegimeMarketContext,
  RegimeSelectedStrategy,
  RegimeStrategyDefinition,
  RegimeStrategySignal,
} from "../types.ts";

type FamilyAccumulator = {
  buy: number;
  sell: number;
  activeStrategyCount: number;
};

export function aggregateRegimeStrategyScores(strategyOutputs: RegimeSelectedStrategy[]): RegimeAggregationResult {
  const directionalOutputs = activeDirectionalRegimeOutputs(strategyOutputs);
  const directionalActive = votingDirectionalRegimeOutputs(strategyOutputs);
  const familyAccumulators = new Map<RegimeAggregationFamily, FamilyAccumulator>();

  directionalActive.forEach((output) => {
    const family = regimeStrategyAggregationFamily(output.strategy);
    const accumulator = familyAccumulators.get(family) ?? { buy: 0, sell: 0, activeStrategyCount: 0 };
    const cappedContribution = cappedRegimeStrategyContribution(output.confidence, output.effectiveWeight || output.effective_weight || 0);
    if (output.signal === "buy") {
      accumulator.buy += cappedContribution;
    } else {
      accumulator.sell += cappedContribution;
    }
    accumulator.activeStrategyCount += 1;
    familyAccumulators.set(family, accumulator);
  });

  const familyScores = Array.from(familyAccumulators.entries()).map(([family, accumulator]): RegimeFamilyScore =>
    applyRegimeFamilyContributionCap({
      family,
      buyScore: accumulator.buy,
      sellScore: accumulator.sell,
      activeStrategyCount: accumulator.activeStrategyCount,
    }),
  );

  const rawBuy = familyScores.reduce((sum, family) => sum + family.buyScore, 0);
  const rawSell = familyScores.reduce((sum, family) => sum + family.sellScore, 0);
  const directionalTotal = rawBuy + rawSell;
  const buyScore = directionalTotal > 0 ? roundNumber(rawBuy / directionalTotal, 4) : 0;
  const sellScore = directionalTotal > 0 ? roundNumber(rawSell / directionalTotal, 4) : 0;
  const abstentionRate = regimeAbstentionRate(directionalOutputs, directionalActive);
  const winningDirection = directionalTotal <= 0 ? "hold" : buyScore > sellScore ? "buy" : sellScore > buyScore ? "sell" : "hold";
  const winningScore = winningDirection === "buy" ? buyScore : winningDirection === "sell" ? sellScore : 0;
  const secondBestScore = winningDirection === "buy" ? sellScore : winningDirection === "sell" ? buyScore : Math.max(buyScore, sellScore);
  const directionalEdge = roundNumber(winningScore - secondBestScore, 4);

  return {
    finalSignal: winningDirection,
    scores: { buy: buyScore, sell: sellScore, hold: abstentionRate },
    buyScore,
    sellScore,
    winningDirection,
    winningScore,
    secondBestScore,
    directionalEdge,
    activeStrategyCount: directionalActive.length,
    activeFamilyCount: familyScores.filter((family) => family.buyScore + family.sellScore > 0).length,
    abstentionRate,
    familyScores,
  };
}

export function regimeSystemWeightMultiplier(_strategy: RegimeStrategyDefinition, signal: RegimeStrategySignal, market: RegimeMarketContext): number {
  if (signal === "hold") {
    return 1;
  }
  return roundNumber(
    regimeAtrWeightMultiplier(market) *
      regimeVolumeWeightMultiplier(market) *
      regimeTimeOfDayWeightMultiplier(market),
    4,
  );
}

function regimeAtrWeightMultiplier(market: RegimeMarketContext): number {
  if (market.atr.regime === "too_low") {
    return 0.9;
  }
  if (market.atr.regime === "high") {
    return 0.9;
  }
  if (market.atr.regime === "extreme") {
    return 0.35;
  }
  return 1;
}

function regimeVolumeWeightMultiplier(market: RegimeMarketContext): number {
  if (market.volume.weakVolume || market.volume.smallCandle) {
    return 0.8;
  }
  return 1;
}

function regimeTimeOfDayWeightMultiplier(market: RegimeMarketContext): number {
  return Math.min(1, market.timeOfDay.weightMultiplier);
}

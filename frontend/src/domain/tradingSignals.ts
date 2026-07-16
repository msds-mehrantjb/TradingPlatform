export type AlgoSignal = "Buy" | "Sell" | "Hold";
export type DirectionBias = "long" | "short" | "neutral" | "cash";
export type StrategyEligibilityStatus = "Strong Fit" | "Allowed" | "Watch" | "Avoid";

export type StrategyVoteLike = {
  status?: StrategyEligibilityStatus;
  signal?: AlgoSignal;
};

export function directionalSignal(primary: DirectionBias, fallback: DirectionBias): AlgoSignal {
  const direction = primary === "neutral" || primary === "cash" ? fallback : primary;
  if (direction === "long") {
    return "Buy";
  }
  if (direction === "short") {
    return "Sell";
  }
  return "Hold";
}

export function oppositeSignal(direction: DirectionBias): AlgoSignal {
  if (direction === "long") {
    return "Sell";
  }
  if (direction === "short") {
    return "Buy";
  }
  return "Hold";
}

export function isEligibleStrategyVote(vote: StrategyVoteLike): boolean {
  return vote.status === "Allowed" || vote.status === "Strong Fit";
}

export function winningVoteSignal(buyVotes: number, sellVotes: number, holdVotes: number): AlgoSignal {
  const maxVotes = Math.max(buyVotes, sellVotes, holdVotes);
  const winners = [
    buyVotes === maxVotes ? "Buy" : "",
    sellVotes === maxVotes ? "Sell" : "",
    holdVotes === maxVotes ? "Hold" : "",
  ].filter(Boolean);
  return winners.length === 1 ? (winners[0] as AlgoSignal) : "Hold";
}


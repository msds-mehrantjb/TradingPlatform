export type OrderIntentSide = "Buy" | "Sell" | "Hold";

export type PositionEffect =
  | "enter_long"
  | "exit_long"
  | "enter_short"
  | "cover_short"
  | "none";

export type OrderIntent = {
  eligible: boolean;
  side: OrderIntentSide;
  signalDirection: OrderIntentSide;
  positionEffect: PositionEffect;
  currentPosition: number;
  requestedResultingPosition: number;
  symbol: string;
  quantity: number;
  orderType: string;
  triggerPrice: number | null;
  limitPrice: number | null;
  stopPrice: number | null;
  targetPrice: number | null;
  timeInForce: "Day";
  reasonCodes: string[];
};

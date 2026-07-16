export type AccountSnapshot = {
  accountId: string;
  equity: number;
  buyingPower: number;
  paperTrading: boolean;
};

export type PositionSnapshot = {
  symbol: string;
  quantity: number;
  marketValue: number;
  averageEntryPrice: number | null;
};

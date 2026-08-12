"""Regime-owned local paper account state.

This module is intentionally scoped to the Regime algorithm. It does not read
or write broker paper-account state, shared positions, or another algorithm's
simulated account.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping, Protocol

REGIME_ALGORITHM_ID = "regime"
REGIME_LOCAL_PAPER_ACCOUNT_VERSION = "regime_local_paper_account_v1"
REGIME_LOCAL_PAPER_SOURCE_AUTHORITY = "regime_local_paper_account"
REGIME_LOCAL_PAPER_SNAPSHOT_KEY = "local_paper_account"
REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE = 100_000.0


class RegimeLocalPaperAccountRepository(Protocol):
    def write_local_paper_account_snapshot(self, identity: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]: ...

    def read_local_paper_account_snapshot(self, identity: dict[str, Any]) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class RegimeLocalPaperLotSnapshot:
    lotId: str
    algorithmId: str
    algorithmInstanceId: str
    accountId: str
    runtimeMode: str
    symbol: str
    side: str
    quantity: int
    remainingQuantity: int
    entryPrice: float
    entryTimestamp: datetime
    decisionId: str | None = None
    orderIntentId: str | None = None
    stopPrice: float | None = None
    targetPrice: float | None = None


@dataclass(frozen=True)
class RegimeLocalPaperFillSnapshot:
    fillId: str
    algorithmId: str
    algorithmInstanceId: str
    accountId: str
    runtimeMode: str
    symbol: str
    side: str
    quantity: int
    fillPrice: float
    timestamp: datetime
    realizedPnl: float = 0.0
    commission: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    decisionId: str | None = None
    orderIntentId: str | None = None


@dataclass(frozen=True)
class RegimeLocalPaperPositionSnapshot:
    algorithmId: str
    algorithmInstanceId: str
    accountId: str
    runtimeMode: str
    symbol: str
    side: str
    quantity: int
    averageEntryPrice: float
    marketPrice: float
    marketValue: float
    realizedPnl: float
    unrealizedPnl: float
    stopPrice: float | None = None
    targetPrice: float | None = None
    openedAt: datetime | None = None
    updatedAt: datetime | None = None
    lots: tuple[RegimeLocalPaperLotSnapshot, ...] = ()


@dataclass(frozen=True)
class RegimeLocalPaperAccountSnapshot:
    algorithmId: str
    algorithmInstanceId: str
    accountId: str
    runtimeMode: str
    initialBalance: float
    cash: float
    equity: float
    buyingPower: float
    availableBuyingPower: float
    reservedCash: float
    realizedPnl: float
    unrealizedPnl: float
    dailyRealizedPnl: float
    dailyUnrealizedPnl: float
    grossExposure: float
    netExposure: float
    feesPaid: float
    slippagePaid: float
    tradeCount: int
    winningTrades: int
    losingTrades: int
    consecutiveLosses: int
    sessionStartEquity: float
    highWaterMark: float
    drawdown: float
    positions: tuple[RegimeLocalPaperPositionSnapshot, ...]
    openOrders: tuple[dict[str, Any], ...]
    reservations: tuple[dict[str, Any], ...]
    lots: tuple[RegimeLocalPaperLotSnapshot, ...]
    fills: tuple[RegimeLocalPaperFillSnapshot, ...]
    dailyCounters: dict[str, Any]
    riskState: dict[str, Any]
    sourceAuthority: str
    accountVersion: str
    stateVersion: str
    observedAt: datetime

    def to_dict(self) -> dict[str, Any]:
        return _snapshot_to_dict(self)


class RegimeLocalPaperAccount:
    """Authoritative simulated cash/equity account for Regime local paper trading."""

    algorithmId = REGIME_ALGORITHM_ID

    def __init__(
        self,
        *,
        algorithmInstanceId: str = "regime-default",
        accountId: str = "regime-local-paper",
        runtimeMode: str = "paper",
        initialBalance: float = REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE,
        cash: float | None = None,
        reservedCash: float = 0.0,
        realizedPnl: float = 0.0,
        dailyRealizedPnl: float = 0.0,
        feesPaid: float = 0.0,
        slippagePaid: float = 0.0,
        tradeCount: int = 0,
        winningTrades: int = 0,
        losingTrades: int = 0,
        consecutiveLosses: int = 0,
        sessionStartEquity: float | None = None,
        highWaterMark: float | None = None,
        lots: Iterable[RegimeLocalPaperLotSnapshot | Mapping[str, Any]] = (),
        fills: Iterable[RegimeLocalPaperFillSnapshot | Mapping[str, Any]] = (),
        marks: Mapping[str, float] | None = None,
    ) -> None:
        self.algorithmInstanceId = str(algorithmInstanceId or "regime-default")
        self.accountId = str(accountId or "regime-local-paper")
        self.runtimeMode = str(runtimeMode or "paper")
        self.initialBalance = _positive_amount(initialBalance, "initialBalance")
        self._cash = round(float(self.initialBalance if cash is None else cash), 10)
        self._reservedCash = round(max(0.0, float(reservedCash)), 10)
        self._realizedPnl = round(float(realizedPnl), 10)
        self._dailyRealizedPnl = round(float(dailyRealizedPnl), 10)
        self._feesPaid = round(max(0.0, float(feesPaid)), 10)
        self._slippagePaid = round(max(0.0, float(slippagePaid)), 10)
        self._tradeCount = max(0, int(tradeCount))
        self._winningTrades = max(0, int(winningTrades))
        self._losingTrades = max(0, int(losingTrades))
        self._consecutiveLosses = max(0, int(consecutiveLosses))
        self._lots = tuple(self._coerce_lot(lot) for lot in lots)
        self._fills = tuple(self._coerce_fill(fill) for fill in fills)
        self._marks = {str(symbol).upper(): float(price) for symbol, price in dict(marks or {}).items()}
        snapshot = self.get_account_snapshot(observedAt=_utc_now())
        self._sessionStartEquity = round(float(sessionStartEquity if sessionStartEquity is not None else snapshot.equity), 10)
        self._highWaterMark = round(float(highWaterMark if highWaterMark is not None else max(snapshot.equity, self._sessionStartEquity)), 10)
        self._validate_owned_state()

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> "RegimeLocalPaperAccount":
        _require_regime_payload(snapshot, expected_account_id=snapshot.get("accountId"))
        marks = {
            str(position.get("symbol") or "").upper(): float(position.get("marketPrice") or 0.0)
            for position in _records(snapshot.get("positions"))
            if position.get("symbol")
        }
        return cls(
            algorithmInstanceId=str(snapshot.get("algorithmInstanceId") or "regime-default"),
            accountId=str(snapshot.get("accountId") or "regime-local-paper"),
            runtimeMode=str(snapshot.get("runtimeMode") or "paper"),
            initialBalance=float(snapshot.get("initialBalance") or REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE),
            cash=float(snapshot.get("cash") or 0.0),
            reservedCash=float(snapshot.get("reservedCash") or 0.0),
            realizedPnl=float(snapshot.get("realizedPnl") or 0.0),
            dailyRealizedPnl=float(snapshot.get("dailyRealizedPnl") or 0.0),
            feesPaid=float(snapshot.get("feesPaid") or 0.0),
            slippagePaid=float(snapshot.get("slippagePaid") or 0.0),
            tradeCount=int(snapshot.get("tradeCount") or 0),
            winningTrades=int(snapshot.get("winningTrades") or 0),
            losingTrades=int(snapshot.get("losingTrades") or 0),
            consecutiveLosses=int(snapshot.get("consecutiveLosses") or 0),
            sessionStartEquity=float(snapshot.get("sessionStartEquity") or 0.0),
            highWaterMark=float(snapshot.get("highWaterMark") or 0.0),
            lots=tuple(_records(snapshot.get("lots"))),
            fills=tuple(_records(snapshot.get("fills"))),
            marks=marks,
        )

    @classmethod
    def restore(
        cls,
        repository: RegimeLocalPaperAccountRepository,
        *,
        identity: Mapping[str, Any],
        initialBalance: float = REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE,
    ) -> "RegimeLocalPaperAccount":
        resolved = _identity(identity)
        snapshot = repository.read_local_paper_account_snapshot(resolved)
        if snapshot is None:
            return cls(
                algorithmInstanceId=resolved["algorithmInstanceId"],
                accountId=resolved["accountId"],
                runtimeMode=resolved["runtimeMode"],
                initialBalance=initialBalance,
            )
        _require_regime_payload(snapshot, expected_identity=resolved)
        return cls.from_snapshot(snapshot)

    def get_account_snapshot(self, *, observedAt: datetime | str | None = None) -> RegimeLocalPaperAccountSnapshot:
        positions = self._positions(observedAt=observedAt)
        unrealized = round(sum(position.unrealizedPnl for position in positions), 10)
        gross = round(sum(abs(position.marketValue) for position in positions), 10)
        net = round(sum(position.marketValue if position.side == "Long" else -position.marketValue for position in positions), 10)
        equity = round(self._cash + gross, 10)
        high_water = max(getattr(self, "_highWaterMark", equity), equity)
        drawdown = round(max(0.0, high_water - equity), 10)
        available = round(max(0.0, self._cash - self._reservedCash), 10)
        observed = _parse_dt(observedAt) or _utc_now()
        seed = {
            "algorithmId": REGIME_ALGORITHM_ID,
            "algorithmInstanceId": self.algorithmInstanceId,
            "accountId": self.accountId,
            "runtimeMode": self.runtimeMode,
            "cash": self._cash,
            "reservedCash": self._reservedCash,
            "realizedPnl": self._realizedPnl,
            "unrealizedPnl": unrealized,
            "positions": [_dataclass_to_dict(position) for position in positions],
            "fills": [_dataclass_to_dict(fill) for fill in self._fills],
        }
        return RegimeLocalPaperAccountSnapshot(
            algorithmId=REGIME_ALGORITHM_ID,
            algorithmInstanceId=self.algorithmInstanceId,
            accountId=self.accountId,
            runtimeMode=self.runtimeMode,
            initialBalance=round(self.initialBalance, 10),
            cash=round(self._cash, 10),
            equity=equity,
            buyingPower=available,
            availableBuyingPower=available,
            reservedCash=round(self._reservedCash, 10),
            realizedPnl=round(self._realizedPnl, 10),
            unrealizedPnl=unrealized,
            dailyRealizedPnl=round(self._dailyRealizedPnl, 10),
            dailyUnrealizedPnl=unrealized,
            grossExposure=gross,
            netExposure=net,
            feesPaid=round(self._feesPaid, 10),
            slippagePaid=round(self._slippagePaid, 10),
            tradeCount=self._tradeCount,
            winningTrades=self._winningTrades,
            losingTrades=self._losingTrades,
            consecutiveLosses=self._consecutiveLosses,
            sessionStartEquity=round(getattr(self, "_sessionStartEquity", equity), 10),
            highWaterMark=round(high_water, 10),
            drawdown=drawdown,
            positions=positions,
            openOrders=(),
            reservations=(),
            lots=tuple(self._lots),
            fills=tuple(self._fills),
            dailyCounters={
                "dailyRealizedPnl": round(self._dailyRealizedPnl, 10),
                "dailyUnrealizedPnl": unrealized,
                "tradeCount": self._tradeCount,
                "winningTrades": self._winningTrades,
                "losingTrades": self._losingTrades,
                "consecutiveLosses": self._consecutiveLosses,
            },
            riskState={
                "reservedCash": round(self._reservedCash, 10),
                "grossExposure": gross,
                "netExposure": net,
                "sessionStartEquity": round(getattr(self, "_sessionStartEquity", equity), 10),
                "highWaterMark": round(high_water, 10),
                "drawdown": drawdown,
            },
            sourceAuthority=REGIME_LOCAL_PAPER_SOURCE_AUTHORITY,
            accountVersion=REGIME_LOCAL_PAPER_ACCOUNT_VERSION,
            stateVersion=_stable_hash(seed),
            observedAt=observed,
        )

    def get_position(self, symbol: str) -> RegimeLocalPaperPositionSnapshot | None:
        selected = str(symbol or "").upper()
        return next((position for position in self.get_account_snapshot().positions if position.symbol == selected), None)

    def reserve_cash(self, amount: float, *, algorithmId: str = REGIME_ALGORITHM_ID) -> RegimeLocalPaperAccountSnapshot:
        _require_regime_algorithm_id(algorithmId)
        parsed = _positive_amount(amount, "cash reservation")
        if parsed > self.get_account_snapshot().availableBuyingPower + 1e-9:
            raise ValueError("Regime local paper account cannot reserve more cash than available buying power")
        self._reservedCash = round(self._reservedCash + parsed, 10)
        return self.get_account_snapshot()

    def release_cash(self, amount: float, *, algorithmId: str = REGIME_ALGORITHM_ID) -> RegimeLocalPaperAccountSnapshot:
        _require_regime_algorithm_id(algorithmId)
        parsed = _positive_amount(amount, "cash release")
        if parsed > self._reservedCash + 1e-9:
            raise ValueError("Regime local paper account cannot release more cash than reserved")
        self._reservedCash = round(max(0.0, self._reservedCash - parsed), 10)
        return self.get_account_snapshot()

    def apply_fill(self, fill: Mapping[str, Any]) -> RegimeLocalPaperAccountSnapshot:
        _require_regime_payload(fill, expected_identity=self.identity)
        symbol = str(fill.get("symbol") or self.identity.get("symbol") or "SPY").upper()
        side = _normal_side(fill.get("side") or fill.get("orderSide") or "Buy")
        quantity = _positive_quantity(fill.get("filledQuantity") or fill.get("quantity"))
        price = _positive_amount(fill.get("averageFillPrice") or fill.get("fillPrice") or fill.get("price"), "fill price")
        timestamp = _parse_dt(fill.get("filledAt") or fill.get("timestamp")) or _utc_now()
        commission = max(0.0, float(fill.get("commission") or fill.get("commissions") or 0.0))
        fees = max(0.0, float(fill.get("fees") or 0.0))
        slippage = max(0.0, float(fill.get("slippage") or fill.get("slippagePaid") or 0.0))
        charges = round(commission + fees + slippage, 10)
        fill_id = str(fill.get("fillId") or fill.get("fill_id") or _generated_fill_id(self.identity, fill, timestamp))
        if any(existing.fillId == fill_id for existing in self._fills):
            return self.get_account_snapshot(observedAt=timestamp)

        if side == "Buy":
            notional = round(quantity * price, 10)
            if notional + charges > self.get_account_snapshot().availableBuyingPower + self._reservedCash + 1e-9:
                raise ValueError("Regime local paper fill exceeds Regime available buying power")
            self._cash = round(self._cash - notional - charges, 10)
            lot = RegimeLocalPaperLotSnapshot(
                lotId=str(fill.get("lotId") or f"regime-local-lot-{_stable_hash((self.accountId, symbol, fill_id))[:12]}"),
                algorithmId=REGIME_ALGORITHM_ID,
                algorithmInstanceId=self.algorithmInstanceId,
                accountId=self.accountId,
                runtimeMode=self.runtimeMode,
                symbol=symbol,
                side="Long",
                quantity=quantity,
                remainingQuantity=quantity,
                entryPrice=price,
                entryTimestamp=timestamp,
                decisionId=_optional_str(fill.get("decisionId") or fill.get("decision_id")),
                orderIntentId=_optional_str(fill.get("orderIntentId") or fill.get("order_intent_id")),
                stopPrice=_optional_float(fill.get("stopPrice") or fill.get("stop_price")),
                targetPrice=_optional_float(fill.get("targetPrice") or fill.get("target_price")),
            )
            self._lots = (*self._lots, lot)
            realized = 0.0
        else:
            realized = self._close_lots(symbol=symbol, quantity=quantity, price=price)
            self._cash = round(self._cash + (quantity * price) - charges, 10)
            realized = round(realized - charges, 10)
            self._realizedPnl = round(self._realizedPnl + realized, 10)
            self._dailyRealizedPnl = round(self._dailyRealizedPnl + realized, 10)
            self._tradeCount += 1
            if realized > 0:
                self._winningTrades += 1
                self._consecutiveLosses = 0
            elif realized < 0:
                self._losingTrades += 1
                self._consecutiveLosses += 1

        self._feesPaid = round(self._feesPaid + commission + fees, 10)
        self._slippagePaid = round(self._slippagePaid + slippage, 10)
        if side == "Buy":
            self._reservedCash = round(max(0.0, self._reservedCash - min(self._reservedCash, (quantity * price) + charges)), 10)
        self._marks[symbol] = price
        self._fills = (
            *self._fills,
            RegimeLocalPaperFillSnapshot(
                fillId=fill_id,
                algorithmId=REGIME_ALGORITHM_ID,
                algorithmInstanceId=self.algorithmInstanceId,
                accountId=self.accountId,
                runtimeMode=self.runtimeMode,
                symbol=symbol,
                side=side,
                quantity=quantity,
                fillPrice=price,
                timestamp=timestamp,
                realizedPnl=realized,
                commission=commission,
                fees=fees,
                slippage=slippage,
                decisionId=_optional_str(fill.get("decisionId") or fill.get("decision_id")),
                orderIntentId=_optional_str(fill.get("orderIntentId") or fill.get("order_intent_id")),
            ),
        )
        self._highWaterMark = max(self._highWaterMark, self.get_account_snapshot(observedAt=timestamp).equity)
        return self.get_account_snapshot(observedAt=timestamp)

    def mark_to_market(
        self,
        *,
        symbol: str,
        marketPrice: float,
        algorithmId: str = REGIME_ALGORITHM_ID,
        observedAt: datetime | str | None = None,
    ) -> RegimeLocalPaperAccountSnapshot:
        _require_regime_algorithm_id(algorithmId)
        self._marks[str(symbol or "").upper()] = _positive_amount(marketPrice, "marketPrice")
        snapshot = self.get_account_snapshot(observedAt=observedAt)
        self._highWaterMark = max(self._highWaterMark, snapshot.equity)
        return self.get_account_snapshot(observedAt=observedAt)

    def reset_daily_state(self, *, sessionStartEquity: float | None = None) -> RegimeLocalPaperAccountSnapshot:
        snapshot = self.get_account_snapshot()
        self._dailyRealizedPnl = 0.0
        self._sessionStartEquity = round(float(sessionStartEquity if sessionStartEquity is not None else snapshot.equity), 10)
        self._highWaterMark = max(self._sessionStartEquity, snapshot.equity)
        return self.get_account_snapshot()

    def persist(self, repository: RegimeLocalPaperAccountRepository, *, symbol: str = "SPY") -> RegimeLocalPaperAccountSnapshot:
        snapshot = self.get_account_snapshot()
        repository.write_local_paper_account_snapshot(
            {**self.identity, "symbol": str(symbol or "SPY").upper()},
            snapshot.to_dict(),
        )
        return snapshot

    @property
    def identity(self) -> dict[str, str]:
        return {
            "algorithmId": REGIME_ALGORITHM_ID,
            "algorithmInstanceId": self.algorithmInstanceId,
            "accountId": self.accountId,
            "runtimeMode": self.runtimeMode,
        }

    def _positions(self, *, observedAt: datetime | str | None = None) -> tuple[RegimeLocalPaperPositionSnapshot, ...]:
        del observedAt
        positions: list[RegimeLocalPaperPositionSnapshot] = []
        for symbol in sorted({lot.symbol for lot in self._lots if lot.remainingQuantity > 0}):
            lots = tuple(lot for lot in self._lots if lot.symbol == symbol and lot.remainingQuantity > 0)
            quantity = sum(lot.remainingQuantity for lot in lots)
            if quantity <= 0:
                continue
            average = round(sum(lot.remainingQuantity * lot.entryPrice for lot in lots) / quantity, 10)
            market_price = float(self._marks.get(symbol) or average)
            market_value = round(quantity * market_price, 10)
            unrealized = round((market_price - average) * quantity, 10)
            positions.append(
                RegimeLocalPaperPositionSnapshot(
                    algorithmId=REGIME_ALGORITHM_ID,
                    algorithmInstanceId=self.algorithmInstanceId,
                    accountId=self.accountId,
                    runtimeMode=self.runtimeMode,
                    symbol=symbol,
                    side="Long",
                    quantity=quantity,
                    averageEntryPrice=average,
                    marketPrice=market_price,
                    marketValue=market_value,
                    realizedPnl=round(self._realizedPnl, 10),
                    unrealizedPnl=unrealized,
                    stopPrice=next((lot.stopPrice for lot in lots if lot.stopPrice is not None), None),
                    targetPrice=next((lot.targetPrice for lot in lots if lot.targetPrice is not None), None),
                    openedAt=min((lot.entryTimestamp for lot in lots), default=None),
                    updatedAt=_utc_now(),
                    lots=lots,
                )
            )
        return tuple(positions)

    def _close_lots(self, *, symbol: str, quantity: int, price: float) -> float:
        available = sum(lot.remainingQuantity for lot in self._lots if lot.symbol == symbol)
        if quantity > available:
            raise ValueError("Regime local paper account cannot sell more Regime quantity than it owns")
        remaining_to_close = quantity
        updated_lots: list[RegimeLocalPaperLotSnapshot] = []
        realized = 0.0
        for lot in self._lots:
            if lot.symbol != symbol or remaining_to_close <= 0:
                updated_lots.append(lot)
                continue
            closing = min(lot.remainingQuantity, remaining_to_close)
            realized += (price - lot.entryPrice) * closing
            remaining = lot.remainingQuantity - closing
            if remaining > 0:
                updated_lots.append(replace(lot, remainingQuantity=remaining, quantity=remaining))
            remaining_to_close -= closing
        self._lots = tuple(updated_lots)
        return round(realized, 10)

    def _coerce_lot(self, lot: RegimeLocalPaperLotSnapshot | Mapping[str, Any]) -> RegimeLocalPaperLotSnapshot:
        if isinstance(lot, RegimeLocalPaperLotSnapshot):
            _require_regime_payload(_dataclass_to_dict(lot), expected_identity=self.identity)
            return lot
        _require_regime_payload(lot, expected_identity=self.identity)
        timestamp = _parse_dt(lot.get("entryTimestamp") or lot.get("openedAt")) or _utc_now()
        return RegimeLocalPaperLotSnapshot(
            lotId=str(lot.get("lotId") or lot.get("lot_id") or ""),
            algorithmId=REGIME_ALGORITHM_ID,
            algorithmInstanceId=self.algorithmInstanceId,
            accountId=self.accountId,
            runtimeMode=self.runtimeMode,
            symbol=str(lot.get("symbol") or "SPY").upper(),
            side=str(lot.get("side") or "Long"),
            quantity=_positive_quantity(lot.get("quantity") or lot.get("remainingQuantity")),
            remainingQuantity=_positive_quantity(lot.get("remainingQuantity") or lot.get("quantity")),
            entryPrice=_positive_amount(lot.get("entryPrice") or lot.get("averageEntryPrice"), "entryPrice"),
            entryTimestamp=timestamp,
            decisionId=_optional_str(lot.get("decisionId") or lot.get("decision_id")),
            orderIntentId=_optional_str(lot.get("orderIntentId") or lot.get("order_intent_id")),
            stopPrice=_optional_float(lot.get("stopPrice") or lot.get("stop_price")),
            targetPrice=_optional_float(lot.get("targetPrice") or lot.get("target_price")),
        )

    def _coerce_fill(self, fill: RegimeLocalPaperFillSnapshot | Mapping[str, Any]) -> RegimeLocalPaperFillSnapshot:
        if isinstance(fill, RegimeLocalPaperFillSnapshot):
            _require_regime_payload(_dataclass_to_dict(fill), expected_identity=self.identity)
            return fill
        _require_regime_payload(fill, expected_identity=self.identity)
        return RegimeLocalPaperFillSnapshot(
            fillId=str(fill.get("fillId") or fill.get("fill_id") or ""),
            algorithmId=REGIME_ALGORITHM_ID,
            algorithmInstanceId=self.algorithmInstanceId,
            accountId=self.accountId,
            runtimeMode=self.runtimeMode,
            symbol=str(fill.get("symbol") or "SPY").upper(),
            side=_normal_side(fill.get("side") or "Buy"),
            quantity=_positive_quantity(fill.get("quantity") or fill.get("filledQuantity")),
            fillPrice=_positive_amount(fill.get("fillPrice") or fill.get("averageFillPrice"), "fillPrice"),
            timestamp=_parse_dt(fill.get("timestamp") or fill.get("filledAt")) or _utc_now(),
            realizedPnl=float(fill.get("realizedPnl") or 0.0),
            commission=max(0.0, float(fill.get("commission") or fill.get("commissions") or 0.0)),
            fees=max(0.0, float(fill.get("fees") or 0.0)),
            slippage=max(0.0, float(fill.get("slippage") or 0.0)),
            decisionId=_optional_str(fill.get("decisionId") or fill.get("decision_id")),
            orderIntentId=_optional_str(fill.get("orderIntentId") or fill.get("order_intent_id")),
        )

    def _validate_owned_state(self) -> None:
        if self._cash < 0:
            raise ValueError("Regime local paper cash cannot be negative")
        if self._reservedCash < 0:
            raise ValueError("Regime local paper reserved cash cannot be negative")
        if self._reservedCash > self._cash + 1e-9:
            raise ValueError("Regime local paper reserved cash cannot exceed cash")
        if self.initialBalance <= 0:
            raise ValueError("Regime local paper initial balance must be positive")
        for lot in self._lots:
            _require_regime_payload(_dataclass_to_dict(lot), expected_identity=self.identity)
        for fill in self._fills:
            _require_regime_payload(_dataclass_to_dict(fill), expected_identity=self.identity)


def _identity(identity: Mapping[str, Any]) -> dict[str, str]:
    if str(identity.get("algorithmId") or identity.get("algorithm_id") or REGIME_ALGORITHM_ID) != REGIME_ALGORITHM_ID:
        raise ValueError("Regime local paper account rejects cross-algorithm identity")
    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "algorithmInstanceId": str(identity.get("algorithmInstanceId") or identity.get("algorithm_instance_id") or "regime-default"),
        "accountId": str(identity.get("accountId") or identity.get("account_id") or "regime-local-paper"),
        "runtimeMode": str(identity.get("runtimeMode") or identity.get("runtime_mode") or "paper"),
        "symbol": str(identity.get("symbol") or "SPY").upper(),
    }


def _require_regime_algorithm_id(algorithm_id: Any) -> None:
    if str(algorithm_id or "") != REGIME_ALGORITHM_ID:
        raise ValueError("Regime local paper account rejects cross-algorithm mutation")


def _require_regime_payload(
    payload: Mapping[str, Any], *, expected_identity: Mapping[str, Any] | None = None, expected_account_id: Any | None = None
) -> None:
    algorithm_id = payload.get("algorithmId") or payload.get("algorithm_id")
    if str(algorithm_id or "") != REGIME_ALGORITHM_ID:
        raise ValueError("Regime local paper account rejects cross-algorithm payload")
    if expected_account_id is not None and str(payload.get("accountId") or payload.get("account_id") or "") != str(expected_account_id):
        raise ValueError("Regime local paper account rejects account mismatch")
    if expected_identity is None:
        return
    expected = _identity(expected_identity)
    aliases = {
        "algorithmInstanceId": ("algorithmInstanceId", "algorithm_instance_id"),
        "accountId": ("accountId", "account_id"),
        "runtimeMode": ("runtimeMode", "runtime_mode"),
    }
    if expected_identity.get("symbol") or expected_identity.get("symbol_id"):
        aliases["symbol"] = ("symbol",)
    for expected_key, candidate_keys in aliases.items():
        expected_value = expected.get(expected_key)
        supplied = next((payload.get(key) for key in candidate_keys if payload.get(key) not in (None, "")), None)
        if supplied is None:
            continue
        supplied_value = str(supplied).upper() if expected_key == "symbol" else str(supplied)
        if supplied_value != expected_value:
            raise ValueError(f"Regime local paper account rejects {expected_key} mismatch")


def _normal_side(value: Any) -> str:
    side = str(value or "").strip().lower()
    if side in {"buy", "long"}:
        return "Buy"
    if side in {"sell", "short"}:
        return "Sell"
    raise ValueError("Regime local paper fill side must be Buy or Sell")


def _positive_quantity(value: Any) -> int:
    quantity = int(value or 0)
    if quantity <= 0:
        raise ValueError("Regime local paper quantity must be positive")
    return quantity


def _positive_amount(value: Any, label: str) -> float:
    amount = float(value or 0.0)
    if amount <= 0:
        raise ValueError(f"Regime local paper {label} must be positive")
    return amount


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None or value == "":
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _generated_fill_id(identity: Mapping[str, Any], fill: Mapping[str, Any], timestamp: datetime) -> str:
    seed = {
        "identity": dict(identity),
        "side": fill.get("side") or fill.get("orderSide"),
        "quantity": fill.get("filledQuantity") or fill.get("quantity"),
        "price": fill.get("averageFillPrice") or fill.get("fillPrice") or fill.get("price"),
        "timestamp": timestamp.isoformat(),
        "orderIntentId": fill.get("orderIntentId") or fill.get("order_intent_id"),
    }
    return f"regime-local-fill-{_stable_hash(seed)[:16]}"


def _dataclass_to_dict(value: Any) -> dict[str, Any]:
    return {key: getattr(value, key) for key in value.__dataclass_fields__}


def _snapshot_to_dict(snapshot: RegimeLocalPaperAccountSnapshot) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat().replace("+00:00", "Z")
        if isinstance(value, date):
            return value.isoformat()
        if hasattr(value, "__dataclass_fields__"):
            return {key: convert(getattr(value, key)) for key in value.__dataclass_fields__}
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        return value

    return convert(snapshot)


def _records(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


__all__ = [
    "REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE",
    "REGIME_LOCAL_PAPER_ACCOUNT_VERSION",
    "REGIME_LOCAL_PAPER_SOURCE_AUTHORITY",
    "RegimeLocalPaperAccount",
    "RegimeLocalPaperAccountSnapshot",
    "RegimeLocalPaperFillSnapshot",
    "RegimeLocalPaperLotSnapshot",
    "RegimeLocalPaperPositionSnapshot",
]

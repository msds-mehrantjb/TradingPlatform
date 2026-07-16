from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.domain.feature_engine import (
    BidAskQuote,
    MarketCandle,
    PointInTimeFeatureEngine,
    PointInTimeFeatureRequest,
    PriorDayOHLC,
)
from backend.app.domain.models import AccountRiskState, GateStatus
from backend.app.strategies.registry import directional_voters_from
from backend.app.strategies.safety import (
    CashAvoidTradingConfig,
    CashAvoidTradingSafety,
    SafetyEvaluationContext,
    SafetyOperationalState,
)


SESSION_DATE = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
NOW = OPEN_UTC + timedelta(minutes=80)


def candle_at(minute: int, symbol: str = "SPY") -> MarketCandle:
    base = 100 + minute * 0.02
    return MarketCandle(
        timestamp=OPEN_UTC + timedelta(minutes=minute),
        open=base - 0.01,
        high=base + 0.08,
        low=base - 0.08,
        close=base + 0.02,
        volume=120000,
        tradeCount=1000 + minute,
        provider="fixture",
        symbol=symbol,
        timeframe="1Min",
    )


def candles(symbol: str = "SPY", count: int = 90) -> list[MarketCandle]:
    return [candle_at(minute, symbol) for minute in range(count)]


def timeframe_history(*, timeframe: str, step_minutes: int) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    start = NOW - timedelta(minutes=step_minutes * 89)
    for index in range(90):
        base = 100 + index * 0.02
        rows.append(
            MarketCandle(
                timestamp=start + timedelta(minutes=step_minutes * index),
                open=base - 0.01,
                high=base + 0.08,
                low=base - 0.08,
                close=base + 0.02,
                volume=120000,
                tradeCount=2000 + index,
                provider="fixture",
                symbol="SPY",
                timeframe=timeframe,  # type: ignore[arg-type]
            )
        )
    return rows


def snapshot(spread: float = 0.04, event: dict | None = None):
    spy = candles()
    evaluation = spy[-1].timestamp
    return PointInTimeFeatureEngine().compute(
        PointInTimeFeatureRequest(
            evaluationTimestamp=evaluation,
            sessionDate=SESSION_DATE,
            spy1mCandles=spy,
            spy5mCandles=timeframe_history(timeframe="5Min", step_minutes=5),
            spy15mCandles=timeframe_history(timeframe="15Min", step_minutes=15),
            sessionVwap=100.5,
            sessionVwapTimestamp=evaluation,
            qqqAlignedCandles=candles("QQQ"),
            iwmAlignedCandles=candles("IWM"),
            priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
            quote=BidAskQuote(bid=spy[-1].close - spread / 2, ask=spy[-1].close + spread / 2, timestamp=evaluation),
            economicEventState=event or {"active": False, "category": "none"},
            breadthComponents={"XLK": candles("XLK"), "XLF": candles("XLF")},
        )
    )


def account_state(**updates) -> AccountRiskState:
    state = AccountRiskState(
        accountId="paper-account",
        equity=25000,
        buyingPower=10000,
        openPositionNotional=0,
        realizedPnlToday=0,
        tradesToday=0,
        observedAt=NOW,
        sessionDate=SESSION_DATE,
    )
    return state.model_copy(update=updates)


def operational_state(**updates) -> SafetyOperationalState:
    state = SafetyOperationalState(
        marketOpen=True,
        eventBlackoutActive=False,
        haltOrLuld=False,
        circuitBreaker=False,
        brokerAccountRestricted=False,
        manualCashMode=False,
        restrictionExplanation=None,
        observedAt=NOW,
    )
    return state.model_copy(update=updates)


def evaluate(
    *,
    order_intent: str = "new_entry",
    config: CashAvoidTradingConfig | None = None,
    feature_snapshot=None,
    ops: SafetyOperationalState | None = None,
    account: AccountRiskState | None = None,
):
    module = CashAvoidTradingSafety(config)
    return module.evaluate(
        SafetyEvaluationContext(
            orderIntent=order_intent,  # type: ignore[arg-type]
            checkedAt=NOW,
            sessionDate=SESSION_DATE,
            accountRiskState=account if account is not None else account_state(),
            operationalState=ops if ops is not None else operational_state(),
            featureSnapshot=feature_snapshot if feature_snapshot is not None else snapshot(),
        )
    )


class CashAvoidTradingSafetyTest(unittest.TestCase):
    def test_cash_safety_is_not_a_directional_voter(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a directional voter"):
            directional_voters_from(["Cash / Avoid Trading Filter"])

    def test_new_entries_are_blocked_when_cash_mode_is_active(self) -> None:
        result = evaluate(config=CashAvoidTradingConfig(manualCashMode=True))

        self.assertEqual(result.status, GateStatus.FAIL.value)
        self.assertFalse(result.eligible)
        self.assertTrue(result.gateResults[0].blocksTrading)
        self.assertIn("safety.manual_cash_mode", result.reasonCodes)
        self.assertGreaterEqual(len(result.reasonCodes), 1)

    def test_protective_exits_remain_allowed_in_cash_mode(self) -> None:
        result = evaluate(order_intent="protective_exit", config=CashAvoidTradingConfig(manualCashMode=True))

        self.assertEqual(result.status, GateStatus.CAUTION.value)
        self.assertTrue(result.eligible)
        self.assertFalse(result.gateResults[0].blocksTrading)
        self.assertIn("safety.manual_cash_mode", result.reasonCodes)
        self.assertIn("safety.intent_allowed:protective_exit", result.reasonCodes)

    def test_unknown_critical_operational_state_fails_closed_for_new_entries(self) -> None:
        result = evaluate(ops=operational_state(marketOpen=None))

        self.assertEqual(result.status, GateStatus.FAIL.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("safety.unknown_critical_state:marketOpen", result.reasonCodes)

    def test_unknown_critical_operational_state_does_not_block_reconciliation(self) -> None:
        result = evaluate(order_intent="reconciliation", ops=operational_state(marketOpen=None))

        self.assertEqual(result.status, GateStatus.CAUTION.value)
        self.assertTrue(result.eligible)
        self.assertFalse(result.gateResults[0].blocksTrading)
        self.assertIn("safety.intent_allowed:reconciliation", result.reasonCodes)

    def test_market_closed_and_extreme_spread_block_new_entries(self) -> None:
        result = evaluate(feature_snapshot=snapshot(spread=0.50), ops=operational_state(marketOpen=False))

        self.assertFalse(result.eligible)
        self.assertIn("safety.market_closed", result.reasonCodes)
        self.assertIn("safety.extreme_spread", result.reasonCodes)

    def test_daily_loss_limit_blocks_new_entries(self) -> None:
        result = evaluate(account=account_state(realizedPnlToday=-800), config=CashAvoidTradingConfig(maxDailyLossPercent=3.0))

        self.assertFalse(result.eligible)
        self.assertIn("safety.daily_loss_limit", result.reasonCodes)

    def test_event_blackout_and_halts_are_explicit(self) -> None:
        event = {"active": True, "importance": "high"}
        result = evaluate(feature_snapshot=snapshot(event=event), ops=operational_state(haltOrLuld=True))

        self.assertFalse(result.eligible)
        self.assertIn("safety.event_blackout", result.reasonCodes)
        self.assertIn("safety.halt_or_luld", result.reasonCodes)

    def test_safe_new_entry_contains_reason_code(self) -> None:
        result = evaluate()

        self.assertEqual(result.status, GateStatus.PASS.value)
        self.assertTrue(result.eligible)
        self.assertEqual(result.reasonCodes, ["safety.new_entries_allowed"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Protocol

from backend.app.algorithms.voting_ensemble import settings as voting_ensemble_settings
from backend.app.algorithms.voting_ensemble.backtesting_adapter import run_voting_ensemble_backtest
from backend.app.algorithms.voting_ensemble.candidate_dataset import (
    VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH,
    VotingEnsembleCandidateDatasetBuilder,
)
from backend.app.algorithms.voting_ensemble.ensemble import FamilyAwareDeterministicEnsemble
from backend.app.algorithms.voting_ensemble.entry_policy import VotingEnsembleOrderValidator, VotingEnsembleReplayPolicyEngine
from backend.app.algorithms.voting_ensemble.exit_policy import VotingEnsembleExecutionSimulator
from backend.app.algorithms.voting_ensemble.gates import VotingEnsembleLocalGateEngine
from backend.app.algorithms.voting_ensemble.ml_model import voting_ensemble_ml_config
from backend.app.algorithms.voting_ensemble.profit_target_policy import VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE
from backend.app.algorithms.voting_ensemble.risk_budget import position_size_for_config as voting_ensemble_position_size_for_config
from backend.app.algorithms.voting_ensemble.stop_loss_policy import VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE
from backend.app.algorithms.voting_ensemble.trade_counter_state import VotingEnsembleTradeCounterState
from backend.app.backtesting import EventDrivenReplayEngine, ReplayComponents, ReplayEngineConfig
from backend.app.backtesting import v1 as backtesting_v1
from backend.app.domain.feature_engine import MarketCandle, PriorDayOHLC
from backend.app.domain.models import Signal
from backend.app.ensemble import v1 as ensemble_v1
from backend.app.execution import v1 as execution_v1
from backend.app.algorithms.voting_ensemble.strategies.context import (
    MarketBreadthMomentumContext,
    RelativeStrengthQqqIwmContext,
)
from backend.app.algorithms.voting_ensemble.strategies.directional import (
    BollingerAtrReversionStrategy,
    FailedBreakoutReversalStrategy,
    FirstPullbackAfterOpenStrategy,
    LiquiditySweepReversalStrategy,
    MultiTimeframeTrendAlignmentStrategy,
)
from backend.app.strategies.regime import AdxAtrRegimeClassifier
from backend.app.strategies import v1 as strategies_v1
from backend.app.trading_policy import v1 as trading_policy_v1


class TradingEngine(Protocol):
    version: str

    def strategy_votes(self, history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> list[str]:
        ...

    def vote_summary(self, history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> dict[str, Any]:
        ...

    def dynamic_risk_config(self, settings_payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def position_size_for_config(self, config: dict[str, Any], *, equity: float, entry_price: float, stop_distance: float) -> tuple[int, float, str]:
        ...

    def run_backtest(self, candles: list[dict[str, Any]], *, timeframe: str, risk_config_override: dict[str, Any] | None = None) -> dict[str, Any]:
        ...


class V1TradingEngine:
    version = "voting_ensemble_v1"

    def strategy_votes(self, history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> list[str]:
        return strategies_v1.strategy_votes(history, prior_close, timeframe=timeframe)

    def vote_summary(self, history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> dict[str, Any]:
        return ensemble_v1.vote_summary(history, prior_close, timeframe=timeframe)

    def dynamic_risk_config(self, settings_payload: dict[str, Any]) -> dict[str, Any]:
        return trading_policy_v1.dynamic_risk_config(settings_payload)

    def position_size_for_config(self, config: dict[str, Any], *, equity: float, entry_price: float, stop_distance: float) -> tuple[int, float, str]:
        return execution_v1.position_size_for_config(
            config,
            equity=equity,
            entry_price=entry_price,
            stop_distance=stop_distance,
        )

    def run_backtest(self, candles: list[dict[str, Any]], *, timeframe: str, risk_config_override: dict[str, Any] | None = None) -> dict[str, Any]:
        return backtesting_v1.run_voting_ensemble_backtest(
            candles,
            timeframe=timeframe,
            risk_config_override=risk_config_override,
        )


class V2TradingEngine:
    version = "voting_ensemble_v2"

    def __init__(self, replay_engine: EventDrivenReplayEngine | None = None) -> None:
        self.replay_engine = replay_engine or EventDrivenReplayEngine(
            ReplayComponents(
                directionalStrategies=(
                    MultiTimeframeTrendAlignmentStrategy(),
                    FirstPullbackAfterOpenStrategy(),
                    FailedBreakoutReversalStrategy(),
                    LiquiditySweepReversalStrategy(),
                    BollingerAtrReversionStrategy(),
                ),
                contextModules=(
                    RelativeStrengthQqqIwmContext(),
                    MarketBreadthMomentumContext(),
                ),
                regimeModule=AdxAtrRegimeClassifier(),
                familyEnsemble=FamilyAwareDeterministicEnsemble(),
                globalGateEngine=VotingEnsembleLocalGateEngine(),
                mlConfig=voting_ensemble_ml_config(),
                policyEngine=VotingEnsembleReplayPolicyEngine(),
                orderValidator=VotingEnsembleOrderValidator(),
                executionSimulator=VotingEnsembleExecutionSimulator(),
                sessionStateFactory=VotingEnsembleTradeCounterState,
                featureBuilder=VotingEnsembleCandidateDatasetBuilder(),
            ),
            ReplayEngineConfig(
                minWarmupCandles=1,
                defaultTargetDistance=VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE,
                defaultStopDistance=VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE,
                featureSchemaHash=VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH,
                configurationHash="active_voting_ensemble_v2",
            ),
        )

    def strategy_votes(self, history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> list[str]:
        snapshot = self._decide(history, prior_close)
        return [self._display_signal(signal.get("signal")) for signal in snapshot.strategyOutputs]

    def vote_summary(self, history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> dict[str, Any]:
        snapshot = self._decide(history, prior_close)
        decision = snapshot.ensembleDecision
        strategy_outputs = snapshot.strategyOutputs
        buy_votes = sum(1 for signal in strategy_outputs if signal.get("eligible") and signal.get("signal") == Signal.BUY.value)
        sell_votes = sum(1 for signal in strategy_outputs if signal.get("eligible") and signal.get("signal") == Signal.SELL.value)
        hold_votes = sum(1 for signal in strategy_outputs if signal.get("eligible") and signal.get("signal") == Signal.HOLD.value)
        return {
            "engineVersion": self.version,
            "algorithmVersion": decision.get("engineVersion", "family_aware_deterministic_ensemble_v1"),
            "signal": self._display_signal(decision.get("signal")),
            "rawSignal": decision.get("signal"),
            "buyVotes": buy_votes,
            "sellVotes": sell_votes,
            "holdVotes": hold_votes,
            "voteStrength": abs(float(decision.get("finalScore") or 0.0)),
            "confidence": float(decision.get("confidence") or 0.0),
            "regime": (snapshot.regimeState or {}).get("label", "UNKNOWN"),
            "configurationHash": decision.get("configurationHash"),
            "reasonCodes": decision.get("reasonCodes", []),
            "explanation": decision.get("explanation", "Voting Ensemble V2 decision."),
        }

    def dynamic_risk_config(self, settings_payload: dict[str, Any]) -> dict[str, Any]:
        return voting_ensemble_settings.dynamic_risk_config(settings_payload)

    def position_size_for_config(self, config: dict[str, Any], *, equity: float, entry_price: float, stop_distance: float) -> tuple[int, float, str]:
        return voting_ensemble_position_size_for_config(config, equity=equity, entry_price=entry_price, stop_distance=stop_distance)

    def run_backtest(self, candles: list[dict[str, Any]], *, timeframe: str, risk_config_override: dict[str, Any] | None = None) -> dict[str, Any]:
        return run_voting_ensemble_backtest(
            candles,
            timeframe=timeframe,
            risk_config_override=risk_config_override,
        )

    def _decide(self, history: list[dict[str, Any]], prior_close: float):
        candles = [MarketCandle.model_validate(self._market_candle_payload(row)) for row in history]
        if not candles:
            raise ValueError("Voting Ensemble V2 requires at least one candle")
        evaluation_at = candles[-1].timestamp
        session_date = evaluation_at.date()
        prior_day = PriorDayOHLC(
            sessionDate=session_date - timedelta(days=1),
            open=prior_close,
            high=prior_close,
            low=prior_close,
            close=prior_close,
        )
        return self.replay_engine.decide_at(
            symbol=str(candles[-1].symbol or "SPY"),
            sessionDate=session_date,
            evaluationTimestamp=evaluation_at,
            spy1mCandles=candles,
            spy5mCandles=candles,
            spy15mCandles=candles,
            qqqCandles=candles,
            iwmCandles=candles,
            priorDayOHLC=prior_day,
            breadthComponents={},
            economicEventState={"active": False, "source": "active_voting_ensemble_v2"},
        )

    @staticmethod
    def _display_signal(signal: Any) -> str:
        normalized = str(signal or Signal.HOLD.value).upper()
        if normalized == Signal.BUY.value:
            return "Buy"
        if normalized == Signal.SELL.value:
            return "Sell"
        return "Hold"

    @staticmethod
    def _market_candle_payload(row: dict[str, Any]) -> dict[str, Any]:
        timeframe = row.get("timeframe")
        return {
            "timestamp": row["timestamp"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row.get("volume", 0),
            "tradeCount": row.get("tradeCount", row.get("trade_count")),
            "provider": row.get("provider", row.get("feed", "market_data")),
            "symbol": row.get("symbol"),
            "timeframe": timeframe if timeframe in {"1Min", "5Min", "15Min"} else None,
        }

    @staticmethod
    def _display_trade(trade: dict[str, Any]) -> dict[str, Any]:
        side = "Long" if trade.get("side") == Signal.BUY.value else "Short"
        pnl = round(float(trade.get("pnl") or 0.0), 2)
        entry = float(trade.get("entryPrice") or 0.0)
        exit_price = float(trade.get("exitPrice") or entry)
        return {
            "side": side,
            "entryAt": trade.get("filledAt") or trade.get("submittedAt"),
            "exitAt": trade.get("exitAt"),
            "entryPrice": entry,
            "exitPrice": exit_price,
            "quantity": int(trade.get("quantity") or 0),
            "pnl": pnl,
            "expenses": round(float((trade.get("costs") or {}).get("total") or 0.0), 2),
            "exitReason": trade.get("exitStatus") or "Open",
            "strategy": "Voting Ensemble V2",
        }

    @staticmethod
    def _empty_backtest(starting_capital: float, timeframe: str) -> dict[str, Any]:
        return {
            "dateLabel": "No candles",
            "trades": [],
            "totalPnl": 0,
            "totalReturnPercent": 0,
            "startingCapital": starting_capital,
            "finalEquity": starting_capital,
            "maxDrawdown": 0,
            "maxDrawdownPercent": 0,
            "grossProfit": 0,
            "grossLoss": 0,
            "totalExpenses": 0,
            "profitFactor": None,
            "averageWin": 0,
            "averageLoss": 0,
            "expectancy": 0,
            "winners": 0,
            "losers": 0,
            "bars": 0,
            "sessions": 0,
            "riskConfig": {},
            "timeframe": timeframe,
            "strategyDescription": "Voting Ensemble V2 event replay",
            "totalTrades": 0,
        }

    @staticmethod
    def _backtest_metrics(
        *,
        trades: list[dict[str, Any]],
        bars: int,
        sessions: int,
        starting_capital: float,
        timeframe: str,
        date_label: str,
    ) -> dict[str, Any]:
        total_pnl = round(sum(float(trade.get("pnl") or 0.0) for trade in trades), 2)
        gross_profit = round(sum(float(trade.get("pnl") or 0.0) for trade in trades if float(trade.get("pnl") or 0.0) > 0), 2)
        gross_loss = round(abs(sum(float(trade.get("pnl") or 0.0) for trade in trades if float(trade.get("pnl") or 0.0) < 0)), 2)
        winners = sum(1 for trade in trades if float(trade.get("pnl") or 0.0) > 0)
        losers = sum(1 for trade in trades if float(trade.get("pnl") or 0.0) < 0)
        final_equity = round(starting_capital + total_pnl, 2)
        max_drawdown = abs(min(0.0, total_pnl))
        return {
            "dateLabel": date_label,
            "trades": trades,
            "totalPnl": total_pnl,
            "totalReturnPercent": round(((final_equity - starting_capital) / starting_capital) * 100, 2) if starting_capital else 0,
            "startingCapital": starting_capital,
            "finalEquity": final_equity,
            "maxDrawdown": round(max_drawdown, 2),
            "maxDrawdownPercent": round((max_drawdown / starting_capital) * 100, 2) if starting_capital else 0,
            "grossProfit": gross_profit,
            "grossLoss": gross_loss,
            "totalExpenses": round(sum(float(trade.get("expenses") or 0.0) for trade in trades), 2),
            "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
            "averageWin": round(gross_profit / winners, 2) if winners else 0,
            "averageLoss": round(gross_loss / losers, 2) if losers else 0,
            "expectancy": round(total_pnl / len(trades), 2) if trades else 0,
            "winners": winners,
            "losers": losers,
            "bars": bars,
            "sessions": sessions,
            "riskConfig": {},
            "timeframe": timeframe,
            "strategyDescription": "Voting Ensemble V2 event replay",
            "totalTrades": len(trades),
        }


def trading_engine_for_config(application_config: dict[str, Any] | None = None) -> TradingEngine:
    flags = ((application_config or {}).get("featureFlags") or {}) if isinstance(application_config, dict) else {}
    if flags.get("deterministicV2RollbackMode") == "V1":
        return V1TradingEngine()
    if flags.get("strategyEngineV2Enabled") and flags.get("familyEnsembleV2Enabled"):
        return V2TradingEngine()
    return V1TradingEngine()

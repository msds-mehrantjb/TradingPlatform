"""Meta-Strategy-owned safety strategy implementations."""

from __future__ import annotations

from backend.app.algorithms.meta_strategy.strategies.safety.cash_avoid_trading import CashAvoidTradingFilterStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.economic_event_blackout import EconomicEventBlackoutFilterStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.excessive_spread import ExcessiveSpreadFilterStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.extreme_volatility import ExtremeVolatilityFilterStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.halt_luld import HaltLuldFilterStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.insufficient_liquidity import InsufficientLiquidityFilterStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.local_risk_controls import (
    DailyLossLimitFilterStrategy,
    DuplicateOrderProtectionFilterStrategy,
    ExistingPositionPolicyFilterStrategy,
    LocalRiskBudgetFilterStrategy,
    TradeCountLimitFilterStrategy,
)
from backend.app.algorithms.meta_strategy.strategies.safety.missing_critical_data import MissingCriticalDataFilterStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.operational_health import OperationalHealthFilterStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.stale_market_data import StaleMarketDataFilterStrategy
from backend.app.algorithms.meta_strategy.strategies.safety.unsupported_session import UnsupportedSessionFilterStrategy


__all__ = [
    "CashAvoidTradingFilterStrategy",
    "DailyLossLimitFilterStrategy",
    "DuplicateOrderProtectionFilterStrategy",
    "EconomicEventBlackoutFilterStrategy",
    "ExcessiveSpreadFilterStrategy",
    "ExistingPositionPolicyFilterStrategy",
    "ExtremeVolatilityFilterStrategy",
    "HaltLuldFilterStrategy",
    "InsufficientLiquidityFilterStrategy",
    "LocalRiskBudgetFilterStrategy",
    "MissingCriticalDataFilterStrategy",
    "OperationalHealthFilterStrategy",
    "SafetySnapshotStrategy",
    "StaleMarketDataFilterStrategy",
    "TradeCountLimitFilterStrategy",
    "UnsupportedSessionFilterStrategy",
]

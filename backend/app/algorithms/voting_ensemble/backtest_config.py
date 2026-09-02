from __future__ import annotations

from typing import Any

from pydantic import Field

from backend.app.algorithms.voting_ensemble.exit_policy import VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES, voting_ensemble_execution_config
from backend.app.algorithms.voting_ensemble.profit_target_policy import VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE
from backend.app.algorithms.voting_ensemble.stop_loss_policy import VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE
from backend.app.domain.models import DomainModel
from backend.app.execution.simulation import ExecutionSimulationConfig


VOTING_ENSEMBLE_BACKTEST_CONFIG_VERSION = "voting_ensemble_backtest_config_v2"

# The history the live producer hands the pipeline on every bar: 390 SPY one-minute
# candles and 240 candles of each auxiliary stream (`finalized_bar_producer.py`). Replay
# used to hand it the entire prefix instead, which was both a parity gap and the reason
# a multi-year replay never finished: every bar re-scanned and re-serialised the whole
# five-minute, QQQ, IWM and breadth histories.
VOTING_ENSEMBLE_LIVE_ONE_MINUTE_HISTORY_LIMIT = 390
VOTING_ENSEMBLE_LIVE_AUXILIARY_HISTORY_LIMIT = 240


def backtest_config_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_BACKTEST_CONFIG_VERSION,
        "voting_ensemble.backtest_config.starting_capital",
        "voting_ensemble.backtest_config.warmup_candles",
        "voting_ensemble.backtest_config.stop_target_defaults",
        "voting_ensemble.backtest_config.execution_defaults",
        "voting_ensemble.backtest_config.decision_record_controls",
        "voting_ensemble.backtest_config.live_history_windows",
    )


def voting_ensemble_execution_stress_scenarios() -> tuple[ExecutionSimulationConfig, ...]:
    baseline = voting_ensemble_execution_config()
    return (
        baseline.model_copy(update={"scenarioName": "baseline_costs"}),
        baseline.model_copy(update={"scenarioName": "two_times_costs", "costMultiplier": 2.0}),
        baseline.model_copy(update={"scenarioName": "three_times_costs", "costMultiplier": 3.0}),
        baseline.model_copy(update={"scenarioName": "elevated_latency", "queueLatencySeconds": 3, "routingLatencySeconds": 2}),
        baseline.model_copy(update={"scenarioName": "stale_quotes", "quoteAgeSeconds": 10.0, "maxQuoteAgeSeconds": 5.0}),
        baseline.model_copy(update={"scenarioName": "thin_liquidity", "liquidityHaircut": 0.10, "partialFillRatio": 0.25}),
        baseline.model_copy(update={"scenarioName": "high_volatility_event_period", "eventShock": True, "volatilitySlippageMultiplier": 2.0, "spreadWideningMultiplier": 2.0}),
        baseline.model_copy(update={"scenarioName": "opening_session_spread_expansion", "openingSessionSpreadMultiplier": 3.0, "spreadWideningMultiplier": 1.5}),
    )


class VotingEnsembleBacktestConfig(DomainModel):
    startingCapital: float = Field(default=100_000.0, gt=0)
    warmupCandles: int = Field(default=40, ge=2)
    targetDistance: float = Field(default=VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE, gt=0)
    stopDistance: float = Field(default=VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE, gt=0)
    quantity: int = Field(default=1, ge=1)
    maximumHoldingMinutes: int = Field(default=VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES, ge=1)
    includeDecisionRecords: bool = True
    maximumDecisionRecords: int | None = Field(default=None, ge=0)
    # The gate configurations a replay ran under. A baseline recorded without these
    # cannot be reproduced: the calendar and the segment map are what determine which
    # bars were vetoed, so they belong in the run's configuration, not around it.
    # The operational posture the replay is simulating. Replay has no operations to
    # measure, so this is an assumption and is written down as one: the default says
    # trading was enabled and in paper mode. Market-open, entry-window and session
    # validity are deliberately left out, so they keep deriving from the bars being
    # replayed and those gates still bind.
    # Exchange-local segment boundaries, so a replay can reproduce a live run that
    # used non-default ones. Shared with the live producer, not a second copy.
    sessionSegments: dict[str, Any] | None = None
    # Replay applied no entry window at all, so it kept taking entries after the live
    # path had stopped for the session. These make replay run the live rule; set
    # applyEntryWindow False to reproduce a baseline recorded before that was fixed.
    applyEntryWindow: bool = True
    sessionStart: str = "09:35"
    newTradesUntil: str = "15:30"
    operationalHealth: dict[str, Any] | None = None
    sessionPolicy: dict[str, Any] | None = None
    eventCalendar: dict[str, Any] | None = None
    # How much history each bar is evaluated against. The defaults are what the live
    # producer supplies, so a replay bar sees the same window a live bar would. The
    # five- and fifteen-minute windows follow from the one-minute limit, because live
    # aggregates them from that same one-minute history.
    oneMinuteHistoryLimit: int = Field(default=VOTING_ENSEMBLE_LIVE_ONE_MINUTE_HISTORY_LIMIT, ge=2)
    auxiliaryHistoryLimit: int = Field(default=VOTING_ENSEMBLE_LIVE_AUXILIARY_HISTORY_LIMIT, ge=1)
    # The resolved live settings this replay was derived from, when it was. A baseline
    # recorded against a settings hash can be reproduced; one recorded against "whatever
    # the defaults were" cannot.
    liveSettingsConfigurationHash: str | None = None
    liveSettingsVersion: str | None = None
    execution: ExecutionSimulationConfig = Field(default_factory=voting_ensemble_execution_config)
    executionStressScenarios: tuple[ExecutionSimulationConfig, ...] = Field(default_factory=voting_ensemble_execution_stress_scenarios)
    configVersion: str = VOTING_ENSEMBLE_BACKTEST_CONFIG_VERSION

    @property
    def fiveMinuteHistoryLimit(self) -> int:
        return max(1, -(-self.oneMinuteHistoryLimit // 5))

    @property
    def fifteenMinuteHistoryLimit(self) -> int:
        return max(1, -(-self.oneMinuteHistoryLimit // 15))


def backtest_config_from_live_settings(settings_payload: dict[str, Any] | None = None, **overrides: Any) -> VotingEnsembleBacktestConfig:
    """Build the replay configuration from the settings the live path resolves.

    The live service resolves its one-minute settings through
    `resolve_one_minute_trading_settings` and reads the session policy, event calendar and
    segment boundaries off that resolved object with the same lookups used here. Deriving
    the replay configuration from that one resolution, rather than restating the values,
    is what keeps the two from drifting: turning a gate on live moves replay with it, and
    the settings hash travels with the result so the baseline names what it ran under.

    Explicit overrides win, so a caller can still pin a recorded run's exact policy.
    """
    from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings

    settings = resolve_one_minute_trading_settings(settings_payload)
    windows = getattr(settings, "sessionWindows", None)
    segments = getattr(windows, "sessionSegments", None) if windows is not None else None
    if segments is None:
        segments = getattr(settings, "sessionSegments", None)
    if hasattr(segments, "model_dump"):
        segments = segments.model_dump(mode="json")
    derived: dict[str, Any] = {
        "startingCapital": float(settings.riskPerTrade.startingCapital),
        "sessionStart": str(windows.sessionStart) if windows is not None else "09:35",
        "newTradesUntil": str(windows.newTradesUntil) if windows is not None else "15:30",
        "sessionSegments": segments if isinstance(segments, dict) else None,
        "sessionPolicy": _dict_or_none(getattr(settings, "sessionPolicy", None)),
        "eventCalendar": _dict_or_none(getattr(settings, "eventCalendar", None)),
        "liveSettingsConfigurationHash": str(settings.configurationHash),
        "liveSettingsVersion": str(settings.settingsVersion),
    }
    derived.update(overrides)
    return VotingEnsembleBacktestConfig(**derived)


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return value if isinstance(value, dict) else None


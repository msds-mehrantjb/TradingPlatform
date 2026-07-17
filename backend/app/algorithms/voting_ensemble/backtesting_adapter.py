from __future__ import annotations

from typing import Any

from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig


VOTING_ENSEMBLE_BACKTESTING_ADAPTER_VERSION = "voting_ensemble_backtesting_adapter_v1"


def backtesting_adapter_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_BACKTESTING_ADAPTER_VERSION,
        "voting_ensemble.backtesting_adapter.request_normalized",
        "voting_ensemble.backtesting_adapter.risk_override_applied",
        "voting_ensemble.backtesting_adapter.dedicated_runner_invoked",
    )


class VotingEnsembleBacktestingAdapter:
    def run_backtest(
        self,
        candles: list[dict[str, Any]],
        *,
        timeframe: str,
        risk_config_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = self.config_from_request(risk_config_override)
        result = VotingEnsembleBacktestRunner(config=config).run(
            symbol=self.symbol_from_candles(candles),
            spy_1m_candles=candles,
            timeframe=timeframe,
        )
        return {
            **result,
            "backtestingAdapterVersion": VOTING_ENSEMBLE_BACKTESTING_ADAPTER_VERSION,
            "adapterReasonCodes": list(backtesting_adapter_reason_codes()),
        }

    def config_from_request(self, risk_config_override: dict[str, Any] | None = None) -> VotingEnsembleBacktestConfig:
        override = risk_config_override or {}
        return VotingEnsembleBacktestConfig(
            startingCapital=float(override.get("startingCapital", 100_000.0)),
            includeDecisionRecords=False,
        )

    def symbol_from_candles(self, candles: list[dict[str, Any]]) -> str:
        if not candles:
            return "SPY"
        return str(candles[-1].get("symbol") or "SPY").upper()


def run_voting_ensemble_backtest(
    candles: list[dict[str, Any]],
    *,
    timeframe: str,
    risk_config_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return VotingEnsembleBacktestingAdapter().run_backtest(
        candles,
        timeframe=timeframe,
        risk_config_override=risk_config_override,
    )

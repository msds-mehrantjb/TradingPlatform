from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.contracts import RegimeHysteresisState
from backend.app.algorithms.regime.hysteresis import confirm_regime_transition
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_state import migrate_regime_runtime_state, runtime_state_to_hysteresis
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.tests.regime.fixtures.classification_cases import classification


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase6_hysteresis"


def test_phase6_state_to_state_transitions_are_reason_coded() -> None:
    settings = {"confirmationBars": 2, "minimumDwellBars": 0}
    initial = confirm_regime_transition(classification(raw_regime="strong_uptrend"), settings=settings)
    held = confirm_regime_transition(classification(raw_regime="strong_uptrend"), initial, settings)
    waiting = confirm_regime_transition(classification(raw_regime="weak_downtrend", direction="weak_down"), held, settings)
    confirmed = confirm_regime_transition(classification(raw_regime="weak_downtrend", direction="weak_down"), waiting, settings)
    safety = confirm_regime_transition(classification(raw_regime="event_risk", event_risk="blackout"), confirmed, {"confirmationBars": 99})
    recovery_waiting = confirm_regime_transition(classification(raw_regime="range_bound", direction="neutral"), safety, settings)

    assert initial.confirmed_regime == "strong_uptrend"
    assert initial.transition_reason == "initial_confirmation"
    assert held.confirmed_regime == "strong_uptrend"
    assert held.bars_in_current_regime == 2
    assert waiting.confirmed_regime == "strong_uptrend"
    assert waiting.candidate_regime == "weak_downtrend"
    assert waiting.candidate_start_time == waiting.transition_evidence["candidateStartTimestamp"]
    assert confirmed.confirmed_regime == "weak_downtrend"
    assert confirmed.previous_regime == "strong_uptrend"
    assert safety.confirmed_regime == "event_risk"
    assert safety.transition_reason == "risk_off_immediate"
    assert recovery_waiting.confirmed_regime == "event_risk"
    assert recovery_waiting.candidate_regime == "range_bound"
    assert recovery_waiting.transition_evidence["requiredConfirmationBars"] == settings["confirmationBars"]


def test_phase6_minimum_dwell_blocks_non_safety_oscillation() -> None:
    previous = RegimeHysteresisState(
        confirmed_regime="strong_uptrend",
        previous_regime=None,
        candidate_regime="weak_downtrend",
        candidate_confirmation_count=2,
        regime_start_time="2026-07-23T15:00:00Z",
        transition_confidence=0.8,
        transition_reason="candidate_waiting",
        bars_in_current_regime=2,
        state_version=4,
    )

    state = confirm_regime_transition(
        classification(raw_regime="weak_downtrend", direction="weak_down", timestamp="2026-07-23T15:03:00Z"),
        previous,
        {"confirmationBars": 2, "minimumDwellBars": 5},
    )

    assert state.confirmed_regime == "strong_uptrend"
    assert state.candidate_regime == "weak_downtrend"
    assert state.transition_reason == "candidate_waiting_minimum_dwell"
    assert state.transition_evidence["minimumDwellSatisfied"] is False


def test_phase6_candidate_replacement_resets_unsafe_oscillation_counter() -> None:
    previous = confirm_regime_transition(classification(raw_regime="strong_uptrend"), settings={"minimumDwellBars": 0})
    first_candidate = confirm_regime_transition(
        classification(raw_regime="weak_downtrend", direction="weak_down"),
        previous,
        {"confirmationBars": 3, "minimumDwellBars": 0},
    )
    replacement = confirm_regime_transition(
        classification(raw_regime="range_bound", direction="neutral"),
        first_candidate,
        {"confirmationBars": 3, "minimumDwellBars": 0},
    )

    assert replacement.confirmed_regime == "strong_uptrend"
    assert replacement.candidate_regime == "range_bound"
    assert replacement.candidate_confirmation_count == 1
    assert replacement.candidate_start_time == replacement.transition_evidence["candidateStartTimestamp"]


def test_phase6_restart_restores_candidate_confirmation_history_from_repository() -> None:
    repository = _repository()
    identity = _identity("restart")
    checkpoint = {
        **identity,
        "schemaVersion": "regime_runtime_state_v1",
        "confirmedRegime": "strong_uptrend",
        "previousConfirmedRegime": None,
        "candidateRegime": "weak_downtrend",
        "candidateStartTimestamp": "2026-07-23T15:01:00Z",
        "candidateConfirmationCount": 2,
        "regimeConfidence": 0.77,
        "regimeStartTimestamp": "2026-07-23T15:00:00Z",
        "lastTransitionTimestamp": "2026-07-23T15:00:00Z",
        "regimeDwellBars": 4,
        "transitionReason": "candidate_waiting",
        "lastProcessedBarTimestamp": "2026-07-23T15:02:00Z",
        "lastDecisionId": "decision-before-restart",
    }
    repository.write_runtime_checkpoint(checkpoint, expected_sequence_version=0)

    restored = migrate_regime_runtime_state(repository.read_runtime_checkpoint(identity), identity, timestamp="2026-07-23T15:03:00Z")
    hysteresis = runtime_state_to_hysteresis(restored)

    assert hysteresis is not None
    assert hysteresis.confirmed_regime == "strong_uptrend"
    assert hysteresis.candidate_regime == "weak_downtrend"
    assert hysteresis.candidate_confirmation_count == 2
    assert hysteresis.candidate_start_time == "2026-07-23T15:01:00Z"
    assert hysteresis.bars_in_current_regime == 4
    assert hysteresis.state_version == 1


def test_phase6_duplicate_and_out_of_order_bars_do_not_mutate_runtime_state() -> None:
    repository = _repository()
    service = RegimeApplicationService(repository)
    identity = _identity("bar-order")
    checkpoint = {
        **identity,
        "schemaVersion": "regime_runtime_state_v1",
        "confirmedRegime": "strong_uptrend",
        "previousConfirmedRegime": None,
        "candidateRegime": "weak_downtrend",
        "candidateStartTimestamp": "2026-07-23T15:04:00Z",
        "candidateConfirmationCount": 1,
        "regimeConfidence": 0.72,
        "regimeStartTimestamp": "2026-07-23T15:00:00Z",
        "lastTransitionTimestamp": "2026-07-23T15:00:00Z",
        "regimeDwellBars": 5,
        "transitionReason": "candidate_waiting",
        "lastProcessedBarTimestamp": "2026-07-23T15:05:00Z",
        "lastDecisionId": "decision-1505",
    }
    repository.write_runtime_checkpoint(checkpoint, expected_sequence_version=0)

    duplicate = service.evaluate({**identity, "marketData": _market_payload("2026-07-23T13:56:00Z", count=70)})
    out_of_order = service.evaluate({**identity, "marketData": _market_payload("2026-07-23T13:55:00Z", count=70)})
    stored = repository.read_runtime_checkpoint(identity)

    assert duplicate["ignoredBar"] is True
    assert duplicate["reasonCodes"] == ("regime.hysteresis.duplicate_bar_ignored",)
    assert out_of_order["ignoredBar"] is True
    assert out_of_order["reasonCodes"] == ("regime.hysteresis.out_of_order_bar_ignored",)
    assert stored["candidateConfirmationCount"] == 1
    assert stored["candidateStartTimestamp"] == "2026-07-23T15:04:00Z"
    assert stored["lastProcessedBarTimestamp"] == "2026-07-23T15:05:00Z"
    assert stored["sequenceVersion"] == 1


def _repository() -> RegimeRepository:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")


def _identity(instance: str) -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": f"phase6-{instance}",
        "accountId": "paper-account-phase6",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }


def _market_payload(start_timestamp: str, *, count: int) -> dict:
    start = datetime.fromisoformat(start_timestamp.replace("Z", "+00:00")).astimezone(UTC)
    candles = []
    price = 100.0
    for index in range(count):
        price += 0.03
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        candles.append(
            {
                "timestamp": timestamp,
                "open": round(price - 0.02, 4),
                "high": round(price + 0.08, 4),
                "low": round(price - 0.08, 4),
                "close": round(price, 4),
                "volume": 150_000 + index,
                "finalized": True,
            }
        )
    return {
        "symbol": "SPY",
        "timeframe": "1Min",
        "primaryCandles": candles,
        "oneMinuteCandles": candles,
        "contextFeeds": {
            "quoteFreshness": {"status": "fresh", "ageMs": 500, "bid": 100.0, "ask": 100.02, "spreadBps": 2.0, "expectedFillQuantity": 100},
            "scheduledEconomicEvent": {"state": "none", "minutesUntilEvent": 999},
        },
    }

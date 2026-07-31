"""Backend-authoritative Regime backtest engine."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.app.algorithms.regime.backtest.execution import estimate_transaction_cost_per_share, simulate_order_execution
from backend.app.algorithms.regime.backtest.ledger import close_trade
from backend.app.algorithms.regime.backtest.metrics import calculate_backtest_metrics
from backend.app.algorithms.regime.backtest.walk_forward import walk_forward_summary
from backend.app.algorithms.regime.configuration import flatten_regime_trading_settings, validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.execution_pipeline import execute_regime_pipeline as _production_execute_regime_pipeline
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.stateful_core import process_regime_bar
from backend.app.algorithms.regime.trade_management import evaluate_regime_exit


REGIME_BACKTEST_ENGINE_VERSION = "regime_backtest_v3_backend"
execute_regime_pipeline = _production_execute_regime_pipeline

REGIME_BACKTEST_PARITY_COMPONENTS = (
    {
        "component": "market_snapshot_builder",
        "paperImplementation": "backend.app.algorithms.regime.market_snapshot.build_regime_market_snapshot",
        "backtestImplementation": "backend.app.algorithms.regime.market_snapshot.build_regime_market_snapshot",
        "adapterBoundary": "data_adapter",
    },
    {
        "component": "classifier_hysteresis_transition_router_strategy_profile_family_local_gates_sizing_entry",
        "paperImplementation": "backend.app.algorithms.regime.stateful_core.process_regime_bar",
        "backtestImplementation": "backend.app.algorithms.regime.stateful_core.process_regime_bar",
        "adapterBoundary": "clock_and_persistence_adapter",
    },
    {
        "component": "strategy_registry_and_implementations",
        "paperImplementation": "backend.app.algorithms.regime.strategy_registry",
        "backtestImplementation": "backend.app.algorithms.regime.strategy_registry",
        "adapterBoundary": "none",
    },
    {
        "component": "cost_model_interface",
        "paperImplementation": "backend.app.algorithms.regime.execution_cost_adapter",
        "backtestImplementation": "backend.app.algorithms.regime.execution_cost_adapter",
        "adapterBoundary": "model_feature_source_adapter",
    },
    {
        "component": "exit_logic_trade_management",
        "paperImplementation": "backend.app.algorithms.regime.trade_management.evaluate_regime_exit",
        "backtestImplementation": "backend.app.algorithms.regime.trade_management.evaluate_regime_exit",
        "adapterBoundary": "broker_fill_and_persistence_adapter",
    },
    {
        "component": "broker_fill_execution_adapter",
        "paperImplementation": "backend.app.algorithms.regime.execution_worker",
        "backtestImplementation": "backend.app.algorithms.regime.backtest.execution.simulate_order_execution",
        "adapterBoundary": "allowed_difference",
    },
    {
        "component": "position_ledger",
        "paperImplementation": "backend.app.algorithms.regime.inventory",
        "backtestImplementation": "backend.app.algorithms.regime.backtest.ledger",
        "adapterBoundary": "persistence_adapter",
    },
)


def run_regime_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = str(payload.get("symbol") or "SPY").upper()
    candles = sorted(payload.get("candles") or payload.get("primaryCandles") or [], key=lambda item: item.get("timestamp", ""))
    settings_snapshot = _settings_snapshot(payload, symbol)
    settings = flatten_regime_trading_settings(settings_snapshot)
    starting_capital = float(payload.get("startingCapital") or settings.get("startingCapital") or 25_000)
    warmup_bars = max(0, int((payload.get("backtest") or {}).get("warmupBars", settings_snapshot.get("backtest", {}).get("warmupBars", 0) if isinstance(settings_snapshot.get("backtest"), dict) else 0)))
    market_model = _market_model(payload)
    account_snapshot = {
        **(payload.get("account") or {}),
        "availableBuyingPower": float((payload.get("account") or {}).get("availableBuyingPower") or starting_capital),
        "remainingAlgorithmRiskDollars": float((payload.get("account") or {}).get("remainingAlgorithmRiskDollars") or starting_capital),
        "globalRiskCapacityQuantity": (payload.get("account") or {}).get("globalRiskCapacityQuantity", 1_000_000),
    }
    identity = _identity(payload, settings_snapshot, symbol)
    context_feeds = payload.get("contextFeeds") or payload.get("context_feeds") or {}
    decisions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    open_trade: dict[str, Any] | None = None
    previous_state: dict[str, Any] | None = payload.get("__regime_previous_state") if isinstance(payload.get("__regime_previous_state"), dict) else None
    seen_decision_ids: set[str] = set()
    for index, candle in enumerate(candles):
        history = candles[: index + 1]
        if index < warmup_bars:
            decisions.append(_warmup_decision(candle, index, warmup_bars, settings_snapshot))
            continue
        snapshot_payload = {
            "symbol": symbol,
            "primaryCandles": history,
            "oneMinuteCandles": history,
            "contextFeeds": _point_in_time_context(context_feeds, candle),
        }
        snapshot = build_regime_market_snapshot(snapshot_payload)
        inventory_snapshot = {
            **identity,
            "dataManifestHash": _data_manifest_hash(symbol, history, snapshot),
            "openPosition": _inventory_position(open_trade),
        }
        output = _process_backtest_bar(snapshot, settings_snapshot, previous_state, inventory_snapshot, account_snapshot)
        previous_state = output.get("nextRuntimeState") if isinstance(output.get("nextRuntimeState"), dict) else previous_state
        decision_id = str(output["decision"].get("decision_id") or output.get("decisionId"))
        duplicate = decision_id in seen_decision_ids
        seen_decision_ids.add(decision_id)
        if open_trade is not None:
            exit_result = evaluate_regime_exit(open_trade, {**candle, "barIndex": index}, output["decision"]["confirmed_state"]["confirmed_regime"], settings)
            if exit_result["action"] != "hold":
                reason = str((exit_result.get("reasonCodes") or ("regime.exit.policy",))[0])
                exit_price = float(exit_result.get("price") or candle.get("close", 0))
                exit_cost = _exit_cost(open_trade, candle, exit_price, settings, market_model)
                trades.append(close_trade(open_trade, candle, exit_price, reason, exit_cost=exit_cost["totalCost"], exit_slippage=exit_cost["slippage"], exit_bar_index=index))
                open_trade = None
        decision_record = _decision_record(output, candle, index, warmup_bars, duplicate, snapshot)
        decisions.append(decision_record)
        intent = output["orderProposal"]
        if open_trade is not None or duplicate or intent is None or not output["orderValidation"].get("valid"):
            if intent is not None and not output["orderValidation"].get("valid"):
                decision_record["execution"] = {"status": "rejected", "reasonCodes": tuple(output["orderValidation"].get("reasonCodes") or ())}
            continue
        execution = simulate_order_execution(intent, candles, start_index=index + 1, settings=settings, market_model=market_model)
        decision_record["execution"] = execution
        if execution["filledQuantity"] > 0:
            open_trade = _open_trade(intent, execution, decision_record, settings)
    if open_trade and candles:
        exit_candle = candles[-1]
        exit_price = float(exit_candle.get("close", 0))
        exit_cost = _exit_cost(open_trade, exit_candle, exit_price, settings, market_model)
        trades.append(
            close_trade(
                open_trade,
                exit_candle,
                exit_price,
                "end_of_backtest",
                exit_cost=exit_cost["totalCost"],
                exit_slippage=exit_cost["slippage"],
                exit_bar_index=len(candles) - 1,
            )
        )
    metrics = calculate_backtest_metrics(trades, decisions, starting_capital)
    walk_forward = walk_forward_summary(
        candles,
        trades,
        folds=int((payload.get("walkForward") or {}).get("folds", 3)),
        holdout_fraction=float((payload.get("walkForward") or {}).get("holdoutFraction", 0.2)),
        minimum_fold_net_profit=(payload.get("walkForward") or {}).get("minimumFoldNetProfit"),
        minimum_holdout_net_profit=(payload.get("walkForward") or {}).get("minimumHoldoutNetProfit"),
    )
    replay = _replay_manifest(symbol, candles, decisions, trades, settings, warmup_bars, market_model)
    daily_session_replays = _daily_session_replays(decisions, trades)
    first_day = str(candles[0].get("timestamp", "na"))[:10] if candles else "na"
    last_day = str(candles[-1].get("timestamp", "na"))[:10] if candles else "na"
    return {
        "algorithmId": "regime",
        "engineVersion": REGIME_BACKTEST_ENGINE_VERSION,
        "authoritativeEngine": "backend.app.algorithms.regime.backtest.engine",
        "productionDecisionCore": "backend.app.algorithms.regime.stateful_core.process_regime_bar",
        "productionSnapshotBuilder": "backend.app.algorithms.regime.market_snapshot.build_regime_market_snapshot",
        "symbol": symbol,
        "candles": len(candles),
        "warmupBars": warmup_bars,
        "decisions": decisions,
        "trades": trades,
        "totalPnl": metrics["netProfit"],
        "metrics": metrics,
        "walkForward": [walk_forward],
        "walkForwardValidation": walk_forward,
        "acceptance": {"accepted": bool(walk_forward["accepted"]), "reasonCodes": _acceptance_reasons(walk_forward)},
        "parity": _parity_report(),
        "replay": replay,
        "dailySessionReplays": daily_session_replays,
        "restartDeterminism": {
            "checkpointPolicy": "previous_runtime_state_replayed_from_each_completed_bar",
            "checkpointCount": sum(1 for decision in decisions if decision.get("runtimeState") is not None),
            "decisionHash": replay["decisionHash"],
            "tradeHash": replay["tradeHash"],
            "resultHash": replay["resultHash"],
        },
        "holdoutPolicy": {
            "untouched": True,
            "optimizationAllowed": False,
            "holdoutFraction": float((payload.get("walkForward") or {}).get("holdoutFraction", 0.2)),
            "reasonCodes": ("regime.backtest.holdout_not_used_for_optimization",),
        },
        "diagnostics": ("backend_authoritative_runtime", "point_in_time_replay", "production_stateful_core", "simulated_paper_execution"),
        "settingsVersion": settings.get("settingsVersion"),
        "settingsSnapshot": settings_snapshot,
        "codeVersion": REGIME_BACKTEST_ENGINE_VERSION,
        "dataManifestVersion": "regime_backtest_manifest_v1",
        "dataManifestHash": _full_manifest_hash(symbol, candles, settings.get("settingsVersion")),
        "artifactPath": f"backend/data/regime-backtests/{symbol}_{first_day}_{last_day}.json",
        "cacheKey": f"{symbol}:{first_day}:{last_day}:{len(candles)}:{settings.get('settingsVersion')}",
        "storageKey": f"regime-backtest:{symbol}:{first_day}:{last_day}:{len(candles)}:{settings.get('settingsVersion')}",
        "failureMessage": None,
    }


def _settings_snapshot(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    supplied = payload.get("__regime_settings_snapshot")
    if isinstance(supplied, dict):
        return supplied
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": payload.get("algorithmInstanceId") or "regime-default",
        "accountId": payload.get("accountId") or "default",
        "runtimeMode": payload.get("runtimeMode") or "backtest",
        "symbol": symbol,
    }
    settings = payload.get("__regime_authoritative_settings") or payload.get("settings") or {}
    sections = settings if isinstance(settings, dict) else {}
    return validate_regime_trading_settings_snapshot({"identity": identity, **sections}).as_dict()


def _process_backtest_bar(
    snapshot: Any,
    settings_snapshot: dict[str, Any],
    previous_state: dict[str, Any] | None,
    inventory_snapshot: dict[str, Any],
    account_snapshot: dict[str, Any],
) -> dict[str, Any]:
    if execute_regime_pipeline is not _production_execute_regime_pipeline:
        output = execute_regime_pipeline(
            {
                "marketData": {"symbol": snapshot.symbol, "primaryCandles": [item.__dict__ for item in snapshot.candles]},
                "__regime_settings_snapshot": settings_snapshot,
                "__regime_previous_state": previous_state,
                "__regime_inventory_snapshot": inventory_snapshot,
                "account": account_snapshot,
            }
        )
        return _normalize_pipeline_output(output, snapshot, settings_snapshot, previous_state, inventory_snapshot)
    return process_regime_bar(
        snapshot=snapshot,
        settings_snapshot=settings_snapshot,
        previous_state=previous_state,
        inventory_snapshot=inventory_snapshot,
        account_snapshot=account_snapshot,
    )


def _normalize_pipeline_output(
    output: dict[str, Any],
    snapshot: Any,
    settings_snapshot: dict[str, Any],
    previous_state: dict[str, Any] | None,
    inventory_snapshot: dict[str, Any],
) -> dict[str, Any]:
    decision = dict(output.get("decision") or {})
    decision.setdefault("settings_version", settings_snapshot.get("settingsVersion"))
    decision.setdefault("profile_version", settings_snapshot.get("profileVersion"))
    decision.setdefault("decision_id", f"regime-backtest-mocked:{snapshot.symbol}:{snapshot.latest.timestamp}")
    decision.setdefault("strategy_outputs", [])
    decision.setdefault("trade_blockers", ())
    decision.setdefault("confirmed_state", {"confirmed_regime": "unknown"})
    proposal = output.get("orderProposal") or output.get("orderIntent")
    if isinstance(proposal, dict):
        proposal = {
            "algorithm_id": "regime",
            "symbol": snapshot.symbol,
            "entry_price": snapshot.latest.close,
            **proposal,
        }
    return {
        **output,
        "decision": decision,
        "orderProposal": proposal,
        "orderValidation": output.get("orderValidation") or {"valid": False, "reasonCodes": ("regime.backtest.mocked_order_validation_missing",)},
        "tradeManagement": output.get("tradeManagement") or {"action": "hold", "reasonCodes": ()},
        "nextRuntimeState": output.get("nextRuntimeState") or previous_state or {},
        "dataManifestHash": inventory_snapshot.get("dataManifestHash"),
        "familyAggregation": output.get("familyAggregation") or {},
        "effectiveProfile": output.get("effectiveProfile") or {},
    }


def _identity(payload: dict[str, Any], settings_snapshot: dict[str, Any], symbol: str) -> dict[str, str]:
    identity = settings_snapshot.get("identity") if isinstance(settings_snapshot.get("identity"), dict) else {}
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": str(payload.get("algorithmInstanceId") or identity.get("algorithmInstanceId") or "regime-default"),
        "accountId": str(payload.get("accountId") or identity.get("accountId") or "default"),
        "runtimeMode": str(payload.get("runtimeMode") or identity.get("runtimeMode") or "backtest"),
        "symbol": symbol,
    }


def _market_model(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("marketModel") or payload.get("executionModel") or {}
    return raw if isinstance(raw, dict) else {}


def _point_in_time_feed(feed: list[dict[str, Any]], candle: dict[str, Any]) -> list[dict[str, Any]]:
    timestamp = str(candle.get("timestamp") or "")
    return [item for item in feed if str(item.get("timestamp") or item.get("t") or "") <= timestamp]


def _point_in_time_context(context_feeds: dict[str, Any], candle: dict[str, Any]) -> dict[str, Any]:
    timestamp = str(candle.get("timestamp") or "")
    filtered: dict[str, Any] = {}
    for name, value in context_feeds.items():
        if isinstance(value, list):
            filtered[name] = [item for item in value if str(item.get("timestamp") or item.get("t") or "") <= timestamp]
        else:
            filtered[name] = value
    return filtered


def _data_manifest_hash(symbol: str, history: list[dict[str, Any]], snapshot: Any) -> str:
    latest = history[-1] if history else {}
    payload = {
        "symbol": symbol,
        "latestTimestamp": latest.get("timestamp"),
        "oneMinuteCount": len(history),
        "fiveMinuteCount": len(getattr(snapshot, "five_minute_candles", ()) or ()),
        "fifteenMinuteCount": len(getattr(snapshot, "fifteen_minute_candles", ()) or ()),
        "higherTimeframePolicy": "derived_point_in_time_from_finalized_one_minute",
        "latestClose": latest.get("close"),
        "latestVolume": latest.get("volume"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _full_manifest_hash(symbol: str, candles: list[dict[str, Any]], settings_version: str | None) -> str:
    payload = {"symbol": symbol, "count": len(candles), "first": candles[0].get("timestamp") if candles else None, "last": candles[-1].get("timestamp") if candles else None, "settingsVersion": settings_version}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _inventory_position(open_trade: dict[str, Any] | None) -> dict[str, Any]:
    if not open_trade:
        return {}
    return {
        "side": open_trade["side"],
        "quantity": open_trade["quantity"],
        "entryPrice": open_trade["entryPrice"],
        "stopPrice": open_trade["stopPrice"],
        "targetPrice": open_trade["targetPrice"],
    }


def _decision_record(output: dict[str, Any], candle: dict[str, Any], index: int, warmup_bars: int, duplicate: bool, snapshot: Any) -> dict[str, Any]:
    decision = output["decision"]
    eligible = [item for item in decision["strategy_outputs"] if item["eligible"]]
    primary_strategy = eligible[0] if eligible else {}
    source_metadata = _record(snapshot.context_feeds.get("marketDataSource") if hasattr(snapshot, "context_feeds") else {})
    local_risk = _record(output.get("localRiskResult"))
    local_details = _record(local_risk.get("details"))
    return {
        "timestamp": candle.get("timestamp"),
        "barIndex": index,
        "signal": decision["signal"],
        "regime": decision["confirmed_state"]["confirmed_regime"],
        "strategyIds": [item["strategy_id"] for item in eligible],
        "primaryStrategyId": primary_strategy.get("strategy_id"),
        "primaryStrategyFamily": primary_strategy.get("family"),
        "orderIntent": output["orderProposal"],
        "tradeManagement": output["tradeManagement"],
        "tradeBlockers": decision["trade_blockers"],
        "settingsVersion": output.get("decision", {}).get("settings_version"),
        "profileVersion": output.get("decision", {}).get("profile_version"),
        "dataManifestHash": output.get("dataManifestHash"),
        "decisionId": decision.get("decision_id") or output.get("decisionId") or f"regime-backtest:SPY:{candle.get('timestamp')}",
        "runtimeState": output.get("nextRuntimeState"),
        "familyAggregation": output.get("familyAggregation"),
        "effectiveProfile": output.get("effectiveProfile"),
        "orderValidation": output.get("orderValidation"),
        "execution": None,
        "duplicateDecision": duplicate,
        "pointInTime": {
            "historyBars": index + 1,
            "warmupBars": warmup_bars,
            "latestTimestamp": candle.get("timestamp"),
            "futureCandlesVisible": 0,
            "higherTimeframePolicy": source_metadata.get("higherTimeframePolicy") or "derived_point_in_time_from_finalized_one_minute",
            "derivedFiveMinuteBars": len(getattr(snapshot, "five_minute_candles", ()) or ()),
            "derivedFifteenMinuteBars": len(getattr(snapshot, "fifteen_minute_candles", ()) or ()),
            "suppliedFiveMinuteBarsIgnoredForEvidence": int(source_metadata.get("suppliedFiveMinuteCount") or 0),
            "suppliedFifteenMinuteBarsIgnoredForEvidence": int(source_metadata.get("suppliedFifteenMinuteCount") or 0),
        },
        "attribution": {
            "algorithmId": "regime",
            "settingsVersion": output.get("decision", {}).get("settings_version"),
            "profileVersion": output.get("decision", {}).get("profile_version"),
            "strategyIds": [item["strategy_id"] for item in eligible],
            "strategyFamilies": sorted({str(item.get("family")) for item in eligible if item.get("family")}),
            "familyAggregation": output.get("familyAggregation"),
        },
        "costModel": {
            "interface": "backend.app.algorithms.regime.execution_cost_adapter",
            "estimate": local_details.get("executionCostEstimate"),
            "gate": local_details.get("executionCostGate"),
        },
    }


def _warmup_decision(candle: dict[str, Any], index: int, warmup_bars: int, settings_snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": candle.get("timestamp"),
        "barIndex": index,
        "signal": "Hold",
        "regime": "warmup",
        "strategyIds": [],
        "orderIntent": None,
        "tradeManagement": {"action": "hold", "reasonCodes": ("regime.backtest.warmup",)},
        "tradeBlockers": ("regime.backtest.warmup",),
        "settingsVersion": settings_snapshot.get("settingsVersion"),
        "profileVersion": settings_snapshot.get("profileVersion"),
        "decisionId": f"regime-backtest-warmup-{index}",
        "runtimeState": None,
        "orderValidation": {"valid": False, "reasonCodes": ("regime.backtest.warmup",)},
        "execution": None,
        "pointInTime": {"historyBars": index + 1, "warmupBars": warmup_bars, "latestTimestamp": candle.get("timestamp"), "futureCandlesVisible": 0},
    }


def _open_trade(intent: dict[str, Any], execution: dict[str, Any], decision_record: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    side = "Long" if intent["side"] == "Buy" else "Short"
    return {
        "tradeId": f"{intent['symbol']}-{decision_record['decisionId']}",
        "algorithmId": "regime",
        "strategyId": decision_record.get("primaryStrategyId"),
        "strategyFamily": decision_record.get("primaryStrategyFamily"),
        "regime": decision_record.get("regime"),
        "sessionPhase": _session_phase(str(decision_record.get("timestamp") or "")),
        "volatilityBucket": _volatility_bucket(decision_record),
        "transactionCostBucket": _transaction_cost_bucket(float(execution.get("totalCostPerShare") or 0.0), float(execution.get("entryPrice") or 1.0)),
        "spreadBucket": _spread_bucket(float(execution.get("spreadPerShare") or 0.0), float(execution.get("entryPrice") or 1.0)),
        "side": side,
        "quantity": int(execution["filledQuantity"]),
        "entryAt": execution["timestamp"],
        "entryBarIndex": int(execution["barIndex"]),
        "entryPrice": float(execution["entryPrice"]),
        "entryReferencePrice": float(execution.get("referencePrice") or execution["entryPrice"]),
        "entryCost": float(execution.get("totalCost") or 0.0),
        "entrySlippage": float(execution.get("slippage") or 0.0),
        "stopPrice": intent["stop_price"],
        "targetPrice": intent["target_price"],
        "settingsVersion": settings.get("settingsVersion"),
    }


def _exit_cost(open_trade: dict[str, Any], candle: dict[str, Any], price: float, settings: dict[str, Any], market_model: dict[str, Any]) -> dict[str, float]:
    spread = _spread_per_share(price, market_model, candle)
    per_share = estimate_transaction_cost_per_share(price, spread, settings, market_model)
    quantity = int(open_trade.get("quantity") or 0)
    return {"totalCost": per_share["totalCostPerShare"] * quantity, "slippage": per_share["slippagePerShare"] * quantity}


def _spread_per_share(price: float, market_model: dict[str, Any], candle: dict[str, Any]) -> float:
    if candle.get("bid") is not None and candle.get("ask") is not None:
        return max(0.0, float(candle["ask"]) - float(candle["bid"]))
    if market_model.get("fixedSpreadPerShare") is not None:
        return max(0.0, float(market_model["fixedSpreadPerShare"]))
    return max(0.0, price * float(market_model.get("spreadBps", 1.0)) / 10_000)


def _session_phase(timestamp: str) -> str:
    minute = timestamp[11:16] if len(timestamp) >= 16 else ""
    if "13:30" <= minute < "14:00":
        return "opening"
    if "19:30" <= minute <= "20:00":
        return "closing"
    return "intraday"


def _volatility_bucket(decision_record: dict[str, Any]) -> str:
    profile = decision_record.get("effectiveProfile") if isinstance(decision_record.get("effectiveProfile"), dict) else {}
    regime = str(decision_record.get("regime") or "")
    if "extreme" in regime or profile.get("noNewEntries"):
        return "extreme_or_risk_off"
    if "high_volatility" in regime or "expansion" in regime:
        return "high"
    if "low_volatility" in regime:
        return "low"
    return "normal"


def _transaction_cost_bucket(cost_per_share: float, price: float) -> str:
    bps = (cost_per_share / max(price, 0.01)) * 10_000
    if bps < 2:
        return "low_cost"
    if bps < 8:
        return "medium_cost"
    return "high_cost"


def _spread_bucket(spread_per_share: float, price: float) -> str:
    bps = (spread_per_share / max(price, 0.01)) * 10_000
    if bps < 2:
        return "tight_spread"
    if bps < 8:
        return "normal_spread"
    return "wide_spread"


def _acceptance_reasons(walk_forward: dict[str, Any]) -> tuple[str, ...]:
    reasons: list[str] = []
    if not walk_forward.get("walkForwardStable"):
        reasons.append("regime.backtest.walk_forward_failed")
    holdout = walk_forward.get("holdout") if isinstance(walk_forward.get("holdout"), dict) else {}
    if holdout and not holdout.get("accepted"):
        reasons.append("regime.backtest.holdout_failed")
    return tuple(reasons)


def _parity_report() -> dict[str, Any]:
    return {
        "version": "regime_backtest_paper_parity_v1",
        "deterministicPipelineAuthoritative": True,
        "apiOrFrontendTradingAuthority": False,
        "allowedAdapterDifferences": ("data", "clock", "broker_fill", "persistence"),
        "components": [dict(component, sameAuthoritativeImplementation=component["adapterBoundary"] != "allowed_difference") for component in REGIME_BACKTEST_PARITY_COMPONENTS],
        "reasonCodes": ("regime.backtest.paper_parity_components_declared",),
    }


def _replay_manifest(
    symbol: str,
    candles: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    settings: dict[str, Any],
    warmup_bars: int,
    market_model: dict[str, Any],
) -> dict[str, Any]:
    decision_hash = _stable_hash(_decision_fingerprint(decisions))
    trade_hash = _stable_hash(_trade_fingerprint(trades))
    payload = {
        "engineVersion": REGIME_BACKTEST_ENGINE_VERSION,
        "symbol": symbol,
        "settingsVersion": settings.get("settingsVersion"),
        "candleCount": len(candles),
        "firstTimestamp": candles[0].get("timestamp") if candles else None,
        "lastTimestamp": candles[-1].get("timestamp") if candles else None,
        "warmupBars": warmup_bars,
        "marketModel": market_model,
        "decisionHash": decision_hash,
        "tradeHash": trade_hash,
    }
    return {
        **payload,
        "eventType": "finalized_one_minute_bar_replay",
        "barFinalizationRequired": True,
        "higherTimeframePolicy": "derived_point_in_time_from_finalized_one_minute",
        "executionPolicy": "next_bar_or_explicitly_modeled_execution",
        "partialFillPolicy": "modeled_when_participation_or_available_volume_limits_fill",
        "orderExpirationModeled": True,
        "positionLedger": "backend.app.algorithms.regime.backtest.ledger",
        "deterministic": True,
        "decisionHash": decision_hash,
        "tradeHash": trade_hash,
        "resultHash": _stable_hash(payload),
        "reasonCodes": (
            "regime.backtest.finalized_one_minute_replay",
            "regime.backtest.no_lookahead",
            "regime.backtest.deterministic_versions_and_data",
        ),
    }


def _daily_session_replays(decisions: list[dict[str, Any]], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    days = sorted({str(decision.get("timestamp") or "")[:10] for decision in decisions if decision.get("timestamp")})
    rows: list[dict[str, Any]] = []
    for day in days:
        day_decisions = [decision for decision in decisions if str(decision.get("timestamp") or "").startswith(day)]
        day_trades = [trade for trade in trades if str(trade.get("exitAt") or trade.get("entryAt") or "").startswith(day)]
        rows.append(
            {
                "sessionDate": day,
                "finalizedOneMinuteBars": len(day_decisions),
                "decisions": len(day_decisions),
                "trades": len(day_trades),
                "firstBar": day_decisions[0].get("timestamp") if day_decisions else None,
                "lastBar": day_decisions[-1].get("timestamp") if day_decisions else None,
                "restartCheckpointAvailable": any(decision.get("runtimeState") is not None for decision in day_decisions),
            }
        )
    return rows


def _decision_fingerprint(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": decision.get("timestamp"),
            "decisionId": decision.get("decisionId"),
            "signal": decision.get("signal"),
            "regime": decision.get("regime"),
            "settingsVersion": decision.get("settingsVersion"),
            "profileVersion": decision.get("profileVersion"),
            "orderIntent": _record(decision.get("orderIntent")),
            "execution": _record(decision.get("execution")),
            "runtimeState": decision.get("runtimeState"),
            "dataManifestHash": decision.get("dataManifestHash"),
        }
        for decision in decisions
    ]


def _trade_fingerprint(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "tradeId": trade.get("tradeId"),
            "side": trade.get("side"),
            "quantity": trade.get("quantity"),
            "entryAt": trade.get("entryAt"),
            "exitAt": trade.get("exitAt"),
            "entryPrice": trade.get("entryPrice"),
            "exitPrice": trade.get("exitPrice"),
            "exitReason": trade.get("exitReason"),
            "netPnl": trade.get("netPnl"),
            "settingsVersion": trade.get("settingsVersion"),
            "strategyId": trade.get("strategyId"),
            "strategyFamily": trade.get("strategyFamily"),
            "regime": trade.get("regime"),
        }
        for trade in trades
    ]


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

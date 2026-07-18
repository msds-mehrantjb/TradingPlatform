# Regime V2 Architecture

## Purpose

Regime V2 is a separate trading algorithm with its own strategy catalog, classifier, hysteresis state, decision thresholds, dynamic profiles, position sizing, order-intent creation, trade history, performance statistics, ML artifact policy, and dedicated backtest path.

It may use shared read-only market/account data, logging, persistence, a common execution adapter, and backend-enforced global portfolio risk gates. It must not read or mutate Weighted Voting, WCA, Meta-Strategy, Future Market Prediction, or any other algorithm's learned state, weights, settings, trade history, position-sizing profile, or strategy output.

## Core Modules

| Area | Files | Responsibility |
| --- | --- | --- |
| Shared contracts | `frontend/src/trading/shared/*` | Market data, account, order-intent, and gate-result types shared without algorithm state. |
| Regime core | `frontend/src/algorithms/regime/*` | Pure TypeScript Regime decision logic, strategy routing, aggregation, dynamic profiles, sizing, intent building, diagnostics, and persistence helpers. |
| Regime ML | `frontend/src/algorithms/regime/ml/*` | Point-in-time feature building, artifact validation/loading, conservative prediction, offline labels, validation, and promotion policy. |
| Regime backtest | `frontend/src/algorithms/regime/backtest/*` | Dedicated Regime replay engine, execution simulation, metrics, diagnostics, walk-forward summaries, and runner integration. |
| Backend Regime API | `backend/app/algorithms/regime/*` | Persistence, API routes, and staged paper rollout status. |
| Global risk | `backend/app/risk/*` | Backend-enforced global gates, portfolio/account snapshots, order integrity, reservations, and risk decisions shared across algorithms. |

## Identity And Contracts

These package-root files are owned exclusively by the Regime algorithm:

| Component | Dedicated responsibility |
| --- | --- |
| `index.ts` | Public Regime exports. |
| `types.ts` | Regime decisions, classifications, strategy outputs, profiles, orders, and positions. |
| `versions.ts` | Algorithm, settings, strategy-catalog, and profile versions. |
| `config.ts` | Regime defaults and thresholds. |
| `validation.ts` | Regime configuration and contract validation. |

## Authoritative Flow

```text
MarketDataSnapshot
  -> buildRegimeMarketContext
  -> classifyRawRegime
  -> updateConfirmedRegimeState
  -> routeRegimeStrategies
  -> evaluate context and safety outputs
  -> aggregateRegimeStrategyScores
  -> resolveRegimeDecision
  -> resolveEffectiveRegimeSettings
  -> calculateRegimePositionSize
  -> buildRegimeOrderIntent
  -> backend GlobalPortfolioRiskManager
  -> paper execution adapter
```

Backtesting imports the same Regime core used for paper decisions. There is no Python duplicate of the Regime decision engine.

## Isolation Rules

Regime may import neutral helper utilities only when they are stateless, deterministic, receive inputs explicitly, and do not know which algorithm called them. Regime does not import WCA sizing, WCA target-order adapters, WCA confidence thresholds, WCA storage keys, or WCA backtest results.

The Regime algorithm stops at immutable `RegimeOrderIntent` creation. The global risk layer may approve, resize, or deny the intent, but it must not change the Regime signal, strategy scores, classifier state, profile settings, or learned artifacts.

## Direction Model

Regime separates direction from position effect:

| Signal | Position state | Short entries | Position effect |
| --- | --- | --- | --- |
| `Buy` | flat or long | any | `enter_long` |
| `Buy` | short | any | `cover_short` |
| `Sell` | long | any | `exit_long` |
| `Sell` | flat | disabled | `none` |
| `Sell` | flat | enabled and short gates pass | `enter_short` |
| `Hold` | any | any | `none` |

Allowed Sell decisions remain Sell. A bearish signal while flat cannot become a short entry unless Regime short entries and global short gates are explicitly enabled.

## Definition Of Done

Regime V2 is complete when all of the following remain true:

- Regime business logic is isolated from `frontend/src/main.ts`.
- Allowed Sell decisions remain Sell.
- Regime no longer uses WCA sizing or order adapters.
- Directional, context, and safety roles are separated.
- Strategy aliases cannot double vote.
- Regime classification is deterministic and explainable.
- Hysteresis is configurable and tested.
- Dynamic settings derive from immutable defaults.
- Dynamic risk cannot exceed permitted baseline/global limits.
- Global account risk is enforced across all algorithms.
- Global evaluation is enforced server-side.
- Regime has a dedicated backtest and daily scheduler path.
- Regime archives reference Regime results.
- ML defaults to shadow mode and has no lookahead leakage.
- Other algorithms' outputs remain unchanged.
- Frontend build, frontend tests, and backend tests pass.
- Paper rollout is controlled through feature flags and live trading is not enabled automatically.

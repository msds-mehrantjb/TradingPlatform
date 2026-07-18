# Regime V2 Architecture

## Purpose

Regime V2 is a separate trading algorithm with its own strategy catalog, classifier, hysteresis state, decision thresholds, dynamic profiles, position sizing, order-intent creation, trade history, performance statistics, ML artifact policy, and dedicated backtest path.

It may use shared read-only market/account data, logging, persistence, a common execution adapter, and backend-enforced global portfolio risk gates. It must not read or mutate Weighted Voting, WCA, Meta-Strategy, Future Market Prediction, or any other algorithm's learned state, weights, settings, trade history, position-sizing profile, or strategy output.

## Core Modules

| Area | Files | Responsibility |
| --- | --- | --- |
| Shared contracts | `frontend/src/trading/shared/*` | Market data, account, order-intent, and gate-result types shared without algorithm state. |
| Regime core | `frontend/src/algorithms/regime/*` | Pure TypeScript Regime decision logic, strategy routing, aggregation, dynamic profiles, sizing, intent building, diagnostics, and persistence helpers. |
| Regime classification | `frontend/src/algorithms/regime/classification/*` | Isolated classifier inventory for axes, composite regimes, evidence, hysteresis, transition policy, and no-trade classification. |
| Regime market boundary | `frontend/src/algorithms/regime/market/*` | Immutable Regime market snapshots, read-only feature snapshots, context-feed adapters, quote freshness, indicators, and session context. |
| Regime strategies | `frontend/src/algorithms/regime/strategies/*` | Dedicated strategy inventory for directional strategies, confirmation modules, context modules, safety gates, and aliases. |
| Regime routing | `frontend/src/algorithms/regime/routing/*` | Dedicated compatibility matrix, strategy eligibility, family mapping, conflict resolution, and alias deduplication. |
| Regime decision | `frontend/src/algorithms/regime/decision/*` | Dedicated family aggregation, contribution caps, decision gates, abstention policy, evidence, and decision orchestration. |
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

## Market Snapshot Boundary

Regime consumes shared raw market-data services as read-only input, then converts supplied candles and optional context feeds into its own immutable snapshot boundary:

| Component | Dedicated responsibility |
| --- | --- |
| `market/market-snapshot.ts` | Immutable Regime input snapshot for candles and context feeds. |
| `market/feature-snapshot.ts` | Read-only outbound Regime feature export for analytics and ML consumers. |
| `market/indicators.ts` | Regime-owned indicator export surface. |
| `market/context-feeds.ts` | Adapter for quote freshness, QQQ/IWM relative strength, breadth, VIX, ES futures, scheduled events, halt/LULD, and circuit-breaker state. |
| `market/freshness.ts` | Quote freshness contract and resolver. |
| `market/session-context.ts` | Regime session-phase context. |

## Classification Boundary

The classifier is isolated from strategy voting and execution. It classifies independent axes, builds evidence, applies hysteresis, and derives one canonical composite regime before strategy routing begins.

| Component | Dedicated responsibility |
| --- | --- |
| `classification/classifier.ts` | Public classification workflow exports. |
| `classification/classification-axes.ts` | Direction, volatility, structure, liquidity, session, and event-risk axes. |
| `classification/composite-regimes.ts` | Authoritative composite-regime IDs, legacy aliases, and opportunity tags. |
| `classification/evidence-builder.ts` | Classification feature and evidence exports. |
| `classification/hysteresis.ts` | Confirmed-regime hysteresis exports. |
| `classification/transition-policy.ts` | Risk-off transition policy. |
| `classification/no-trade-classifier.ts` | No-trade reason classification. |

`MarketRegimeId` is reserved for canonical composite market states. Older labels such as `low_volatility`, `trend_continuation`, `bullish_breakout`, and `mean_reversion` are legacy aliases or opportunity tags, not authoritative market-regime identifiers.

## Strategy Inventory

The Regime catalog contains 28 definitions split into four roles:

| Role | Count | Dedicated files | Rule |
| --- | ---: | --- | --- |
| Directional strategies | 14 | `strategies/directional/*` | May emit Buy, Sell, or Hold. |
| Confirmation modules | 2 | `strategies/confirmation/*` | Modify eligibility, confidence, quality, or weight without emitting Buy/Sell votes. |
| Regime-context modules | 2 | `strategies/context/*` | Describe the environment for routing or weighting without acting as primary voters. |
| Safety gates | 10 | `strategies/safety/*` | Execute before order creation and may reject, reduce, or delay entries only. |

Aliases in `strategies/alias-map.ts` map to canonical strategies and must never receive separate votes.

## Strategy Routing

The Regime algorithm owns the mapping between classified regimes and eligible strategies. The compatibility matrix and routing output are Regime-owned and must not be modified by another algorithm.

| Component | Dedicated responsibility |
| --- | --- |
| `routing/router.ts` | Public Regime routing workflow. |
| `routing/compatibility-matrix.ts` | Authoritative regime-to-strategy compatibility matrix and permitted direction. |
| `routing/strategy-eligibility.ts` | Strategy eligibility, incompatible, disabled, unhealthy, and abstention decisions. |
| `routing/regime-family-map.ts` | Strategy-family representation and independent-family participation. |
| `routing/conflict-resolution.ts` | Context compatibility, reliability, and correlation multipliers. |
| `routing/alias-deduplication.ts` | Alias canonicalization and duplicate-vote prevention. |

The routing output records selected strategies, incompatible strategies, permitted direction, represented families, alias deduplication status, independent-family participation, abstentions, disabled strategies, unhealthy strategies, context results, and safety results.

## Decision Boundary

The Regime algorithm owns family aggregation, contribution limits, abstention policy, local decision gates, decision evidence, and final Buy/Sell/Hold resolution.

| Component | Dedicated responsibility |
| --- | --- |
| `decision/family-aggregation.ts` | Family-level Buy/Sell score aggregation. |
| `decision/contribution-caps.ts` | Individual strategy and family contribution caps. |
| `decision/decision-engine.ts` | Authoritative Regime decision workflow. |
| `decision/decision-gates.ts` | Regime-local decision blockers and gate settings. |
| `decision/abstention-policy.ts` | Active directional output and abstention-rate policy. |
| `decision/decision-evidence.ts` | Decision snapshot/evidence creation boundary. |

The decision sequence is:

```text
Immutable market snapshot
  -> raw regime classification
  -> hysteresis and confirmed regime
  -> strategy routing
  -> directional strategy evaluations
  -> confirmation and context adjustments
  -> family-level aggregation
  -> Regime-local decision gates
  -> Buy / Sell / Hold decision
```

The decision path preserves valid Sell decisions and contains no WCA sizing or WCA order adapters.

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

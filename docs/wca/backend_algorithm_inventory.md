# WCA Backend Algorithm Inventory

This inventory records the WCA-owned identity, contract, configuration, and catalog files. WCA remains an isolated backend algorithm under `backend/app/algorithms/wca`, with isolated strategy modules under `backend/app/algorithms/wca/strategies`.

## WCA Identity And Contracts

These files are authoritative and must remain WCA-owned.

| File | Responsibility |
| --- | --- |
| `backend/app/algorithms/wca/__init__.py` | WCA package identity and exports. |
| `backend/app/algorithms/wca/contracts.py` | WCA-specific data models, enums, decisions, strategy evaluations, settings, weights, orders, schema versions, and result contracts. |
| `backend/app/algorithms/wca/configuration.py` | Baseline WCA settings, WCA configuration version, and configuration validation. |
| `backend/app/algorithms/wca/strategy_registry.py` | Authoritative WCA strategy, modifier, hard-filter, and strategy identifier catalogs. |

## Dedicated WCA Market Inputs

| File | Responsibility |
| --- | --- |
| `backend/app/algorithms/wca/market_snapshot.py` | Immutable WCA market-data input construction and validation. |
| `backend/app/algorithms/wca/feature_snapshot.py` | Read-only export of WCA information for analytics or ML consumers. This is outbound only and does not make ML part of WCA. |
| `backend/app/algorithms/wca/market_status.py` | WCA-specific market-condition classification from WCA snapshots. |

The WCA strategy layer reads immutable `WcaMarketSnapshot` inputs and must not directly read or mutate another algorithm's state. `feature_snapshot.py` remains a one-way read-only interface. The acceptance ledger records that WCA does not depend on ML and that ML cannot write into, block, or delay WCA decisions or exits.

## Primary Voter Inventory

The backend WCA catalog defines 11 primary voting strategies. Modifiers and hard filters do not cast primary votes.

| ID | Dedicated WCA strategy | Family | Baseline weight |
| --- | --- | --- | --- |
| C1 | Moving Average Trend | Trend | 0.10 |
| C2 | Trend Pullback | Trend | 0.09 |
| C3 | VWAP Trend Continuation | Trend | 0.09 |
| C4 | VWAP Mean Reversion | Mean reversion | 0.08 |
| C5 | RSI Mean Reversion | Mean reversion | 0.08 |
| C6 | Bollinger/ATR Reversion | Mean reversion | 0.08 |
| C7 | Opening Range Breakout | Breakout | 0.10 |
| C8 | Intraday/Volatility Breakout | Breakout | 0.10 |
| C9 | Failed Breakout Reversal | Reversal | 0.09 |
| C10 | Liquidity Sweep Reversal | Reversal | 0.09 |
| C11 | Gap Continuation/Fade | Event | 0.10 |

Baseline weights total 1.00.

## Dedicated Strategy Files

The WCA strategy package contains one dedicated strategy class implementation per primary voter. `primary_voters.py` registers those classes once and does not maintain duplicate standalone strategy implementations.

```text
strategies/
  __init__.py
  base.py
  indicators.py
  primary_voters.py
  moving_average_trend.py
  trend_pullback.py
  vwap_trend_continuation.py
  vwap_mean_reversion.py
  rsi_mean_reversion.py
  bollinger_atr_reversion.py
  opening_range_breakout.py
  intraday_volatility_breakout.py
  failed_breakout_reversal.py
  liquidity_sweep_reversal.py
  gap_continuation_fade.py
```

These strategy implementations are deterministic and derive evaluations only from the supplied WCA market snapshot and strategy configuration.

## Confidence, Weighting, And Aggregation

These files are core WCA-owned components.

| File | Dedicated responsibility |
| --- | --- |
| `backend/app/algorithms/wca/confidence.py` | Statistical confidence calibration. |
| `backend/app/algorithms/wca/weights.py` | WCA strategy-weight computation and snapshots. |
| `backend/app/algorithms/wca/aggregation.py` | Weighted confidence aggregation and final directional score. |
| `backend/app/algorithms/wca/strategy_registry.py` | Strategy family and baseline-weight metadata. |

The WCA weight system owns baseline weights, performance-derived weights, sample-size reliability, shrinkage toward baseline, time decay, strategy health, regime adjustment, correlation penalties, maximum strategy weight, maximum family concentration, and versioned weight snapshots.

The repository records statistical confidence calibration, leakage-free weighting, reliability/shrinkage, strategy/family caps, and reproducible weight snapshots as completed WCA features. No other algorithm may modify WCA weights or write WCA weight snapshots.

## Dynamic Trading Profile

These files are core WCA-owned dynamic-profile components.

| File | Responsibility |
| --- | --- |
| `backend/app/algorithms/wca/configuration.py` | User baseline/default settings. |
| `backend/app/algorithms/wca/market_status.py` | Current WCA market-status classification. |
| `backend/app/algorithms/wca/dynamic_profile.py` | Bounded effective-settings profile. |

The correct flow is:

```text
WCA baseline settings
  -> WCA market-status classification
  -> defensive bounded adjustments
  -> WCA effective settings for this decision
```

The dynamic profile must not overwrite the user's baseline configuration. The repository records baseline preservation, bounded profiles, defensive-only initial behavior, and market-status hysteresis as passed.

WCA privately owns these dynamic values: entry-score threshold, minimum confidence, minimum agreement, risk percentage, maximum WCA position allocation, stop-distance multiplier, reward/risk requirement, maximum trade count, session restrictions, spread limits, liquidity limits, volatility reductions, and drawdown reductions.

## Decision And Execution Proposal Pipeline

The dedicated WCA execution pipeline is defined by `WCA_EXECUTION_PIPELINE_MODULES` in `backend/app/algorithms/wca/execution_pipeline.py`.

```text
strategy_registry
  -> confidence_calibration
  -> weight_engine
  -> market_status
  -> dynamic_profile
  -> aggregation
  -> local_gates
  -> sizing
  -> order_proposal
  -> order_validation
  -> exits
```

Related dedicated files:

| File | Responsibility |
| --- | --- |
| `backend/app/algorithms/wca/execution_pipeline.py` | Authoritative WCA decision workflow. |
| `backend/app/algorithms/wca/sizing.py` | WCA position sizing and order-proposal construction. |
| `backend/app/algorithms/wca/order_validation.py` | Final WCA order validation. |
| `backend/app/algorithms/wca/exits.py` | WCA protective and strategy exits. |
| `backend/app/algorithms/wca/broker_reconciliation.py` | WCA attribution and broker-state reconciliation boundary. |

WCA position sizing privately calculates proposed quantity from WCA signal strength, WCA confidence and edge, WCA risk allocation, stop distance, available buying power, position-cap limit, liquidity participation, maximum shares, remaining WCA risk budget, and the global-gate quantity cap.

WCA produces an order proposal. Shared broker infrastructure may submit it only after global approval.

## State And Persistence

These files are core WCA-owned state and persistence components.

| File | Responsibility |
| --- | --- |
| `backend/app/algorithms/wca/repository.py` | WCA-specific SQLite persistence. |
| `backend/app/algorithms/wca/service.py` | WCA application service. |
| `backend/app/algorithms/wca/api.py` | WCA API endpoints. |
| `backend/app/algorithms/wca/contracts.py` | Persisted record contracts. |

WCA maintains its own configuration versions, weight snapshots, market-status history, dynamic-profile history, strategy evaluations, decisions, order intents, WCA-attributed orders, WCA-attributed fills, WCA positions, WCA trades, backtest runs, backtest results, shadow-comparison records, paper-stability evidence, and rollout status.

The repository uses shared application configuration and database-path utilities, but WCA owns its repository schema and records. This is acceptable only when every record is explicitly namespaced or attributed to WCA. The authoritative persistence inventory lives in `WCA_PERSISTENCE_RECORD_INVENTORY`, and its tables are WCA-prefixed.

## Backtesting Inventory

The dedicated WCA backtest package contains exactly these files:

```text
backtest/
  __init__.py
  engine.py
  execution.py
  ledger.py
  metrics.py
  reports.py
  walk_forward.py
```

The package owns WCA replay orchestration, point-in-time snapshots, signal generation, next-bar execution, fill simulation, slippage and trading costs, partial-fill simulation, WCA position ledger, WCA trade ledger, WCA metrics, rolling diagnostics, walk-forward testing, untouched holdout testing, WCA reports, and comparison with baseline alternatives.

The authoritative machine-readable inventory lives in `WCA_BACKTEST_INVENTORY` and `WCA_BACKTEST_RESPONSIBILITY_IDS` under `backend/app/algorithms/wca/backtest/__init__.py`. The WCA backtester is backend-authoritative and records evidence for no same-candle signal/fill bias, warm-up handling, costs, open-position drawdown, walk-forward testing, and holdout results.

## Validation And Rollout Inventory

These files are dedicated WCA validation and rollout components.

| File | Responsibility |
| --- | --- |
| `backend/app/algorithms/wca/shadow_comparison.py` | Legacy-versus-new WCA comparison. |
| `backend/app/algorithms/wca/paper_stability.py` | Paper-run stability validation. |
| `backend/app/algorithms/wca/rollout.py` | Controlled WCA rollout and rollback. |
| `backend/app/algorithms/wca/final_acceptance.py` | WCA completion ledger. |
| `backend/app/algorithms/wca/test_coverage.py` | WCA test coverage reporting. |

The authoritative validation and rollout inventory lives in `WCA_VALIDATION_ROLLOUT_FILE_INVENTORY` under `backend/app/algorithms/wca/test_coverage.py`.

The dedicated WCA test suite records coverage for structure, strategy isolation, confidence, weights, market status, dynamic settings, aggregation, gates, sizing, backtesting, persistence, rollout, paper execution, reconciliation, stability, and final acceptance. Test-file presence does not prove that the suite currently passes; `WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION` explicitly records that passing status requires running pytest.

## Shared Platform Components

These components remain shared platform infrastructure rather than being duplicated inside WCA.

| Shared component | Sharing rule |
| --- | --- |
| Raw and normalized market-data services | Read-only input. |
| Clock and market-calendar service | Read-only input. |
| Account-equity and buying-power snapshot | Read-only input. |
| Broker API client | Executes approved proposals only. |
| Global account-risk engine | May reduce or reject WCA risk. |
| Global portfolio-risk ledger | Must preserve algorithm attribution. |
| Global emergency controls | May block new entries. |
| Idempotency service | Must include WCA algorithm and intent identifiers. |
| Broker reconciliation infrastructure | Must preserve WCA ownership. |
| Database connection/path utilities | Infrastructure only. |
| Logging, metrics, and tracing | Must tag records with `algorithm_id=wca`. |
| API framework and authentication | Transport only. |

The authoritative shared-platform contract lives in `WCA_SHARED_PLATFORM_COMPONENT_INVENTORY` under `backend/app/algorithms/wca/contracts.py`.

The shared global-risk engine may constrain WCA by reducing or rejecting WCA risk and blocking new entries. It must not rewrite WCA signals, strategy confidence, strategy weights, WCA thresholds, WCA dynamic profiles, WCA stop logic, or WCA backtest results.

## Dedicated Non-Shared Components

These components must not be shared with another algorithm. Another algorithm may independently implement a similarly named strategy or behavior, but it must have its own implementation, configuration, state, performance history, and tests.

| Dedicated WCA component |
| --- |
| WCA strategies |
| WCA modifier implementations |
| WCA indicator interpretation |
| WCA confidence calibration |
| WCA performance statistics |
| WCA weight snapshots |
| WCA family-correlation state |
| WCA aggregation logic |
| WCA local gates |
| WCA baseline settings |
| WCA dynamic profiles |
| WCA sizing policy |
| WCA exit policy |
| WCA decisions |
| WCA order intents |
| WCA positions and trades |
| WCA backtesting |
| WCA diagnostics |
| WCA rollout state |

The authoritative non-shared component contract lives in `WCA_DEDICATED_COMPONENT_INVENTORY` under `backend/app/algorithms/wca/contracts.py`. Structure tests scan sibling algorithm packages to prevent imports of these WCA-owned modules and references to WCA-owned persistence tables.

## Contextual Modifier Inventory

The WCA catalog defines 11 contextual modifiers. They do not cast independent Buy/Sell votes. They return bounded modifier evaluations that can adjust strategy eligibility, strategy confidence, effective weight, market-status classification, entry permission, and risk or size multipliers.

```text
modifiers/
  __init__.py
  base.py
  vwap_position.py
  volume_confirmation.py
  macd_momentum.py
  market_structure.py
  adx_trend_strength.py
  atr_volatility_regime.py
  multi_timeframe_trend_alignment.py
  relative_strength_vs_qqq_iwm.py
  market_breadth.py
  session_phase.py
  spread_liquidity.py
```

| Modifier | Executable module |
| --- | --- |
| VWAP Position | `vwap_position.py` |
| Volume Confirmation | `volume_confirmation.py` |
| MACD Momentum | `macd_momentum.py` |
| Market Structure | `market_structure.py` |
| ADX Trend Strength | `adx_trend_strength.py` |
| ATR Volatility Regime | `atr_volatility_regime.py` |
| Multi-Timeframe Trend Alignment | `multi_timeframe_trend_alignment.py` |
| Relative Strength vs QQQ/IWM | `relative_strength_vs_qqq_iwm.py` |
| Market Breadth | `market_breadth.py` |
| Session Phase | `session_phase.py` |
| Spread/Liquidity | `spread_liquidity.py` |

## Hard-Filter And Local-Gate Inventory

The WCA registry defines seven hard-filter categories. These are filters, not primary voting strategies.

| Hard filter |
| --- |
| Cash/Avoid Trading |
| Economic Event Risk |
| Invalid or Stale Data |
| Unsafe Spread |
| Unsafe Liquidity |
| Extreme Volatility |
| Session Entry Block |

The executable WCA-local gate layer belongs in `backend/app/algorithms/wca/local_gates.py`. These gates enforce WCA-specific controls and remain separate from account-level global gates.

| Local gate |
| --- |
| Minimum active strategies |
| Minimum directional agreement |
| Minimum average calibrated confidence |
| Minimum aggregate score |
| Minimum winner edge |
| Minimum expectancy after costs |
| Maximum strategy-family concentration |
| Strategy-health eligibility |
| WCA trade-count limit |
| WCA cooldown |
| WCA pyramiding restrictions |
| WCA daily-loss allocation |
| WCA allocated-risk budget |
| Session entry restrictions |
| Dynamic-profile restrictions |

## Current Owned Constants

| Owner | Constants |
| --- | --- |
| `__init__.py` | `WCA_PACKAGE_VERSION` |
| `contracts.py` | `WCA_ALGORITHM_ID`, `WCA_CONTRACT_VERSION`, `WCA_FEATURE_SNAPSHOT_SCHEMA_VERSION`, `WCA_BROKER_RECONCILIATION_SCHEMA_VERSION`, `WCA_SHADOW_COMPARISON_EVIDENCE_SCHEMA_VERSION`, `WCA_PAPER_STABILITY_VALIDATION_SCHEMA_VERSION`, `WCA_SHARED_PLATFORM_COMPONENT_INVENTORY`, `WCA_SHARED_PLATFORM_COMPONENT_IDS`, `WCA_GLOBAL_RISK_FORBIDDEN_REWRITE_TARGETS`, `WCA_GLOBAL_RISK_ALLOWED_CONSTRAINTS`, `WCA_DEDICATED_COMPONENT_INVENTORY`, `WCA_DEDICATED_COMPONENT_IDS`, `WCA_DEDICATED_COMPONENT_OWNER_MODULES`, `WcaOrderValidationContext`, `WcaOrderValidationResult` |
| `configuration.py` | `WCA_CONFIGURATION_VERSION` |
| `strategy_registry.py` | `WCA_STRATEGY_REGISTRY`, `WCA_STRATEGY_IDS`, `WCA_PRIMARY_VOTER_SLUGS`, `WCA_MODIFIER_REGISTRY`, `WCA_MODIFIER_SLUGS`, `WCA_HARD_FILTER_REGISTRY`, `WCA_HARD_FILTER_SLUGS` |
| `local_gates.py` | `WCA_LOCAL_GATE_INVENTORY`, `WCA_LOCAL_GATE_IDS` |
| `weights.py` | `WCA_WEIGHT_SYSTEM_INVENTORY`, `WCA_WEIGHT_SYSTEM_COMPONENT_IDS` |
| `dynamic_profile.py` | `WCA_DYNAMIC_PROFILE_VALUE_INVENTORY`, `WCA_DYNAMIC_PROFILE_VALUE_IDS` |
| `sizing.py` | `WCA_SIZING_INPUT_INVENTORY`, `WCA_SIZING_INPUT_IDS` |
| `repository.py` | `WCA_PERSISTENCE_RECORD_INVENTORY`, `WCA_PERSISTENCE_RECORD_IDS`, `WCA_PERSISTENCE_TABLES` |
| `backtest/__init__.py` | `WCA_BACKTEST_FILE_INVENTORY`, `WCA_BACKTEST_INVENTORY`, `WCA_BACKTEST_RESPONSIBILITY_IDS` |
| `test_coverage.py` | `WCA_VALIDATION_ROLLOUT_FILE_INVENTORY`, `WCA_VALIDATION_ROLLOUT_FILE_NAMES`, `WCA_TEST_SUITE_COVERAGE_INVENTORY`, `WCA_TEST_SUITE_COVERAGE_AREA_IDS`, `WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION` |

The ownership guard lives in `backend/tests/test_wca_step1_backend_structure.py` and fails if these constants are reassigned outside their dedicated owner files.

# WCA Dynamic Settings, Costs, and Latency Controls

Step 13 keeps the persisted WCA baseline configuration as the starting point for every decision. The dynamic resolver applies bounded WCA-owned overlays for market status, event risk, data quality, liquidity, session phase, WCA drawdown, and runtime health. Risk expansion above baseline remains disabled by default, effective values are capped by configuration hard limits, and stale dynamic profiles are rejected instead of being held indefinitely.

The production pipeline, paper adapter, replay adapter, and backtest adapter all call the same `resolve_dynamic_profile` and WCA cost-model adapter. Legacy request cost and expectancy fields remain accepted only at the API compatibility boundary; they do not override the active configuration or the snapshot-derived transaction-cost estimate.

The WCA cost adapter estimates conservative round-trip cost from entry and exit half-spread, expected market impact, expected adverse selection, non-fill or replacement cost, configured fees, configured observed WCA slippage, and the effective uncertainty buffer. New entries are blocked unless conservative gross edge minus conservative round-trip cost minus uncertainty exceeds the configured minimum net edge.

Each persisted decision can carry a complete effective-settings snapshot, cost estimate, and latency snapshot. Runtime finalized-bar events provide bar finalization, event publication, receipt, and snapshot completion timestamps. The paper-broker outbox adds broker request, acknowledgement, first-fill, final-fill, slippage, and fill-quality details to durable broker/outbox payloads.

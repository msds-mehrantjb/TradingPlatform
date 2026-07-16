# Replay Session and Position Rules V2

The event-driven replay engine enforces session and position rules before any simulated new-entry order is submitted.

Implemented rules:

- entry start time,
- new-entry cutoff time,
- end-of-day liquidation time,
- maximum concurrent positions,
- one global symbol exposure state for SPY across algorithms,
- cooldown after entry,
- cooldown after protective stop,
- maximum entries per setup,
- maximum trades per day,
- pyramiding permission,
- duplicate-order prevention.

These restrictions apply only to new entries. Protective exits, bracket exits, and configured end-of-day liquidation remain handled by the execution simulator even when new entries are blocked.

When a new entry is blocked, the replay snapshot keeps the deterministic candidate and records a `NO_ORDER` order plan with explicit validation errors such as `session.new_entry_cutoff`, `session.global_symbol_exposure_limit`, or `session.duplicate_order_prevented`.

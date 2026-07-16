# Dynamic Trading Policy Engine V2

The V2 policy engine lives in `backend/app/trading_policy/`.

Modules:

- `models.py`: versioned policy inputs, outputs, entry/exit plans, cap breakdowns, and config hashing
- `baseline.py`: baseline/fallback risk and holding settings
- `regime_profiles.py`: bounded regime, context, and ML risk multipliers
- `risk_caps.py`: hard risk and notional caps
- `position_sizing.py`: risk/notional/share capped quantity
- `entry_policy.py`: entry plan generation
- `exit_policy.py`: stop, target, and holding-period plan generation
- `engine.py`: deterministic orchestration
- `validator.py`: final no-trade validation

Modes:

- `OFF`: baseline policy only
- `SHADOW`: computes dynamic adjustments but applies baseline sizing
- `ACTIVE`: applies bounded dynamic adjustments
- `FALLBACK`: baseline policy only when dynamic inputs are unavailable or intentionally disabled

Hard limits are always absolute. Favorable signals, regimes, or ML probabilities can never raise approved risk above hard caps.

Risk caps:

Step 42 uses independent bounded caps and applies the most restrictive one. The engine calculates:

- `signalQualityCap`
- `familyAgreementCap`
- `regimeCap`
- `volatilityCap`
- `liquidityCap`
- `eventCap`
- `timeOfDayCap`
- `drawdownCap`
- `MLCap`
- `dataQualityCap`

The effective dynamic multiplier is the minimum applicable cap and is additionally bounded to `<= 1.0`. Favorable caps do not multiply together, and a favorable ML/regime/signal input cannot cancel a severe adverse cap. The limiting cap is recorded in the cap breakdown.

Stop and quantity sizing:

Step 43 calculates stop components independently and uses the widest necessary distance:

- ATR/volatility stop
- minimum percentage stop
- spread/microstructure stop
- strategy structural-invalidation stop

Share quantity is `floor(min(all share caps))`. Returned share caps include:

- `riskBasedShares`
- `orderNotionalShares`
- `positionLimitShares`
- `buyingPowerShares`
- `remainingDailyAllocationShares`
- `liquidityParticipationShares`
- `absoluteMaximumShares`
- `globalExposureShares`

Liquidity participation uses current volume or a conservative expected-volume reference, not a stale average alone. Cross-algorithm exposure is represented by `globalExposureShares`.

Family entry policies:

- Trend/pullback and mean-reversion use limit entries with structural invalidation, maximum chase distance, and short expiration.
- Breakout uses stop-limit when supported, otherwise a confirmed limit/retest fallback when limit orders are supported.
- Reversal requires reclaim/rejection confirmation, enters relative to the reclaimed level, and invalidates beyond the sweep or failed-breakout extreme.
- Gap/session derives the order intent from continuation or fade subtype.

Supported broker order capabilities are checked before producing an entry plan. Unsupported order combinations produce no entry plan and are not submitted.

Dynamic exit policies:

Every accepted trade receives:

- initial protective stop
- profit target
- maximum holding time
- strategy invalidation exit
- end-of-day exit

Break-even stops, trailing stops, partial exits, and pyramiding remain disabled until separately validated. Stop updates reject any change that would move the protective stop farther from entry, and trailing behavior may only maintain or reduce risk. Protective-order quantity follows the actual filled quantity, so partial fills do not create oversized protective orders.

Replay execution honors strategy invalidation before price stop/target, exits on maximum holding time when configured, and retains the existing end-of-day liquidation behavior.

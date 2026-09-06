# Voting Ensemble replay baselines and shadow evidence

Record of what the bar-close audit and its follow-up work changed, and what that did to the
recorded baselines.

## Status: superseded by the 2026-09-05 same-dataset re-record

The reference baseline is now the run recorded at the end of this file, on the dataset the
application actually serves, with a like-for-like before beside it. Every section between
here and there is kept for what it measured, and each states the configuration it ran
under; read them as history, not as the current numbers.

The sections immediately below were written while the baselines genuinely had not moved,
and their reasoning about why individual changes were inert stays correct. What changed is
that the audit fixes, taken together, do move the numbers.

### What the audit expected to change, and did not

The audit set out to make the algorithm close-confirmed. It found it already was. Every
directional trigger compares a **close** to a level:

| Strategy | Trigger | file |
|---|---|---|
| Opening range breakout | `latest.close - range_high` | `strategies/directional/opening_range_breakout.py:116` |
| Liquidity sweep reversal | `reclaimed = latest.close > level` | `strategies/directional/liquidity_sweep_reversal.py:123` |
| Failed breakout reversal | `closed_back_inside = failed.close < level` | `strategies/directional/failed_breakout_reversal.py:116` |
| Multi-timeframe (3 paths) | `current_close > previous_high`, and `latest["close"]` in both other paths | `strategies/directional/multi_timeframe_trend_alignment.py:612, 647, 668` |

Intrabar extremes appear in three roles, none of them an entry gate: level and range
definitions taken from completed bars, **rejection** filters that only ever turn a signal
into a hold (`opening_range_breakout.py:120-126`), and indicator inputs such as ATR.

So no signal logic was rewritten, and replay output is identical. Converting further would
have removed the rejection filters and made the algorithm trade *more*, which is the
opposite of bar-close discipline.

### What did change, and why it does not move baselines

| Change | Effect on replay |
|---|---|
| Session entry window applied live (`99ba553`) | None. `run_voting_ensemble_backtest` already gated on `sessionStart` and `newTradesUntil` (`main.py:7793-7794`). This brought the **live** path up to what replay already did, closing a live/replay divergence rather than creating one |
| Session policy gate (`1899706`) | None while disabled, which is the shipped state |
| Scheduled-event veto (`2edb348`) | None while disabled, which is the shipped state |

Both new gates ship disabled and fall back to disabled on a malformed configuration. Turning
either on **will** move replay output, and that is the point at which baselines must be
re-recorded with the enabling configuration captured alongside them.

## Shadow evidence

Unaffected. The four shadow strategies (S1, S3, S4, S8 in Weighted Voting; Voting Ensemble's
own shadow set is `gap_continuation_fade`, `opening_range_breakout`, `vwap_trend_continuation`)
still hold zero recorded observations, so there is no accumulated evidence for a signal
change to invalidate. Recording began after the last stored session and needs the runtime to
evaluate during live hours.

## A/B: gates off vs gates on

One fixed replay dataset, 390 one-minute SPY bars from 13:30Z on 2026-07-14, with QQQ, IWM
and a three-component breadth set so the ensemble is data-ready. 40 warm-up bars, 351
evaluated bars per arm. Run through the real pipeline end to end, no stub service.

### Trade level, with the event scheduled over two entries

A high-importance event at 17:13Z, giving a 16:58-17:43Z blackout that covers the entries at
17:12Z and 17:14Z:

| Arm | Trades | Wins | Win rate | Avg entry | Net PnL |
|---|---|---|---|---|---|
| gates off | 7 | 1 | 14.3% | 565.81 | -85.04 |
| event veto | 5 | 1 | 20.0% | 571.03 | -45.60 |

The veto removed exactly the two entries inside its window and left the other five untouched.
Win rate and average entry move because two losers were removed, not because anything was
re-weighted: the veto is a filter above the voting layer and does not touch a voter.

### The same dataset with the event at 12:00 ET

With the blackout at 15:45-16:30Z instead, the veto blocks 51 bars but removes no trade: none
of the seven entries fall in that window. Worth recording, because "the gate fired" and "the
gate changed the outcome" are different claims and only the second one is a result.

### Session policy, against real segment labels

The policy keys on a session segment, and until now nothing produced one. The live path
reported `phase: "regular"`, which the policy's alias table normalises to `midday`, and
replay supplied no session state at all, which falls back to the same place. So a policy
written to stand aside at the open or run smaller into the close could only ever see
`midday`, in every environment. The gate was enforcing correctly against a label with one
possible value.

`session_segments.py` now resolves the segment from the bar's end timestamp in exchange-local
time, and both the live producer and replay call it -- one implementation, for the same
reason the event veto is shared. Boundaries are configurable and default to 09:30/10:30/
15:00/16:00 New York. They are read as half-open intervals on the bar end, so a bar ending
at 09:30 is still premarket and one ending at 16:00 is the last bar of the close.

Over the same 390-bar session the segments now come out as 60 `open`, 270 `midday`, 59
`close` and 1 `premarket`, and the policy discriminates between them:

| Arm | Trades | Shares | Wins | Win rate | Net PnL | Entries by segment |
|---|---|---|---|---|---|---|
| policy off | 7 | 121 | 1 | 14.3% | -85.04 | midday 5, close 2 |
| open closed | 7 | 121 | 1 | 14.3% | -85.04 | midday 5, close 2 |
| midday closed | 2 | 32 | 0 | 0.0% | -35.04 | close 2 |
| close closed | 5 | 89 | 1 | 20.0% | -50.00 | midday 5 |
| close at half size | 7 | 105 | 1 | 14.3% | -67.52 | midday 5, close 2 |
| close: reversion only | 5 | 89 | 1 | 20.0% | -50.00 | midday 5 |

Each arm removes exactly the entries in the segment it closes and leaves the others
untouched. Closing the open segment removes nothing, because this session produced no entry
there -- a negative result worth keeping, since an arm that changes nothing is evidence the
gate is keyed on the segment rather than firing indiscriminately.

The half-size arm keeps all seven trades and takes 121 shares down to 105. The two close
entries account for 32 shares, and 16 of them are what the multiplier removed: exactly half,
applied only in the segment configured for it.

### The size multiplier was inert until this run

`apply_session_policy` resolved a `max_position_multiplier` on every bar and returned it on a
decision object that `service.py` assigned and never read. Vote blocking worked, because that
is carried on the votes themselves, but the sizing half of the policy was applied to nothing
and reported nowhere. "Run smaller into the close" was a comment, not a behaviour.

The multiplier now travels as `sessionCap` alongside `dynamicRiskCap`, `eventRiskCap`,
`drawdownCap` and `liquidityCap`, and combines with them the same way, so it is one more cap
in an existing family rather than a parallel mechanism. The policy's reason codes now reach
the decision record too, so a bar that was sized down says so.

### What had to be fixed to measure any of this

Two defects, both found by running the A/B rather than by reading the code:

1. The event veto was lost in the snapshot round trip (`8b5ea70`). The first A/B showed the
   event arm identical to the ungated one, including an entry inside the blackout.
2. Replay could not drive the real pipeline to a fill at all. Every bar failed
   `local_gate.trading_disabled` and `local_gate.account_risk_state_missing`, because the
   runner supplied market data but no operational or account context. The only
   trade-producing replay test in the repository substituted a stub `AlwaysBuyService`, which
   measures the fill simulator rather than the algorithm, and the local risk gates were never
   exercised in replay at all.

The account the runner now supplies is the simulated one and moves as the replay trades, so
the daily-loss, drawdown and exposure gates bind on real numbers. Realised PnL accrues only
once a fill's exit has actually happened: the simulator resolves a trade's whole life at
entry, and accruing any earlier would let the risk gates decide on money the account had not
yet made when the gate ran.

### A note on family support

The ensemble requires two independent families before it will trade
(`minimumIndependentFamilySupport=2`). On a smooth synthetic drift only one family ever
votes, so such a dataset produces no trades however the gates are set — the algorithm
working, not the harness failing. The dataset above swings widely enough for a second family
to have a view: 14 bars clear the family-support gate, 140 fall short.

## Futures readiness phase: what changed and what it moved

Audit and implementation for a bar-close intraday strategy on index futures (MES/MNQ). Six
changes, each its own commit.

| Commit | Change |
|---|---|
| `9541941` | The instrument capability refusal is enforced, as a gate |
| `e73f5b2` | Index futures get their own Globex session shape |
| `aa92f2a` | Entry window applied on both paths, for both session shapes |
| `01e9a5f` | The contract-roll veto gets its dates |
| `616db79` | Index futures are sized by their point value |
| `ec17b5a` | A rotted event calendar no longer reads as a calm market |

### Before and after, one fixed replay dataset

390 one-minute SPY bars from 13:30Z on 2026-07-14, 351 evaluated, real pipeline throughout.
The previous recorded numbers are the first row and are kept rather than replaced.

| Arm | Trades | Wins | Win rate | Avg entry | Net PnL |
|---|---|---|---|---|---|
| Previously recorded (no entry window) | 7 | 1 | 14.3% | 565.81 | -85.04 |
| After this phase, default configuration | 5 | 1 | 20.0% | 554.23 | -50.00 |
| ...with the event veto on over two entries | 3 | 1 | 33.3% | 555.19 | -10.56 |
| ...with the close segment at half size | 5 | 1 | 20.0% | 554.23 | -50.00 |

**Only one change moved the default numbers, and it was a correctness fix.** Replay applied
no entry window at all, so it kept taking entries after the live path had stopped for the
session. The two removed entries are at 15:52 and 15:54 ET, past the 15:30 cutoff live
enforces. Replay had been overstating late-session activity, which is the mirror of the bug
the live entry window was added to fix. `applyEntryWindow: false` reproduces the old
behaviour for anything recorded against it.

The other five changes leave equity replay untouched by construction: the capability gate
passes for SPY, the Globex profile engages only for an instrument declaring
`extended_session`, SPY declares no roll schedule, the contract multiplier is 1.0 for shares
(asserted by test, not assumed), and calendar staleness only applies to a dated event list.

**The half-size close arm shows no effect, and the reason matters.** After the entry window,
no entry survives in the `close` segment for the multiplier to act on -- both close-segment
entries were the ones the window removed. The gate is not inert; it has nothing to do on this
dataset. Reporting it as "no effect" without that explanation would be misleading.

### A Phase 1 finding that was wrong

The audit reported regime weighting as inert -- that `_family_regime_fit` always returned 1.0
because nothing produced `trendFit`, `breakoutFit` and the rest. **That was wrong.** The
classifier emits all five (`adx_atr_regime_classifier.py:186-190`) and they carry real,
differentiated values: on a trending series TREND 0.94 and BREAKOUT 0.84 against REVERSAL
0.18 and MEAN_REVERSION 0.16, which is the intended behaviour.

The error came from a grep truncated by `head -12` while a fixture file held more than twelve
matching lines, and was falsely confirmed by looking for the keys in the decision record,
which does not carry regime features at all. Two weak checks agreeing looked like evidence.
The finding is withdrawn and nothing was implemented against it.

One real observation from checking properly: an unclassifiable regime drives every family fit
to 0.0, which silences all voters rather than falling back to neutral weighting. That is
fail-closed and defensible, but it is a behaviour worth knowing about.

### Not done, deliberately

**MES and MNQ still cannot be traded.** All three capabilities they declare are now built,
but `SUPPORTED_CAPABILITIES` has not been widened, so `require_tradeable` still refuses them
and the gate blocks every bar. Two reasons: there is no CME data source, so the pipeline
would be permitted to trade an instrument it cannot quote; and none of this futures work has
been exercised against a single real MES bar. It is correct by construction and by unit test,
which is not the same as correct, and futures sizing errors are 5x errors. Widening that set
is the act that lets real money size against this code and should be taken deliberately, with
data, not as the tail end of an implementation phase.

**Notional binds before margin on futures.** An MES contract at 5000 index points carries
$25,000 of notional, so $100k of equity holds four regardless of stop size. Futures post
margin (~$1-2k), not notional, so the exposure caps are materially conservative for them.
Safe direction, but a modelling decision to revisit before going live.

**Replay still builds its snapshot directly** rather than constructing a finalized-bar event
through the producer. The structural divergence remains; the behavioural one does not.
`test_voting_ensemble_live_replay_parity.py` pins segment, entry window and event veto to
agree bar for bar, which is what the shared path would have bought. Each of those three
agreed only after a specific defect was fixed, and all three looked fine from one side.

## Full-dataset replay under the live pipeline (recorded 2026-09-02)

Every number recorded above came from one synthetic 390-bar session. Every number stored
on disk before this section came from something else entirely: the legacy engine in
`main.py`, nine hard-coded strategies under a majority vote with none of the gates,
session policy, event veto, risk budget or reliability weighting this pipeline applies.
That engine wrote the June "best baseline" file, every `voting_ensemble_risk_v16_*` cache,
the daily Trading Settings artifact and the ML comparison. The dedicated runner that does
share the live pipeline had never produced a result over real data, and could not: it
re-scanned and re-serialised the whole five-minute, QQQ, IWM and breadth histories on every
one-minute bar, which is quadratic over six years and is what the daily artifact job had
been stuck on for a day when this was found.

### What changed (`98974f8`, `890f3f1`)

| Change | Effect on replay |
|---|---|
| Every legacy-engine result carries `engine`, `engineLabel`, `matchesLiveAlgorithm=false` and a note; the frontend shows the badge | None on numbers. A legacy result can no longer be read as evidence about this algorithm |
| Each bar sees the live producer's windows: 390 SPY one-minute candles (within the session), 240 of each auxiliary stream, 78 five-minute and 26 fifteen-minute bars derived from the one-minute limit. Pinned to the producer's own defaults by test | **None on the 390-bar session** (5 trades, 1 win, -50.00, 351 decisions, re-verified). On multi-session data the auxiliary window is now live's instead of the whole prefix, which is the parity fix |
| The replay configuration is derived from the resolved live settings (`backtest_config_from_live_settings`), and the settings hash travels with the result | None while the gates ship disabled. Turning one on live now moves replay with it instead of leaving replay on stale defaults |
| The cached endpoint starts where QQQ, IWM and breadth context begins and records `effectiveStartDate` | The ensemble is data-not-ready without context, so the excluded years held no trades. They held a day of compute |
| The in-memory intelligence-capture store is bounded (2048 records) | None on numbers. Unbounded, it retained about a megabyte per bar and reached 10 GB in twenty minutes; the live process leaks the same way, one session at a time |

### The recorded run

Dataset `SPY/20260901T202755Z` (prepared 2026-09-01T20:27Z, `feed=iex`), timeframe 1Min,
requested range 2020-07-28 to 2026-09-01. Real pipeline end to end, default configuration,
cache `voting_ensemble_dedicated_v2_1Min_2020-07-28_2026-09-01.json`.

| | |
|---|---|
| Effective start | 2026-06-29, where the context streams begin; 583,869 earlier SPY bars excluded |
| Sessions evaluated | 33 (14,568 one-minute bars, 13,281 decisions after a 40-bar warm-up) |
| Live settings hash | `aed8572a2685bb5c` (`voting_ensemble_one_minute_settings_v1`), capital 25,000, entries 09:35-15:30, session policy off, event calendar off |
| History windows | 390 / 78 / 26 / 240 |
| Trades | 84 (30 long, 54 short), 3 shares each |
| Wins / losses | 39 / 45 |
| Net PnL | **+9.76** (gross +24.88, costs 15.12) |
| Profit factor | 1.11 |
| Expectancy | 0.12 per trade |
| Exits | 52 time stop, 17 protective stop, 15 profit target |
| By family | trend 78 trades, +21.01 net; reversal 6 trades, -11.25 net |
| Wall clock | 5,072 s on one core |

Flat, in other words: the pipeline trades in 32 of 33 sessions, sizes every entry to three
shares under the allocation rule, and gives most of the gross back in costs. That is the
first honest number for this algorithm over real data, and it is nothing like the legacy
figures beside it on disk.

### Three things the run exposed, none fixed here

1. **The dataset is missing 13 sessions.** Aug 11 through Aug 27 have no bars at all
   (Jul 3 is a holiday). The daily refresh runs in `full_history_plus_latest_session` mode,
   so a day the refresh does not run is a day the history never gets. The replay is
   correct for the data it was given; the data is not the period it claims to be.
2. **Every trade is labelled `unknown_regime`.** The classifier runs (family fits are
   real, see the withdrawn finding above); the trade record's regime label reads a key the
   decision does not carry. A by-regime breakdown of this run is therefore empty.
3. **`maxDrawdownPercent` is 0.0 and does not mean no drawdown.** The metric is the
   absolute of a negative total, not a running peak-to-trough. It reports zero for any run
   that ends above water.

### Wall-clock note

Roughly 0.35 s per bar on real data with eleven breadth streams, so a session costs
about two and a half minutes and this dataset about 85 minutes. The daily artifact job
now runs this for 1Min and again for 5Min, so the job takes about three hours where it
previously never finished. The `/api/voting-ensemble/backtest` endpoint no longer computes
on a cache miss: it answers 409 with the expected cache path and the latest artifact job.
It used to compute inside the request thread, and because the frontend gave up after 20 s
while the thread kept going, a few page loads against a fresh dataset were enough to pin
the backend for hours. The cache is produced by the daily job or
`scripts/run_voting_ensemble_dedicated_replay.py`, and the panel shows it once it exists.

## Live engine exits brought up to the simulator (2026-09-02)

The audit of the live decision path found two ways the local paper engine could not
execute what the replay measured. Both are closed.

| Gap | Was | Now |
|---|---|---|
| Time stop | The simulator exits at `filledAt + maximumHoldingMinutes`; the local engine's maintenance loop only evaluated protective legs and end of day, so a live position ran to stop, target or the close. 52 of the 84 baseline exits were time stops | `submit_maximum_holding_exits` runs in the maintenance loop every 15 s, reads the limit the entry order recorded (the algorithm default of 30 when it recorded none), and flattens with a limit at the mark. A flat position clears its `openedAt`, so a re-entry starts its own clock |
| Short protection | The OCO was created only for BUY fills; a short had no stop and no target, end-of-day flattening skipped it, and the stored-order ownership check rejected the short entry's fill outright | Every exit helper keys on signed exposure. A short fill gets a BUY stop-limit above and a BUY limit below, covers resize or cancel them, and end of day covers shorts |

Short entries are now enabled in the runtime (`c296392`): it constructs its worker with
`short_trading_enabled` unless `VOTING_ENSEMBLE_SHORT_TRADING_ENABLED=false` is set. The
gap-through stop (a stop-limit whose limit equals its trigger) is unchanged.

## No fixed trade count (2026-09-02)

The three-trades-per-day cap is gone from the baseline settings, the local gate engine's
default and the local paper engine's default. `maxTradesPerDay=0` now means no cap, and
the day's activity is bounded by what the cap was standing in for: the 2 % daily-loss
limit (which also zeroes the risk multiplier), the 5 % intraday drawdown cap, and the
open-risk and exposure caps. A positive value restores a hard cap, and an overlay still
scales a positive cap but cannot reduce "no cap".

On the recorded 390-bar session this moves nothing: the default run stays at 5 trades,
1 win, -50.00, 351 decisions (re-verified by test). The cap had never bound there. The
full-dataset baseline below is re-run under the new rule, since on real data it did.

## Dynamic exits and loss-budget sizing (2026-09-02)

Three mechanisms replace the fixed geometry (a $1 stop, a 1.5 R target, a static stop
until the time stop). All three live in the decision and are carried on the order plan,
so the backtest simulator and the local paper engine manage a position by the same rule.

| Mechanism | Rule | Where |
|---|---|---|
| Volatility-scaled stop | stop distance = `stopAtrMultiplier` (1.5) x session ATR, floored at `minimumStopDistanceDollars`; the fixed $1 is the fallback when no ATR exists | `service._exit_geometry` |
| Level-aware target | the R-multiple target (1.5 R) unless a structural level (VWAP, opening range, prior day, premarket) sits between `minimumTakeProfitR` (1 R) and it, in which case the level less the spread is the target | `service._exit_geometry`, `_structural_levels` |
| Loss-budget sizing | risk per trade = min(0.5 % of equity x caps, remaining daily loss budget), where the budget is 2 % of equity plus today's net loss less open risk. Zero budget is quantity zero | `service._remaining_daily_loss_budget`, `risk_budget.py` |
| Breakeven and trail | once the mark has moved `breakevenTriggerR` (1 R) in the trade's favour the stop moves to the entry, then trails by `trailingStopR` (1 R) initial-stop distances, ratcheting only in the favourable direction; the simulator applies it on completed closes, the engine every 15 s against the mark | `execution/simulation.trailed_stop`, `paper_execution.trail_protective_stops` |

The settings hash moves to `6d558ae3820428c0` (baseline: no trade cap, ATR 1.5, trigger
1 R, trail 1 R, minimum target 1 R, structural targets on).

**This changes the recorded single-session numbers**, since every trade's stop and target
now depend on that session's ATR and levels. Same 390-bar synthetic session, default
configuration, 351 decisions in both rows:

| Geometry | Trades | Wins | Net PnL | Exits |
|---|---|---|---|---|
| Fixed ($1 stop, 1.5 R, static) | 5 | 1 | -50.00 | time stops and stops |
| Dynamic (ATR stop, level target, trail) | 3 | 1 | -81.90 | 1 profit target, 2 protective stops |

The synthetic tape is a sawtooth with 1.4-point steps, so its ATR is large and the
ATR-scaled stop is wide; the two shorts stopped out for about 4 points each where the fixed
geometry had lost $1 a share. That is the mechanism working on a tape built to swing, not a
verdict on it; the verdict is the real-data pair below. The test pins the new row.

## Sizing is the minimum over caps (2026-09-03)

The baseline used to carry `positionSizingMode: "allocation"` and
`riskBudgetPercentOfOrder: 50`. Neither was ever read by the risk budget. The share count
has one rule: the minimum over every cap, being risk dollars / stop distance, the per-order
allocation, the daily allocation, the maximum position, the maximum share quantity, buying
power and the remaining daily-loss budget. The mode only chose between two descriptions of
the rule, and the percent was echoed into metadata and validated against 100 and nothing
else.

Both are removed from the Voting Ensemble baseline, resolver, settings model, validation
and metadata echo. A payload that still carries either key is ignored, not rejected. The
shared domain model, the legacy engine, the meta strategy, their fixtures and the panel's
"Risk budget %" input keep their own copies; they are separate readers and were left alone
by decision. One consequence to know: the panel's manual-order preview still computes a
risk budget from that input, and the backend no longer reads it, so the preview's share
count and the sized order can disagree. No recorded number moves for this change: it is of
text that never sized a trade. The `positionSizing` string documents the rule.

## Inert controls: wired, removed, or kept documented (2026-09-03)

The audit listed the controls that could not fire live. Each was decided, and the
decision is recorded here so nobody re-audits them as surprises.

Wired (each has a test and a reason code of its own). This is the priority now that the
trade-count cap is gone: the day is bounded by losses, and these two are how an operator
and the loss streak stop it.

- **Kill switch.** `POST /api/voting-ensemble/runtime/kill-switch` throws or clears it
  with a reason; the control file records who, why and when, and the supervisor's
  permission recompute reads it. Every enqueue path already read `killSwitchActive`; it
  just had no setter.
- **Consecutive-loss gate.** The account snapshot now carries today's trailing losing
  streak from the local paper closed trades (a winner resets it), gated at 3 as
  configured. The gate's input used to be a constant zero.

Removed:

- **Signal-fade exit.** Declared on the holding-time policy, consumed by nothing, with no
  path to a consumer. The legacy engine in `main.py` keeps its own.

Approved for removal, then kept after it broke another algorithm:

- **Context conflict limit 0.20.** Removing it was approved on the ground that it can
  never fire, which is true of the Voting Ensemble and only of the Voting Ensemble: two
  context modules capped at `maxContextAdjustmentPerSignal` 0.08 reach 0.072. The family
  aware engine is shared, re-exported as `backend.app.ensemble.family_aware`, and the meta
  strategy's characterization aggregates through it with the default configuration and
  enough conflicting context to cross 0.20. Removing the check flipped a recorded Hold to
  a Buy and failed that algorithm's characterization fixtures. The control is restored and
  documented inert for this algorithm at its definition, with what would make it reachable
  here: a third context module, or a higher per-signal cap.

Kept, documented inert. Each carries a one-line comment at its read site saying why it is
inert and what would feed it:

- **Session policy multipliers** (`session_policy.py`): read as the session cap, but the
  policy ships off, so every segment resolves to 1.0.
- **eventRiskCap, liquidityCap, minimum tradable size, participation limit**
  (`service.py` risk-budget config, `risk_budget.py`): read from the operational snapshot,
  which the producer never populates.
- **Existing-position conflict** (`gates.py` `_risk_limits`): never set; an opposite
  candidate is the reversal exit the paper account nets.
- **Max concurrent positions** (`resolver.py`): no gate reads it; the exposure caps bound
  a second position in practice.
- **Warm-up bars, entry confirmation bars, feed-age limits, decision and queue latency
  limits** (`resolver.py`, `baseline.py`): resolved into the settings, consumed by
  nothing; the producer's freshness checks use their own constants and only
  `commandDeadlineSeconds` reaches a gate.
- **Event-risk state gate** (`adx_atr_regime_classifier.py`): reads keys the snapshot
  never carries, so it always reports clear.
- **Pyramiding, allowed entry hours**: the baseline comments them.

## Golden re-record after the audit fixes (recorded 2026-09-03)

**Superseded by the 2026-09-05 same-dataset re-record at the end of this file.** This run
was compared against a different dataset with a different capital, so its verdict that the
corrected algorithm is simply worse does not survive a like-for-like comparison. Kept for
the configuration it recorded and for the driver arithmetic below, which still holds.

This was the reference baseline. It is the single run taken after every approved audit
change was committed: entries counted as entries, the candidate sized before it is costed,
profile multipliers applied once, gapped protective stops filled at the quote, one starting
equity of 100,000, shorts off with refused shorts shadowed, the family minimum read from
the settings, the sizing mode gone, the kill switch settable and the consecutive-loss input
fed. Prior rows above are kept and stay valid for what they measured.

Dataset `SPY/20260902T200952Z`, timeframe 1Min, requested range 2020-07-28 to 2026-09-02,
cache `voting_ensemble_dedicated_v2_1Min_2020-07-28_2026-09-02.json`. The run it replaced
is beside it as `.before_final_rerecord_2026-09-03.json`.

| | |
|---|---|
| Live settings hash | `54fae2592a09797b` (was `aed8572a2685bb5c` at the first recorded run) |
| Starting capital | 100,000 (final equity 99,966.76) |
| Effective start | 2026-06-29, where the context streams begin |
| Sessions evaluated | 34 (14,962 bars, 13,636 decisions); 16 sessions traded |
| Trades | 32, all long, 8 to 13 shares each |
| Wins / losses | 11 / 21, win rate 34.4% |
| Average R | **-0.233** |
| Net PnL | **-33.24** (gross -9.42, costs 23.82) |
| Profit factor / expectancy | 0.70 / -1.04 per trade |
| Max drawdown | 33.24 dollars, 0.033% of capital, peak to trough |
| Exits | 21 protective stop, 11 profit target, no time stops |
| By family | trend 27 trades -10.95; reversal 5 trades -22.29 |
| Refused shorts (shadow) | 74 trades, **-52.77** |
| Wall clock | 5,655 s on one core |

**This baseline loses money, and that is the honest reading of the algorithm under the
corrected code.** It is not a regression to fix before merging: every driver below is a
change that was asked for and is working as specified.

### Why it is not comparable with the run above it

Four deliberate changes separate the two, and the arithmetic of each is known:

1. **Shorts are off.** The previous run's +9.50 was 58 short trades making +14.43 against
   30 long trades losing -4.93. Removing the short side removes the half that was carrying
   the total. The long side was already negative.
2. **Position sizes are roughly four times larger**, from the 100,000 equity replacing
   25,000. Every entry was 3 shares before; entries here are 8 to 13. Per-share losses
   that cost cents now cost dollars.
3. **The exits actually manage the position.** This is the first recorded run whose trades
   show `execution.stop_trailed` (9), `execution.stop_moved_to_breakeven` (1) and the
   bounded gap fills `execution.stop_gap` and `execution.target_gap`. The previous run
   shows none of these and instead 56 time stops: stops that could not fill drifted to the
   30-minute limit, where they exited near breakeven (14 long time stops, +0.03 between
   them). Those escapes were doing real work in the old total and are gone.
4. **Average holding time fell from 19.1 to 6.3 minutes**, and the longest trade from 30
   minutes to 19. The stop distance realised on exit fell from about 0.98 to 0.36, which
   is the trail and the breakeven move pulling the stop in behind price.

Trade counts, win rate, average R and drawdown may therefore not be compared line to line
with any row above. Only this row describes the shipped configuration.

### What the run says on its own terms

- **The shadow evidence supports keeping shorts off.** The 74 refused shorts, simulated
  apart from the account, lost 52.77. Under the corrected exit geometry the short side is
  not the winner the previous run made it look like.
- **Costs eat the edge.** Gross is -9.42 and costs are 23.82, so even the gross is
  negative before expenses; this is not a spread-and-fees story alone.
- **Both families lose.** Reversal loses 22.29 on 5 trades, trend 10.95 on 27.
- **Two-thirds of trades stop out.** 21 of 32, with the trail contributing 9. A trail that
  fires this often on a 6-minute average hold is a tuning question worth its own
  experiment, not something to change silently under a baseline.

Nothing here is promoted by recording it. Promotion still needs real shadow evidence from
the live path, and this run argues against seeking it yet.

## Reference baseline: same dataset, before and after (recorded 2026-09-05)

This supersedes the 2026-09-03 run above. That one compared two different datasets, which
made every number non-comparable and hid what the fixes actually did. This is the same
dataset, the same 15,355 bars and the same 13,990 decisions, run twice: once by the daily
job on the pre-fix code, once by the corrected code after the branch merged. It is also the
dataset `/api/voting-ensemble/backtest` serves, so these are the numbers the panel shows.

Dataset `SPY/20260903T201027Z`, timeframe 1Min, requested range 2020-07-28 to 2026-09-03,
effective start 2026-06-29 where the context streams begin. The pre-fix run is kept beside
the live cache as `...pre_audit_fixes_20260904.json`.

| | Before (pre-fix) | After (reference) |
|---|---|---|
| Settings hash | `6d558ae3820428c0` | `54fae2592a09797b` |
| Starting capital | 25,000 | 100,000 |
| Sessions evaluated / traded | 35 / 34 | 35 / 16 |
| Trades | 123 (36 long, 87 short) | 32, all long |
| Shares per entry | 3 | 8 to 13 |
| Win rate | 40.7% | 34.4% |
| Average R | -0.242 | **-0.233** |
| Net PnL | -20.63 | -33.24 |
| **Net as % of capital** | **-0.083%** | **-0.033%** |
| Gross / costs | +1.51 / 22.14 | -9.42 / 23.82 |
| Max drawdown (peak to trough) | 20.63, 0.083% of capital | 33.24, **0.033%** of capital |
| Exits | 74 stop, 49 target | 21 stop, 11 target |
| Refused shorts (shadow) | not recorded | 80 trades, -66.19 |

**Read the percentage rows, not the dollar rows.** The dollar loss grows from 20.63 to
33.24 only because the capital grew four times. Against the equity actually at risk the
corrected algorithm loses less than half as much, 0.033% against 0.083%, and its worst
peak-to-trough drawdown falls by the same measure. Average R is also marginally better,
-0.233 against -0.242. An earlier note in this file called the corrected run simply worse;
that was an artifact of comparing across datasets and capital, and it was wrong.

What still holds, and is not flattered by the normalisation:

- **The algorithm loses money on this sample**, in both configurations. Profit factor 0.70,
  expectancy -1.04 a trade, final equity 99,966.76.
- **Costs exceed the edge.** Gross is -9.42 before 23.82 of costs. The pre-fix run had a
  gross of +1.51 and still finished under water once costs were paid.
- **Both families lose**: reversal -22.29 on 5 trades, trend -10.95 on 27.
- **Two-thirds of trades stop out**, 21 of 32.
- **The shadow evidence argues for keeping shorts off.** The 80 refused shorts, simulated
  apart from the account, lost 66.19. The pre-fix run took 87 shorts for real.

### Why the trade count fell from 123 to 32

Three approved changes, in order of size:

1. **Shorts are off.** 87 of the 123 were short. They are recorded as shadow trades now and
   would have lost 66.19.
2. **The family minimum is read from the settings and is 2.** Fewer candidates survive
   aggregation, which is why the long side also fell, from 36 to 32.
3. **Sizing is the minimum over caps against 100,000.** Entries are 8 to 13 shares rather
   than a flat 3, so each trade carries roughly four times the risk and the daily-loss
   budget binds sooner.

Sessions traded fell from 34 to 16: the algorithm now stands aside on more than half the
days it used to trade.

Nothing here is promoted by recording it. Promotion still needs real shadow evidence from
the live path, and this run argues against seeking it yet.

## When to re-record

Re-record, keeping the prior version beside the new one, when any of these happen:

1. `sessionPolicy.enabled` is turned on — capture the segment map used.
2. `eventCalendar.enabled` is turned on — capture the dated calendar used, not just the flag,
   since the calendar is the input that determines which bars were vetoed.
3. Any directional trigger changes from close to something else, or vice versa.
4. ATR, VWAP or swing-detection inputs change. They were deliberately untouched here.
5. `sessionSegments` boundaries change. They decide which segment each bar falls in, so a
   replay under different boundaries applies a different policy to the same bars while
   reporting the same segment names.
6. `applyEntryWindow` is turned off, or `sessionStart`/`newTradesUntil` change. They decide
   which bars may open a position at all.
7. An instrument's `point_value` or `roll_schedule` changes, or `SUPPORTED_CAPABILITIES` is
   widened. The first two change every sized quantity; the third changes what may trade.
8. `oneMinuteHistoryLimit` or `auxiliaryHistoryLimit` change, or the live producer's
   `history_limit` moves away from them. They decide how much history every bar is judged
   against; the test pins them together, so the failure will say which side moved.
9. The dataset's context coverage changes. The effective start follows the first bar at
   which QQQ, IWM and every breadth component exist, so a re-prepared dataset with earlier
   context evaluates more sessions and is not the same run.
10. The resolved live settings hash changes. The replay configuration is derived from it,
    so a different hash is a different configuration whether or not any field looks
    different.
11. `stopAtrMultiplier`, `minimumTakeProfitR`, `structuralTargets`, `breakevenTriggerR`,
    `trailingStopR` or `trailingStopEnabled` change, or the ATR or level inputs they read
    change. They decide every trade's stop, target and post-fill management.

A baseline recorded without its enabling configuration cannot be reproduced, so the
configuration is part of the record, not context around it.

# Voting Ensemble replay baselines and shadow evidence

Record of what the bar-close audit and its follow-up work changed, and what that did to the
recorded baselines.

## Status: baselines unchanged, nothing re-recorded

No previous version has been superseded, so there is no prior copy to keep alongside. The
reason is worth stating rather than assuming, because "no change" is a claim that has to be
earned.

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

A baseline recorded without its enabling configuration cannot be reproduced, so the
configuration is part of the record, not context around it.

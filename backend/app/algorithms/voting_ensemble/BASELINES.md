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

### Session policy

Zero trades in every configuration tried. Every bar of this synthetic dataset segments as
`midday`, so the arm exercises one branch of the segment map with an allow-list that excluded
the strategies that were voting. It shows the gate is wired and enforcing; it says nothing
about whether that segment map is right. The gate stays disabled by default until it has run
against real session labels across `open`, `midday` and `close`.

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

## When to re-record

Re-record, keeping the prior version beside the new one, when any of these happen:

1. `sessionPolicy.enabled` is turned on — capture the segment map used.
2. `eventCalendar.enabled` is turned on — capture the dated calendar used, not just the flag,
   since the calendar is the input that determines which bars were vetoed.
3. Any directional trigger changes from close to something else, or vice versa.
4. ATR, VWAP or swing-detection inputs change. They were deliberately untouched here.

A baseline recorded without its enabling configuration cannot be reproduced, so the
configuration is part of the record, not context around it.

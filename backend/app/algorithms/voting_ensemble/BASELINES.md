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
and a three-component breadth set supplied so the ensemble is data-ready. 40 warm-up bars,
351 evaluated bars per arm. A high-importance CPI event at 16:00Z with the default 15-minute
pre and 30-minute post windows, giving a 15:45-16:30Z blackout.

Measured at the decision layer, at the point a candidate forms
(`voting_ensemble.local_gate.minimum_family_support`):

| Arm | Candidate bars | Effect |
|---|---|---|
| gates off | 11 | baseline |
| event veto only | 10 | vetoed 16:07Z, the one candidate inside the blackout |
| session policy only | 0 | every bar segments as `midday`; the configured midday allow-list excluded the strategies that were voting |
| both gates | 0 | session policy dominates |

The event veto behaved exactly as specified: one candidate suppressed, the ten outside the
window untouched. It took a bug fix to get there — see `8b5ea70`, the veto had been lost in
the snapshot round trip and the first run of this A/B showed the event arm identical to the
ungated one.

### Two limits on this measurement, stated rather than papered over

**No trade-level numbers.** The A/B stops at candidate formation because
`VotingEnsembleBacktestRunner` cannot drive the real pipeline to a fill. Every bar in both
arms carries `local_gate.trading_disabled`, `local_gate.account_risk_state_missing` and
`local_gate.global_upstream_not_provided`: the runner supplies market data but no
operational, account-risk or upstream-gate context, and `run()` exposes no way to provide
it. The repository's only trade-producing replay test
(`test_voting_ensemble_backtest_runner.py:110-129`) substitutes a stub `AlwaysBuyService`,
which measures the fill simulator rather than the algorithm. So trade count, win rate and
average entry are not obtainable this way, and no such table is offered here.

**The session arm is not a tuned result.** All 390 synthetic bars segment as `midday`, so
the arm exercises one branch of the segment map with an allow-list that happened to exclude
the voting strategies. It shows the gate is wired and enforcing; it says nothing about
whether that segment map is the right one. The gate stays disabled by default until it has
been run against real session labels across `open`, `midday` and `close`.

## When to re-record

Re-record, keeping the prior version beside the new one, when any of these happen:

1. `sessionPolicy.enabled` is turned on — capture the segment map used.
2. `eventCalendar.enabled` is turned on — capture the dated calendar used, not just the flag,
   since the calendar is the input that determines which bars were vetoed.
3. Any directional trigger changes from close to something else, or vice versa.
4. ATR, VWAP or swing-detection inputs change. They were deliberately untouched here.

A baseline recorded without its enabling configuration cannot be reproduced, so the
configuration is part of the record, not context around it.

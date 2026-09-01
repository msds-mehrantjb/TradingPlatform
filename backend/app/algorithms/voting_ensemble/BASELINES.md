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

## When to re-record

Re-record, keeping the prior version beside the new one, when any of these happen:

1. `sessionPolicy.enabled` is turned on — capture the segment map used.
2. `eventCalendar.enabled` is turned on — capture the dated calendar used, not just the flag,
   since the calendar is the input that determines which bars were vetoed.
3. Any directional trigger changes from close to something else, or vice versa.
4. ATR, VWAP or swing-detection inputs change. They were deliberately untouched here.

A baseline recorded without its enabling configuration cannot be reproduced, so the
configuration is part of the record, not context around it.

/**
 * The trading window's exit prices have to be the ones the algorithm will use.
 *
 * These assertions restate the backend's `_exit_geometry`
 * (backend/app/algorithms/voting_ensemble/service.py) by hand rather than importing
 * anything from it, so a change on either side shows up as a disagreement here instead of
 * silently drifting: the dashboard would go on quoting stops the pipeline never sets.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  exitGeometryForEntry,
  tradingGeometryFromView,
  type GeometrySourceView,
  type VotingEnsembleTradingGeometry,
} from "../src/trading-settings/voting-ensemble-geometry.ts";

/**
 * Compare with a tolerance. The geometry keeps raw floats exactly as the backend does --
 * 0.8 ATR x 1.5 is 1.2000000000000002 in both languages -- and rounds only where a price
 * is displayed, so asserting exact equality here would be testing IEEE754, not the rule.
 */
function closeTo(actual: number | null, expected: number, message?: string) {
  assert.ok(actual !== null, message ?? "expected a number, got null");
  assert.ok(
    Math.abs(actual - expected) < 1e-9,
    `${message ?? "value"}: expected ${expected}, got ${actual}`,
  );
}

const BASELINE: VotingEnsembleTradingGeometry = {
  atrMultiplier: 1.5,
  fixedStopDistanceDollars: 1,
  minimumStopDistanceDollars: 0.01,
  takeProfitR: 1.5,
  minimumTakeProfitR: 1,
  structuralTargets: true,
  breakevenTriggerR: 1,
  trailingStopR: 1,
  trailingEnabled: true,
  maximumHoldingMinutes: 30,
  slippagePerShare: 0.02,
  startingCapital: 100000,
  maxDailyLossPercent: 2,
  entriesBlocked: false,
  configurationHash: "54fae2592a09797b",
};

test("the ATR stop wins over the fixed distance, which is only the fallback", () => {
  // The bug this guards: the old client returned the fixed distance whenever it was above
  // zero, so with the baseline's $1.00 fixed stop the ATR multiplier could never apply.
  const withAtr = exitGeometryForEntry(600, BASELINE, 0.8);

  assert.equal(withAtr.stopSource, "atr");
  closeTo(withAtr.stopDistance, 1.2, "stop distance"); // 0.8 ATR x 1.5
  closeTo(withAtr.stopPrice, 598.8, "stop price");
});

test("the fixed distance is used only when the session has no ATR", () => {
  const noAtr = exitGeometryForEntry(600, BASELINE, 0);

  assert.equal(noAtr.stopSource, "fixed_dollars");
  assert.equal(noAtr.stopDistance, 1);
  assert.equal(noAtr.stopPrice, 599);
});

test("a zero ATR multiplier disables ATR scaling, as the baseline documents", () => {
  const disabled = exitGeometryForEntry(600, { ...BASELINE, atrMultiplier: 0 }, 0.8);

  assert.equal(disabled.stopSource, "fixed_dollars");
  assert.equal(disabled.stopDistance, 1);
});

test("the minimum stop distance floors whichever source produced the stop", () => {
  const floored = exitGeometryForEntry(600, { ...BASELINE, minimumStopDistanceDollars: 2 }, 0.1);

  assert.equal(floored.stopDistance, 2); // 0.1 x 1.5 = 0.15, floored to 2
  closeTo(floored.stopPrice, 598, "floored stop price");
});

test("the target is the R multiple of the resolved stop distance", () => {
  const geometry = exitGeometryForEntry(600, BASELINE, 0.8);

  closeTo(geometry.targetDistance, 1.8, "target distance"); // 1.2 stop x 1.5 R
  closeTo(geometry.targetPrice, 601.8, "target price");
});

test("breakeven and trail are expressed in the same initial stop distance", () => {
  const geometry = exitGeometryForEntry(600, BASELINE, 0.8);

  closeTo(geometry.breakevenPrice, 601.2, "breakeven"); // entry + 1R
  closeTo(geometry.trailingDistance, 1.2, "trail"); // 1R
});

test("trailing off removes both the breakeven move and the trail", () => {
  const geometry = exitGeometryForEntry(600, { ...BASELINE, trailingEnabled: false }, 0.8);

  assert.equal(geometry.breakevenPrice, null);
  assert.equal(geometry.trailingDistance, null);
  closeTo(geometry.stopDistance, 1.2, "the initial stop is unaffected");
});

test("a short reverses the geometry around the entry", () => {
  const geometry = exitGeometryForEntry(600, BASELINE, 0.8, "Sell");

  closeTo(geometry.stopPrice, 601.2, "short stop");
  closeTo(geometry.targetPrice, 598.2, "short target");
  closeTo(geometry.breakevenPrice, 598.8, "short breakeven");
});

test("a structural target is flagged, never invented", () => {
  // Reproducing the level selection in the browser would give a second answer that
  // disagrees with the decision; the flag says the live target may come in tighter.
  const possible = exitGeometryForEntry(600, BASELINE, 0.8);
  const off = exitGeometryForEntry(600, { ...BASELINE, structuralTargets: false }, 0.8);
  const noRoom = exitGeometryForEntry(600, { ...BASELINE, minimumTakeProfitR: 1.5 }, 0.8);

  assert.equal(possible.structuralTargetPossible, true);
  assert.equal(off.structuralTargetPossible, false);
  assert.equal(noRoom.structuralTargetPossible, false, "no room between the minimum and the target");
});

test("the geometry is read from the backend catalogue, not from local defaults", () => {
  const view = {
    configurationHash: "abc123",
    entriesBlocked: true,
    groups: [
      {
        id: "targetOrder",
        label: "Target Order",
        description: "",
        fields: [
          { key: "stopAtrMultiplier", value: 2.5 },
          { key: "takeProfitR", value: 3 },
          { key: "trailingStopEnabled", value: false },
          { key: "maximumHoldingMinutes", value: 45 },
        ],
      },
    ],
  } as unknown as GeometrySourceView;

  const geometry = tradingGeometryFromView(view);

  assert.ok(geometry);
  assert.equal(geometry.atrMultiplier, 2.5);
  assert.equal(geometry.takeProfitR, 3);
  assert.equal(geometry.trailingEnabled, false);
  assert.equal(geometry.maximumHoldingMinutes, 45);
  assert.equal(geometry.entriesBlocked, true);
  assert.equal(geometry.configurationHash, "abc123");
  // A field the catalogue did not carry keeps the documented baseline rather than 0.
  assert.equal(geometry.fixedStopDistanceDollars, 1);
});

test("no backend answer yields no geometry, so nothing invented is displayed", () => {
  assert.equal(tradingGeometryFromView(null), null);
});

test("an edited multiplier moves the quoted stop and target together", () => {
  const edited = exitGeometryForEntry(600, { ...BASELINE, atrMultiplier: 2.5 }, 0.8);

  closeTo(edited.stopDistance, 2, "edited stop"); // 0.8 x 2.5
  closeTo(edited.stopPrice, 598, "edited stop price");
  closeTo(edited.targetPrice, 603, "edited target"); // 600 + 2 x 1.5R
});

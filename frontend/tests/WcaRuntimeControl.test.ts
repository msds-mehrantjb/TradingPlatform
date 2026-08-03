import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  createInitialWcaState,
  failClosedWcaRuntimeControl,
  withWcaRuntimeControlError,
  withWcaRuntimeControlReady,
} from "../src/features/wca/state.ts";

const ROOT = join(fileURLToPath(new URL("..", import.meta.url)), "..");

function read(path: string): string {
  return readFileSync(join(ROOT, path), "utf8");
}

test("WCA runtime control fails closed when the backend is unavailable", () => {
  const control = failClosedWcaRuntimeControl("network unavailable");
  const state = withWcaRuntimeControlError(createInitialWcaState(), new Error("network unavailable"));

  assert.equal(control.paperTradingRequested, false);
  assert.equal(control.automaticEntriesRequested, false);
  assert.equal(control.pauseNewEntries, true);
  assert.equal(control.killSwitchOpen, true);
  assert.equal(control.effectivePaperTradingEnabled, false);
  assert.equal(control.effectiveAutomaticEntriesEnabled, false);
  assert.deepEqual(control.reasonCodes?.slice(0, 1), ["wca.frontend.backend_unreachable_fail_closed"]);
  assert.equal(state.runtimeControlStatus, "error");
  assert.equal(state.runtimeControl?.effectiveAutomaticEntriesEnabled, false);
});

test("WCA requested ON remains blocked until the backend reports effective permission", () => {
  const blocked = withWcaRuntimeControlReady(createInitialWcaState(), {
    algorithmId: "wca",
    brokerAccountId: "paper",
    symbol: "SPY",
    paperTradingRequested: true,
    automaticEntriesRequested: true,
    effectivePaperTradingEnabled: false,
    effectiveAutomaticEntriesEnabled: false,
    reasonCodes: ["wca.runtime_control.rollout_automatic_paper_blocked"],
  });
  const ready = withWcaRuntimeControlReady(blocked, {
    algorithmId: "wca",
    brokerAccountId: "paper",
    symbol: "SPY",
    paperTradingRequested: true,
    automaticEntriesRequested: true,
    effectivePaperTradingEnabled: true,
    effectiveAutomaticEntriesEnabled: true,
    reasonCodes: [],
  });

  assert.equal(blocked.runtimeControlStatus, "blocked");
  assert.equal(blocked.runtimeControl?.effectiveAutomaticEntriesEnabled, false);
  assert.equal(ready.runtimeControlStatus, "ready");
  assert.equal(ready.runtimeControl?.effectiveAutomaticEntriesEnabled, true);
});

test("global Paper fanout uses WCA backend confirmation and WCA feature code avoids local authority", () => {
  const main = read("frontend/src/main.ts");
  const api = read("frontend/src/features/wca/api.ts");
  const panel = read("frontend/src/features/wca/WcaPanel.ts");
  const state = read("frontend/src/features/wca/state.ts");
  const wcaFeatureText = [
    "frontend/src/features/wca/api.ts",
    "frontend/src/features/wca/state.ts",
    "frontend/src/features/wca/types.ts",
    "frontend/src/features/wca/WcaPanel.ts",
  ].map(read).join("\n");

  assert.match(main, /syncWcaAutomaticPaperControl\(Boolean\(control\.requestedPaperTradingEnabled\)\)/);
  assert.match(main, /await setWcaAutomaticPaperTrading/);
  assert.match(main, /const control = await fetchWcaRuntimeControl\(\)/);
  assert.match(api, /paperTradingRequested: payload\.enabled/);
  assert.match(api, /automaticEntriesRequested: payload\.enabled/);
  assert.match(api, /pauseNewEntries: !payload\.enabled/);
  assert.match(panel, /Paper Blocked/);
  assert.match(panel, /Active blocking reasons/);
  assert.match(state, /failClosedWcaRuntimeControl/);
  assert.doesNotMatch(wcaFeatureText, /localStorage\./);
});

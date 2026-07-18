import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const ROOT = join(fileURLToPath(new URL("..", import.meta.url)), "..");

function read(path: string): string {
  return readFileSync(join(ROOT, path), "utf8");
}

function walk(path: string): string[] {
  const absolute = join(ROOT, path);
  if (!existsSync(absolute)) {
    return [];
  }
  return readdirSync(absolute).flatMap((entry) => {
    const child = join(absolute, entry);
    const stat = statSync(child);
    const relative = join(path, entry).replaceAll("\\", "/");
    return stat.isDirectory() ? walk(relative) : [relative];
  });
}

test("frontend Regime panel consumes the backend authoritative runtime", () => {
  const main = read("frontend/src/main.ts");
  const api = read("frontend/src/features/regime/api.ts");

  assert.match(main, /evaluateRegimeOnBackend/);
  assert.match(api, /\/api\/regime\/evaluate/);
  assert.match(api, /\/api\/regime\/backtests\/run/);
  assert.doesNotMatch(main, /\bcalculateRegimeDecision\(/);
  assert.doesNotMatch(main, /\bbuildRegimeMarketContext\(/);
  assert.doesNotMatch(main, /\bbuildRegimeTargetOrder\(/);
});

test("frontend no longer contains an executable Regime algorithm implementation", () => {
  const files = walk("frontend/src/algorithms/regime");

  assert.deepEqual(files, []);
});

test("backend Regime paths are the only authoritative Regime runtime paths", () => {
  const backendApi = read("backend/app/algorithms/regime/api.py");
  const backendBacktest = read("backend/app/algorithms/regime/backtest/engine.py");
  const frontendFiles = walk("frontend/src").filter((path) => path.endsWith(".ts") || path.endsWith(".tsx"));
  const frontendText = frontendFiles.map(read).join("\n");

  assert.match(backendApi, /backend\.app\.algorithms\.regime\.execution_pipeline/);
  assert.match(backendApi, /backend\.app\.algorithms\.regime\.backtest\.engine/);
  assert.match(backendBacktest, /execute_regime_pipeline/);
  assert.doesNotMatch(frontendText, /frontend\/src\/algorithms\/regime\/backtest\/engine\.ts/);
  assert.doesNotMatch(frontendText, /runRegimeBacktest\(/);
});

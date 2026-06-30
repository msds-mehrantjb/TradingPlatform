import os from "node:os";
import path from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  cacheDir: path.join(os.tmpdir(), "trading-dashboard-vite-cache"),
});

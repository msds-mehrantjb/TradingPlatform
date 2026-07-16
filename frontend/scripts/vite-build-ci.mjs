import childProcess from "node:child_process";
import { EventEmitter } from "node:events";
import { syncBuiltinESMExports } from "node:module";

const originalExec = childProcess.exec;

function fakeChildProcess() {
  const process = new EventEmitter();
  process.stdout = new EventEmitter();
  process.stderr = new EventEmitter();
  process.kill = () => false;
  return process;
}

childProcess.exec = function patchedExec(command, options, callback) {
  if (typeof command === "string" && command.trim().toLowerCase() === "net use") {
    const onComplete = typeof options === "function" ? options : callback;
    if (onComplete) {
      queueMicrotask(() => onComplete(new Error("Skipped Windows network-drive probe in CI"), "", ""));
    }
    return fakeChildProcess();
  }

  return originalExec.apply(this, arguments);
};

syncBuiltinESMExports();

const { build } = await import("vite");

await build({ configLoader: "native" });

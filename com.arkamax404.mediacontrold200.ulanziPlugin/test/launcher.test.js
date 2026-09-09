import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs, {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { createLauncher, runtimePaths } from "../src/launcher.js";

function safeRuntimeFs() {
  const entries = new Map();
  const stats = (target) => {
    if (!entries.has(target)) entries.set(target, { dev: 1, ino: entries.size + 1 });
    return {
      ...entries.get(target),
      mode: 0o100755,
      isFile: () => true,
      isSymbolicLink: () => false,
    };
  };
  return {
    constants: { O_RDONLY: 0, O_NOFOLLOW: 1, O_NONBLOCK: 2 },
    lstatSync: stats,
    openSync: (target) => target,
    fstatSync: stats,
    fchmodSync: () => {},
    closeSync: () => {},
  };
}

function fixture({
  argv = ["node", "launcher.js", "127.0.0.1", "3906", "en"],
  baseDirectory = "/Applications/Ulanzi Studio/plugin/src",
  fsImpl = safeRuntimeFs(),
  onSpawn = () => {},
} = {}) {
  const stdin = new EventEmitter();
  const processImpl = Object.assign(new EventEmitter(), {
    argv,
    exitCode: undefined,
    pid: 123,
    stdin,
    env: { TEST_ENV: "preserved" },
    execPath: "/Applications/Ulanzi Studio/Ulanzi Studio",
    removeListener: EventEmitter.prototype.removeListener,
  });
  const child = Object.assign(new EventEmitter(), {
    exitCode: null,
    signalCode: null,
    stdin: { destroyed: false, ends: 0, end() { this.ends += 1; } },
    signals: [],
    kill(signal) { this.signals.push(signal); return true; },
  });
  const calls = [];
  const spawnImpl = (...args) => { onSpawn(...args); calls.push(args); return child; };
  const propagated = [];
  const errors = [];
  const launcher = createLauncher({
    spawnImpl,
    fsImpl,
    processImpl,
    baseDirectory,
    propagateSignal: (signal) => propagated.push(signal),
    consoleImpl: { error: (message) => errors.push(message) },
  });
  return { calls, child, errors, launcher, processImpl, propagated };
}

function runtimeFixture(t) {
  const pluginRoot = mkdtempSync(path.join(tmpdir(), "media-control-launcher-"));
  t.after(() => rmSync(pluginRoot, { recursive: true, force: true }));
  const baseDirectory = path.join(pluginRoot, "src");
  const runtimeDirectory = path.join(pluginRoot, "runtime");
  mkdirSync(baseDirectory);
  mkdirSync(runtimeDirectory);
  return { baseDirectory, runtimeDirectory };
}

test("repairs mode-stripped regular runtime executables before spawn", (t) => {
  const { baseDirectory, runtimeDirectory } = runtimeFixture(t);
  const executable = path.join(runtimeDirectory, "MediaControlRuntime");
  const helper = path.join(runtimeDirectory, "MediaRemoteHelper");
  for (const target of [executable, helper]) {
    writeFileSync(target, "runtime");
    chmodSync(target, 0o666);
  }
  const modesAtSpawn = [];
  const state = fixture({
    baseDirectory,
    fsImpl: fs,
    onSpawn: () => modesAtSpawn.push(
      statSync(executable).mode & 0o777,
      statSync(helper).mode & 0o777,
    ),
  });

  state.launcher.launch();

  assert.deepEqual(modesAtSpawn, [0o755, 0o755]);
  assert.equal(state.calls.length, 1);
});

test("fails closed without repairing or spawning when a runtime target is unsafe", (t) => {
  for (const unsafeName of ["MediaControlRuntime", "MediaRemoteHelper"]) {
    for (const unsafeKind of ["symlink", "directory"]) {
      const { baseDirectory, runtimeDirectory } = runtimeFixture(t);
      const unsafeTarget = path.join(runtimeDirectory, unsafeName);
      const safeTarget = path.join(
        runtimeDirectory,
        unsafeName === "MediaControlRuntime" ? "MediaRemoteHelper" : "MediaControlRuntime",
      );
      writeFileSync(safeTarget, "runtime");
      chmodSync(safeTarget, 0o666);
      if (unsafeKind === "symlink") symlinkSync(safeTarget, unsafeTarget);
      else mkdirSync(unsafeTarget);
      const state = fixture({ baseDirectory, fsImpl: fs });

      assert.equal(state.launcher.launch(), null);
      assert.equal(state.calls.length, 0);
      assert.equal(statSync(safeTarget).mode & 0o777, 0o666);
      assert.match(state.errors[0], /Unsafe runtime executable target/);
    }
  }
});

test("forwards exact host arguments and uses safe spawn options", () => {
  const hostArgs = ["127.0.0.1", "3906", "zh-CN", "--future=value with spaces", "quoted\"value"];
  const state = fixture({ argv: ["node", "launcher.js", ...hostArgs] });

  state.launcher.launch();

  assert.equal(state.calls.length, 1);
  const [executable, args, options] = state.calls[0];
  assert.equal(executable, "/Applications/Ulanzi Studio/plugin/runtime/MediaControlRuntime");
  assert.deepEqual(args, hostArgs);
  assert.deepEqual(options, {
    cwd: "/Applications/Ulanzi Studio/plugin/runtime",
    shell: false,
    detached: false,
    stdio: ["pipe", "inherit", "inherit"],
    env: {
      TEST_ENV: "preserved",
      MEDIA_CONTROL_NODE_EXECUTABLE: "/Applications/Ulanzi Studio/Ulanzi Studio",
      MEDIA_CONTROL_PLUGIN_ROOT: "/Applications/Ulanzi Studio/plugin",
    },
  });
});

test("launcher uses spawn without exec or a WebSocket dependency", () => {
  const source = readFileSync(new URL("../src/launcher.js", import.meta.url), "utf8");
  assert.match(source, /import \{ spawn \} from "node:child_process"/);
  assert.doesNotMatch(source, /\bexec(?:File)?\b|from ["']ws["']/);
});

test("runtime path remains absolute when the plugin path contains spaces", () => {
  const paths = runtimePaths("/Applications/Physical Test/Media Control.ulanziPlugin/src");
  assert.equal(paths.runtimeDirectory, "/Applications/Physical Test/Media Control.ulanziPlugin/runtime");
  assert.equal(path.isAbsolute(paths.executable), true);
});

test("reports asynchronous spawn failure once and never restarts", () => {
  const state = fixture();
  state.launcher.launch();
  state.child.emit("error", new Error("ENOENT"));
  state.child.emit("exit", 1, null);

  assert.equal(state.processImpl.exitCode, 1);
  assert.deepEqual(state.errors, ["Failed to launch Media Control runtime: ENOENT"]);
  assert.equal(state.calls.length, 1);
});

test("rejects a second launch without spawning or replacing the child", () => {
  const state = fixture();
  const first = state.launcher.launch();

  assert.throws(() => state.launcher.launch(["different"]), /started once/);
  assert.equal(state.calls.length, 1);
  assert.equal(first, state.child);
});

test("reports synchronous spawn failure without registering lifecycle handlers", () => {
  const state = fixture();
  const launcher = createLauncher({
    spawnImpl: () => { throw new Error("blocked"); },
    fsImpl: safeRuntimeFs(),
    processImpl: state.processImpl,
    consoleImpl: { error: (message) => state.errors.push(message) },
  });

  assert.equal(launcher.launch(), null);
  assert.equal(state.processImpl.exitCode, 1);
  assert.match(state.errors[0], /blocked/);
  assert.equal(state.processImpl.listenerCount("SIGTERM"), 0);
});

test("propagates child exit code", () => {
  const state = fixture();
  state.launcher.launch();
  state.child.emit("exit", 23, null);
  assert.equal(state.processImpl.exitCode, 23);
});

test("forwards parent signals and propagates signaled child exit", () => {
  const state = fixture();
  state.launcher.launch();
  state.processImpl.emit("SIGTERM");
  state.processImpl.emit("SIGTERM");
  state.child.emit("exit", null, "SIGTERM");

  assert.deepEqual(state.child.signals, ["SIGTERM"]);
  assert.equal(state.child.stdin.ends, 1);
  assert.deepEqual(state.propagated, ["SIGTERM"]);
});

test("stdin EOF closes the lifecycle channel idempotently without killing child", () => {
  const state = fixture();
  state.launcher.launch();
  state.processImpl.stdin.emit("end");
  state.processImpl.stdin.emit("close");
  state.launcher.requestStop();
  state.child.emit("exit", 0, null);

  assert.equal(state.child.stdin.ends, 1);
  assert.deepEqual(state.child.signals, []);
  assert.equal(state.processImpl.exitCode, 0);
  assert.equal(state.calls.length, 1);
});

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const moduleDirectory = path.dirname(fileURLToPath(import.meta.url));

export function runtimePaths(baseDirectory = moduleDirectory, pathImpl = path) {
  const runtimeDirectory = pathImpl.resolve(baseDirectory, "..", "runtime");
  return {
    executable: pathImpl.resolve(runtimeDirectory, "MediaControlRuntime"),
    helper: pathImpl.resolve(runtimeDirectory, "MediaRemoteHelper"),
    runtimeDirectory,
  };
}

function repairRuntimePermissions(targets, fsImpl) {
  const noFollow = fsImpl.constants?.O_NOFOLLOW;
  if (!Number.isInteger(noFollow))
    throw new Error("Secure runtime validation is unavailable");

  const handles = [];
  try {
    for (const target of targets) {
      const pathStats = fsImpl.lstatSync(target);
      if (pathStats.isSymbolicLink() || !pathStats.isFile()) {
        throw new Error(`Unsafe runtime executable target: ${target}`);
      }
      const descriptor = fsImpl.openSync(
        target,
        fsImpl.constants.O_RDONLY | noFollow | fsImpl.constants.O_NONBLOCK,
      );
      handles.push({ descriptor, pathStats, target });
      const descriptorStats = fsImpl.fstatSync(descriptor);
      if (
        !descriptorStats.isFile() ||
        descriptorStats.dev !== pathStats.dev ||
        descriptorStats.ino !== pathStats.ino
      ) {
        throw new Error(
          `Runtime executable changed during validation: ${target}`,
        );
      }
    }

    for (const handle of handles) {
      const descriptorStats = fsImpl.fstatSync(handle.descriptor);
      if ((descriptorStats.mode & 0o777) !== 0o755) {
        fsImpl.fchmodSync(handle.descriptor, 0o755);
      }
    }

    for (const { descriptor, target } of handles) {
      const pathStats = fsImpl.lstatSync(target);
      const descriptorStats = fsImpl.fstatSync(descriptor);
      if (
        pathStats.isSymbolicLink() ||
        !pathStats.isFile() ||
        descriptorStats.dev !== pathStats.dev ||
        descriptorStats.ino !== pathStats.ino
      ) {
        throw new Error(`Runtime executable changed during repair: ${target}`);
      }
    }
  } finally {
    for (const { descriptor } of handles.reverse())
      fsImpl.closeSync(descriptor);
  }
}

export function createLauncher({
  spawnImpl = spawn,
  fsImpl = fs,
  processImpl = process,
  consoleImpl = console,
  pathImpl = path,
  baseDirectory = moduleDirectory,
  propagateSignal = (signal) => processImpl.kill(processImpl.pid, signal),
} = {}) {
  let child = null;
  let launched = false;
  let stopping = false;
  let finished = false;
  let requestedSignal = null;

  const removeLifecycleListeners = () => {
    processImpl.removeListener("SIGINT", onSigint);
    processImpl.removeListener("SIGTERM", onSigterm);
    processImpl.stdin?.removeListener("end", onStdinEnd);
    processImpl.stdin?.removeListener("close", onStdinEnd);
  };

  const requestStop = (signal = null) => {
    if (stopping) return;
    stopping = true;
    requestedSignal = signal;
    if (child?.stdin && !child.stdin.destroyed) child.stdin.end();
    if (
      signal &&
      child &&
      child.exitCode === null &&
      child.signalCode === null
    ) {
      child.kill(signal);
    }
  };

  function onSigint() {
    requestStop("SIGINT");
  }

  function onSigterm() {
    requestStop("SIGTERM");
  }

  function onStdinEnd() {
    requestStop();
  }

  const finish = (code, signal, error = null) => {
    if (finished) return;
    finished = true;
    removeLifecycleListeners();
    if (error)
      consoleImpl.error(
        `Failed to launch Media Control runtime: ${error.message}`,
      );
    const finalSignal = signal || requestedSignal;
    if (finalSignal) propagateSignal(finalSignal);
    else processImpl.exitCode = Number.isInteger(code) ? code : 1;
  };

  const launch = (args = processImpl.argv.slice(2)) => {
    if (launched) throw new Error("Launcher can only be started once");
    launched = true;
    const { executable, helper, runtimeDirectory } = runtimePaths(
      baseDirectory,
      pathImpl,
    );
    try {
      repairRuntimePermissions([executable, helper], fsImpl);
      const pluginRoot = pathImpl.resolve(baseDirectory, "..");
      child = spawnImpl(executable, args, {
        cwd: runtimeDirectory,
        shell: false,
        detached: false,
        stdio: ["pipe", "inherit", "inherit"],
        env: {
          ...processImpl.env,
          MEDIA_CONTROL_NODE_EXECUTABLE: processImpl.execPath,
          MEDIA_CONTROL_PLUGIN_ROOT: pluginRoot,
        },
      });
    } catch (error) {
      finish(null, null, error);
      return null;
    }

    processImpl.once("SIGINT", onSigint);
    processImpl.once("SIGTERM", onSigterm);
    processImpl.stdin?.once("end", onStdinEnd);
    processImpl.stdin?.once("close", onStdinEnd);
    child.once("error", (error) => finish(null, null, error));
    child.once("exit", (code, signal) => finish(code, signal));
    return child;
  };

  return { launch, requestStop };
}

export function main() {
  return createLauncher().launch(process.argv.slice(2));
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) main();

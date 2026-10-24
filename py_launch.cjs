"use strict";
/**
 * Shared Node launcher for single-file stdlib Python tools (Node >= 24, node: builtins only).
 *
 * CLI: require("./vendor/py_launch.cjs").main(__dirname + "/tool.py", { envVar: "IRONMATE_PYTHON", noSite: true })
 * Library: run(scriptPath, args, { python, input, timeout, maxBuffer, maxInputBytes, responseMaxBytes, env, noSite, isolated, envVar }).
 * `maxBuffer` and `responseMaxBytes` are mutually exclusive. `env` merges over `process.env` (never injects PYTHONUTF8).
 *
 * Interpreter resolution (first match that runs Python 3.9+): opts.python, process.env[opts.envVar],
 * process.env.PYTHON, python3, python, and on Windows `py -3`.
 * Explicit opts.python or a set opts.envVar value does not fall back to later candidates.
 * Windows `.cmd`/`.bat` interpreter paths are never spawned with `shell: true`.
 * Explicit `python` or a set `envVar` rejects them; non-strict `PYTHON` skips with a warning.
 *
 * `noSite` adds CPython `-S` (no site.py / no user site); default is off so tools opt in explicitly.
 * Launcher failures (Node floor, missing interpreter, spawn error, signal) exit 2.
 * Text is the default. Set opts.jsonErrors or PY_LAUNCH_ERROR_FORMAT=json for one JSON
 * object on stderr: {ok:false, code, message, hint, exit_code}. That switch does not
 * read argv, so a child tool's own --json flag is forwarded unchanged.
 * A signal also prints the signal name (text mode) instead of exiting 2 silently.
 * `isolated` adds CPython `-I` (implies `-E`, `-s`, and `-P`: ignore all `PYTHON*`
 * environment variables—including values passed via `env`—and omit the script
 * directory from `sys.path`). Sibling imports fail unless the script prepends its
 * directory to `sys.path`; default is off.
 * Both startup flags also apply to the interpreter version probe.
 */
const { spawn, spawnSync } = require("node:child_process");
const { platform } = require("node:os");

const MIN_NODE = [24, 0];
const MIN_PYTHON = [3, 9];
const DEFAULT_ENV_VAR = "PYTHON";
const DEFAULT_PROBE_TIMEOUT_MS = 5_000;
const HOST_RESPONSE_MARGIN_BYTES = 512;
const VERSION_PROBE =
  "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}'); sys.exit(0 if sys.version_info >= (3, 9) else 1)";

/** @typedef {{ cmd: string, prefix: string[], label: string, strict?: boolean }} PythonCandidate */

/**
 * @param {object} opts
 * @returns {PythonCandidate[]}
 */
function buildCandidateSpecs(opts = {}) {
  const envVar = typeof opts.envVar === "string" && opts.envVar ? opts.envVar : DEFAULT_ENV_VAR;
  const explicitEnvVar = Object.hasOwn(opts, "envVar");
  const plat = opts.platform ?? platform();

  if (opts.python) {
    return [{ cmd: opts.python, prefix: [], label: String(opts.python), strict: true }];
  }
  if (explicitEnvVar && process.env[envVar]) {
    return [{ cmd: process.env[envVar], prefix: [], label: `$${envVar}`, strict: true }];
  }

  const specs = [];
  if (!explicitEnvVar && process.env[envVar]) {
    specs.push({ cmd: process.env[envVar], prefix: [], label: `$${envVar}` });
  }
  if (envVar !== "PYTHON" && process.env.PYTHON) {
    specs.push({ cmd: process.env.PYTHON, prefix: [], label: "$PYTHON" });
  }
  specs.push({ cmd: "python3", prefix: [], label: "python3" });
  specs.push({ cmd: "python", prefix: [], label: "python" });
  if (plat === "win32") {
    specs.push({ cmd: "py", prefix: ["-3"], label: "py -3" });
  }
  return specs;
}

function isCmdBatPath(cmd) {
  return /\.(cmd|bat)$/i.test(cmd);
}

function rejectCmdBatInterpreter(cmd, label) {
  if (isCmdBatPath(cmd)) {
    throw interpreterError(
      `py_launch: ${label}: Windows .cmd/.bat interpreters are not supported; use python.exe or py -3`,
      { code: "ENOINTERPRETER", label },
    );
  }
}

function warnSkippedCmdBat(spec) {
  process.stderr.write(
    `py_launch: warning: skipping ${spec.label} (${spec.cmd}): .cmd/.bat interpreters are not supported; use python.exe or py -3\n`,
  );
}

function probeCandidate(spec, opts) {
  if (isCmdBatPath(spec.cmd)) {
    if (spec.strict) {
      rejectCmdBatInterpreter(spec.cmd, spec.label);
    }
    warnSkippedCmdBat(spec);
    return { skip: true };
  }
  const timeout = opts.probeTimeoutMs ?? DEFAULT_PROBE_TIMEOUT_MS;
  const probeArgs = [
    ...spec.prefix,
    ...pythonArgv("-c", [VERSION_PROBE], Boolean(opts.noSite), Boolean(opts.isolated)),
  ];
  const result = spawnSync(spec.cmd, probeArgs, {
    encoding: "utf8",
    timeout,
  });
  const line = String(result.stdout || "").trim().split(/\r?\n/)[0] || "";
  return { result, version: line };
}

/**
 * @param {object} opts
 * @returns {{ cmd: string, prefix: string[], label: string }}
 */
function resolvePython(opts = {}) {
  const specs = opts.candidates ?? buildCandidateSpecs(opts);
  let lastVersion = "";
  for (const spec of specs) {
    const probed = probeCandidate(spec, opts);
    if (probed.skip) continue;
    const { result, version } = probed;
    if (version) lastVersion = version;
    if (result.error) {
      if (spec.strict) {
        throw interpreterError(`py_launch: ${spec.label}: ${result.error.message}`, {
          code: "ENOINTERPRETER",
          label: spec.label,
        });
      }
      continue;
    }
    if (result.status === 0) {
      return { cmd: spec.cmd, prefix: spec.prefix, label: spec.label };
    }
    if (spec.strict) {
      const detail = version
        ? ` (found ${version}; Python ${MIN_PYTHON.join(".")}+ required)`
        : ` (Python ${MIN_PYTHON.join(".")}+ required)`;
      throw interpreterError(`py_launch: ${spec.label} is not suitable${detail}`, {
        code: "ENOINTERPRETER",
        label: spec.label,
        version,
      });
    }
  }

  const detail = lastVersion
    ? ` (found ${lastVersion}; Python ${MIN_PYTHON.join(".")}+ required)`
    : ` (Python ${MIN_PYTHON.join(".")}+ required; no interpreter responded)`;
  throw interpreterError(`py_launch: no suitable Python interpreter${detail}`, { code: "ENOINTERPRETER" });
}

function interpreterError(message, extra = {}) {
  const err = new Error(message);
  Object.assign(err, extra);
  return err;
}

function runFailure(message, result, extra = {}) {
  const err = new Error(message);
  if (result) {
    err.status = result.status ?? null;
    err.stdout = result.stdout ?? Buffer.alloc(0);
    err.stderr = result.stderr ?? Buffer.alloc(0);
    if (result.signal) err.signal = result.signal;
  }
  Object.assign(err, extra);
  return err;
}

function assertNodeVersion(version = process.versions.node) {
  const major = Number.parseInt(String(version).split(".")[0], 10);
  if (!Number.isFinite(major) || major < MIN_NODE[0]) {
    const err = new Error(
      `py_launch requires Node ${MIN_NODE[0]}+ (this process is Node ${version}). Install Node ${MIN_NODE[0]} or newer and rerun.`,
    );
    err.code = "ENODEVERSION";
    err.hint = `Install Node ${MIN_NODE[0]} or newer and rerun. py_launch does not fall back to an older runtime.`;
    throw err;
  }
}

function wantsJsonErrors(opts = {}) {
  return opts.jsonErrors === true || process.env.PY_LAUNCH_ERROR_FORMAT === "json";
}

function launcherHint(error) {
  if (error && error.hint) return String(error.hint);
  const code = error && error.code ? String(error.code) : "";
  if (code === "ENODEVERSION") {
    return `Install Node ${MIN_NODE[0]} or newer and rerun. py_launch does not fall back to an older runtime.`;
  }
  if (code === "ENOINTERPRETER") {
    return "Install Python 3.9+ or set opts.python / the tool envVar to that executable. Windows .cmd and .bat shims are rejected; use python.exe or py -3.";
  }
  if (code === "ESIGNAL") {
    return "The launcher forwards SIGINT and SIGTERM to the child and exits 2. Rerun; a signal is not a Python traceback.";
  }
  return "Launcher failure (exit 2). The Python tool did not run. Read message and fix the interpreter, Node version, or signal.";
}

/**
 * Write one launcher failure. Text is one line. JSON is one object on stderr.
 * Does not inspect argv. error is an Error or string; opts may set jsonErrors.
 */
function writeLauncherError(error, opts = {}) {
  const message = String(error && error.message ? error.message : error);
  const line = message.endsWith("\n") ? message : `${message}\n`;
  if (!wantsJsonErrors(opts)) {
    process.stderr.write(line);
    return;
  }
  const payload = {
    ok: false,
    code: error && error.code ? String(error.code) : "ELAUNCHFAILURE",
    message: message.replace(/\s+$/, ""),
    hint: launcherHint(error),
    exit_code: 2,
  };
  process.stderr.write(`${JSON.stringify(payload)}\n`);
}

/**
 * @param {string | Buffer | Uint8Array | undefined | null} input
 */
function inputByteLength(input) {
  if (input === undefined || input === null) return 0;
  if (typeof input === "string") return Buffer.byteLength(input, "utf8");
  if (Buffer.isBuffer(input)) return input.length;
  return Buffer.byteLength(input);
}

function nonnegativeIntegerOption(name, value) {
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`py_launch: ${name} must be a nonnegative integer`);
  }
}

function inputTooLargeError(message) {
  const err = new Error(message);
  err.code = "EINPUTTOOLARGE";
  return err;
}

function responseTooLargeError(message) {
  const err = new Error(message);
  err.code = "ERESPONSETOOLARGE";
  return err;
}

function launchFailureError(message, result, code = "ELAUNCHFAILURE") {
  const err = runFailure(message, result, { code });
  return err;
}

function envKeyFold(key) {
  return String(key).toLowerCase();
}

/**
 * @param {NodeJS.ProcessEnv} env
 * @param {string} name
 */
function envLookup(env, name) {
  const fold = envKeyFold(name);
  for (const key of Object.keys(env)) {
    if (envKeyFold(key) === fold) return env[key];
  }
  return undefined;
}

/**
 * @param {NodeJS.ProcessEnv | undefined} overrides
 * @returns {NodeJS.ProcessEnv | undefined}
 */
function buildChildEnv(overrides) {
  if (overrides === undefined) return undefined;
  if (process.platform !== "win32") {
    return { ...process.env, ...overrides };
  }
  const env = { ...process.env };
  const index = new Map();
  for (const key of Object.keys(env)) {
    index.set(envKeyFold(key), key);
  }
  for (const [overrideKey, value] of Object.entries(overrides)) {
    const fold = envKeyFold(overrideKey);
    const existing = index.get(fold);
    if (existing !== undefined) {
      delete env[existing];
    }
    env[overrideKey] = value;
    index.set(fold, overrideKey);
  }
  return env;
}

/**
 * @param {Error} err
 * @param {string} pythonProcessPrefix
 */
function relaunchError(err, pythonProcessPrefix) {
  const msg = String(err.message || err);
  let body = msg;
  if (body.startsWith("py_launch: Python process failed: ")) {
    body = body.slice("py_launch: Python process failed: ".length);
  } else if (body.startsWith("py_launch: ")) {
    body = body.slice("py_launch: ".length);
  }
  const out = new Error(pythonProcessPrefix + body);
  if (err.code) out.code = err.code;
  return out;
}

/**
 * @param {number} responseMaxBytes
 */
function hostMaxBufferBytes(responseMaxBytes) {
  nonnegativeIntegerOption("responseMaxBytes", responseMaxBytes);
  return responseMaxBytes + HOST_RESPONSE_MARGIN_BYTES;
}

function spawnSyncErrorCode(error) {
  if (!error) return "";
  const code = error.code ? String(error.code) : "";
  if (code) return code;
  const msg = String(error.message || "");
  if (/\bENOBUFS\b/.test(msg)) return "ENOBUFS";
  if (/\bEPIPE\b/.test(msg)) return "EPIPE";
  return "";
}

/**
 * Map spawnSync failure to a py_launch Error (pure; used by run and tests).
 *
 * @param {{ error?: Error }} result
 * @param {{ responseMaxBytes?: number }} options
 */
function spawnSyncFailureError(result, options = {}) {
  if (!result.error) return null;
  const spawnCode = spawnSyncErrorCode(result.error);
  if (spawnCode === "ENOBUFS") {
    if (options.responseMaxBytes !== undefined) {
      return responseTooLargeError(
        `py_launch: response exceeds responseMaxBytes (${options.responseMaxBytes})`,
      );
    }
    return launchFailureError(`py_launch: Python process failed: ${result.error.message}`, result);
  }
  if (spawnCode === "EPIPE") {
    return launchFailureError(
      "py_launch: Python process failed: stdin pipe closed unexpectedly",
      result,
      "ELAUNCHSTDINCLOSED",
    );
  }
  return null;
}

/**
 * @param {unknown} err
 * @param {string} pythonProcessPrefix e.g. "tool Python process failed: "
 * @param {string} failureFallback when the child exits nonzero with empty stderr
 * @param {{ inputKnownToExceed?: boolean, inputExceededMessage?: string, responseMaxBytes?: number }} [context]
 */
function mapLaunchError(err, pythonProcessPrefix, failureFallback, context = {}) {
  const msg = String(err && err.message ? err.message : err);
  const code = err && err.code ? String(err.code) : "";
  const causeCode =
    err && err.cause && err.cause.code && !code ? String(err.cause.code) : "";
  const inputExceededMessage = context.inputExceededMessage || "input exceeds size limit";

  if (code === "EINPUTTOOLARGE" || code === "ERESPONSETOOLARGE" || code === "ELAUNCHSTDINCLOSED") {
    throw relaunchError(err, pythonProcessPrefix);
  }
  if (code === "ELAUNCHFAILURE") {
    throw relaunchError(err, pythonProcessPrefix);
  }
  if (code === "EPIPE" || causeCode === "EPIPE" || /\bEPIPE\b/.test(msg)) {
    if (context.inputKnownToExceed) {
      const out = new Error(inputExceededMessage);
      out.code = "EINPUTTOOLARGE";
      throw out;
    }
    const out = new Error(pythonProcessPrefix + "Python stdin pipe closed unexpectedly");
    out.code = "ELAUNCHSTDINCLOSED";
    throw out;
  }
  if (code === "ENOBUFS" || /\bENOBUFS\b/.test(msg)) {
    const detail =
      context.responseMaxBytes !== undefined
        ? "internal error: Python stdout exceeded the Node capture buffer despite responseMaxBytes (report as a py_launch consumer bug)"
        : "Python stdout exceeded the Node maxBuffer limit";
    const out = new Error(pythonProcessPrefix + detail);
    out.code = context.responseMaxBytes !== undefined ? "ERESPONSETOOLARGE" : "ELAUNCHFAILURE";
    throw out;
  }
  if (msg.startsWith("py_launch: Python process failed: ")) {
    throw relaunchError(err, pythonProcessPrefix);
  }
  const spawn = msg.match(/^py_launch: ([^:]+): (spawnSync .+)$/);
  if (spawn) {
    const out = new Error(pythonProcessPrefix + spawn[2]);
    out.code = "ELAUNCHFAILURE";
    throw out;
  }
  if (msg.startsWith("py_launch: ")) {
    throw relaunchError(err, pythonProcessPrefix);
  }
  if (err && Object.hasOwn(err, "status") && err.status !== 0) {
    const out = new Error((err.stderr && err.stderr.toString("utf8").trim()) || failureFallback);
    out.code = "ELAUNCHFAILURE";
    throw out;
  }
  throw err;
}

/**
 * @param {string} scriptPath
 * @param {string[]} args
 * @param {boolean} noSite
 * @param {boolean} isolated
 */
function pythonArgv(scriptPath, args, noSite, isolated) {
  const argv = [];
  if (noSite) argv.push("-S");
  if (isolated) argv.push("-I");
  argv.push(scriptPath);
  if (args && args.length) argv.push(...args);
  return argv;
}

/**
 * @param {string} scriptPath
 * @param {string[]} args
 * @param {object} options
 */
function run(scriptPath, args, options = {}) {
  assertNodeVersion();
  const resolved = resolvePython(options);
  rejectCmdBatInterpreter(resolved.cmd, resolved.label);
  const pyArgs = [
    ...resolved.prefix,
    ...pythonArgv(scriptPath, args, Boolean(options.noSite), Boolean(options.isolated)),
  ];

  if (options.maxBuffer !== undefined && options.responseMaxBytes !== undefined) {
    throw new Error("py_launch: cannot set both maxBuffer and responseMaxBytes; use one or the other");
  }

  let maxBuffer = options.maxBuffer;
  if (options.responseMaxBytes !== undefined) {
    maxBuffer = hostMaxBufferBytes(options.responseMaxBytes);
  }

  const inputLen = inputByteLength(options.input);
  if (options.maxInputBytes !== undefined) {
    nonnegativeIntegerOption("maxInputBytes", options.maxInputBytes);
    if (inputLen > options.maxInputBytes) {
      throw inputTooLargeError(
        `py_launch: input exceeds maxInputBytes (${options.maxInputBytes}; got ${inputLen} bytes)`,
      );
    }
  }

  const spawnOpts = {
    input: options.input,
    timeout: options.timeout,
    maxBuffer,
  };
  const childEnv = buildChildEnv(options.env);
  if (childEnv !== undefined) {
    spawnOpts.env = childEnv;
  }

  const result = spawnSync(resolved.cmd, pyArgs, spawnOpts);
  if (result.error) {
    const mapped = spawnSyncFailureError(result, options);
    if (mapped) {
      mapped.cause = result.error;
      throw mapped;
    }
    const err = launchFailureError(`py_launch: Python process failed: ${result.error.message}`, result);
    err.cause = result.error;
    throw err;
  }
  if (result.signal) {
    throw runFailure(`py_launch: Python process terminated by signal ${result.signal}`, result);
  }
  if (result.status !== 0) {
    const msg = (result.stderr && result.stderr.toString("utf8").trim()) || "Python script failed";
    throw runFailure(msg, result);
  }
  return result;
}

/**
 * @param {string} scriptPath
 * @param {object} [opts]
 */
function main(scriptPath, opts = {}) {
  try {
    assertNodeVersion();
  } catch (error) {
    writeLauncherError(error, opts);
    process.exitCode = 2;
    return;
  }
  let resolved;
  try {
    resolved = resolvePython(opts);
  } catch (error) {
    writeLauncherError(error, opts);
    process.exitCode = 2;
    return;
  }
  const argv = opts.argv ?? process.argv.slice(2);
  const pyArgs = [
    ...resolved.prefix,
    ...pythonArgv(scriptPath, argv, Boolean(opts.noSite), Boolean(opts.isolated)),
  ];
  rejectCmdBatInterpreter(resolved.cmd, resolved.label);
  const child = spawn(resolved.cmd, pyArgs, { stdio: "inherit" });
  const forwardSignal = (signal) => {
    // `killed` only means a signal was sent, not that the child has exited.
    if (child.exitCode === null && child.signalCode === null) child.kill(signal);
  };
  process.on("SIGINT", forwardSignal);
  process.on("SIGTERM", forwardSignal);
  child.on("error", (error) => {
    const wrapped = new Error(`py_launch: ${error.message}`);
    wrapped.code = error.code || "ELAUNCHFAILURE";
    wrapped.hint = "The Python executable was found but could not be spawned. Check the path and permissions.";
    writeLauncherError(wrapped, opts);
    process.exitCode = 2;
  });
  child.on("exit", (code, signal) => {
    process.removeListener("SIGINT", forwardSignal);
    process.removeListener("SIGTERM", forwardSignal);
    if (signal) {
      const wrapped = new Error(`py_launch: Python process terminated by signal ${signal}`);
      wrapped.code = "ESIGNAL";
      wrapped.hint = launcherHint({ code: "ESIGNAL" });
      writeLauncherError(wrapped, opts);
      process.exitCode = 2;
    } else process.exitCode = code === null ? 2 : code;
  });
}

module.exports = {
  main,
  run,
  resolvePython,
  buildCandidateSpecs,
  rejectCmdBatInterpreter,
  mapLaunchError,
  spawnSyncFailureError,
  buildChildEnv,
  envLookup,
  assertNodeVersion,
  writeLauncherError,
  wantsJsonErrors,
  launcherHint,
  hostMaxBufferBytes,
  inputByteLength,
  DEFAULT_PROBE_TIMEOUT_MS,
  HOST_RESPONSE_MARGIN_BYTES,
  VERSION_PROBE,
  MIN_NODE,
};

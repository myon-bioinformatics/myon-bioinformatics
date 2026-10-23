"use strict";
/**
 * Shared Node launcher for single-file stdlib Python tools (Node >= 18, node: builtins only).
 *
 * CLI: require("./vendor/py_launch.cjs").main(__dirname + "/tool.py", { envVar: "IRONMATE_PYTHON", noSite: true })
 * Library: run(scriptPath, args, { python, input, timeout, maxBuffer, maxInputBytes, responseMaxBytes, env, noSite, envVar })
 *
 * Interpreter resolution (first match that runs Python 3.9+): opts.python, process.env[opts.envVar],
 * process.env.PYTHON, python3, python, and on Windows `py -3`.
 * Explicit opts.python or a set opts.envVar value does not fall back to later candidates.
 * Windows `.cmd`/`.bat` interpreter paths are never spawned with `shell: true`.
 * Explicit `python` or a set `envVar` rejects them; non-strict `PYTHON` skips with a warning.
 *
 * `noSite` adds CPython `-S` (no site.py / no user site); default is off so tools opt in explicitly.
 */
const { spawn, spawnSync } = require("node:child_process");
const { platform } = require("node:os");

const MIN_NODE = [18, 0];
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
  const result = spawnSync(spec.cmd, [...spec.prefix, "-c", VERSION_PROBE], {
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

function assertNodeVersion() {
  const major = Number.parseInt(String(process.versions.node).split(".")[0], 10);
  if (!Number.isFinite(major) || major < MIN_NODE[0]) {
    throw new Error(`py_launch requires Node ${MIN_NODE[0]}+`);
  }
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
 * @param {unknown} err
 * @param {string} pythonProcessPrefix e.g. "tool Python process failed: "
 * @param {string} failureFallback when the child exits nonzero with empty stderr
 * @param {{ stdinPrechecked?: boolean, inputExceededMessage?: string }} [context]
 */
function mapLaunchError(err, pythonProcessPrefix, failureFallback, context = {}) {
  const msg = String(err && err.message ? err.message : err);
  const code = err && err.code ? String(err.code) : "";
  const causeCode = err && err.cause && err.cause.code ? String(err.cause.code) : "";
  const inputExceededMessage = context.inputExceededMessage || "input exceeds size limit";

  if (code === "EINPUTTOOLARGE") {
    throw new Error(pythonProcessPrefix + msg.replace(/^py_launch: /, ""));
  }
  if (code === "ERESPONSETOOLARGE") {
    throw new Error(pythonProcessPrefix + msg.replace(/^py_launch: /, ""));
  }
  if (code === "EPIPE" || causeCode === "EPIPE" || /\bEPIPE\b/.test(msg)) {
    if (context.stdinPrechecked) {
      throw new Error(pythonProcessPrefix + "Python stdin pipe closed unexpectedly");
    }
    throw new Error(inputExceededMessage);
  }
  if (code === "ENOBUFS" || /\bENOBUFS\b/.test(msg)) {
    throw new Error(
      pythonProcessPrefix +
        "internal error: Python stdout exceeded the Node capture buffer despite responseMaxBytes (report as a py_launch consumer bug)",
    );
  }
  if (msg.startsWith("py_launch: Python process failed: ")) {
    throw new Error(pythonProcessPrefix + msg.slice("py_launch: Python process failed: ".length));
  }
  const spawn = msg.match(/^py_launch: ([^:]+): (spawnSync .+)$/);
  if (spawn) throw new Error(pythonProcessPrefix + spawn[2]);
  if (msg.startsWith("py_launch: ")) {
    throw new Error(pythonProcessPrefix + msg.slice("py_launch: ".length));
  }
  if (err && Object.hasOwn(err, "status") && err.status !== 0) {
    throw new Error((err.stderr && err.stderr.toString("utf8").trim()) || failureFallback);
  }
  throw err;
}

/**
 * @param {string} scriptPath
 * @param {string[]} args
 * @param {boolean} noSite
 */
function pythonArgv(scriptPath, args, noSite) {
  const argv = [];
  if (noSite) argv.push("-S");
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
  const pyArgs = [...resolved.prefix, ...pythonArgv(scriptPath, args, Boolean(options.noSite))];

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
  if (options.env !== undefined) {
    spawnOpts.env = options.env;
  }

  const result = spawnSync(resolved.cmd, pyArgs, spawnOpts);
  if (result.error) {
    const spawnCode = spawnSyncErrorCode(result.error);
    if (spawnCode === "ENOBUFS" && options.responseMaxBytes !== undefined) {
      throw responseTooLargeError(
        `py_launch: response exceeds responseMaxBytes (${options.responseMaxBytes})`,
      );
    }
    if (spawnCode === "EPIPE") {
      if (options.maxInputBytes !== undefined && inputLen > options.maxInputBytes) {
        throw inputTooLargeError(
          `py_launch: input exceeds maxInputBytes (${options.maxInputBytes}; got ${inputLen} bytes)`,
        );
      }
      if (options.maxInputBytes !== undefined) {
        const err = runFailure("py_launch: Python process failed: stdin pipe closed unexpectedly", result);
        err.cause = result.error;
        throw err;
      }
    }
    const err = runFailure(`py_launch: Python process failed: ${result.error.message}`, result);
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
  assertNodeVersion();
  let resolved;
  try {
    resolved = resolvePython(opts);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 2;
    return;
  }
  const argv = opts.argv ?? process.argv.slice(2);
  const pyArgs = [...resolved.prefix, ...pythonArgv(scriptPath, argv, Boolean(opts.noSite))];
  rejectCmdBatInterpreter(resolved.cmd, resolved.label);
  const child = spawn(resolved.cmd, pyArgs, { stdio: "inherit" });
  const forwardSignal = (signal) => {
    if (!child.killed) child.kill(signal);
  };
  process.on("SIGINT", forwardSignal);
  process.on("SIGTERM", forwardSignal);
  child.on("error", (error) => {
    process.stderr.write(`py_launch: ${error.message}\n`);
    process.exitCode = 2;
  });
  child.on("exit", (code, signal) => {
    process.removeListener("SIGINT", forwardSignal);
    process.removeListener("SIGTERM", forwardSignal);
    if (signal) process.exitCode = 2;
    else process.exitCode = code === null ? 2 : code;
  });
}

module.exports = {
  main,
  run,
  resolvePython,
  buildCandidateSpecs,
  rejectCmdBatInterpreter,
  mapLaunchError,
  assertNodeVersion,
  hostMaxBufferBytes,
  inputByteLength,
  DEFAULT_PROBE_TIMEOUT_MS,
  HOST_RESPONSE_MARGIN_BYTES,
  VERSION_PROBE,
  MIN_NODE,
};

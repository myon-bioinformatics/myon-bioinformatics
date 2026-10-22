"use strict";
/**
 * Shared Node launcher for single-file stdlib Python tools (Node >= 18, node: builtins only).
 *
 * CLI: require("./vendor/py_launch.cjs").main(__dirname + "/tool.py", { envVar: "IRONMATE_PYTHON", noSite: true })
 * Library: run(scriptPath, args, { python, input, timeout, maxBuffer, noSite, envVar })
 *
 * Interpreter resolution (first match that runs Python 3.9+): opts.python, process.env[opts.envVar],
 * process.env.PYTHON, python3, python, and on Windows `py -3`.
 * Explicit opts.python or a set opts.envVar value does not fall back to later candidates.
 * Windows `.cmd`/`.bat` interpreter paths are rejected (never spawned with `shell: true`).
 *
 * `noSite` adds CPython `-S` (no site.py / no user site); default is off so tools opt in explicitly.
 */
const { spawn, spawnSync } = require("node:child_process");
const { platform } = require("node:os");

const MIN_PYTHON = [3, 9];
const DEFAULT_ENV_VAR = "PYTHON";
const DEFAULT_PROBE_TIMEOUT_MS = 5_000;
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

function rejectCmdBatInterpreter(cmd, label) {
  if (/\.(cmd|bat)$/i.test(cmd)) {
    throw interpreterError(
      `py_launch: ${label}: Windows .cmd/.bat interpreters are not supported; use python.exe or py -3`,
      { code: "ENOINTERPRETER", label },
    );
  }
}

function probeCandidate(spec, opts) {
  rejectCmdBatInterpreter(spec.cmd, spec.label);
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
    const { result, version } = probeCandidate(spec, opts);
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

function runFailure(message, result) {
  const err = new Error(message);
  if (result) {
    err.status = result.status ?? null;
    err.stdout = result.stdout ?? Buffer.alloc(0);
    err.stderr = result.stderr ?? Buffer.alloc(0);
    if (result.signal) err.signal = result.signal;
  }
  return err;
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
  const resolved = resolvePython(options);
  rejectCmdBatInterpreter(resolved.cmd, resolved.label);
  const pyArgs = [...resolved.prefix, ...pythonArgv(scriptPath, args, Boolean(options.noSite))];
  const result = spawnSync(resolved.cmd, pyArgs, {
    input: options.input,
    timeout: options.timeout,
    maxBuffer: options.maxBuffer,
  });
  if (result.error) {
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
  DEFAULT_PROBE_TIMEOUT_MS,
  VERSION_PROBE,
};

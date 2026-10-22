"use strict";
/**
 * Shared Node launcher for single-file stdlib Python tools (Node >= 18, node: builtins only).
 *
 * CLI: require("./vendor/py_launch.cjs").main(__dirname + "/tool.py", { envVar: "IRONMATE_PYTHON", noSite: true })
 * Library: run(scriptPath, args, { python, input, timeout, maxBuffer, noSite, envVar })
 *
 * Interpreter resolution (first match that runs Python 3.9+): opts.python, process.env[opts.envVar],
 * process.env.PYTHON, python3, python, and on Windows `py -3`.
 *
 * `noSite` adds CPython `-S` (no site.py / no user site); default is off so tools opt in explicitly.
 */
const { spawn, spawnSync } = require("node:child_process");
const { platform } = require("node:os");

const MIN_PYTHON = [3, 9];
const DEFAULT_ENV_VAR = "PYTHON";
const VERSION_PROBE =
  "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}'); sys.exit(0 if sys.version_info >= (3, 9) else 1)";

/** @typedef {{ cmd: string, prefix: string[], label: string }} ResolvedPython */

/**
 * @param {object} opts
 * @returns {ResolvedPython}
 */
function resolvePython(opts = {}) {
  const specs = [];
  if (opts.python) specs.push({ cmd: opts.python, prefix: [], label: String(opts.python) });
  const envVar = typeof opts.envVar === "string" && opts.envVar ? opts.envVar : DEFAULT_ENV_VAR;
  if (process.env[envVar]) {
    specs.push({ cmd: process.env[envVar], prefix: [], label: `$${envVar}` });
  }
  if (envVar !== "PYTHON" && process.env.PYTHON) {
    specs.push({ cmd: process.env.PYTHON, prefix: [], label: "$PYTHON" });
  }
  specs.push({ cmd: "python3", prefix: [], label: "python3" });
  specs.push({ cmd: "python", prefix: [], label: "python" });
  if (platform() === "win32") {
    specs.push({ cmd: "py", prefix: ["-3"], label: "py -3" });
  }

  let lastVersion = "";
  for (const spec of specs) {
    const result = spawnSync(spec.cmd, [...spec.prefix, "-c", VERSION_PROBE], {
      encoding: "utf8",
      timeout: 30_000,
    });
    if (result.error) continue;
    const line = String(result.stdout || "").trim().split(/\r?\n/)[0] || "";
    if (line) lastVersion = line;
    if (result.status === 0) {
      return { cmd: spec.cmd, prefix: spec.prefix, label: spec.label };
    }
  }

  const detail = lastVersion
    ? ` (found ${lastVersion}; Python ${MIN_PYTHON.join(".")}+ required)`
    : ` (Python ${MIN_PYTHON.join(".")}+ required; no interpreter responded)`;
  const err = new Error(`py_launch: no suitable Python interpreter${detail}`);
  err.code = "ENOINTERPRETER";
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
  const resolved = resolvePython(options);
  const pyArgs = [...resolved.prefix, ...pythonArgv(scriptPath, args, Boolean(options.noSite))];
  const result = spawnSync(resolved.cmd, pyArgs, {
    input: options.input,
    timeout: options.timeout,
    maxBuffer: options.maxBuffer,
  });
  if (result.error) {
    const err = new Error(`py_launch: Python process failed: ${result.error.message}`);
    err.cause = result.error;
    throw err;
  }
  if (result.signal) {
    throw new Error(`py_launch: Python process terminated by signal ${result.signal}`);
  }
  if (result.status !== 0) {
    const msg = (result.stderr && result.stderr.toString("utf8").trim()) || "Python script failed";
    throw new Error(msg);
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
  const child = spawn(resolved.cmd, pyArgs, { stdio: "inherit" });
  child.on("error", (error) => {
    process.stderr.write(`py_launch: ${error.message}\n`);
    process.exitCode = 2;
  });
  child.on("exit", (code, signal) => {
    if (signal) process.exitCode = 2;
    else process.exitCode = code === null ? 2 : code;
  });
}

module.exports = { main, run, resolvePython };

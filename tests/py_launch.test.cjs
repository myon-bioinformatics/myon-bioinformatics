"use strict";
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { describe, it } = require("node:test");
const { main, run, resolvePython, buildCandidateSpecs, DEFAULT_PROBE_TIMEOUT_MS } = require("../py_launch.cjs");

const LAUNCHER = path.join(__dirname, "..", "py_launch.cjs");
const FIXTURES = path.join(__dirname, "fixtures", "py_launch");
const PYTHON =
  process.env.PY_LAUNCH_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");

function outText(buffer) {
  return buffer.toString("utf8").replace(/\r\n/g, "\n").trim();
}

function probeStubSource(version, pass, slowMs = 0) {
  return `"use strict";
if (process.argv.includes("-c")) {
  if (${slowMs} > 0) {
    const end = Date.now() + ${slowMs};
    while (Date.now() < end) {}
  }
  console.log(${JSON.stringify(version)});
  process.exit(${pass ? 0 : 1});
}
process.exit(0);
`;
}

function makeExecutableStub(dir, name, source) {
  const stub = path.join(dir, `${name}.cjs`);
  fs.writeFileSync(stub, source, "utf8");
  if (process.platform === "win32") {
    const bat = path.join(dir, `${name}.cmd`);
    fs.writeFileSync(bat, `@echo off\r\n"${process.execPath}" "${stub}" %*\r\n`, "utf8");
    return bat;
  }
  const shim = path.join(dir, name);
  fs.writeFileSync(shim, `#!/bin/sh\nexec "${process.execPath}" "${stub}" "$@"\n`, { mode: 0o755 });
  return shim;
}

function resolveInSubprocess(code, env = {}) {
  return spawnSync(process.execPath, ["-e", code], {
    encoding: "utf8",
    env: { ...process.env, ...env },
  });
}

function runNodeMain(scriptRel, extraArgv, opts = {}) {
  const scriptPath = path.join(FIXTURES, scriptRel);
  const wrapper = `
    const { main } = require(${JSON.stringify(LAUNCHER)});
    main(${JSON.stringify(scriptPath)}, ${JSON.stringify({ python: PYTHON, ...opts, argv: extraArgv })});
  `;
  return spawnSync(process.execPath, ["-e", wrapper], {
    encoding: "utf8",
    env: { ...process.env, ...(opts.env || {}) },
  });
}

describe("py_launch.run (library mode)", () => {
  it("passes argv and returns stdout", () => {
    const result = run(path.join(FIXTURES, "echo_argv.py"), ["a", "b"], { python: PYTHON });
    assert.equal(outText(result.stdout), "a\nb");
  });

  it("pipes stdin and reads stdout", () => {
    const input = Buffer.from('{"n": 3}\n', "utf8");
    const result = run(path.join(FIXTURES, "json_double.py"), [], { python: PYTHON, input });
    assert.deepEqual(JSON.parse(result.stdout.toString("utf8")), 6);
  });

  it("attaches status, stdout, and stderr on nonzero exit", () => {
    try {
      run(path.join(FIXTURES, "exit_code.py"), ["4"], { python: PYTHON });
      assert.fail("expected throw");
    } catch (error) {
      assert.equal(error.status, 4);
      assert.ok(error.stdout !== undefined);
      assert.ok(error.stderr !== undefined);
    }
  });

  it("honors noSite", () => {
    const result = run(path.join(FIXTURES, "check_nosite.py"), [], { python: PYTHON, noSite: true });
    assert.equal(outText(result.stdout), "1");
  });

  it("times out long-running scripts", () => {
    assert.throws(
      () => run(path.join(FIXTURES, "sleep.py"), ["5"], { python: PYTHON, timeout: 200 }),
      /ETIMEDOUT|timed out|Python process failed/i,
    );
  });

  it("surfaces maxBuffer overflow", () => {
    assert.throws(
      () => run(path.join(FIXTURES, "big_stdout.py"), ["200000"], { python: PYTHON, maxBuffer: 1024 }),
      (error) => /maxBuffer|ENOBUFS/i.test(String(error.cause?.message || error.message)),
    );
  });

  it("rejects when candidates list is empty", () => {
    assert.throws(
      () => run(path.join(FIXTURES, "echo_argv.py"), [], { candidates: [] }),
      (error) => /no suitable Python interpreter/.test(error.message),
    );
  });

  it("runs script paths and args containing spaces", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-space-"));
    const script = path.join(dir, "tool with spaces.py");
    fs.writeFileSync(script, 'import sys\nprint(" ".join(sys.argv[1:]))\n', "utf8");
    const result = run(script, ["arg one", "arg two"], { python: PYTHON });
    assert.equal(outText(result.stdout), "arg one arg two");
  });
});

describe("py_launch.resolvePython", () => {
  it("prefers explicit envVar over PYTHON without opts.python", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-env-"));
    const preferred = makeExecutableStub(dir, "preferred", probeStubSource("3.12", true));
    const fallback = makeExecutableStub(dir, "fallback", probeStubSource("3.12", true));
    const result = resolveInSubprocess(
      `const { resolvePython } = require(${JSON.stringify(LAUNCHER)});
       const resolved = resolvePython({ envVar: "PY_LAUNCH_TEST_PREFERRED" });
       console.log(resolved.cmd);`,
      { PY_LAUNCH_TEST_PREFERRED: preferred, PYTHON: fallback },
    );
    assert.equal(result.status, 0);
    assert.equal(result.stdout.trim(), preferred);
  });

  it("does not fall back when explicit python is missing", () => {
    assert.throws(
      () => resolvePython({ python: path.join(FIXTURES, "missing-interpreter") }),
      (error) => /missing-interpreter/.test(error.message),
    );
  });

  it("does not fall back when explicit envVar points at too-old python", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-old-"));
    const old = makeExecutableStub(dir, "old", probeStubSource("3.8", false));
    const key = "PY_LAUNCH_TEST_OLD";
    const previous = process.env[key];
    process.env[key] = old;
    try {
      assert.throws(
        () => resolvePython({ envVar: key }),
        (error) => /3\.9\+ required/.test(error.message),
      );
    } finally {
      if (previous === undefined) delete process.env[key];
      else process.env[key] = previous;
    }
  });

  it("fails clearly when no interpreter is available", () => {
    assert.throws(
      () => resolvePython({ candidates: [] }),
      (error) => /Python 3\.9\+ required/.test(error.message),
    );
  });

  it("uses probe timeout near five seconds by default", () => {
    assert.equal(DEFAULT_PROBE_TIMEOUT_MS, 5_000);
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-slow-probe-"));
    const slow = makeExecutableStub(dir, "slow", probeStubSource("3.12", true, 30_000));
    const started = Date.now();
    assert.throws(
      () =>
        resolvePython({
          candidates: [{ cmd: slow, prefix: [], label: "slow" }],
          probeTimeoutMs: 500,
        }),
      (error) => /no suitable Python interpreter/.test(error.message),
    );
    assert.ok(Date.now() - started < 5_000);
  });

  it("includes py -3 on Windows candidate lists", () => {
    const specs = buildCandidateSpecs({ platform: "win32" });
    assert.ok(specs.some((spec) => spec.cmd === "py" && spec.prefix[0] === "-3"));
    const linuxSpecs = buildCandidateSpecs({ platform: "linux" });
    assert.ok(!linuxSpecs.some((spec) => spec.cmd === "py"));
  });
});

describe("py_launch.main (CLI passthrough)", () => {
  it("forwards argv and exit code", () => {
    const ok = runNodeMain("exit_code.py", ["0"]);
    assert.equal(ok.status, 0);
    const bad = runNodeMain("exit_code.py", ["7"]);
    assert.equal(bad.status, 7);
  });

  it("inherits stdio for stdin", async () => {
    const script = path.join(FIXTURES, "echo_stdin.py");
    const child = spawn(
      process.execPath,
      [
        "-e",
        `require(${JSON.stringify(LAUNCHER)}).main(${JSON.stringify(script)}, { python: ${JSON.stringify(PYTHON)} });`,
      ],
      { stdio: ["pipe", "pipe", "inherit"] },
    );
    child.stdin.write("hello");
    child.stdin.end();
    const chunks = [];
    child.stdout.on("data", (chunk) => chunks.push(chunk));
    const code = await new Promise((resolve) => child.on("close", resolve));
    assert.equal(Buffer.concat(chunks).toString("utf8"), "hello");
    assert.equal(code, 0);
  });

  it("uses exit code 2 when explicit python is missing", () => {
    const script = path.join(FIXTURES, "echo_argv.py");
    const result = spawnSync(
      process.execPath,
      [
        "-e",
        `require(${JSON.stringify(LAUNCHER)}).main(${JSON.stringify(script)}, { python: ${JSON.stringify(path.join(FIXTURES, "missing-interpreter"))} });`,
      ],
      { encoding: "utf8" },
    );
    assert.equal(result.status, 2);
    assert.match(result.stderr, /missing-interpreter/);
  });

  it("uses exit code 2 when explicit envVar python is too old", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-cli-old-"));
    const old = makeExecutableStub(dir, "old-cli", probeStubSource("3.8", false));
    const script = path.join(FIXTURES, "echo_argv.py");
    const result = resolveInSubprocess(
      `require(${JSON.stringify(LAUNCHER)}).main(${JSON.stringify(script)}, { envVar: "PY_LAUNCH_TEST_OLD" });`,
      { PY_LAUNCH_TEST_OLD: old },
    );
    assert.equal(result.status, 2);
    assert.match(result.stderr, /3\.9\+ required/);
  });

  it("uses exit code 2 when the child is signaled", () => {
    if (process.platform === "win32") return;
    const result = runNodeMain("sigterm.py", []);
    assert.equal(result.status, 2);
  });

  it("forwards SIGTERM to the python child on POSIX", async () => {
    if (process.platform === "win32") return;
    const script = path.join(FIXTURES, "sleep.py");
    const child = spawn(
      process.execPath,
      [
        "-e",
        `require(${JSON.stringify(LAUNCHER)}).main(${JSON.stringify(script)}, { python: ${JSON.stringify(PYTHON)}, argv: ["30"] });`,
      ],
      { stdio: "inherit" },
    );
    await new Promise((resolve) => setTimeout(resolve, 500));
    child.kill("SIGTERM");
    const code = await new Promise((resolve) => child.on("close", resolve));
    assert.equal(code, 2);
  });
});

describe("py_launch consumer wrapper", () => {
  it("matches the documented three-line pattern", () => {
    const consumer = path.join(FIXTURES, "consumer.cjs");
    fs.writeFileSync(
      consumer,
      `"use strict";
const { main } = require(${JSON.stringify(LAUNCHER)});
if (require.main === module) main(__dirname + "/echo_argv.py", { python: process.env.PY_LAUNCH_TEST_PYTHON });
`,
      "utf8",
    );
    const result = spawnSync(process.execPath, [consumer, "x", "y"], {
      encoding: "utf8",
      env: { ...process.env, PY_LAUNCH_TEST_PYTHON: PYTHON },
    });
    assert.equal(result.status, 0);
    assert.equal(outText(Buffer.from(result.stdout, "utf8")), "x\ny");
  });
});

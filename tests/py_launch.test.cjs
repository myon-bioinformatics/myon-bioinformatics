"use strict";
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { describe, it } = require("node:test");
const { main, run, resolvePython } = require("../py_launch.cjs");

const FIXTURES = path.join(__dirname, "fixtures", "py_launch");
const PYTHON = process.env.PY_LAUNCH_TEST_PYTHON || "python3";

function runNodeMain(scriptRel, extraArgv, env = {}) {
  const wrapper = `
    const { main } = require(${JSON.stringify(path.join(__dirname, "..", "py_launch.cjs"))});
    main(${JSON.stringify(path.join(FIXTURES, scriptRel))}, { python: ${JSON.stringify(PYTHON)}, argv: ${JSON.stringify(extraArgv)} });
  `;
  return spawnSync(process.execPath, ["-e", wrapper], {
    encoding: "utf8",
    env: { ...process.env, ...env },
  });
}

describe("py_launch.run (library mode)", () => {
  it("passes argv and returns stdout", () => {
    const result = run(path.join(FIXTURES, "echo_argv.py"), ["a", "b"], { python: PYTHON });
    assert.equal(result.stdout.toString("utf8").trim(), "a\nb");
  });

  it("pipes stdin and reads stdout", () => {
    const input = Buffer.from('{"n": 3}\n', "utf8");
    const result = run(path.join(FIXTURES, "json_double.py"), [], { python: PYTHON, input });
    assert.deepEqual(JSON.parse(result.stdout.toString("utf8")), 6);
  });

  it("surfaces stderr on nonzero exit", () => {
    assert.throws(
      () => run(path.join(FIXTURES, "exit_code.py"), ["4"], { python: PYTHON }),
      (error) => error.message.length > 0,
    );
  });

  it("honors noSite", () => {
    const result = run(path.join(FIXTURES, "check_nosite.py"), [], { python: PYTHON, noSite: true });
    assert.equal(result.stdout.toString("utf8").trim(), "1");
  });

  it("rejects a missing interpreter", () => {
    const isolated = spawnSync(
      process.execPath,
      [
        "-e",
        `const { run } = require(${JSON.stringify(path.join(__dirname, "..", "py_launch.cjs"))});
         try { run(${JSON.stringify(path.join(FIXTURES, "echo_argv.py"))}, [], { python: "/missing/py_launch-test" }); process.exit(3); }
         catch (e) { console.error(e.message); process.exit(0); }`,
      ],
      { encoding: "utf8", env: { ...process.env, PATH: path.dirname(process.execPath), PYTHON: "" } },
    );
    assert.equal(isolated.status, 0);
    assert.match(isolated.stderr, /no suitable Python interpreter/);
  });
});

describe("py_launch.resolvePython", () => {
  it("respects envVar before PYTHON", () => {
    const resolved = resolvePython({
      python: PYTHON,
      envVar: "PY_LAUNCH_TEST_ALT",
    });
    assert.equal(resolved.cmd, PYTHON);
  });

  it("fails clearly when no interpreter is available", () => {
    const isolated = spawnSync(
      process.execPath,
      [
        "-e",
        `const { resolvePython } = require(${JSON.stringify(path.join(__dirname, "..", "py_launch.cjs"))});
         try { resolvePython({ python: "/missing/py_launch-test", envVar: "PY_LAUNCH_TEST_EMPTY" }); process.exit(3); }
         catch (e) { console.error(e.message); process.exit(0); }`,
      ],
      { encoding: "utf8", env: { ...process.env, PATH: path.dirname(process.execPath), PYTHON: "" } },
    );
    assert.equal(isolated.status, 0);
    assert.match(isolated.stderr, /Python 3\.9\+ required/);
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
        `require(${JSON.stringify(path.join(__dirname, "..", "py_launch.cjs"))}).main(${JSON.stringify(script)}, { python: ${JSON.stringify(PYTHON)} });`,
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

  it("uses exit code 2 on spawn failure", () => {
    const script = path.join(FIXTURES, "echo_argv.py");
    const result = spawnSync(
      process.execPath,
      [
        "-e",
        `require(${JSON.stringify(path.join(__dirname, "..", "py_launch.cjs"))}).main(${JSON.stringify(script)}, { python: "/missing/py_launch-test" });`,
      ],
      { encoding: "utf8", env: { ...process.env, PATH: path.dirname(process.execPath), PYTHON: "" } },
    );
    assert.equal(result.status, 2);
    assert.match(result.stderr, /py_launch/);
  });

  it("uses exit code 2 when the child is signaled", () => {
    if (process.platform === "win32") return;
    const result = runNodeMain("sigterm.py", []);
    assert.equal(result.status, 2);
  });
});

describe("py_launch consumer wrapper", () => {
  it("matches the documented three-line pattern", () => {
    const consumer = path.join(__dirname, "fixtures", "py_launch", "consumer.cjs");
    const launcher = path.join(__dirname, "..", "py_launch.cjs");
    fs.writeFileSync(
      consumer,
      `"use strict";
const { main } = require(${JSON.stringify(launcher)});
if (require.main === module) main(__dirname + "/echo_argv.py", { python: process.env.PY_LAUNCH_TEST_PYTHON });
`,
      "utf8",
    );
    const result = spawnSync(process.execPath, [consumer, "x", "y"], {
      encoding: "utf8",
      env: { ...process.env, PY_LAUNCH_TEST_PYTHON: PYTHON },
    });
    assert.equal(result.status, 0);
    assert.equal(result.stdout.trim(), "x\ny");
  });
});

"use strict";
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { describe, it } = require("node:test");
const {
  main,
  run,
  resolvePython,
  buildCandidateSpecs,
  mapLaunchError,
  spawnSyncFailureError,
  buildChildEnv,
  envLookup,
  assertNodeVersion,
  hostMaxBufferBytes,
  inputByteLength,
  DEFAULT_PROBE_TIMEOUT_MS,
  HOST_RESPONSE_MARGIN_BYTES,
  MIN_NODE,
} = require("../py_launch.cjs");

const LAUNCHER = path.join(__dirname, "..", "py_launch.cjs");
const FIXTURES = path.join(__dirname, "fixtures", "py_launch");
const PYTHON =
  process.env.PY_LAUNCH_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");
const NODE_MAJOR = Number.parseInt(String(process.versions.node).split(".")[0], 10);

function outText(buffer) {
  return buffer.toString("utf8").replace(/\r\n/g, "\n").trim();
}

function rmTempDir(dir) {
  if (dir) fs.rmSync(dir, { recursive: true, force: true });
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

function makeNodeCandidate(dir, name, source) {
  const stub = path.join(dir, `${name}.cjs`);
  fs.writeFileSync(stub, source, "utf8");
  return { cmd: process.execPath, prefix: [stub], label: name };
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

describe("py_launch Node runtime", () => {
  it("requires Node 24+", () => {
    assert.ok(
      NODE_MAJOR >= MIN_NODE[0],
      `requires Node ${MIN_NODE[0]}+ (running ${process.versions.node})`,
    );
  });
});

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

  it("honors isolated and ignores PYTHONPATH stdlib shadowing", () => {
    const shadowDir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-xml-shadow-"));
    try {
      fs.writeFileSync(
        path.join(shadowDir, "xml.py"),
        'PY_LAUNCH_SHADOW = "shadow"\n',
        "utf8",
      );
      const script = path.join(FIXTURES, "import_xml_shadow.py");
      const shadowed = run(script, [], {
        python: PYTHON,
        env: { PYTHONPATH: shadowDir },
      });
      assert.equal(outText(shadowed.stdout), "shadow");
      const isolated = run(script, [], {
        python: PYTHON,
        env: { PYTHONPATH: shadowDir },
        isolated: true,
      });
      assert.equal(outText(isolated.stdout), "stdlib");
    } finally {
      rmTempDir(shadowDir);
    }
  });

  it("ignores PYTHONPATH from the env option when isolated", () => {
    const shadowDir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-env-pythonpath-"));
    try {
      fs.writeFileSync(path.join(shadowDir, "xml.py"), 'PY_LAUNCH_SHADOW = "from-env"\n', "utf8");
      const script = path.join(FIXTURES, "import_xml_shadow.py");
      const without = run(script, [], { python: PYTHON, env: { PYTHONPATH: shadowDir } });
      assert.equal(outText(without.stdout), "from-env");
      const withIsolated = run(script, [], {
        python: PYTHON,
        env: { PYTHONPATH: shadowDir },
        isolated: true,
      });
      assert.equal(outText(withIsolated.stdout), "stdlib");
    } finally {
      rmTempDir(shadowDir);
    }
  });

  it("blocks sibling imports under isolated but allows sys.path self-insert", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-sibling-"));
    try {
      fs.writeFileSync(path.join(dir, "sib.py"), 'VALUE = "sib"\n', "utf8");
      fs.writeFileSync(path.join(dir, "entry.py"), "import sib\nprint(sib.VALUE)\n", "utf8");
      fs.writeFileSync(
        path.join(dir, "entry_self_path.py"),
        [
          "import os",
          "import sys",
          "sys.path.insert(0, os.path.dirname(__file__))",
          "import sib",
          "print(sib.VALUE)",
          "",
        ].join("\n"),
        "utf8",
      );
      const entry = path.join(dir, "entry.py");
      const ok = run(entry, [], { python: PYTHON });
      assert.equal(outText(ok.stdout), "sib");
      assert.throws(
        () => run(entry, [], { python: PYTHON, isolated: true }),
        (error) => error.status !== 0,
      );
      const fixed = run(path.join(dir, "entry_self_path.py"), [], {
        python: PYTHON,
        isolated: true,
      });
      assert.equal(outText(fixed.stdout), "sib");
    } finally {
      rmTempDir(dir);
    }
  });

  it("times out long-running scripts", () => {
    assert.throws(
      () => run(path.join(FIXTURES, "sleep.py"), ["5"], { python: PYTHON, timeout: 200 }),
      /ETIMEDOUT|timed out|Python process failed/i,
    );
  });

  it("surfaces maxBuffer overflow without responseMaxBytes mapping", () => {
    assert.throws(
      () => run(path.join(FIXTURES, "big_stdout.py"), ["200000"], { python: PYTHON, maxBuffer: 1024 }),
      (error) => /maxBuffer|ENOBUFS/i.test(String(error.cause?.message || error.message)),
    );
  });

  it("maps responseMaxBytes overflow to ERESPONSETOOLARGE", () => {
    assert.throws(
      () =>
        run(path.join(FIXTURES, "big_stdout.py"), ["200000"], {
          python: PYTHON,
          responseMaxBytes: 1024,
        }),
      (error) => error.code === "ERESPONSETOOLARGE" && /responseMaxBytes/.test(error.message),
    );
    assert.equal(hostMaxBufferBytes(1024), 1024 + HOST_RESPONSE_MARGIN_BYTES);
  });

  it("rejects input above maxInputBytes before spawn", () => {
    const input = "x".repeat(200);
    assert.throws(
      () =>
        run(path.join(FIXTURES, "echo_stdin.py"), [], {
          python: PYTHON,
          input,
          maxInputBytes: 100,
        }),
      (error) => error.code === "EINPUTTOOLARGE" && /maxInputBytes/.test(error.message),
    );
    assert.equal(inputByteLength(input), 200);
  });

  it("rejects maxBuffer together with responseMaxBytes", () => {
    assert.throws(
      () =>
        run(path.join(FIXTURES, "echo_argv.py"), [], {
          python: PYTHON,
          maxBuffer: 1024,
          responseMaxBytes: 2048,
        }),
      /cannot set both maxBuffer and responseMaxBytes/,
    );
  });

  it("merges env over process.env without injecting PYTHONUTF8", () => {
    const merged = buildChildEnv({ PYTHONUTF8: "0", PYTHONIOENCODING: "cp1252" });
    assert.equal(envLookup(merged, "PYTHONUTF8"), "0");
    assert.ok(envLookup(merged, "PATH") && String(envLookup(merged, "PATH")).length > 0);
    const result = run(path.join(FIXTURES, "print_utf8_env.py"), [], {
      python: PYTHON,
      env: { PYTHONUTF8: "0", PYTHONIOENCODING: "cp1252" },
    });
    const seen = JSON.parse(result.stdout.toString("utf8"));
    assert.equal(seen.PYTHONUTF8, "0");
    assert.equal(seen.PYTHONIOENCODING, "cp1252");
  });

  it("merges PATH case-insensitively on Windows without duplicate keys", () => {
    if (process.platform !== "win32") return;
    const merged = buildChildEnv({ PATH: "C:\\py-launch-test-path" });
    const pathKeys = Object.keys(merged).filter((key) => key.toLowerCase() === "path");
    assert.equal(pathKeys.length, 1);
    assert.equal(merged[pathKeys[0]], "C:\\py-launch-test-path");
    const mergedUtf8 = buildChildEnv({ pythonutf8: "0" });
    const utf8Keys = Object.keys(mergedUtf8).filter((key) => key.toLowerCase() === "pythonutf8");
    assert.equal(utf8Keys.length, 1);
    assert.equal(mergedUtf8[utf8Keys[0]], "0");
  });

  it("inherits PATH when env overrides are merged (Windows-safe)", () => {
    const result = run(path.join(FIXTURES, "print_path_env.py"), [], {
      python: PYTHON,
      env: { PY_LAUNCH_ENV_MARKER: "merged" },
    });
    const seen = JSON.parse(result.stdout.toString("utf8"));
    assert.equal(seen.has_path, true);
    assert.equal(seen.marker, "merged");
    if (process.platform === "win32") {
      assert.equal(seen.has_systemroot, true);
    }
  });

  it("rejects when candidates list is empty", () => {
    assert.throws(
      () => run(path.join(FIXTURES, "echo_argv.py"), [], { candidates: [] }),
      (error) => /no suitable Python interpreter/.test(error.message),
    );
  });

  it("runs script paths and args containing spaces", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-space-"));
    try {
      const script = path.join(dir, "tool with spaces.py");
      fs.writeFileSync(script, 'import sys\nprint(" ".join(sys.argv[1:]))\n', "utf8");
      const result = run(script, ["arg one", "arg two"], { python: PYTHON });
      assert.equal(outText(result.stdout), "arg one arg two");
    } finally {
      rmTempDir(dir);
    }
  });
});

describe("py_launch.resolvePython", () => {
  it("prefers explicit envVar over PYTHON without opts.python", () => {
    const key = "PY_LAUNCH_TEST_PREFERRED";
    const previousPreferred = process.env[key];
    const previousPython = process.env.PYTHON;
    process.env[key] = "C:\\preferred\\python.exe";
    process.env.PYTHON = "C:\\fallback\\python.exe";
    try {
      const specs = buildCandidateSpecs({ envVar: key });
      assert.equal(specs.length, 1);
      assert.equal(specs[0].cmd, process.env[key]);
      assert.equal(specs[0].strict, true);
    } finally {
      if (previousPreferred === undefined) delete process.env[key];
      else process.env[key] = previousPreferred;
      if (previousPython === undefined) delete process.env.PYTHON;
      else process.env.PYTHON = previousPython;
    }
  });

  it("does not fall back when explicit python is missing", () => {
    assert.throws(
      () => resolvePython({ python: path.join(FIXTURES, "missing-interpreter") }),
      (error) => /missing-interpreter/.test(error.message),
    );
  });

  it("does not fall back when explicit envVar points at too-old python", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-old-"));
    try {
      const old = makeNodeCandidate(dir, "old", probeStubSource("3.8", false));
      assert.throws(
        () => resolvePython({ candidates: [{ ...old, label: "$PY_LAUNCH_TEST_OLD", strict: true }] }),
        (error) => /3\.9\+ required/.test(error.message),
      );
    } finally {
      rmTempDir(dir);
    }
  });

  it("rejects Windows .cmd and .bat interpreter paths when explicit", () => {
    assert.throws(
      () => resolvePython({ python: "C:\\Tools\\python.cmd", platform: "win32" }),
      (error) => /\.cmd\/\.bat interpreters are not supported/.test(error.message),
    );
    assert.throws(
      () => resolvePython({ python: "C:\\Tools\\launch.bat", platform: "win32" }),
      (error) => /\.cmd\/\.bat interpreters are not supported/.test(error.message),
    );
  });

  it("skips non-explicit PYTHON .cmd with a warning and continues", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-python-cmd-"));
    try {
      const cmd = path.join(dir, "python.cmd");
      fs.writeFileSync(cmd, "@echo off\r\n", "utf8");
      const warnings = [];
      const previousPython = process.env.PYTHON;
      const previousWrite = process.stderr.write;
      process.env.PYTHON = cmd;
      process.stderr.write = (chunk, encoding, callback) => {
        warnings.push(String(chunk));
        return previousWrite.call(process.stderr, chunk, encoding, callback);
      };
      try {
        const resolved = resolvePython({});
        assert.notEqual(resolved.cmd.toLowerCase(), cmd.toLowerCase());
        assert.ok(["python", "python3", "py -3"].includes(resolved.label));
        assert.ok(warnings.some((line) => /warning: skipping \$PYTHON/.test(line)));
      } finally {
        if (previousPython === undefined) delete process.env.PYTHON;
        else process.env.PYTHON = previousPython;
        process.stderr.write = previousWrite;
      }
    } finally {
      rmTempDir(dir);
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
    try {
      const slow = makeNodeCandidate(dir, "slow", probeStubSource("3.12", true, 30_000));
      const started = Date.now();
      assert.throws(
        () =>
          resolvePython({
            candidates: [{ ...slow, label: "slow" }],
            probeTimeoutMs: 500,
          }),
        (error) => /no suitable Python interpreter/.test(error.message),
      );
      assert.ok(Date.now() - started < 5_000);
    } finally {
      rmTempDir(dir);
    }
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

  it("forwards isolated and ignores PYTHONPATH from env", () => {
    const shadowDir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-main-isolated-"));
    try {
      fs.writeFileSync(path.join(shadowDir, "xml.py"), 'PY_LAUNCH_SHADOW = "shadow"\n', "utf8");
      const result = runNodeMain("import_xml_shadow.py", [], {
        isolated: true,
        env: { PYTHONPATH: shadowDir },
      });
      assert.equal(result.status, 0);
      assert.equal(outText(Buffer.from(result.stdout || "", "utf8")), "stdlib");
    } finally {
      rmTempDir(shadowDir);
    }
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
    if (process.platform === "win32") return;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-cli-old-"));
    try {
      const old = makeNodeCandidate(dir, "old-cli", probeStubSource("3.8", false));
      const shim = path.join(dir, "old-shim");
      fs.writeFileSync(shim, `#!/bin/sh\nexec "${process.execPath}" "${old.prefix[0]}" "$@"\n`, { mode: 0o755 });
      const script = path.join(FIXTURES, "echo_argv.py");
      const result = resolveInSubprocess(
        `require(${JSON.stringify(LAUNCHER)}).main(${JSON.stringify(script)}, { envVar: "PY_LAUNCH_TEST_OLD" });`,
        { PY_LAUNCH_TEST_OLD: shim },
      );
      assert.equal(result.status, 2);
      assert.match(result.stderr, /3\.9\+ required/);
    } finally {
      rmTempDir(dir);
    }
  });

  it("uses exit code 2 when explicit python is a .cmd interpreter", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "py-launch-cmd-"));
    try {
      const cmd = path.join(dir, "python.cmd");
      fs.writeFileSync(cmd, "@echo off\r\n", "utf8");
      const script = path.join(FIXTURES, "echo_argv.py");
      const result = resolveInSubprocess(
        `require(${JSON.stringify(LAUNCHER)}).main(${JSON.stringify(script)}, { python: ${JSON.stringify(cmd)} });`,
      );
      assert.equal(result.status, 2);
      assert.match(result.stderr, /\.cmd\/\.bat interpreters are not supported/);
    } finally {
      rmTempDir(dir);
    }
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

describe("py_launch.spawnSyncFailureError", () => {
  it("maps EPIPE to ELAUNCHSTDINCLOSED", () => {
    const mapped = spawnSyncFailureError(
      { error: Object.assign(new Error("spawnSync python3 EPIPE"), { code: "EPIPE" }) },
      {},
    );
    assert.ok(mapped);
    assert.equal(mapped.code, "ELAUNCHSTDINCLOSED");
    assert.match(mapped.message, /stdin pipe closed unexpectedly/);
  });

  it("maps ENOBUFS with responseMaxBytes to ERESPONSETOOLARGE", () => {
    const mapped = spawnSyncFailureError(
      { error: Object.assign(new Error("maxBuffer exceeded"), { code: "ENOBUFS" }) },
      { responseMaxBytes: 4096 },
    );
    assert.ok(mapped);
    assert.equal(mapped.code, "ERESPONSETOOLARGE");
  });

  it("maps ENOBUFS without responseMaxBytes to ELAUNCHFAILURE", () => {
    const mapped = spawnSyncFailureError(
      { error: Object.assign(new Error("maxBuffer exceeded"), { code: "ENOBUFS" }) },
      {},
    );
    assert.ok(mapped);
    assert.equal(mapped.code, "ELAUNCHFAILURE");
  });
});

describe("py_launch.mapLaunchError", () => {
  const PREFIX = "tool Python process failed: ";

  it("rewrites py_launch spawn failures and preserves code", () => {
    const raw = Object.assign(new Error("py_launch: Python process failed: spawnSync ENOENT"), {
      code: "ELAUNCHFAILURE",
    });
    assert.throws(() => mapLaunchError(raw, PREFIX, "fallback"), (error) => {
      assert.equal(error.message, PREFIX + "spawnSync ENOENT");
      assert.equal(error.code, "ELAUNCHFAILURE");
      return true;
    });
  });

  it("maps bare EPIPE to launch failure unless inputKnownToExceed", () => {
    const raw = Object.assign(new Error("py_launch: Python process failed: write EPIPE"), { code: "EPIPE" });
    assert.throws(
      () => mapLaunchError(raw, PREFIX, "fallback"),
      (error) =>
        error.message === PREFIX + "Python stdin pipe closed unexpectedly" &&
        error.code === "ELAUNCHSTDINCLOSED",
    );
    assert.throws(
      () => mapLaunchError(raw, PREFIX, "fallback", { inputKnownToExceed: true }),
      (error) => error.message === "input exceeds size limit" && error.code === "EINPUTTOOLARGE",
    );
  });

  it("does not re-map EPIPE from cause when run marked ELAUNCHSTDINCLOSED", () => {
    const cause = Object.assign(new Error("EPIPE"), { code: "EPIPE" });
    const raw = Object.assign(
      new Error("py_launch: Python process failed: stdin pipe closed unexpectedly"),
      { code: "ELAUNCHSTDINCLOSED", cause },
    );
    assert.throws(() => mapLaunchError(raw, PREFIX, "fallback"), (error) => {
      assert.equal(error.code, "ELAUNCHSTDINCLOSED");
      assert.match(error.message, /stdin pipe closed unexpectedly/);
      return true;
    });
  });

  it("maps ENOBUFS messages based on responseMaxBytes context", () => {
    const raw = Object.assign(new Error("py_launch: Python process failed: maxBuffer exceeded"), {
      code: "ENOBUFS",
    });
    assert.throws(
      () => mapLaunchError(raw, PREFIX, "fallback"),
      (error) =>
        error.message === PREFIX + "Python stdout exceeded the Node maxBuffer limit" &&
        error.code === "ELAUNCHFAILURE",
    );
    assert.throws(
      () => mapLaunchError(raw, PREFIX, "fallback", { responseMaxBytes: 1024 }),
      (error) => /responseMaxBytes/.test(error.message) && error.code === "ERESPONSETOOLARGE",
    );
  });
});

describe("py_launch.assertNodeVersion", () => {
  it("enforces the Node 24+ floor", () => {
    assert.equal(MIN_NODE[0], 24);
    if (NODE_MAJOR >= MIN_NODE[0]) {
      assertNodeVersion();
    } else {
      assert.throws(() => assertNodeVersion(), /requires Node 24\+/);
    }
  });
});

describe("py_launch security", () => {
  it("does not inject PYTHONUTF8 or PYTHONIOENCODING in launcher source", () => {
    const source = fs
      .readFileSync(LAUNCHER, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    assert.ok(!/\bPYTHONUTF8\b/.test(source));
    assert.ok(!/\bPYTHONIOENCODING\b/.test(source));
  });
  it("does not enable shell spawning in the launcher source", () => {
    const source = fs
      .readFileSync(LAUNCHER, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    assert.ok(!/shell\s*:\s*true/.test(source));
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

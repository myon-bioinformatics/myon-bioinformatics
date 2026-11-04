"""Offline tests for tools/nano_apt.py: fake apt/dpkg; one real read-only dpkg-query check."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("nano_apt", Path(__file__).parents[1] / "tools/nano_apt.py")
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)

FAKE_BIN = {"apt-get": "/fake/apt-get", "dpkg-query": "/fake/dpkg-query", "sudo": "/fake/sudo"}


class Fake:
    """Stands in for subprocess.run; `installed` maps package -> version."""

    def __init__(self, installed, apt_rc=0):
        self.installed, self.apt_rc, self.calls = dict(installed), apt_rc, []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if argv[0].endswith("dpkg-query"):
            lines = ["%s ii  %s" % (n, v) for n, v in self.installed.items() if n in argv]
            return subprocess.CompletedProcess(argv, 0 if len(lines) == len(argv[3:]) else 1,
                                               stdout="\n".join(lines) + "\n", stderr="")
        if argv[-3:-1] == ["install", "-y"] or "install" in argv:
            if self.apt_rc == 0:
                for item in argv[argv.index("install") + 3:]:
                    name, _, version = item.partition("=")
                    self.installed[name] = version or "9.9"
        return subprocess.CompletedProcess(argv, self.apt_rc)


class NanoAptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def lst(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def call(self, args, fake, euid=0, which=None):
        which = FAKE_BIN if which is None else which
        out = io.StringIO()
        with patch.object(nano.subprocess, "run", fake), \
                patch.object(nano.shutil, "which", lambda name, path=None: which.get(name)), \
                patch.object(nano.os, "geteuid", lambda: euid), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = nano.main(args)
        return code, (json.loads(out.getvalue()) if out.getvalue() else None)

    def test_parse_comments_pins_and_merge(self):
        a = self.lst("a.txt", "# runtime\n\ntzdata\nlibblas3=3.12.1-6\n")
        b = self.lst("b.txt", "tzdata=2025b-4\ngit\n")
        self.assertEqual(nano.merge([a, b]),
                         {"tzdata": "2025b-4", "libblas3": "3.12.1-6", "git": None})

    def test_bad_lines_and_conflicts_fail_closed(self):
        for text in ("Git\n", "git extra\n", "git # inline\n", "-y\n", "git=\n", "a=1\na=2\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                nano.read_list(self.lst("bad.txt", text))
        with self.assertRaises(ValueError):
            nano.merge([self.lst("x.txt", "a=1\n"), self.lst("y.txt", "a=2\n")])

    def test_non_regular_and_oversized_inputs_rejected(self):
        link = self.root / "link.txt"
        link.symlink_to(self.lst("real.txt", "git\n"))
        with self.assertRaises(ValueError):
            nano.read_list(link)
        with self.assertRaises(ValueError):
            nano.read_list(self.lst("big.txt", "git\n" * (nano.MAX_BYTES // 4 + 1)))
        code, _ = self.call(["--list", str(self.root / "missing.txt")], Fake({}))
        self.assertEqual(code, 2)

    def test_report_mode_runs_only_dpkg_query(self):
        fake = Fake({"git": "1:2.0"})
        code, result = self.call(["--list", self.lst("a.txt", "git\ntzdata\n")], fake)
        self.assertEqual((code, result["status"], result["to_install"]), (1, "missing", ["tzdata"]))
        self.assertEqual(result["packages"]["git"]["state"], "ok")
        self.assertEqual([argv[0] for argv, _ in fake.calls], ["/fake/dpkg-query"])
        self.assertEqual(result["executed"], [])

    def test_satisfied_install_does_nothing(self):
        fake = Fake({"git": "1:2.0"})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], fake)
        self.assertEqual((code, result["status"], result["executed"]), (0, "satisfied", []))
        self.assertEqual(len(fake.calls), 1)

    def test_install_updates_then_installs_only_missing_and_verifies(self):
        fake = Fake({"git": "1:2.0", "libblas3": "3.11"})
        lists = self.lst("a.txt", "git\ntzdata\nlibblas3=3.12.1-6\n")
        code, result = self.call(["--list", lists, "--install"], fake)
        self.assertEqual((code, result["status"]), (0, "installed"))
        self.assertEqual(result["executed"], [
            ["/fake/apt-get", "update"],
            ["/fake/apt-get", "install", "-y", "--no-install-recommends",
             "libblas3=3.12.1-6", "tzdata"]])
        apt_calls = [(argv, kw) for argv, kw in fake.calls if argv[0] == "/fake/apt-get"]
        self.assertEqual(len(apt_calls), 2)
        for _, kwargs in apt_calls:
            self.assertEqual(kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")
            self.assertEqual(set(kwargs["env"]), {"PATH", "DEBIAN_FRONTEND", "LANG"})

    def test_failed_apt_stops_and_reports(self):
        fake = Fake({}, apt_rc=100)
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], fake)
        self.assertEqual((code, result["status"]), (3, "failed"))
        self.assertEqual(result["executed"], [["/fake/apt-get", "update"]])

    def test_install_that_does_not_land_is_failed(self):
        class Silent(Fake):
            def __call__(self, argv, **kwargs):
                if "install" in argv:
                    self.calls.append((list(argv), kwargs))
                    return subprocess.CompletedProcess(argv, 0)
                return super().__call__(argv, **kwargs)
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], Silent({}))
        self.assertEqual((code, result["status"]), (3, "failed"))

    def test_non_root_uses_sudo_or_stops(self):
        fake = Fake({})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], fake, euid=1000)
        self.assertEqual(code, 0)
        self.assertEqual(result["executed"][0], ["/fake/sudo", "-n", "/fake/apt-get", "update"])
        bare = {k: v for k, v in FAKE_BIN.items() if k != "sudo"}
        fake = Fake({})
        code, _ = self.call(["--list", self.lst("b.txt", "git\n"), "--install"], fake, euid=1000, which=bare)
        self.assertEqual(code, 2)
        self.assertEqual([argv[0] for argv, _ in fake.calls], ["/fake/dpkg-query"])

    def test_without_apt_nothing_runs(self):
        fake = Fake({})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], fake, which={})
        self.assertEqual((code, result["status"]), (0, "apt_unavailable"))
        self.assertEqual(fake.calls, [])

    @unittest.skipUnless(shutil.which("dpkg-query") and shutil.which("apt-get"), "needs Debian/Ubuntu")
    def test_real_dpkg_query_is_read_only(self):
        have = self.lst("have.txt", "dpkg\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(nano.main(["--list", have, "--install"]), 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "satisfied")
        lack = self.lst("lack.txt", "dpkg\nnano-apt-no-such-package\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(nano.main(["--list", lack]), 1)
        self.assertEqual(json.loads(out.getvalue())["to_install"], ["nano-apt-no-such-package"])


if __name__ == "__main__":
    unittest.main()

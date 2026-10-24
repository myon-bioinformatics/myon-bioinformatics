import json
import os
import subprocess
import shutil
import time
import sys
import unittest
from unittest.mock import patch
from tools import constrained_lab_exec_audit as audit


class AuditTests(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(audit.classify("/usr/bin/git"), "external")
        self.assertEqual(audit.classify("/usr/bin/python3.12"), "python-node")
        self.assertEqual(audit.classify("/usr/bin/node"), "python-node")

    def test_trace_pid_and_failures(self):
        raw = '[pid 100] execve("/usr/bin/git", ["git", "status"], 0x0) = 0\n' \
              '[pid 101] execve("/bin/sh", ["sh"], 0x0) = -1 ENOENT\n'
        events = audit.parse_trace(raw)
        self.assertEqual([e["pid"] for e in events], [100, 101])
        self.assertEqual([e["category"] for e in events], ["external", "external"])
        self.assertEqual([e["exec_result"] for e in events], [0, -1])
        self.assertIsNone(events[0]["argv"])

    def test_unknown_trace_is_not_clean(self):
        self.assertEqual(audit.parse_trace('execveat(4, "", [], 0, 0) = 0')[0]["category"], "unknown")

    def test_unavailable_is_not_success(self):
        with patch.object(audit.shutil, "which", return_value=None):
            result = audit.audit([sys.executable, "-S", "-c", "print(1)"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["external_count"])
        self.assertEqual(result["coverage"], "unknown")

    def test_bad_inputs(self):
        for argv, limit in (([], 10), (["echo"], 0), (["echo"], True)):
            with self.assertRaises(ValueError):
                audit.audit(argv, max_trace_bytes=limit)

    def test_execveat_named_path(self):
        event = audit.parse_trace('100 execveat(AT_FDCWD, "/usr/bin/git", ["git"], 0, 0) = 0')[0]
        self.assertEqual(event["executable"], "/usr/bin/git")
        self.assertEqual(event["category"], "external")

    def test_unknown_result_and_empty_path(self):
        for line in ('100 execve("/bin/sh", ["sh"], 0 <unfinished ...>',
                     '100 execveat(3, "", [], 0, AT_EMPTY_PATH) = 0'):
            event = audit.parse_trace(line)[0]
            self.assertTrue(event["exec_result"] is None or event["category"] == "unknown")

    def test_result_like_argv_in_truncated_line_stays_unknown(self):
        event = audit.parse_trace('1 execve("/usr/bin/python3", ["python3", "a) = 7 ')[0]
        self.assertIsNone(event["exec_result"])
        event = audit.parse_trace('1 execve("/usr/bin/python3", ["a) = 7 "], 0) = 0')[0]
        self.assertEqual(event["exec_result"], 0)

    def test_escaped_or_truncated_path_is_not_classified(self):
        for raw in ('1 execve("/usr/bin/python"..., [], 0) = 0',
                    r'1 execve("/usr/bin/pyth\x6fn3", [], 0) = 0'):
            self.assertEqual(audit.parse_trace(raw)[0]["category"], "unknown")

    def test_nonfinite_and_wrong_type_timeouts(self):
        for timeout in (float("nan"), float("inf"), -1, 0, True, "1"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                audit.audit(["python"], timeout=timeout)
        for argv in ("python", [1], [""]):
            with self.assertRaises(ValueError):
                audit.audit(argv)

    def test_empty_non_executable_argument_is_valid(self):
        command = [sys.executable, "-S", "-c", ""]
        with patch.object(audit.sys, "platform", "linux"), \
             patch.object(audit.shutil, "which", return_value="strace"), \
             patch.object(audit, "_capture_trace", return_value=(b"", "observed", 0, False)) as capture:
            audit.audit(command)
        self.assertEqual(capture.call_args[0][1], command)

    def _report(self, raw, status="observed", code=0, truncated=False):
        with patch.object(audit.sys, "platform", "linux"), \
             patch.object(audit.shutil, "which", return_value="strace"), \
             patch.object(audit, "_capture_trace", return_value=(raw, status, code, truncated)):
            return audit.audit(["python"])

    def test_empty_or_restricted_trace_never_claims_clean(self):
        for code in (0, 1):
            report = self._report(b"", code=code)
            self.assertEqual(report["status"], "tracing_error")
            self.assertEqual(report["coverage"], "unknown")
            self.assertIsNone(report["external_count"])

    def test_retained_events_are_not_total_coverage(self):
        report = self._report(b'1 execve("/usr/bin/python3", ["private-argv"], 0) = 0\n'
                              b'2 execve("/bin/git", ["git"], 0) = -1 ENOENT\n')
        self.assertEqual(report["coverage"], "partial-or-unknown")
        self.assertIsNone(report["external_count"])
        self.assertEqual(report["observed_external_count"], 1)
        self.assertNotIn("private-argv", json.dumps(report))

    def test_event_limit_is_explicit(self):
        raw = b'1 execve("/bin/git", [], 0) = 0\n'
        for count in (1000, 1001):
            report = self._report(raw * count)
            self.assertEqual(len(report["events"]), 1000)
            self.assertEqual(report["events_truncated"], count > 1000)
            self.assertIsNone(report["external_count"])

    def _capture_helper(self, body, timeout=2, limit=1024):
        real_popen = subprocess.Popen
        def start(command, **kwargs):
            output = command[command.index("-o") + 1]
            script = ("import os, sys, time, subprocess\n"
                      "fd = os.open(sys.argv[1], os.O_WRONLY)\n"
                      "os.close(int(sys.argv[1].rsplit('/', 1)[1]))\n" + body)
            return real_popen([sys.executable, "-S", "-c", script, output], **kwargs)
        with patch.object(audit.subprocess, "Popen", side_effect=start):
            return audit._capture_trace("strace", ["ignored"], timeout, limit)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux trace pipe")
    def test_capture_enforces_trace_limit_without_disk_spool(self):
        data, status, code, truncated = self._capture_helper("os.write(fd, b'x' * 65536)\ntime.sleep(2)", limit=256)
        self.assertEqual(len(data), 256)
        self.assertEqual(status, "trace_limit")
        self.assertTrue(truncated)
        self.assertNotEqual(code, 0)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux trace pipe")
    def test_capture_timeout_after_trace_pipe_closes(self):
        started = time.monotonic()
        result = self._capture_helper("os.close(fd)\ntime.sleep(2)", timeout=0.1)
        self.assertEqual(result[1], "timeout")
        self.assertLess(time.monotonic() - started, 1.5)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux trace pipe")
    def test_capture_timeout_with_escaped_pipe_holder(self):
        started = time.monotonic()
        result = self._capture_helper(
            "subprocess.Popen([sys.executable, '-S', '-c', 'import time; time.sleep(0.8)'], "
            "pass_fds=(fd,), start_new_session=True)\nos.close(fd)", timeout=0.15)
        self.assertEqual(result[1], "timeout")
        self.assertLess(time.monotonic() - started, 0.7)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux trace pipe")
    def test_workload_stderr_is_not_a_trace_event(self):
        data, status, code, truncated = self._capture_helper(
            "os.write(2, b'1 execve(\"/bin/false\", [], 0) = 0\\n')\nos.close(fd)")
        self.assertEqual((data, status, code, truncated), (b"", "observed", 0, False))


    @unittest.skipUnless(sys.platform.startswith("linux") and
                         os.environ.get("GITHUB_ACTIONS") == "true" and shutil.which("strace"),
                         "live strace check only on Linux CI with strace installed")
    def test_live_strace_observes_external_exec_on_ci(self):
        report = audit.audit([sys.executable, "-S", "-c",
                              "import subprocess; subprocess.run(['/bin/true'], check=True)"], timeout=10)
        self.assertEqual((report["status"], report["exit"]), ("observed", 0), report)
        self.assertTrue(any(event["executable"] == "/bin/true" and event["exec_result"] == 0
                            for event in report["events"]), report)
        self.assertIsNone(report["external_count"])

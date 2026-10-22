import json
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
        raw = '[pid 100] execve("/usr/bin/git", ["git", "status"], 0x0) = 0\\n' \
              '[pid 101] execve("/bin/sh", ["sh"], 0x0) = -1 ENOENT\\n'
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

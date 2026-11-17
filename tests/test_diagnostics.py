"""Command failures preserve raw evidence and show actionable console errors."""
from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import onn2026 as o


class DiagnosticsTests(unittest.TestCase):
    def test_command_failure_prints_exit_detail_and_keeps_full_output(self):
        with tempfile.TemporaryDirectory() as work:
            session = o.Session(work, "fixture")
            raw = "x" * 2000 + "\nPermission denied\x1b[2J"
            with patch.object(o.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="partial output", stderr=raw)):
                with self.assertRaises(o.Stop) as error:
                    session.run("push-dsu", ["fake-adb", "push", "fixture.gz"], timeout=180)
            report_path = session.path / "001-push-dsu.json"
            report = json.loads(report_path.read_text())
            self.assertEqual(report["stderr"], raw)
            self.assertEqual(report["stdout"], "partial output")
            self.assertEqual(report["timeout_seconds"], 180)
            self.assertGreaterEqual(report["duration_seconds"], 0)
            self.assertEqual(report["returncode"], 1)
            for text in ("push-dsu", "exited with code 1", "Permission denied", str(report_path), "No automatic retry"):
                self.assertIn(text, str(error.exception))
            self.assertNotIn("\x1b", str(error.exception))
            self.assertLess(len(str(error.exception)), len(raw))

    def test_timeout_retains_partial_bytes_and_command_is_only_attempted_once(self):
        with tempfile.TemporaryDirectory() as work:
            session = o.Session(work, "fixture")
            timeout = subprocess.TimeoutExpired(["fixture"], 20, output=b"copied part", stderr=b"connection lost")
            with patch.object(o.subprocess, "run", side_effect=timeout) as run:
                with self.assertRaisesRegex(o.Stop, "timed out after 20 seconds"):
                    session.run("upload", ["fixture"], timeout=20)
            run.assert_called_once()
            report = json.loads((session.path / "001-upload.json").read_text())
            self.assertTrue(report["timed_out"])
            self.assertEqual(report["stdout"], "copied part")
            self.assertEqual(report["stderr"], "connection lost")

    def test_os_error_and_unchecked_failure_are_saved(self):
        with tempfile.TemporaryDirectory() as work:
            session = o.Session(work, "fixture")
            with patch.object(o.subprocess, "run", side_effect=FileNotFoundError("missing command")):
                with self.assertRaisesRegex(o.Stop, "could not start"):
                    session.run("first", ["fixture"])
                result = session.run("optional", ["fixture"], check=False)
            self.assertIsNone(result["returncode"])
            self.assertEqual(len(list(session.path.glob("*.json"))), 2)

    def test_manual_command_unexpected_error_saves_traceback(self):
        with tempfile.TemporaryDirectory() as work:
            output = StringIO()
            with patch.object(o, "verify_files", side_effect=RuntimeError("fixture unexpected error")), redirect_stderr(output):
                self.assertEqual(o.main(["--work", work, "verify-files"]), 2)
            records = list(Path(work).glob("*/stopped.json"))
            self.assertEqual(len(records), 1)
            report = json.loads(records[0].read_text())
            self.assertEqual(report["command"], "verify-files")
            self.assertEqual(report["error_type"], "RuntimeError")
            self.assertIn("fixture unexpected error", report["traceback"])
            self.assertFalse(report["automatic_retry"])
            self.assertTrue(report["package_manifest_sha256"])
            self.assertIn(str(records[0].parent), output.getvalue())


if __name__ == "__main__":
    unittest.main()

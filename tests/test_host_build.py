"""Local build progress, failures and cancellation; no ADB or device access."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import host_build
import onn2026 as o
from tools import build_dsu as builder

ROOT = Path(__file__).resolve().parents[1]


def process_active(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


class HostBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.session = o.Session(self.folder, "host-build-fixture")

    def test_delayed_build_streams_progress_and_keeps_machine_output_in_log(self):
        marker = self.folder / "finished"
        code = ("import json,time\nfrom pathlib import Path\n"
                "print('[build] First stage',flush=True)\n"
                "time.sleep(0.4)\n"
                f"Path({str(marker)!r}).write_text('done')\n"
                "print('[build] Second stage',flush=True)\n"
                "print(json.dumps({'private_build_receipt': 'retained'}))\n")
        messages = []
        def emit(message):
            if message == "First stage":
                self.assertFalse(marker.exists(), "Progress arrived only after the build finished")
            messages.append(message)
        result = host_build.run(self.session, "build-offline-dsu", [sys.executable, "-u", "-c", code], emit)
        self.assertEqual(messages, ["First stage", "Second stage"])
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["timeout_seconds"], 900)
        self.assertFalse(result["timed_out"])
        self.assertIn("private_build_receipt", Path(result["output_log"]).read_text())
        self.assertEqual(json.loads((self.session.path / "001-build-offline-dsu.json").read_text()), result)

    def test_nonzero_exit_is_reported_as_failure_and_is_not_retried(self):
        marker = self.folder / "attempts"
        code = ("from pathlib import Path\nimport sys\n"
                f"p=Path({str(marker)!r})\n"
                "p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
                "print('[build] FAILED: fixture tool failed',flush=True)\nsys.exit(7)")
        messages = []
        with self.assertRaisesRegex(o.Stop, r"failed \(exit 7\)") as stopped:
            host_build.run(self.session, "build-offline-dsu", [sys.executable, "-c", code], messages.append)
        self.assertNotIn("timed out", str(stopped.exception))
        self.assertEqual(marker.read_text(), "x")
        self.assertEqual(messages, ["FAILED: fixture tool failed"])
        receipt = json.loads((self.session.path / "001-build-offline-dsu.json").read_text())
        self.assertFalse(receipt["timed_out"])
        self.assertFalse(receipt["automatic_retry"])
        self.assertEqual(receipt["returncode"], 7)

    def test_missing_build_tool_saves_clear_failure_receipt(self):
        with self.assertRaisesRegex(o.Stop, "could not run"):
            host_build.run(self.session, "build-offline-dsu", [self.folder / "missing-tool"])
        receipt = json.loads((self.session.path / "001-build-offline-dsu.json").read_text())
        self.assertIn("error", receipt)
        self.assertIsNone(receipt["returncode"])
        self.assertFalse(receipt["timed_out"])

    def test_timeout_stops_build_and_term_ignoring_child_despite_live_progress(self):
        marker = self.folder / "child-pid"
        attempts = self.folder / "attempts"
        child_code = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
        code = ("import signal,subprocess,sys,time\nfrom pathlib import Path\n"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                f"p=Path({str(attempts)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
                f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}])\n"
                f"Path({str(marker)!r}).write_text(str(child.pid))\n"
                "while True:\n print('[build] Still running',flush=True)\n time.sleep(0.05)\n")
        messages = []
        started = time.monotonic()
        try:
            with patch.object(host_build, "BUILD_TIMEOUT_SECONDS", 0.6), \
                 patch.object(host_build, "CANCEL_GRACE_SECONDS", 0.1):
                with self.assertRaisesRegex(o.Stop, "timed out after 0.6 seconds"):
                    host_build.run(self.session, "build-offline-dsu", [sys.executable, "-c", code], messages.append)
            self.assertLess(time.monotonic() - started, 5)
            receipt = json.loads((self.session.path / "001-build-offline-dsu.json").read_text())
            self.assertEqual(receipt["timeout_seconds"], 0.6)
            self.assertTrue(receipt["timed_out"])
            self.assertFalse(receipt["interrupted"])
            self.assertFalse(receipt["automatic_retry"])
            self.assertEqual(attempts.read_text(), "x")
            self.assertGreater(len(messages), 1)
            self.assertIn("Still running", receipt["stdout"])
            for pid in (receipt["pid"], int(marker.read_text())):
                deadline = time.monotonic() + 2
                while process_active(pid) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(process_active(pid), "Timed-out build left a running process")
        finally:
            if marker.exists() and process_active(int(marker.read_text())):
                os.kill(int(marker.read_text()), signal.SIGKILL)

    def test_timeout_remains_failure_even_if_builder_exits_zero_on_term(self):
        code = ("import signal,sys,time\n"
                "signal.signal(signal.SIGTERM,lambda *_: sys.exit(0))\n"
                "print('[build] Waiting',flush=True)\ntime.sleep(30)\n")
        with patch.object(host_build, "BUILD_TIMEOUT_SECONDS", 0.5):
            with self.assertRaisesRegex(o.Stop, "timed out"):
                host_build.run(self.session, "build-offline-dsu", [sys.executable, "-c", code], lambda _: None)
        receipt = json.loads((self.session.path / "001-build-offline-dsu.json").read_text())
        self.assertTrue(receipt["timed_out"])
        self.assertEqual(receipt["returncode"], 0)

    def test_large_output_is_preserved_on_disk_with_bounded_receipt_tail(self):
        result = host_build.run(self.session, "build-offline-dsu",
                                [sys.executable, "-c", "print('x'*100000); print('[build] Done')"], lambda _: None)
        self.assertTrue(result["output_truncated"])
        self.assertLessEqual(len(result["stdout"].encode()), host_build.OUTPUT_TAIL_BYTES)
        self.assertGreater(Path(result["output_log"]).stat().st_size, 100000)

    def test_failed_builder_does_not_leave_a_descendant_holding_its_log(self):
        marker = self.folder / "child-pid"
        code = ("import subprocess,sys\nfrom pathlib import Path\n"
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
                f"Path({str(marker)!r}).write_text(str(child.pid))\n"
                "sys.exit(5)\n")
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(o.Stop, r"failed \(exit 5\)"):
                host_build.run(self.session, "build-offline-dsu", [sys.executable, "-c", code], lambda _: None)
            self.assertLess(time.monotonic() - started, 5)
            child_pid = int(marker.read_text())
            deadline = time.monotonic() + 2
            while process_active(child_pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(process_active(child_pid))
        finally:
            if marker.exists() and process_active(int(marker.read_text())):
                os.kill(int(marker.read_text()), signal.SIGKILL)

    def test_ctrl_c_stops_builder_and_term_ignoring_child_and_preserves_receipts(self):
        pids = self.folder / "pids.json"
        tool = self.folder / "slow-build.py"
        child_code = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
        tool.write_text("import json,os,signal,subprocess,sys,time\nfrom pathlib import Path\n"
                        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}])\n"
                        "time.sleep(0.1)\n"
                        f"Path({str(pids)!r}).write_text(json.dumps([os.getpid(),child.pid]))\n"
                        "print('[build] Slow fixture stage',flush=True)\ntime.sleep(30)\n")
        worker = r'''
from pathlib import Path
import sys
import host_build
import onn2026 as o
root, tool = map(Path, sys.argv[1:])
session = o.Session(root, 'cancel-worker')
host_build.CANCEL_GRACE_SECONDS = 0.2
try:
    host_build.run(session, 'build-offline-dsu', [sys.executable, '-u', str(tool)])
except KeyboardInterrupt:
    (root / 'cancelled').write_text('clean cancellation')
else:
    raise AssertionError('Fixture was not interrupted')
'''
        process = subprocess.Popen([sys.executable, "-u", "-c", worker, str(self.folder), str(tool)],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not pids.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(pids.exists(), "The isolated fixture did not start")
            os.kill(process.pid, signal.SIGINT)
            output, errors = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 0, output + errors)
            self.assertTrue((self.folder / "cancelled").exists())
            for pid in json.loads(pids.read_text()):
                deadline = time.monotonic() + 2
                while process_active(pid) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(process_active(pid), "Cancelled build left a running process")
            records = list(self.folder.glob("*-cancel-worker-*/001-build-offline-dsu.json"))
            self.assertEqual(len(records), 1)
            receipt = json.loads(records[0].read_text())
            self.assertTrue(receipt["interrupted"])
            self.assertFalse(receipt["timed_out"])
            self.assertFalse(receipt["automatic_retry"])
            self.assertIn("Slow fixture stage", Path(receipt["output_log"]).read_text())
        finally:
            if pids.exists():
                builder_pid, child_pid = json.loads(pids.read_text())
                if process_active(builder_pid) or process_active(child_pid):
                    try:
                        os.killpg(builder_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_dsu_tools_have_no_per_command_wall_clock_limit_and_keep_error_log(self):
        log = self.folder / "tool.log"
        with patch.object(builder.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run, \
             redirect_stdout(StringIO()) as output:
            builder.command(["fixture-tool", "--build"], log, "Building fixture")
        self.assertNotIn("timeout", run.call_args.kwargs)
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertIn("[build] Building fixture", output.getvalue())
        failed_log = self.folder / "failed-tool.log"
        def fail(argv, **kwargs):
            kwargs["stdout"].write("actual tool diagnostic\n")
            raise subprocess.CalledProcessError(4, argv)
        with patch.object(builder.subprocess, "run", side_effect=fail), redirect_stdout(StringIO()):
            with self.assertRaisesRegex(o.Stop, "Tool log:"):
                builder.command(["fixture-tool"], failed_log, "Building fixture")
        self.assertEqual(failed_log.read_text(), "actual tool diagnostic\n")

    def test_device_command_timeouts_are_unchanged(self):
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        device = o.Device(self.session, "192.0.2.80:5555", sys.executable)
        with patch.object(o.subprocess, "run", return_value=result) as run:
            device.call("bounded-device-fixture", "fixture-only")
            self.assertEqual(run.call_args.kwargs["timeout"], 15)
            device.call("bounded-reboot-fixture", "fixture-only", timeout=8)
            self.assertEqual(run.call_args.kwargs["timeout"], 8)


if __name__ == "__main__":
    unittest.main()

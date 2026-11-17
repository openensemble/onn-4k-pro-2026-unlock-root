"""DSU error, stale-progress and completion evidence; no device access."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import dsu_diagnostics as diagnostics
import onn2026 as o
import root_workflow as flow
from test_workflow import Device, prepared_stage
from test_dsu_recovery import Android


REMOTE = "/storage/emulated/0/Download/fixture.raw.gz"
MARKER = "1900.000 10 10 D InstallationAsyncTask: Start doInBackground(), URL: file://" + REMOTE + "\n"


def event(status, cause, epoch=1990, detail=""):
    return f"{epoch:.3f} 10 10 D DynamicSystemInstallationService: status: {status}, cause: {cause}{detail}\n"


class StatusTests(unittest.TestCase):
    def check(self, log, state="installed\ndisabled", same_boot=True, started=None):
        return diagnostics.installation(log, REMOTE, state, same_boot, 2000, started)

    def test_completion_requires_matching_image_and_disabled_installation(self):
        complete = event("READY", "INSTALL_COMPLETED")
        self.assertEqual(self.check(MARKER + complete)["installation_status"], "ready")
        for log, state in ((complete, "installed\ndisabled"), (complete + MARKER, "installed\ndisabled"),
                           (MARKER, "installed\ndisabled"), (MARKER + complete, "normal")):
            with self.subTest(log=log, state=state):
                self.assertEqual(self.check(log, state)["installation_status"], "unverified")

    def test_errors_and_cancellation_override_earlier_completion(self):
        for cause in ("ERROR_IO", "ERROR_INVALID_URL", "ERROR_EXCEPTION", "INSTALL_CANCELLED"):
            with self.subTest(cause=cause):
                result = self.check(MARKER + event("READY", "INSTALL_COMPLETED") + event("NOT_STARTED", cause))
                self.assertEqual(result["installation_status"], "cancelled" if cause == "INSTALL_CANCELLED" else "failed")
                self.assertEqual(result["last_event"]["cause"], cause)

    def test_errors_before_this_image_do_not_override_current_success(self):
        result = self.check(event("NOT_STARTED", "ERROR_IO") + MARKER + event("READY", "INSTALL_COMPLETED"))
        self.assertEqual(result["installation_status"], "ready")

    def test_recent_progress_shows_partition_bytes_and_age(self):
        detail = ", partition name: system, progress: 100/200, total_progress: 50%"
        result = self.check(MARKER + event("IN_PROGRESS", "CAUSE_NOT_SPECIFIED", detail=detail), "normal")
        self.assertEqual(result["installation_status"], "installing")
        self.assertEqual(result["last_event_age_seconds"], 10)
        self.assertIn("progress: 100/200", result["last_event"]["detail"])

    def test_old_progress_is_stalled_even_on_the_first_check(self):
        for clock, stamp in ((2000, 1400), (2000, 100), (172900, 100)):
            log = MARKER + event("IN_PROGRESS", "CAUSE_NOT_SPECIFIED", epoch=stamp)
            result = diagnostics.installation(log, REMOTE, "normal", True, clock)
            self.assertEqual(result["installation_status"], "stalled")

    def test_missing_or_future_progress_time_cannot_claim_current_installation(self):
        for line in ("status: IN_PROGRESS, cause: CAUSE_NOT_SPECIFIED", event("IN_PROGRESS", "CAUSE_NOT_SPECIFIED", 3000)):
            self.assertEqual(self.check(MARKER + line)["installation_status"], "unverified")

    def test_new_attempt_rejects_older_or_undated_completion(self):
        for line in (event("READY", "INSTALL_COMPLETED", 1800), "status: READY, cause: INSTALL_COMPLETED"):
            self.assertEqual(self.check(MARKER + line, started=1900)["installation_status"], "unverified")
        self.assertEqual(self.check(MARKER + event("READY", "INSTALL_COMPLETED"), started=1900)["installation_status"], "ready")

    def test_another_image_after_our_marker_cannot_authorize_boot(self):
        other = MARKER.replace("fixture.raw.gz", "other.raw.gz")
        result = self.check(MARKER + other + event("READY", "INSTALL_COMPLETED"))
        self.assertEqual(result["installation_status"], "unverified")
        self.assertFalse(result["log_matches_archive"])

    def test_running_enabled_changed_boot_and_unknown_states_are_unverified(self):
        log = MARKER + event("READY", "INSTALL_COMPLETED")
        for state in ("installed\nenabled", "running\ninstalled\ndisabled", "unexpected"):
            self.assertEqual(self.check(log, state)["installation_status"], "unverified")
        self.assertEqual(self.check(log, same_boot=False)["installation_status"], "unverified")

    def test_failure_detail_is_readable_without_terminal_controls(self):
        log = MARKER + event("NOT_STARTED", "ERROR_IO", detail=", detail: \x1b[2JNo space left on device")
        result = self.check(log)
        self.assertIn("No space left", result["last_event"]["detail"])
        self.assertNotIn("\x1b", result["last_event"]["detail"])


class WorkflowDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.device = Device(self.temp.name)
        self.stage = prepared_stage(self.temp.name)
        (self.stage.parent / "installed.json").unlink()
        self.args = SimpleNamespace(stage=self.stage)
        guard = patch.object(o.subprocess, "run", side_effect=AssertionError("Live I/O forbidden"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_failed_installation_saves_diagnosis_and_supporting_reads_without_boot_proof(self):
        self.device.state = "normal"
        self.device.log = MARKER + event("NOT_STARTED", "ERROR_IO", detail=", detail: No space left on device")
        result = flow.dsu_status(self.device, self.args)
        self.assertEqual(result["installation_status"], "failed")
        self.assertFalse(result["installed_disabled_verified"])
        self.assertFalse((self.stage.parent / "installed.json").exists())
        self.assertEqual(json.loads((self.device.s.path / "dsu-status.json").read_text()), result)
        self.assertEqual(set(result["diagnostic_checks"]), {"installation-storage", "installation-service"})
        self.assertFalse(any(label in ("enable-single-use-dsu", "single-dsu-reboot") for label, _ in self.device.calls))

    def test_supporting_read_failure_does_not_hide_installation_error(self):
        self.device.log = MARKER + event("NOT_STARTED", "ERROR_IO")
        with patch.object(self.device, "shell", return_value={"returncode": None, "timed_out": True}):
            result = flow.dsu_status(self.device, self.args)
        self.assertEqual(result["installation_status"], "failed")
        self.assertTrue(result["diagnostic_checks"]["installation-storage"]["timed_out"])

    def test_restart_during_collection_cannot_save_completion(self):
        self.device.log = MARKER + event("READY", "INSTALL_COMPLETED")
        read = self.device.text
        def reboot(label, *args, **kwargs):
            if label == "installation-log":
                self.device.boot = "another-boot"
            return read(label, *args, **kwargs)
        with patch.object(self.device, "text", side_effect=reboot):
            result = flow.dsu_status(self.device, self.args)
        self.assertFalse(result["installed_disabled_verified"])
        self.assertIn("restarted", result["message"])
        self.assertFalse((self.stage.parent / "installed.json").exists())

    def test_activity_launch_error_is_reported_even_when_adb_exits_zero(self):
        device = Android(self.temp.name)
        device.state, device.files = "normal", {}
        archive = Path(self.temp.name) / "test.gz"
        archive.write_bytes(b"offline fixture")
        build = Path(self.temp.name) / "build.json"
        o.save(build, {"stock_gzip": str(archive), "stock_gzip_sha256": o.digest(archive), "system_bytes": 123})
        shell = device.shell
        def failed_activity(label, *args, **kwargs):
            if label == "stage-disabled-dsu":
                return {"returncode": 0, "stdout": "Error type 3\nError: Activity class does not exist.", "stderr": ""}
            return shell(label, *args, **kwargs)
        with patch.object(flow, "build_matches"), patch.object(device, "shell", side_effect=failed_activity):
            with self.assertRaisesRegex(o.Stop, "could not start the temporary-system installer") as error:
                flow.stage_dsu(device, SimpleNamespace(build=build, kind="stock", rehearsal=None))
        self.assertIn("Activity class does not exist", str(error.exception))
        self.assertTrue(flow.journal(device, "dsu-stock-stage-attempt.json").exists())
        self.assertTrue((device.s.path / "stage.json").is_file())
        self.assertFalse((device.s.path / "installed.json").exists())


if __name__ == "__main__":
    unittest.main()

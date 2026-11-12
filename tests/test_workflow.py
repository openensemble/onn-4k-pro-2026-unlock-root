import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import onn2026 as o
import root_workflow as flow
from guide_core import RootAccessUnavailable

BOOT = "11111111-2222-3333-4444-555555555555"
BINDING = {"ro.serialno": "JUSA123456", "ro.product.device": o.PROFILE["device"],
           "ro.build.fingerprint": o.PROFILE["fingerprint"], "ro.bootloader": o.PROFILE["bootloader"],
           "ro.boot.slot_suffix": o.PROFILE["slot"]}


class Device:
    def __init__(self, directory):
        self.s = o.Session(directory, "fixture")
        self.binding = dict(BINDING)
        self.state = "installed\ndisabled"
        self.boot = BOOT
        self.log = ""
        self.calls = []
        self.fail_enable = False

    def identify(self, **kwargs):
        return dict(self.binding)

    def boot_id(self):
        return self.boot

    def text(self, label, *args, **kwargs):
        self.calls.append((label, args))
        if args == ("gsi_tool", "status"):
            return self.state
        if args[0] == "logcat":
            return self.log
        if args[0] == "settings":
            return "1122334455667788"
        raise AssertionError((label, args))

    def shell(self, label, *args, **kwargs):
        self.calls.append((label, args))
        if label == "enable-single-use-dsu":
            assert list(self.s.work.glob("devices/*/dsu-stock-enable-attempt.json"))
            if self.fail_enable:
                raise o.Stop("simulated uncertain enable")
            self.state = "installed\nenabled"
        return {"returncode": 0, "timed_out": False, "stdout": ""}

    def call(self, label, *args, **kwargs):
        self.calls.append((label, args))
        return {"returncode": None, "timed_out": True, "stdout": ""}


def prepared_stage(directory):
    stage = Path(directory) / "stage.json"
    o.save(stage, {"binding": BINDING, "boot_id": BOOT, "android_id": "1122334455667788",
                   "kind": "stock", "remote": "/storage/emulated/0/Download/fixture.raw.gz",
                   "build_sha256": "build-digest"})
    o.save(stage.parent / "installed.json", {"stage_sha256": o.digest(stage), "installed_disabled_verified": True})
    return stage


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(o.subprocess, "run", side_effect=AssertionError("Live I/O forbidden in workflow tests"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_receipt_binding_rejects_other_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Device(tmp)
            other = dict(BINDING, **{"ro.serialno": "JUSA999999"})
            with self.assertRaises(o.Stop):
                flow.bound(device, {"binding": other})
            self.assertFalse(device.calls)

    def test_enable_failure_never_reboots_and_consumes_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Device(tmp)
            device.fail_enable = True
            args = SimpleNamespace(stage=prepared_stage(tmp))
            with self.assertRaises(o.Stop):
                flow.boot_dsu(device, args)
            self.assertFalse(any(label == "single-dsu-reboot" for label, _ in device.calls))
            device.fail_enable = False
            with self.assertRaises(FileExistsError):
                flow.boot_dsu(device, args)
            self.assertEqual(sum(label == "enable-single-use-dsu" for label, _ in device.calls), 1)

    def test_reboot_timeout_does_not_repeat_reboot(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Device(tmp)
            args = SimpleNamespace(stage=prepared_stage(tmp))
            result = flow.boot_dsu(device, args)
            self.assertTrue(result["transport"]["timed_out"])
            self.assertEqual(sum(label == "single-dsu-reboot" for label, _ in device.calls), 1)
            self.assertTrue((args.stage.parent / "boot-request.json").exists())
            with self.assertRaises(o.Stop):
                flow.boot_dsu(device, args)
            self.assertEqual(sum(label == "single-dsu-reboot" for label, _ in device.calls), 1)

    def test_changed_boot_prevents_enable(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Device(tmp)
            device.boot = "another-boot"
            with self.assertRaises(o.Stop):
                flow.boot_dsu(device, SimpleNamespace(stage=prepared_stage(tmp)))
            self.assertFalse(any(label == "enable-single-use-dsu" for label, _ in device.calls))

    def test_old_or_negative_installation_log_does_not_authorize_boot(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Device(tmp)
            stage = prepared_stage(tmp)
            for log in ["status: READY, cause: INSTALL_COMPLETED\nStarting fixture.raw.gz",
                        "Starting fixture.raw.gz\nstatus: NOT_READY, cause: INSTALL_COMPLETED",
                        "Another installer: status: READY, cause: INSTALL_COMPLETED"]:
                device.log = log
                with self.subTest(log=log):
                    result = flow.dsu_status(device, SimpleNamespace(stage=stage))
                    self.assertFalse(result["installed_disabled_verified"])
            device.log = "Starting fixture.raw.gz\nstatus: READY, cause: INSTALL_COMPLETED"
            result = flow.dsu_status(device, SimpleNamespace(stage=stage))
            self.assertTrue(result["installed_disabled_verified"])

    def test_install_requires_explicit_recovery_risk_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Device(tmp)
            with self.assertRaises(o.Stop):
                flow.install_root(device, SimpleNamespace(accept_unproven_recovery=False))
            self.assertFalse(device.calls)

    def test_guest_root_cannot_be_reported_as_permanent_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Device(tmp)
            device.state = "running\ninstalled\ndisabled"
            receipt = {"boot_id": "prior-boot", "android_id": "1122334455667788"}
            with patch.object(flow, "patch_receipt", return_value=(receipt, None)):
                with self.assertRaises(o.Stop):
                    flow.verify_root(device, SimpleNamespace(patch_result=Path("unused")))
            self.assertFalse(any(label == "verify-permanent-root" for label, _ in device.calls))

    def verification_fixture(self, directory):
        device = Device(directory)
        receipt = {"boot_id": "prior-boot", "android_id": "1122334455667788",
                   "patched_image": {"sha256": "a" * 64}}
        response = {"returncode": 0, "timed_out": False, "stderr": "",
                    "stdout": "uid=0(root) gid=0(root) context=u:r:magisk:s0\n30.7:MAGISK:R\n30700\n"
                              "Enforcing\n" + "a" * 64 + "  /dev/block/by-name/init_boot_b\nMAGISK_ENVIRONMENT_COMPLETE\n"}
        return device, receipt, response

    def test_root_permission_denial_and_timeout_explain_how_to_retry(self):
        responses = [
            {"returncode": 13, "timed_out": False, "stdout": "", "stderr": "Permission denied\n"},
            {"returncode": None, "timed_out": True, "stdout": "", "stderr": ""},
        ]
        for response in responses:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as tmp:
                device, receipt, _ = self.verification_fixture(tmp)
                with patch.object(flow, "patch_receipt", return_value=(receipt, None)), \
                     patch.object(device, "shell", return_value=response) as request:
                    with self.assertRaises(RootAccessUnavailable) as stopped:
                        flow.verify_root(device, SimpleNamespace(patch_result=Path("unused")))
                request.assert_called_once()
                self.assertFalse(request.call_args.kwargs["check"])
                self.assertEqual(request.call_args.args[:3], ("verify-permanent-root", "/debug_ramdisk/su", "-c"))
                message = str(stopped.exception)
                for text in ("Settings > Apps > See all apps > Magisk > Open", "Superuser", "Shell", "Receipts:"):
                    self.assertIn(text, message)
                self.assertNotIn("No retry", message)
                self.assertFalse((device.s.path / "root-verification.json").exists())

    def test_allowed_shell_saves_full_root_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            device, receipt, response = self.verification_fixture(tmp)
            with patch.object(flow, "patch_receipt", return_value=(receipt, None)), \
                 patch.object(device, "shell", return_value=response) as request:
                result = flow.verify_root(device, SimpleNamespace(patch_result=Path("unused")))
            request.assert_called_once()
            self.assertTrue(result["root_verified"])
            self.assertFalse(result["firmware_written"])
            self.assertEqual(result["partition_sha256"], receipt["patched_image"]["sha256"])
            self.assertEqual(json.loads((device.s.path / "root-verification.json").read_text()), result)

    def test_invalid_root_proof_is_not_treated_as_permission_denial(self):
        for invalid in ("hash", "selinux", "environment", "exit", "missing_su"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                device, receipt, response = self.verification_fixture(tmp)
                if invalid == "hash":
                    response["stdout"] = response["stdout"].replace("a" * 64, "b" * 64)
                elif invalid == "selinux":
                    response["stdout"] = response["stdout"].replace("Enforcing", "Permissive")
                elif invalid == "environment":
                    response["stdout"] = response["stdout"].replace("MAGISK_ENVIRONMENT_COMPLETE", "")
                elif invalid == "exit":
                    response.update(returncode=1, stderr="could not finish\n")
                else:
                    response.update(returncode=127, stdout="", stderr="/debug_ramdisk/su: not found\n")
                with patch.object(flow, "patch_receipt", return_value=(receipt, None)), \
                     patch.object(device, "shell", return_value=response):
                    with self.assertRaises(o.Stop) as stopped:
                        flow.verify_root(device, SimpleNamespace(patch_result=Path("unused")))
                self.assertNotIsInstance(stopped.exception, RootAccessUnavailable)
                self.assertFalse((device.s.path / "root-verification.json").exists())


if __name__ == "__main__":
    unittest.main()

"""Normal restart guards, exercised without ADB or a device."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import onn2026 as o
import root_workflow as flow

BOOT = "11111111-2222-3333-4444-555555555555"
NEW_BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
HASH = "a" * 64
ANDROID_ID = "1122334455667788"
BINDING = {"ro.serialno": "JUSAREBOOTONLY", "ro.product.device": o.PROFILE["device"],
           "ro.build.fingerprint": o.PROFILE["fingerprint"], "ro.bootloader": o.PROFILE["bootloader"],
           "ro.boot.slot_suffix": "_b"}


class Device(o.Device):
    def __init__(self, work, kind):
        self.s = o.Session(work, "reboot-fixture")
        self.binding = dict(BINDING)
        self.props = {**BINDING, "ro.product.cpu.abilist": o.PROFILE["abi"], "sys.boot_completed": "1"}
        self.props.update(o.UNLOCKED if kind == "install" else {
            "ro.boot.flash.locked": "1", "ro.boot.vbmeta.device_state": "locked", "ro.boot.verifiedbootstate": "green"})
        self.kind, self.boot, self.secure = kind, BOOT, 0
        self.dsu = "normal" if kind == "unlock" else "running\ninstalled\ndisabled"
        self.android_id, self.image_hash = ANDROID_ID, HASH
        self.calls = []
        self.on_sync = lambda: None
        self.call_error = None

    def text(self, label, *args, **kwargs):
        if args == ("getprop",):
            return "\n".join(f"[{key}]: [{value}]" for key, value in self.props.items())
        if args == ("cat", "/proc/sys/kernel/random/boot_id"):
            return self.boot
        if args == ("gsi_tool", "status"):
            return self.dsu
        if args == ("settings", "get", "secure", "android_id"):
            return self.android_id
        raise AssertionError((label, args))

    def shell(self, label, *args, **kwargs):
        self.calls.append((label, args))
        if args == ("sync",):
            self.on_sync()
            return {"returncode": 0, "timed_out": False, "stdout": ""}
        if args == ("/system/bin/onn26-su", "-c", flow.partition_hash_command(self.binding)):
            return {"returncode": 0, "timed_out": False,
                    "stdout": self.image_hash + "  " + o.root_partition(self.binding) + "\n"}
        raise AssertionError((label, args))

    def call(self, label, *args, **kwargs):
        assert args == ("reboot",)
        attempt = flow.journal(self, "normal-" + self.kind + "-reboot-attempt.json")
        assert flow.read(attempt)["binding"] == BINDING
        assert kwargs == {"timeout": 8, "check": False}
        self.calls.append((label, args))
        if self.call_error:
            raise self.call_error
        return {"returncode": None, "timed_out": True, "stdout": "", "stderr": "connection lost"}

    def reboot_count(self):
        return sum(args == ("reboot",) for _, args in self.calls)


def proof(kind):
    result = {"binding": dict(BINDING), "boot_id": BOOT}
    if kind == "unlock":
        return {**result, "secure_lock_state": 0, "write_invocations": 1}
    return {**result, "verified_flash": True, "image_sha256": HASH,
            "readback": {"sha256": HASH, "bytes": o.PROFILE["init_boot_size"]},
            "patch_result": "/fixture/patch-result.json", "original_android_id": ANDROID_ID}


class RebootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.patch_record = {"patched_image": {"sha256": HASH}, "android_id": ANDROID_ID}
        for guard in (
            patch.object(o.subprocess, "run", side_effect=AssertionError("Live process forbidden")),
            patch.object(o, "lock_state", side_effect=lambda device: (device.secure, device.boot)),
            patch.object(flow, "patch_receipt", side_effect=lambda device, path: (self.patch_record, None)),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    def test_timeout_consumes_exactly_one_request_and_survives_new_session(self):
        for kind in ("unlock", "install"):
            with self.subTest(kind=kind):
                device = Device(self.work / kind, kind)
                result = flow.reboot_verified(device, kind, proof(kind))
                self.assertEqual(result["reboot_requests"], 1)
                self.assertTrue(result["transport"]["timed_out"])
                self.assertTrue((device.s.path / (kind + "-reboot-result.json")).is_file())
                device.s = o.Session(device.s.work, "resume-fixture")
                with self.assertRaisesRegex(o.Stop, "will not be repeated"):
                    flow.reboot_verified(device, kind, proof(kind))
                self.assertEqual(device.reboot_count(), 1)

    def test_exception_after_request_still_prevents_replay(self):
        device = Device(self.work, "install")
        device.call_error = OSError("lost transport")
        with self.assertRaises(OSError):
            flow.reboot_verified(device, "install", proof("install"))
        device.call_error = None
        with self.assertRaisesRegex(o.Stop, "will not be repeated"):
            flow.reboot_verified(device, "install", proof("install"))
        self.assertEqual(device.reboot_count(), 1)

    def test_already_restarted_device_is_verified_and_not_restarted_again(self):
        for kind in ("unlock", "install"):
            with self.subTest(kind=kind):
                device = Device(self.work / kind, kind)
                device.boot = NEW_BOOT
                device.props.update(o.UNLOCKED)
                device.dsu = "normal" if kind == "unlock" else "installed\ndisabled"
                result = flow.reboot_verified(device, kind, proof(kind))
                self.assertEqual(result["reboot_requests"], 0)
                self.assertTrue(result["already_rebooted"])
                self.assertEqual(device.calls, [])
                self.assertFalse(list(device.s.work.glob("devices/*/*-attempt.json")))

    def test_unverified_unlock_never_reboots(self):
        for changed in ({"secure_lock_state": 1}, {"write_invocations": 2}, {"boot_id": ""}):
            with self.subTest(changed=changed):
                device = Device(self.work, "unlock")
                with self.assertRaises(o.Stop):
                    flow.reboot_verified(device, "unlock", {**proof("unlock"), **changed})
                self.assertEqual(device.reboot_count(), 0)

    def test_fresh_trusted_state_must_still_be_unlocked(self):
        device = Device(self.work, "unlock")
        device.secure = 1
        with self.assertRaisesRegex(o.Stop, "state changed"):
            flow.reboot_verified(device, "unlock", proof("unlock"))
        self.assertEqual(device.reboot_count(), 0)

    def test_wrong_receipt_binding_or_changed_device_never_reboots(self):
        for key, value in (("ro.serialno", "JUSAOTHER"), ("ro.build.fingerprint", "other-build"),
                           ("ro.bootloader", "other-loader"), ("ro.boot.slot_suffix", "_a")):
            for changed_receipt in (True, False):
                with self.subTest(key=key, changed_receipt=changed_receipt):
                    device = Device(self.work, "install")
                    evidence = proof("install")
                    if changed_receipt:
                        evidence["binding"][key] = value
                    else:
                        device.props[key] = value
                    with self.assertRaises(o.Stop):
                        flow.reboot_verified(device, "install", evidence)
                    self.assertEqual(device.reboot_count(), 0)

    def test_failed_or_incomplete_flash_proof_never_reboots(self):
        for changed in ({"verified_flash": False}, {"readback": {}}, {"image_sha256": "b" * 64},
                        {"readback": {"sha256": HASH, "bytes": 1}}, {"patch_result": None}):
            with self.subTest(changed=changed):
                device = Device(self.work, "install")
                with self.assertRaises(o.Stop):
                    flow.reboot_verified(device, "install", {**proof("install"), **changed})
                self.assertEqual(device.calls, [])

    def test_changed_partition_hash_blocks_restart(self):
        device = Device(self.work, "install")
        device.image_hash = "b" * 64
        with self.assertRaisesRegex(o.Stop, "do not restart"):
            flow.reboot_verified(device, "install", proof("install"))
        self.assertEqual(device.reboot_count(), 0)
        self.assertFalse(list(self.work.glob("devices/*/*-attempt.json")))

    def test_changed_patch_or_original_android_record_blocks_restart(self):
        for changed in ({"patched_image": {"sha256": "b" * 64}}, {"android_id": "other-installation"}):
            with self.subTest(changed=changed):
                old = deepcopy(self.patch_record)
                self.patch_record.update(changed)
                device = Device(self.work, "install")
                with self.assertRaises(o.Stop):
                    flow.reboot_verified(device, "install", proof("install"))
                self.assertEqual(device.reboot_count(), 0)
                self.patch_record = old

    def test_enabled_guest_or_wrong_android_state_never_reboots(self):
        for kind, dsu in (("unlock", "installed\nenabled"), ("install", "running\ninstalled\nenabled"),
                          ("install", "installed\ndisabled")):
            with self.subTest(kind=kind, dsu=dsu):
                device = Device(self.work, kind)
                device.dsu = dsu
                with self.assertRaises(o.Stop):
                    flow.reboot_verified(device, kind, proof(kind))
                self.assertEqual(device.reboot_count(), 0)

    def test_new_boot_must_have_returned_to_original_android(self):
        for changed in ({"dsu": "running\ninstalled\ndisabled"}, {"android_id": "other-installation"}):
            with self.subTest(changed=changed):
                device = Device(self.work, "install")
                device.boot, device.dsu = NEW_BOOT, "installed\ndisabled"
                for key, value in changed.items():
                    setattr(device, key, value)
                with self.assertRaisesRegex(o.Stop, "has not returned"):
                    flow.reboot_verified(device, "install", proof("install"))
                self.assertEqual(device.reboot_count(), 0)

    def test_changes_during_preflight_stop_before_restart(self):
        for changed in ({"boot": NEW_BOOT}, {"dsu": "running\ninstalled\nenabled"},
                        {"props": {**BINDING, "ro.serialno": "JUSAOTHER"}}):
            with self.subTest(changed=changed):
                device = Device(self.work, "install")
                device.on_sync = lambda: [setattr(device, key, value) for key, value in changed.items()]
                with self.assertRaises(o.Stop):
                    flow.reboot_verified(device, "install", proof("install"))
                self.assertEqual(device.reboot_count(), 0)

    def test_attempt_must_be_saved_before_any_restart_request(self):
        device = Device(self.work, "unlock")
        with patch.object(o, "save", side_effect=OSError("cannot save attempt")):
            with self.assertRaises(OSError):
                flow.reboot_verified(device, "unlock", proof("unlock"))
        self.assertEqual(device.reboot_count(), 0)


if __name__ == "__main__":
    unittest.main()

"""Accept Android's unpadded hexadecimal IDs without weakening identity checks."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import onn2026 as o
import root_workflow as flow

BOOT = "11111111-2222-3333-4444-555555555555"
BINDING = {"ro.serialno": "JUSAIDENTITYTEST", "ro.product.device": o.PROFILE["device"],
           "ro.build.fingerprint": o.PROFILE["fingerprint"], "ro.bootloader": o.PROFILE["bootloader"],
           "ro.boot.slot_suffix": "_b"}


class BeforeDeviceWrite(Exception):
    pass


class Device:
    def __init__(self, directory, android_id):
        self.s = o.Session(directory, "android-id-fixture")
        self.binding = dict(BINDING)
        self.android_id = android_id
        self.calls = []

    def identify(self, **kwargs):
        assert kwargs == {"unlocked": True}
        return dict(self.binding)

    def boot_id(self):
        return BOOT

    def text(self, label, *args, **kwargs):
        self.calls.append((label, args))
        assert args == ("settings", "get", "secure", "android_id")
        return self.android_id

    def shell(self, label, *args, **kwargs):
        # These boundaries precede any actual staging. This fake never sends
        # commands or transfers files to a device.
        if label == "mkdir-patch":
            raise BeforeDeviceWrite()
        if label == "staging-name-unused":
            return {"returncode": 0, "timed_out": False, "stdout": ""}
        raise AssertionError("Unexpected device command: " + label)

    def call(self, label, *args, **kwargs):
        if label == "push-dsu":
            raise BeforeDeviceWrite()
        raise AssertionError("Unexpected ADB action: " + label)


class AndroidIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Any escape from a fake device is an error, including the trusted reader.
        guard = patch.object(o.subprocess, "run", side_effect=AssertionError("Live I/O forbidden"))
        guard.start()
        self.addCleanup(guard.stop)

    def prepare(self, value):
        device = Device(self.temp.name, value)
        with patch.object(o, "lock_state", return_value=(0, BOOT)), \
             patch.object(o, "validate_image", return_value={"sha256": "stock"}), \
             patch.object(o, "extract_magisk", side_effect=lambda apk, directory: directory.mkdir()):
            with self.assertRaises(BeforeDeviceWrite):
                o.prepare_root(device, "stock.img", "magisk.apk")
        return json.loads((device.s.path / "patch-request.json").read_text())

    def test_root_preparation_accepts_short_id_and_keeps_exact_receipt_value(self):
        for value in ("123456789abcdef", "abcdef12", "0", "001AbC", "1122334455667788"):
            with self.subTest(length=len(value)):
                self.assertEqual(self.prepare(value)["android_id"], value)

    def stage_setup(self, value, root=False, original_id=None):
        device = Device(self.temp.name, value)
        archive = device.s.path / "stock.raw.gz"
        archive.write_bytes(b"fixture archive; not a real image")
        build = device.s.path / "build.json"
        o.save(build, {"stock_gzip": str(archive), "stock_gzip_sha256": o.digest(archive)})
        rehearsal = device.s.path / "rehearsal.json"
        if root:
            o.save(rehearsal, {"binding": device.binding, "stock_single_use_return_verified": True,
                               "build_sha256": o.digest(build), "android_id": original_id})
        args = SimpleNamespace(build=build, kind="root" if root else "stock",
                               rehearsal=rehearsal if root else None)
        return device, args

    def test_stock_dsu_staging_accepts_short_id_and_keeps_exact_receipt_value(self):
        device, args = self.stage_setup("123456789abcdef")
        with patch.object(flow, "build_matches"), patch.object(flow, "state", return_value="normal"):
            with self.assertRaises(BeforeDeviceWrite):
                flow.stage_dsu(device, args)
        request = flow.read(flow.journal(device, "dsu-stock-stage-attempt.json"))
        self.assertEqual(request["android_id"], "123456789abcdef")

    def test_missing_and_malformed_ids_stop_root_preparation_before_staging(self):
        for value in ("", "null", "g123456789abcdef", "1" * 17, "1234\n5678", "0x1234"):
            with self.subTest(value=value):
                device = Device(self.temp.name, value)
                with patch.object(o, "lock_state", return_value=(0, BOOT)), \
                     patch.object(o, "validate_image") as image, \
                     patch.object(o, "extract_magisk") as extract:
                    with self.assertRaisesRegex(o.Stop, "Android installation identity unavailable") as error:
                        o.prepare_root(device, "unused", "unused")
                self.assertFalse((device.s.path / "patch-request.json").exists())
                image.assert_not_called()
                extract.assert_not_called()
                if len(value) > 4:
                    self.assertNotIn(value, str(error.exception))

    def test_missing_id_cannot_create_a_dsu_attempt_or_stage(self):
        device, args = self.stage_setup("null")
        with patch.object(flow, "build_matches"), patch.object(flow, "state", return_value="normal"):
            with self.assertRaisesRegex(o.Stop, "Android installation identity unavailable"):
                flow.stage_dsu(device, args)
        self.assertFalse(flow.journal(device, "dsu-stock-stage-attempt.json").exists())
        self.assertFalse((device.s.path / "stage.json").exists())

    def test_changed_short_id_still_blocks_root_dsu_before_any_attempt(self):
        device, args = self.stage_setup("123456789abcdee", root=True, original_id="123456789abcdef")
        with patch.object(flow, "build_matches"), \
             patch.object(flow, "state", return_value="installed\ndisabled"):
            with self.assertRaisesRegex(o.Stop, "Original Android installation changed"):
                flow.stage_dsu(device, args)
        self.assertFalse(flow.journal(device, "dsu-root-stage-attempt.json").exists())


if __name__ == "__main__":
    unittest.main()

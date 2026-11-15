"""Manual ROW USB preparation and read-only proof, using synthetic images and fake ADB."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import connection
import guide_core
import onn2026 as o
import root_workflow as flow
import row_usb_guide as usb
from test_row import BOOT, Device as RegionalDevice, props

NEXT_BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CONFIG = "KEEPVERITY=true\nKEEPFORCEENCRYPT=true\nRECOVERYMODE=false\nVENDORBOOT=false\n"


def image(path, marker):
    header = bytearray(4096)
    header[:8] = b"ANDROID!"
    struct.pack_into("<II", header, 8, 0, 64)
    struct.pack_into("<I", header, 40, 4)
    with path.open("wb") as output:
        output.write(header)
        output.write(marker)
        output.truncate(o.PROFILE["init_boot_size"])
    return path


class Device(RegionalDevice):
    def __init__(self, work, patched, slot="_a"):
        self.boot, self.android_id, self.dsu = BOOT, "123456789abcdef", "normal"
        self.patched, self.proof_override, self.boot_after_proof = patched, None, None
        super().__init__(work, slot)

    def text(self, label, *args, **kwargs):
        if args == ("cat", "/proc/sys/kernel/random/boot_id"):
            return self.boot
        if args == ("settings", "get", "secure", "android_id"):
            return self.android_id
        if args == ("gsi_tool", "status"):
            return self.dsu
        if label == "read-patch-config":
            return CONFIG
        return super().text(label, *args, **kwargs)

    def call(self, label, *args, **kwargs):
        self.calls.append((label, *args))
        if label == "pull-patched-image":
            assert args[0] == "pull" and args[1].endswith("/new-boot.img")
            shutil.copyfile(self.patched, args[2])
            return {"returncode": 0, "timed_out": False, "stdout": "", "stderr": ""}
        raise AssertionError("Unexpected ADB command: " + label)

    def shell(self, label, *args, **kwargs):
        self.calls.append((label, *args))
        result = {"returncode": 0, "timed_out": False, "stdout": "", "stderr": ""}
        if label == "cpio-test":
            return {**result, "returncode": 1}
        if label == "manager-presence":
            return {**result, "stdout": "package:/fixture/Magisk.apk"}
        if label == "verify-permanent-root":
            if self.boot_after_proof:
                self.boot = self.boot_after_proof
            return self.proof_override or {**result, "stdout":
                "uid=0(root) gid=0(root) context=u:r:magisk:s0\n30.7:MAGISK:R\n30700\nEnforcing\n"
                + o.digest(self.patched) + "  " + o.root_partition(self.binding)
                + "\nMAGISK_ENVIRONMENT_COMPLETE\n"}
        assert label in {"mkdir-patch", "chmod-busybox", "patch-local-image-only", "mkdir-verify",
                         "unpack-patch-check", "extract-config", "read-own-config-mode", "remove-own-staging"}, label
        return result


class UsbRootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.stock = image(self.work / "source.img", b"synthetic stock")
        self.patched = image(self.work / "patched.img", b"synthetic patch")
        self.apk = self.work / "Magisk.apk"
        with zipfile.ZipFile(self.apk, "w") as archive:
            for name in ("busybox", "magisk", "magiskinit", "magiskboot", "init-ld"):
                archive.writestr("lib/armeabi-v7a/lib" + name + ".so", b"fake tool; never executed")
            for name in ("stub.apk", "boot_patch.sh", "util_functions.sh"):
                archive.writestr("assets/" + name, b"fixture")
        pins = patch.dict(o.PROFILE, init_boot_sha256=o.digest(self.stock), magisk_apk_sha256=o.digest(self.apk))
        pins.start()
        self.addCleanup(pins.stop)
        for owner, name in ((o.subprocess, "run"), (connection.socket, "create_connection"),
                            (o.urllib.request, "urlopen")):
            guard = patch.object(owner, name, side_effect=AssertionError("Live I/O forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def prepare(self, slot="_a"):
        device = Device(self.work / slot, self.patched, slot)
        with patch.object(o, "lock_state", return_value=(0, BOOT)):
            record = o.prepare_root(device, self.stock, self.apk, "usb-fastboot")
        args = SimpleNamespace(command="verify-root", method="usb-fastboot", patch_result=Path(record["patch_result"]))
        device.boot = NEXT_BOOT
        return device, record, args

    def test_both_row_slots_prepare_on_target_and_verify_without_flash_or_reboot(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot):
                device, record, args = self.prepare(slot)
                self.assertEqual(record["fastboot_partition"], "init_boot" + slot)
                self.assertEqual(record["patch_source_profile"], o.PROFILE["id"])
                self.assertEqual(record["patch_source_image"]["sha256"], o.digest(self.stock))
                self.assertFalse(record["original_device_backup"])
                self.assertNotIn("stock_image", record)
                self.assertFalse((device.s.path / "stock-init_boot.img").exists())
                self.assertEqual(o.digest(device.s.path / "patch-source-init_boot.img"), o.digest(self.stock))
                self.assertEqual(sum(c[0] == "patch-local-image-only" for c in device.calls), 1)
                with patch.object(o, "verify_files"):
                    proof = flow.run(device, args)
                self.assertTrue(proof["root_verified"])
                self.assertFalse(proof["firmware_written"])
                self.assertFalse(proof["original_device_backup"])
                self.assertEqual(proof["partition"], "/dev/block/by-name/init_boot" + slot)
                command = next(c[-1] for c in device.calls if c[0] == "verify-permanent-root")
                self.assertIn(o.slot_profile(device.binding)["root_block_path"], command)
                self.assertNotIn("dd ", command)
                self.assertFalse(any("flash" in c[0] or "reboot" in c[0] for c in device.calls))

    def test_us_units_reject_usb_before_download_preparation_or_verification(self):
        for slot in ("_a", "_b"):
            device = Device(self.work / slot, self.patched, slot)
            device.props, device.binding = props(slot, o.PROFILE), None
            device.identify()
            self.assertIs(o.patch_source_profile(device.binding), o.PROFILE)
            actions = [lambda: o.fetch_inputs(device.s, device.binding, "usb-fastboot"),
                       lambda: o.prepare_root(device, self.stock, self.apk, "usb-fastboot"),
                       lambda: flow.run(device, SimpleNamespace(command="verify-root", method="usb-fastboot"))]
            for action in actions:
                with patch.object(o, "verify_files"), self.assertRaisesRegex(o.Stop, "No USB fastboot patch source"):
                    action()
            self.assertEqual(device.calls, [])
            self.assertNotIn("USB", guide_core.slot_notice(device.binding))
        self.assertNotIn("USB root", guide_core.PREVIEW)
        self.assertNotIn("usb-fastboot", guide_core.PREVIEW)
        for command in ("fetch-inputs", "prepare-root", "verify-root"):
            with redirect_stdout(StringIO()) as help_text, self.assertRaises(SystemExit):
                o.parser().parse_args([command, "--help"])
            self.assertNotIn("usb-fastboot", help_text.getvalue())

    def test_fetch_usb_uses_pinned_us_source_only_with_explicit_row_selection(self):
        device = Device(self.work, self.patched)
        cache = self.work / "cache/firmware"
        cache.mkdir(parents=True)
        shutil.copyfile(self.stock, cache / "init_boot.img")
        with patch.object(o, "ROOT", self.work), patch.object(o, "verify_files"), \
             patch.object(o, "download", return_value=self.apk) as download:
            for binding, method in ((None, "usb-fastboot"), (device.binding, "dsu")):
                with self.assertRaises(o.Stop):
                    o.fetch_inputs(device.s, binding, method)
            download.assert_not_called()
            result = o.fetch_inputs(device.s, device.binding, "usb-fastboot")
            self.assertEqual(result["patch_source_profile"], o.PROFILE["id"])
            self.assertFalse(result["original_device_backup"])
            self.assertEqual(Path(result["stock_init_boot"]), cache / "init_boot.img")
            download.assert_called_once_with(o.PROFILE["magisk_url"], self.work / "cache/Magisk-v30.7.apk",
                                             o.PROFILE["magisk_apk_sha256"])

    def test_cli_dispatch_preserves_explicit_method_and_saved_regional_binding(self):
        device = Device(self.work, self.patched)
        saved = connection.remember(device)
        for command, function in (("fetch-inputs", "fetch_inputs"), ("prepare-root", "prepare_root")):
            with self.subTest(command=command), patch.object(o, "ROOT", self.work), \
                 patch.object(o, "Device", return_value=device), patch.object(o, function, return_value={}) as action, \
                 redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(o.main(["--work", str(self.work), "--connection", saved,
                                         command, "--method", "usb-fastboot"]), 0)
                self.assertEqual(action.call_args.args[-1], "usb-fastboot")
                if command == "fetch-inputs":
                    self.assertEqual(action.call_args.args[1], device.binding)
                else:
                    self.assertIs(action.call_args.args[0], device)

    def test_usb_preparation_refuses_running_or_enabled_guest_before_staging(self):
        for state in ("running\ninstalled\ndisabled", "installed\nenabled"):
            device = Device(self.work, self.patched)
            device.dsu = state
            with self.assertRaisesRegex(o.Stop, "original Android"):
                o.prepare_root(device, self.stock, self.apk, "usb-fastboot")
            self.assertEqual(device.calls, [])
            self.assertFalse((device.s.path / "patch-request.json").exists())

    def test_usb_receipt_rejects_other_device_slot_method_and_source(self):
        device, record, args = self.prepare()
        changes = [
            {"binding": {**record["binding"], "ro.serialno": "JCAOTHER"}},
            {"binding": {**record["binding"], "ro.boot.slot_suffix": "_b"}},
            {"root_method": "dsu"}, {"root_method": None},
            {"patch_source_profile": "unverified"}, {"patch_source_fingerprint": "other"},
            {"patch_source_image": {"sha256": "0" * 64}}, {"original_device_backup": True},
            {"fastboot_partition": "init_boot_b"}, {"patched_on_target": False},
        ]
        device.calls.clear()
        for change in changes:
            with self.subTest(change=change):
                o.save(args.patch_result, {**record, **change})
                with self.assertRaises(o.Stop):
                    flow.verify_root(device, args)
                self.assertEqual(device.calls, [])
                self.assertFalse((device.s.path / "root-verification.json").exists())

    def test_usb_receipt_cannot_enter_default_dsu_or_writer_paths(self):
        device, _, args = self.prepare()
        with self.assertRaisesRegex(o.Stop, "matching ROW firmware"):
            flow.patch_receipt(device, args.patch_result)
        with patch.object(o, "verify_files"), self.assertRaisesRegex(o.Stop, "matching ROW firmware"):
            flow.run(device, SimpleNamespace(command="install-root", method="usb-fastboot"))
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            o.parser().parse_args(["build-dsu", "--ndk", "unused", "--method", "usb-fastboot"])

    def test_changed_patched_image_stops_before_root_request(self):
        device, _, args = self.prepare()
        with (args.patch_result.parent / "patched-init_boot.img").open("r+b") as stream:
            stream.seek(4200)
            stream.write(b"changed")
        device.calls.clear()
        with self.assertRaisesRegex(o.Stop, "no longer matches"):
            flow.verify_root(device, args)
        self.assertEqual(device.calls, [])

    def test_wrong_boot_installation_or_guest_cannot_verify(self):
        device, _, args = self.prepare()
        for attribute, value in (("boot", BOOT), ("android_id", "another-installation"),
                                 ("dsu", "running\ninstalled\ndisabled")):
            with self.subTest(attribute=attribute), patch.object(device, attribute, value):
                device.calls.clear()
                with self.assertRaises(o.Stop):
                    flow.verify_root(device, args)
                self.assertEqual(device.calls, [])
                self.assertFalse((device.s.path / "root-verification.json").exists())

    def test_bad_root_readback_and_changed_boot_do_not_save_success(self):
        device, _, args = self.prepare()
        response = device.shell("verify-permanent-root", "fixture")
        for bad in ("hash", "selinux", "permission", "boot"):
            with self.subTest(bad=bad):
                device.boot, device.boot_after_proof = NEXT_BOOT, None
                device.proof_override = dict(response)
                if bad == "hash":
                    device.proof_override["stdout"] = response["stdout"].replace(o.digest(self.patched), "0" * 64)
                elif bad == "selinux":
                    device.proof_override["stdout"] = response["stdout"].replace("Enforcing", "Permissive")
                elif bad == "permission":
                    device.proof_override.update(returncode=13, stdout="", stderr="Permission denied")
                else:
                    device.boot_after_proof = BOOT
                with self.assertRaises(o.Stop):
                    flow.verify_root(device, args)
                self.assertFalse((device.s.path / "root-verification.json").exists())

    def test_real_guide_adapter_prepares_bound_instructions_and_verifies_both_slots(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot):
                device = Device(self.work / slot, self.patched, slot)
                backend = usb.Backend(SimpleNamespace(work=self.work), Path(connection.remember(device)), None)
                context = SimpleNamespace(s=device.s, device=device)
                with patch.object(o, "lock_state", return_value=(0, BOOT)):
                    record = backend.invoke("patch", context, {"stock_init_boot": str(self.stock), "magisk_apk": str(self.apk)})
                with patch.object(usb.shutil, "which", return_value="/fixture/fastboot"):
                    result = backend.invoke("flash_consent", context, {"patch_result": record["patch_result"]})
                self.assertIn("flash init_boot" + slot + " ", result["instructions"])
                self.assertIn("-s " + device.binding["ro.serialno"] + " getvar serialno", result["instructions"])
                self.assertIn("getvar current-slot", result["instructions"])
                self.assertEqual(Path(result["instructions_file"]).read_text(), result["instructions"] + "\n")
                self.assertFalse(result["firmware_written"])
                self.assertFalse(result["reboot_requested"])
                device.boot = NEXT_BOOT
                proof = backend.invoke("verify", context, {"patch_result": record["patch_result"]})
                self.assertTrue(proof["root_verified"])

    def test_canadian_guide_host_download_keeps_saved_binding_without_device(self):
        device = Device(self.work, self.patched)
        backend = usb.Backend(SimpleNamespace(work=self.work), Path(connection.remember(device)), None)
        with patch.object(o, "verify_files"), patch.object(o, "fetch_inputs", return_value={}) as fetch:
            context = backend.prepare("fetch")
            self.assertIsNone(context.device)
            backend.invoke("fetch", context, {})
            fetch.assert_called_once_with(context.s, device.binding, "usb-fastboot")


if __name__ == "__main__":
    unittest.main()

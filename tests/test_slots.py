"""Both-slot workflow coverage with an offline Android/ADB transport."""
import hashlib
import json
from pathlib import Path
import shlex
import socket
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import connection
import onn2026 as o
import root_workflow as flow

BOOT = "11111111-2222-3333-4444-555555555555"
OLD_BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ANDROID_ID = "123456789abcdef"
TARGETS = {"_a": ("init_boot_a", "mmcblk0p22"), "_b": ("init_boot_b", "mmcblk0p23")}


def image_bytes(marker):
    data = bytearray(b"\0" * 8388608)
    data[:8] = b"ANDROID!"
    struct.pack_into("<II", data, 8, 0, 1024)
    struct.pack_into("<I", data, 40, 4)
    data[4096:4100] = marker
    return bytes(data)


class Android(o.Device):
    def __init__(self, work, slot):
        self.s = o.Session(work, "slot-fixture")
        self.target, self.adb, self.connection_path = "192.0.2.80:5555", "/offline-adb", None
        self.props = {"ro.serialno": "JUSASLOTTEST", "ro.product.device": o.PROFILE["device"],
                      "ro.build.fingerprint": o.PROFILE["fingerprint"], "ro.bootloader": o.PROFILE["bootloader"],
                      "ro.product.cpu.abilist": o.PROFILE["abi"], "ro.boot.slot_suffix": slot,
                      "sys.boot_completed": "1", **o.UNLOCKED}
        self.binding = None
        self.identify()
        name, block = TARGETS[slot]
        self.partition, self.block = "/dev/block/by-name/" + name, "/dev/block/" + block
        self.other = "/dev/block/by-name/init_boot" + ("_b" if slot == "_a" else "_a")
        self.boot, self.dsu = BOOT, "running\ninstalled\ndisabled"
        self.image, self.patch_image = image_bytes(b"STOK"), image_bytes(b"ROOT")
        self.helper_hash, self.calls, self.flashes, self.reboots = "c" * 64, [], 0, 0
        self.hash_output_path = self.partition

    def text(self, label, *args, **kwargs):
        if args == ("getprop",):
            return "\n".join(f"[{k}]: [{v}]" for k, v in self.props.items())
        if args == ("cat", "/proc/sys/kernel/random/boot_id"):
            return self.boot
        if args == ("gsi_tool", "status"):
            return self.dsu
        if args == ("settings", "get", "secure", "android_id"):
            return ANDROID_ID
        if args == ("sha256sum", "/system/bin/onn26-su"):
            return self.helper_hash + "  /system/bin/onn26-su"
        raise AssertionError((label, args))

    def check_partition_command(self, command):
        assert self.other not in command, command
        assert self.partition in command and self.block in command, command
        assert 'test "$(getprop ro.boot.slot_suffix)" = ' + self.binding["ro.boot.slot_suffix"] in command
        assert '/sys/class/block/' + self.block.rsplit('/', 1)[1] + '/size' in command

    def shell(self, label, *args, **kwargs):
        self.calls.append((label, args))
        stdout = ""
        if label in ("root-and-stock-proof", "before-root-reboot-readback", "verify-permanent-root"):
            self.check_partition_command(args[2])
            line = hashlib.sha256(self.image).hexdigest() + "  " + self.hash_output_path + "\n"
            stdout = line
            if label == "root-and-stock-proof":
                stdout = "uid=0(root) gid=0(root) context=u:r:onn26_root:s0\n" + line
            if label == "verify-permanent-root":
                stdout = ("uid=0(root) gid=0(root) context=u:r:magisk:s0\n"
                          "30.7:MAGISK:R\n30700\nEnforcing\n" + line + "MAGISK_ENVIRONMENT_COMPLETE\n")
        elif label == "native-preflight-no-write":
            stdout = "PREFLIGHT_VERIFIED\n"
        elif label == "flash-once":
            assert flow.read(flow.journal(self, "root-install-attempt.json"))["binding"] == self.binding
            self.flashes += 1
            self.image = self.patch_image
            stdout = "FLASH_READBACK_VERIFIED\n" + hashlib.sha256(self.image).hexdigest() + "\n"
        elif label == "unlock-once":
            assert args[5] == self.binding["ro.boot.slot_suffix"], args
            assert flow.journal(self, "unlock-attempt.json").exists()
            stdout = json.dumps({"stage": "finished", "outcome": 0, "write_invocations": 1})
        elif label not in ("mkdir-root-staging", "readable-root-input", "remove-used-flasher",
                           "flush-before-normal-reboot", "mkdir-writer", "chmod-writer", "remove-writer"):
            raise AssertionError((label, args))
        return {"returncode": 0, "timed_out": False, "stdout": stdout, "stderr": ""}

    def push_checked(self, source, remote):
        self.calls.append(("push", (str(source), remote)))

    def call(self, label, *args, **kwargs):
        assert args == ("reboot",)
        self.reboots += 1
        return {"returncode": 0, "timed_out": False, "stdout": "", "stderr": ""}

    def backup_transport(self, argv, **kwargs):
        assert argv[:4] == [self.adb, "-s", self.target, "exec-out"]
        helper, flag, command = shlex.split(argv[4])
        assert helper == "/system/bin/onn26-su" and flag == "-c"
        self.check_partition_command(command)
        assert command.endswith("head -c 8388608 " + self.partition), command
        kwargs["stdout"].write(self.image)
        return SimpleNamespace(returncode=0, stderr=b"")


def build_record(device):
    slot = device.binding["ro.boot.slot_suffix"]
    return {"serial": device.binding["ro.serialno"], "profile": "jarvis2-15640252-" + slot[-1],
            "fingerprint": o.PROFILE["fingerprint"], "bootloader": o.PROFILE["bootloader"], "slot": slot,
            "single_use_required": True, "root_helper_autostart": False,
            "all_original_inode_metadata_preserved": True, "all_file_bytes_verified": True,
            "policy_compiled": True, "avb_verified": True, "helper_sha256": device.helper_hash}


def prepare_install(device):
    folder = device.s.path
    image = folder / "patched-init_boot.img"
    image.write_bytes(device.patch_image)
    patch_path = folder / "patch-result.json"
    o.save(patch_path, {"binding": dict(device.binding), "boot_id": OLD_BOOT, "android_id": ANDROID_ID,
                       "patched_on_target": True, "ramdisk_verified": True,
                       "magisk_apk_sha256": o.PROFILE["magisk_apk_sha256"],
                       "magisk_configuration": "KEEPVERITY=true\nKEEPFORCEENCRYPT=true\nRECOVERYMODE=false\nVENDORBOOT=false",
                       "patched_image": o.validate_image(image)})
    build = folder / "build.json"
    o.save(build, build_record(device))
    stage = folder / "stage.json"
    o.save(stage, {"binding": dict(device.binding), "kind": "root", "android_id": ANDROID_ID,
                   "build": str(build), "build_sha256": o.digest(build), "boot_id": OLD_BOOT})
    o.save(folder / "boot-request.json", {"stage_sha256": o.digest(stage)})
    return SimpleNamespace(stage=stage, patch_result=patch_path, accept_unproven_recovery=True)


class SlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        for owner, method in ((o.subprocess, "run"), (socket, "create_connection")):
            guard = patch.object(owner, method, side_effect=AssertionError("Live I/O forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def device(self, slot):
        return Android(self.work / slot, slot)

    def test_both_slots_accepted_but_unknown_or_missing_slot_stops(self):
        for slot in TARGETS:
            device = self.device(slot)
            self.assertEqual(o.validate_identity(device.props)["ro.boot.slot_suffix"], slot)
            for bad in ("", "_c", "a", None, "_a; reboot"):
                with self.subTest(slot=slot, bad=bad), self.assertRaises(o.Stop):
                    o.validate_identity({**device.props, "ro.boot.slot_suffix": bad})

    def test_changed_active_slot_cannot_rebind_or_unlock(self):
        for slot in TARGETS:
            device = self.device(slot)
            device.props["ro.boot.slot_suffix"] = "_b" if slot == "_a" else "_a"
            with patch.object(o, "lock_state") as trusted, self.assertRaises(o.Stop):
                o.unlock(device, True)
            trusted.assert_not_called()
            self.assertEqual(device.calls, [])

    def test_slot_a_and_existing_b_connection_records_remain_bound(self):
        for slot in TARGETS:
            device = self.device(slot)
            path = connection.remember(device, "Fixture")
            self.assertEqual(connection.load(path)["binding"], device.binding)
            before = Path(path).read_bytes()
            device.binding = {**device.binding, "ro.boot.slot_suffix": "_b" if slot == "_a" else "_a"}
            with self.assertRaises(o.Stop):
                connection.remember(device)
            self.assertEqual(Path(path).read_bytes(), before)

    def test_unlock_writer_receives_bound_slot_and_cannot_replay(self):
        for slot in TARGETS:
            device = self.device(slot)
            device.props.update({"ro.boot.flash.locked": "1", "ro.boot.vbmeta.device_state": "locked",
                                 "ro.boot.verifiedbootstate": "green"})
            with patch.object(o, "lock_state", side_effect=[(1, BOOT), (0, BOOT), (1, BOOT)]):
                result = o.unlock(device, True)
                self.assertEqual(result["binding"]["ro.boot.slot_suffix"], slot)
                with self.assertRaises(o.Stop):
                    o.unlock(device, True)
            self.assertEqual(sum(label == "unlock-once" for label, _ in device.calls), 1)

    def test_build_binding_keeps_legacy_b_id_and_rejects_other_slot(self):
        for slot in TARGETS:
            device = self.device(slot)
            build = build_record(device)
            flow.build_matches(device, build)
            for field in ("slot", "profile"):
                changed = dict(build)
                changed[field] = ("_b" if slot == "_a" else "_a") if field == "slot" else "jarvis2-15640252-other"
                with self.subTest(slot=slot, field=field), self.assertRaises(o.Stop):
                    flow.build_matches(device, changed)
            self.assertEqual(device.calls, [])

    def test_each_slot_uses_same_partition_for_backup_flash_reboot_and_root_proof(self):
        for slot in TARGETS:
            with self.subTest(slot=slot):
                device = self.device(slot)
                args = prepare_install(device)
                stock_hash = hashlib.sha256(device.image).hexdigest()
                with patch.dict(o.PROFILE, {"init_boot_sha256": stock_hash}), \
                     patch.object(o.subprocess, "run", side_effect=device.backup_transport):
                    result = flow.install_root(device, args)
                    self.assertEqual(result["stock_backup"]["sha256"], stock_hash)
                    self.assertEqual(result["stock_backup"]["partition"], device.partition)
                    self.assertEqual(result["readback"]["partition"], device.partition)
                    script = (device.s.path / "flash-once.sh").read_text()
                    self.assertIn("partition=" + device.partition, script)
                    self.assertIn(device.block, script)
                    self.assertNotIn(device.other, script)
                    self.assertTrue(result["verified_flash"])
                    self.assertEqual(device.flashes, 1)
                    with self.assertRaises(o.Stop):
                        flow.install_root(device, args)
                    self.assertEqual(device.flashes, 1)
                    reboot = flow.reboot_verified(device, "install", result)
                    self.assertEqual(reboot["reboot_requests"], 1)
                    device.boot, device.dsu = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff", "installed\ndisabled"
                    verified = flow.verify_root(device, args)
                    self.assertTrue(verified["root_verified"])
                    self.assertEqual(verified["partition"], device.partition)

    def test_wrong_stock_hash_or_other_partition_proof_stops_before_attempt(self):
        for slot in TARGETS:
            for bad in ("hash", "partition"):
                with self.subTest(slot=slot, bad=bad):
                    device = self.device(slot)
                    args = prepare_install(device)
                    expected = hashlib.sha256(device.image).hexdigest()
                    if bad == "hash":
                        expected = "d" * 64
                    else:
                        device.hash_output_path = device.other
                    with patch.dict(o.PROFILE, {"init_boot_sha256": expected}), self.assertRaises(o.Stop):
                        flow.install_root(device, args)
                    self.assertEqual(device.flashes, 0)
                    self.assertFalse(flow.journal(device, "root-install-attempt.json").exists())

    def test_other_slot_patch_receipt_cannot_install(self):
        for slot in TARGETS:
            device = self.device(slot)
            args = prepare_install(device)
            receipt = flow.read(args.patch_result)
            receipt["binding"]["ro.boot.slot_suffix"] = "_b" if slot == "_a" else "_a"
            o.save(args.patch_result, receipt)
            with self.assertRaises(o.Stop):
                flow.install_root(device, args)
            self.assertEqual(device.calls, [])

    def test_inactive_partition_hash_cannot_prove_permanent_root(self):
        for slot in TARGETS:
            device = self.device(slot)
            args = prepare_install(device)
            device.dsu, device.image, device.hash_output_path = "normal", device.patch_image, device.other
            with self.assertRaises(o.Stop):
                flow.verify_root(device, args)
            self.assertFalse((device.s.path / "root-verification.json").exists())


if __name__ == "__main__":
    unittest.main()

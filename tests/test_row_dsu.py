"""Canadian network DSU lifecycle and refusal paths with no live device access."""
from contextlib import redirect_stderr
import gzip
import hashlib
from io import StringIO
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import connection
import dsu_recovery as recovery
import guide
import onn2026 as o
import root_workflow as flow
import row_usb_guide
from test_connection import FakeSession, OLD, NEW
from test_dsu_recovery import Android as RehearsalAndroid
from test_guide import FakeBackend
from test_row import ROW, props
from test_slots import Android as SlotAndroid, BOOT, build_record, image_bytes, prepare_install


def guest_props(slot="_a"):
    return {**props(slot), "ro.build.fingerprint": ROW["dsu_fingerprint"], "ro.gsid.image_running": "1"}


def row_build(device):
    return {**build_record(device), "profile": o.slot_profile(device.binding)["id"],
            "fingerprint": ROW["fingerprint"], "bootloader": ROW["bootloader"],
            "source_profile": o.PROFILE["id"], "source_fingerprint": o.PROFILE["fingerprint"],
            "dsu_fingerprint": ROW["dsu_fingerprint"]}


class Android(SlotAndroid):
    def __init__(self, work, slot, profile=ROW):
        super().__init__(work, slot)
        self.native_props = props(slot, profile)
        self.props = {**self.native_props, "ro.gsid.image_running": "1",
                      "ro.build.fingerprint": profile.get("dsu_fingerprint", profile["fingerprint"])}
        self.binding = None
        self.identify(unlocked=True)
        self.uploads = {}

    def original(self):
        self.props = dict(self.native_props)
        self.boot, self.dsu = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff", "installed\ndisabled"

    def push_checked(self, source, remote):
        super().push_checked(source, remote)
        self.uploads[remote] = Path(source).read_bytes()

    def shell(self, label, *args, **kwargs):
        if label not in ("save-pre-flash-snapshot", "save-magisk-stock-backup", "remove-backup-transfer", "remove-used-dsu",
                         "known-download-present"):
            return super().shell(label, *args, **kwargs)
        self.calls.append((label, args))
        result = {"returncode": 0, "timed_out": False, "stdout": "", "stderr": ""}
        if label in ("save-pre-flash-snapshot", "save-magisk-stock-backup"):
            command = args[2]
            compressed = next(data for path, data in self.uploads.items() if path.endswith(".gz"))
            expected = hashlib.sha256(gzip.decompress(compressed)).hexdigest()
            if label == "save-pre-flash-snapshot":
                assert "/data/adb/onn26-backups/" + expected + "/pre-flash-init_boot.img.gz" in command
                assert "magisk_backup_" not in command, "A device snapshot must not become a factory-stock backup"
            else:
                assert expected == o.PROFILE["init_boot_sha256"], "US backups still require the pinned stock hash"
                assert "/data/magisk_backup_" + o.PROFILE["root_stock_sha1"] + "/boot.img.gz" in command
                assert "onn26-backups" not in command
            result["stdout"] = expected + "  -\n"
        elif label == "remove-used-dsu":
            assert args == ("gsi_tool", "wipe")
            self.dsu = "normal"
        elif label == "known-download-present":
            result["returncode"] = 1
        return result


def preparation(device):
    args = prepare_install(device)
    receipt = flow.read(args.patch_result)
    receipt.update(root_method="dsu", patch_source_profile=o.PROFILE["id"],
                   patch_source_fingerprint=o.PROFILE["fingerprint"],
                   patch_source_image={"sha256": o.PROFILE["init_boot_sha256"]}, original_device_backup=False)
    o.save(args.patch_result, receipt)
    stage = flow.read(args.stage)
    o.save(Path(stage["build"]), row_build(device))
    stage.update(build_sha256=o.digest(stage["build"]),
                 remote="/storage/emulated/0/Download/onn26-root-123456789abc.raw.gz", archive_sha256="a" * 64)
    o.save(args.stage, stage)
    o.save(args.stage.parent / "boot-request.json", {"stage_sha256": o.digest(args.stage)})
    return args


class CanadianDsuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        for owner, method in ((o.subprocess, "run"), (socket, "create_connection"), (o.urllib.request, "urlopen")):
            guard = patch.object(owner, method, side_effect=AssertionError("Live I/O forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_guest_identity_is_exact_requires_dsu_and_keeps_native_binding(self):
        for slot in ("_a", "_b"):
            values = guest_props(slot)
            expected = o.validate_identity(props(slot), unlocked=True)
            self.assertEqual(o.validate_identity(values, unlocked=True), expected)
            self.assertEqual(values["ro.build.fingerprint"], ROW["dsu_fingerprint"])
            for key, value in (("ro.gsid.image_running", "0"), ("ro.gsid.image_running", ""),
                               ("ro.serialno", "JUSAOTHER"), ("ro.bootloader", o.PROFILE["bootloader"]),
                               ("ro.build.fingerprint", o.PROFILE["fingerprint"]),
                               ("ro.build.fingerprint", ROW["dsu_fingerprint"] + "-other"),
                               ("ro.boot.slot_suffix", "_c")):
                with self.subTest(slot=slot, key=key, value=value), self.assertRaises(o.Stop):
                    o.validate_identity({**values, key: value}, unlocked=True)
            with self.assertRaises(o.Stop):
                o.validate_identity({key: value for key, value in values.items() if key != "ro.gsid.image_running"})

    def test_reconnect_across_native_guest_and_native_with_changed_ip(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot), patch.object(o.shutil, "which", return_value="fake-adb"):
                values = props(slot)
                session = FakeSession(self.work / slot, connected={OLD: "device", NEW: "device"},
                                      properties={OLD: values, NEW: values})
                device = o.Device(session, OLD, "fake-adb")
                device.identify()
                path = Path(connection.remember(device))
                binding = dict(device.binding)
                session.properties[OLD] = {**values, "ro.serialno": "JCAOTHER"}
                session.properties[NEW] = guest_props(slot)
                self.assertEqual(connection.reconnect(device, path)["target"], NEW)
                self.assertEqual(device.identify()["ro.build.fingerprint"], ROW["dsu_fingerprint"])
                self.assertEqual(connection.load(path)["binding"], binding)
                observed = connection.inspect(device, {NEW: "device"}, [NEW])
                self.assertEqual(observed[0]["observed_fingerprint"], ROW["dsu_fingerprint"])
                session.properties[NEW] = values
                connection.reconnect(device, path)
                self.assertEqual(device.identify()["ro.build.fingerprint"], ROW["fingerprint"])
                self.assertEqual(connection.load(path)["binding"], binding)

    def test_guest_cannot_run_original_android_operations_even_with_normal_status(self):
        device = Android(self.work, "_a")
        args = preparation(device)
        device.dsu = "normal"
        checks = (lambda: o.unlock(device, True), lambda: o.prepare_root(device, Path("unused.img"), Path("unused.apk")),
                  lambda: flow.automatic_updates(device, SimpleNamespace()), lambda: flow.verify_root(device, args),
                  lambda: recovery.original_android(device), lambda: flow.boot_dsu(device, args),
                  lambda: flow.stage_dsu(device, SimpleNamespace(build=Path(flow.read(args.stage)["build"]), kind="stock")))
        for check in checks:
            with self.subTest(operation=check), self.assertRaisesRegex(o.Stop, "original Android firmware"):
                check()
        self.assertEqual(device.calls, [])
        self.assertEqual(device.flashes, 0)

    def test_build_and_patch_receipts_bind_source_target_and_method(self):
        device = Android(self.work, "_b")
        args = preparation(device)
        build = row_build(device)
        flow.build_matches(device, build)
        flow.patch_receipt(device, args.patch_result)
        for key in ("source_profile", "source_fingerprint", "dsu_fingerprint", "fingerprint", "slot"):
            with self.subTest(build_field=key), self.assertRaises(o.Stop):
                flow.build_matches(device, {**build, key: "another"})
        original = flow.read(args.patch_result)
        for key, value in (("patch_source_profile", ROW["id"]), ("patch_source_fingerprint", ROW["fingerprint"]),
                           ("patch_source_image", {"sha256": "a" * 64}), ("original_device_backup", True),
                           ("root_method", "usb-fastboot")):
            o.save(args.patch_result, {**original, key: value})
            with self.subTest(patch_field=key), self.assertRaises(o.Stop):
                flow.patch_receipt(device, args.patch_result)
        self.assertIs(o.dsu_source_profile(device.binding), o.PROFILE)
        with patch.dict(ROW, {"dsu_source_profile": "unpinned"}), self.assertRaises(o.Stop):
            o.dsu_source_profile(device.binding)

    def test_snapshot_is_required_and_cannot_override_us_stock_pin(self):
        row = o.validate_identity(props())
        us = o.validate_identity(props(profile=o.PROFILE))
        for value in (None, "", "a" * 63, "z" * 64):
            with self.subTest(value=value), self.assertRaises(o.Stop):
                flow.flash_script(row, BOOT, "/tmp/input", "/metadata/attempt", "c" * 64, value)
        with self.assertRaisesRegex(o.Stop, "cannot be overridden"):
            flow.flash_script(us, BOOT, "/tmp/input", "/metadata/attempt", "c" * 64, "a" * 64)

    def test_install_reboot_verify_and_cleanup_both_slots_preserve_real_snapshot(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot):
                device = Android(self.work / slot, slot)
                args = preparation(device)
                original = device.image
                original_hash = hashlib.sha256(original).hexdigest()
                self.assertNotEqual(original_hash, o.PROFILE["init_boot_sha256"])
                with patch.object(o.subprocess, "run", side_effect=device.backup_transport):
                    installed = flow.install_root(device, args)
                    self.assertNotIn("stock_backup", installed)
                    snapshot = installed["pre_flash_backup"]
                    self.assertFalse(snapshot["factory_stock_verified"])
                    self.assertEqual(snapshot["partition"], device.partition)
                    self.assertEqual(snapshot["sha256"], original_hash)
                    self.assertEqual(Path(snapshot["file"]).read_bytes(), original)
                    script = (device.s.path / "flash-once.sh").read_text()
                    for expected in (ROW["dsu_fingerprint"], ROW["bootloader"], original_hash, device.partition):
                        self.assertIn(expected, script)
                    self.assertNotIn(device.other, script)
                    with self.assertRaisesRegex(o.Stop, "attempt already exists"):
                        flow.install_root(device, args)
                    self.assertEqual(device.flashes, 1)
                    self.assertEqual(flow.reboot_verified(device, "install", installed)["reboot_requests"], 1)
                with self.assertRaisesRegex(o.Stop, "original Android firmware"):
                    flow.verify_root(device, args)
                device.original()
                self.assertTrue(flow.verify_root(device, args)["root_verified"])
                result = flow.cleanup_root(device, SimpleNamespace(install_result=Path(installed["install_result"])))
                self.assertTrue(result["cleanup_completed"])
                self.assertFalse(result["factory_stock_verified"])
                self.assertEqual(result["pre_flash_backup_on_device"],
                                 "/data/adb/onn26-backups/" + original_hash + "/pre-flash-init_boot.img.gz")
                self.assertNotIn("stock_backup_on_device", result)
                self.assertEqual((device.dsu, device.flashes, device.reboots), ("normal", 1, 1))

    def test_us_cleanup_retains_pinned_magisk_stock_backup_on_both_slots(self):
        for slot in ("_a", "_b"):
            device = Android(self.work / slot, slot, o.PROFILE)
            args = prepare_install(device)
            stage = flow.read(args.stage)
            stage.update(remote="/storage/emulated/0/Download/onn26-root-123456789abc.raw.gz", archive_sha256="a" * 64)
            o.save(args.stage, stage)
            o.save(args.stage.parent / "boot-request.json", {"stage_sha256": o.digest(args.stage)})
            stock_hash = hashlib.sha256(device.image).hexdigest()
            with patch.dict(o.PROFILE, {"init_boot_sha256": stock_hash}), \
                 patch.object(o.subprocess, "run", side_effect=device.backup_transport):
                installed = flow.install_root(device, args)
                self.assertEqual(installed["stock_backup"]["sha256"], stock_hash)
                self.assertNotIn("pre_flash_backup", installed)
                device.original()
                result = flow.cleanup_root(device, SimpleNamespace(install_result=Path(installed["install_result"])))
            self.assertEqual(result["stock_backup_on_device"],
                             "/data/magisk_backup_" + o.PROFILE["root_stock_sha1"] + "/boot.img.gz")
            self.assertNotIn("pre_flash_backup_on_device", result)
            self.assertTrue(result["cleanup_completed"])

    def test_root_proof_cannot_survive_a_changed_boot_device_slot_or_guest(self):
        for profile in (o.PROFILE, ROW):
            for slot in ("_a", "_b"):
                for change in ("boot", "serial", "slot", "guest", "dsu-state", "locked"):
                    with self.subTest(profile=profile["id"], slot=slot, change=change):
                        device = Android(self.work / profile["id"] / slot / change, slot, profile)
                        args = preparation(device) if profile is ROW else prepare_install(device)
                        device.image = device.patch_image
                        device.original()
                        original = device.shell
                        def changed(label, *argv, **kwargs):
                            result = original(label, *argv, **kwargs)
                            if label == "verify-permanent-root":
                                if change == "boot":
                                    device.boot = "cccccccc-dddd-eeee-ffff-111111111111"
                                elif change == "serial":
                                    device.props["ro.serialno"] += "OTHER"
                                elif change == "slot":
                                    device.props["ro.boot.slot_suffix"] = "_b" if slot == "_a" else "_a"
                                elif change == "guest":
                                    device.props.update({"ro.gsid.image_running": "1",
                                                         "ro.build.fingerprint": profile.get("dsu_fingerprint", profile["fingerprint"])})
                                elif change == "dsu-state":
                                    device.dsu = "running\ninstalled\ndisabled"
                                elif change == "locked":
                                    device.props["ro.boot.flash.locked"] = "1"
                            return result
                        with patch.object(device, "shell", side_effect=changed), self.assertRaises(o.Stop):
                            flow.verify_root(device, args)
                        self.assertFalse((device.s.path / "root-verification.json").exists())
                        self.assertEqual((device.flashes, device.reboots), (0, 0))

    def test_uncertain_canadian_flash_or_bad_readback_never_reboots_or_replays(self):
        for slot in ("_a", "_b"):
            for failure in ("timeout", "nonzero", "missing-proof", "bad-readback"):
                with self.subTest(slot=slot, failure=failure):
                    device = Android(self.work / slot / failure, slot)
                    args = preparation(device)
                    original = device.shell
                    def failed(label, *argv, **kwargs):
                        result = original(label, *argv, **kwargs)
                        if label == "flash-once":
                            if failure == "timeout":
                                result.update(timed_out=True, returncode=None)
                            elif failure == "nonzero":
                                result["returncode"] = 1
                            elif failure == "missing-proof":
                                result["stdout"] = ""
                            elif failure == "bad-readback":
                                device.image = image_bytes(b"DIFF")
                        return result
                    with patch.object(device, "shell", side_effect=failed), \
                         patch.object(o.subprocess, "run", side_effect=device.backup_transport):
                        with self.assertRaisesRegex(o.Stop, "uncertain|readback mismatch"):
                            flow.install_root(device, args)
                        before = flow.journal(device, "root-install-attempt.json").read_bytes()
                        with self.assertRaisesRegex(o.Stop, "attempt already exists"):
                            flow.install_root(device, args)
                    self.assertEqual(flow.journal(device, "root-install-attempt.json").read_bytes(), before)
                    self.assertFalse((device.s.path / "install-result.json").exists())
                    self.assertEqual((device.flashes, device.reboots), (1, 0))

    def test_invalid_canadian_snapshot_stops_before_upload_or_write(self):
        for invalid in ("header", "length"):
            device = Android(self.work / invalid, "_a")
            args = preparation(device)
            device.image = bytes(o.PROFILE["init_boot_size"]) if invalid == "header" else image_bytes(b"STOK")[:4096]
            with patch.object(o.subprocess, "run", side_effect=device.backup_transport), self.assertRaises(o.Stop):
                flow.install_root(device, args)
            self.assertFalse(flow.journal(device, "root-install-attempt.json").exists())
            self.assertEqual((device.flashes, device.reboots), (0, 0))
            self.assertFalse(device.uploads)

    def test_partition_changed_during_backup_never_reaches_write_or_attempt(self):
        for slot in ("_a", "_b"):
            device = Android(self.work / slot, slot)
            args = preparation(device)
            def changed(argv, **kwargs):
                device.image = image_bytes(b"DIFF")
                return device.backup_transport(argv, **kwargs)
            with patch.object(o.subprocess, "run", side_effect=changed), \
                 self.assertRaisesRegex(o.Stop, "Partition changed between proof and backup"):
                flow.install_root(device, args)
            self.assertFalse(flow.journal(device, "root-install-attempt.json").exists())
            self.assertEqual(device.flashes, 0)
            self.assertFalse(device.uploads)

    def test_cleanup_rejects_changed_backup_or_factory_stock_claim(self):
        for change in ("bytes", "provenance"):
            device = Android(self.work / change, "_a")
            with patch.object(o.subprocess, "run", side_effect=device.backup_transport):
                installed = flow.install_root(device, preparation(device))
            device.original()
            path = Path(installed["install_result"])
            if change == "bytes":
                Path(installed["pre_flash_backup"]["file"]).write_bytes(image_bytes(b"DIFF"))
            else:
                installed["pre_flash_backup"]["factory_stock_verified"] = True
                o.save(path, installed)
            with self.assertRaisesRegex(o.Stop, "Backup changed|snapshot provenance"):
                flow.cleanup_root(device, SimpleNamespace(install_result=path))
            self.assertFalse(any(label in ("save-pre-flash-snapshot", "remove-used-dsu") for label, _ in device.calls))

    def test_restart_rehearsal_return_and_second_guest_boot_on_both_slots(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot):
                folder = self.work / slot
                device = RehearsalAndroid(folder)
                device.props = props(slot)
                device.binding = o.validate_identity(device.props)
                base = recovery.directory(device)
                selected = base / "connection.json"
                record = {"schema": connection.SCHEMA, "binding": device.binding, "target": OLD}
                o.save(selected, record)
                ui = guide.UI(read=lambda _: "", choose=lambda: True, emit=lambda _: None, clear=lambda: None)
                recovery.restart(device, base / "guide.json", guide.initial_state(record, selected), record, selected, ui)
                device.helper_hash = "c" * 64
                build = {**row_build(device), "system_bytes": 100}
                for kind in ("stock", "root"):
                    archive = folder / (kind + ".gz")
                    archive.write_bytes(kind.encode())
                    build[kind + "_gzip"], build[kind + "_gzip_sha256"] = str(archive), o.digest(archive)
                build_path = folder / "build.json"
                o.save(build_path, build)
                rehearsal = None
                for kind in ("stock", "root"):
                    device.prepare(kind)
                    result = flow.stage_dsu(device, SimpleNamespace(build=build_path, kind=kind, rehearsal=rehearsal))
                    args = SimpleNamespace(stage=Path(result["stage"]), observed_setup=True, power_cycled_back=True)
                    self.assertTrue(flow.dsu_status(device, args)["installed_disabled_verified"])
                    self.assertEqual(flow.boot_dsu(device, args)["reboot_requests"], 1)
                    device.props = guest_props(slot)
                    device.identify(unlocked=True)
                    if kind == "stock":
                        device.boot_number += 1
                        device.state = "installed\ndisabled"
                        with self.assertRaisesRegex(o.Stop, "original Android firmware"):
                            flow.stock_return(device, args)
                        device.props = props(slot)
                        rehearsal = Path(flow.stock_return(device, args)["rehearsal"])
                self.assertEqual(device.state, "running\ninstalled\ndisabled")
                self.assertEqual(device.writes.count("push-dsu"), 2)
                self.assertEqual(device.writes.count("single-dsu-reboot"), 2)

    def test_network_menu_reaches_full_shared_guide_and_preserves_usb_progress(self):
        for slot in ("_a", "_b"):
            for locked in (True, False):
                folder = self.work / (slot + str(locked))
                binding = o.validate_identity(props(slot))
                base = folder / "devices" / hashlib.sha256(binding["ro.serialno"].encode()).hexdigest()
                selected = base / "connection.json"
                o.save(selected, {"schema": connection.SCHEMA, "binding": binding, "target": OLD})
                usb_progress = base / "usb-guide.json"
                usb_progress.write_text('"keep separate USB history"\n')
                backend = FakeBackend(folder, locked=locked, binding=binding)
                messages = []
                answers = iter(("1",))
                ui = guide.UI(read=lambda _: next(answers, ""), choose=lambda: True,
                              emit=messages.append, clear=lambda: None)
                args = o.parser().parse_args(["--work", str(folder), "--connection", str(selected), "guide"])
                with patch.object(guide.sys.stdin, "isatty", return_value=True), patch.object(o, "verify_files"), \
                     patch.object(guide.shutil, "which", return_value="fake-adb"), \
                     patch.object(guide, "Backend", return_value=backend), patch.object(row_usb_guide, "Backend") as usb:
                    self.assertEqual(guide.start(args, ui), 0)
                usb.assert_not_called()
                self.assertEqual(flow.read(base / "guide.json")["phase"], "complete")
                self.assertEqual(backend.count("unlock"), int(locked))
                for phase in ("stock_boot", "root_boot", "install", "verify", "cleanup"):
                    self.assertEqual(backend.count(phase), 1)
                self.assertIn("not independently tested", "\n".join(messages))
                self.assertEqual(usb_progress.read_text(), '"keep separate USB history"\n')

    def test_reinstall_is_not_a_supported_bypass_and_rooted_restart_stays_blocked(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            o.parser().parse_args(["guide", "--reinstall"])
        device = RehearsalAndroid(self.work)
        device.props = props()
        device.binding = o.validate_identity(device.props)
        device.root = True
        with self.assertRaisesRegex(o.Stop, "root"):
            recovery.original_android(device)
        self.assertEqual(device.writes, [])


if __name__ == "__main__":
    unittest.main()

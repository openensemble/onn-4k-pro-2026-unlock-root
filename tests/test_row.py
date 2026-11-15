"""Regional identities, unlock binding and refusal of unverified DSU inputs. No live I/O."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import connection
from connection_identity import validate_binding
import guide
import guide_core
import onn2026 as o
import root_workflow as flow
from tools import build_dsu, slot_report
from test_connection import FakeSession, OLD, NEW
from test_slot_report import FakeADB, TARGET

ROW = o.PROFILE["variants"]["jarvis2-row-15016191"]
BOOT = "11111111-2222-3333-4444-555555555555"


def props(slot="_a", profile=ROW):
    return {"ro.serialno": profile["serial_prefixes"][0] + "ROWTEST001",
            "ro.product.device": profile["device"], "ro.build.fingerprint": profile["fingerprint"],
            "ro.bootloader": profile["bootloader"], "ro.product.cpu.abilist": profile["abi"],
            "ro.boot.slot_suffix": slot, "sys.boot_completed": "1", **o.UNLOCKED}


class Device(o.Device):
    def __init__(self, work, slot="_a"):
        self.s = o.Session(work, "row-fixture")
        self.target, self.adb, self.connection_path = OLD, "fake-adb", None
        self.props, self.binding, self.calls = props(slot), None, []
        self.identify()
        self.result = {"returncode": 0, "timed_out": False,
                       "stdout": json.dumps({"stage": "finished", "outcome": 0, "write_invocations": 1})}

    def text(self, label, *args, **kwargs):
        if args == ("getprop",):
            return "\n".join(f"[{key}]: [{value}]" for key, value in self.props.items())
        if args == ("cat", "/proc/sys/kernel/random/boot_id"):
            return BOOT
        raise AssertionError((label, args))

    def push_checked(self, source, remote):
        self.calls.append(("push", str(source), remote))

    def shell(self, label, *args, **kwargs):
        self.calls.append((label, *args))
        if label == "unlock-once":
            attempts = list(self.s.work.glob("devices/*/unlock-attempt.json"))
            assert len(attempts) == 1, "Writer invoked without durable attempt"
            assert json.loads(attempts[0].read_text())["binding"] == self.binding
            return self.result
        if label in ("mkdir-writer", "chmod-writer", "remove-writer"):
            return {"returncode": 0, "timed_out": False, "stdout": ""}
        raise AssertionError("Unexpected mutation: " + label)


class RegionalSupport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        for owner, name in ((o.subprocess, "run"), (connection.socket, "create_connection"),
                            (o.urllib.request, "urlopen")):
            guard = patch.object(owner, name, side_effect=AssertionError("Live I/O forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_exact_regional_identities_and_partition_maps_for_both_slots(self):
        for profile in (o.PROFILE, ROW):
            for slot, node in (("_a", "22"), ("_b", "23")):
                with self.subTest(profile=profile["id"], slot=slot):
                    binding = o.validate_identity(props(slot, profile), unlocked=True)
                    self.assertEqual(validate_binding(binding), binding)
                    self.assertIs(o.active_profile(binding), profile)
                    self.assertEqual(o.slot_profile(binding)["id"], profile["id"] + "-" + slot[-1])
                    self.assertEqual(o.root_partition(binding), "/dev/block/by-name/init_boot" + slot)
                    self.assertEqual(o.slot_profile(binding)["root_block_path"], "/dev/block/mmcblk0p" + node)

    def test_row_identity_rejects_changed_fields_and_cross_region_serial(self):
        for key, value in (("ro.serialno", "JUSAOTHER"), ("ro.serialno", "JCA"),
                           ("ro.serialno", "JCA_BAD"), ("ro.serialno", "JCAtest;id"),
                           ("ro.build.fingerprint", ROW["fingerprint"] + "-other"),
                           ("ro.bootloader", o.PROFILE["bootloader"]), ("ro.product.device", "jarvis"),
                           ("ro.boot.slot_suffix", ""), ("ro.product.cpu.abilist", "arm64-v8a")):
            with self.subTest(key=key, value=value), self.assertRaises(o.Stop):
                o.validate_identity({**props(), key: value})
        with self.assertRaises(o.Stop):
            o.validate_identity({**props(profile=o.PROFILE), "ro.serialno": "JCAOTHER"})
        with self.assertRaises(o.Stop):
            o.slot_profile({"ro.boot.slot_suffix": "_a"})

    def test_row_profile_never_inherits_us_image_pins_or_root_verification(self):
        self.assertFalse(ROW["dsu_root_supported"])
        for key in ("ota_url", "ota_sha256", "init_boot_sha256", "root_stock_sha1", "dsu_partitions"):
            self.assertNotIn(key, ROW)
        for slot in ROW["slots"].values():
            self.assertFalse(slot["root_method_hardware_verified"])
        self.assertFalse(ROW["slots"]["_b"]["unlock_hardware_verified"])

    def test_status_saves_row_connection_and_reports_capabilities(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot):
                device = Device(self.work / slot, slot)
                with patch.object(o, "lock_state", return_value=(0, BOOT)):
                    result = o.status(device, "ROW fixture")
                saved = connection.load(result["connection_file"])
                self.assertEqual(saved["binding"], device.binding)
                self.assertEqual(result["firmware_profile"], ROW["id"])
                self.assertFalse(result["dsu_root_supported"])
                self.assertEqual(result["slot_profile"]["root_partition"], "init_boot" + slot)
                self.assertEqual(device.calls, [])

    def test_row_reconnect_rejects_old_ip_other_device_and_preserves_binding(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot), patch.object(o.shutil, "which", return_value="fake-adb"):
                values = props(slot)
                session = FakeSession(self.work / slot, connected={OLD: "device", NEW: "device"},
                                      properties={OLD: values, NEW: values})
                device = o.Device(session, OLD, "fake-adb")
                device.identify()
                path = Path(connection.remember(device, "ROW fixture"))
                session.properties[OLD] = {**values, "ro.serialno": "JCAOTHER"}
                result = connection.reconnect(device, path)
                self.assertEqual(result["target"], NEW)
                self.assertEqual(connection.load(path)["binding"], o.validate_identity(values))
                self.assertEqual((result["device_write_invocations"], result["reboot_invocations"]), (0, 0))

    def test_unlock_uses_row_firmware_and_bound_active_slot_once(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot):
                device = Device(self.work / slot, slot)
                device.props.update({"ro.boot.flash.locked": "1", "ro.boot.verifiedbootstate": "green"})
                with patch.object(o, "lock_state", side_effect=[(1, BOOT), (0, BOOT)]):
                    result = o.unlock(device, True)
                invocation = next(c for c in device.calls if c[0] == "unlock-once")
                self.assertEqual(invocation[2:7], ("--unlock-once", device.binding["ro.serialno"],
                                                ROW["fingerprint"], ROW["bootloader"], slot))
                self.assertEqual(result["write_invocations"], 1)
                self.assertFalse(result["reboot_requested"])
                before = list(device.calls)
                with patch.object(o, "lock_state", return_value=(1, BOOT)), self.assertRaisesRegex(o.Stop, "no replay"):
                    o.unlock(device, True)
                self.assertEqual(device.calls, before)

    def test_already_unlocked_row_skips_writer_and_timeout_never_replays(self):
        device = Device(self.work)
        with patch.object(o, "lock_state", return_value=(0, BOOT)):
            self.assertTrue(o.unlock(device, True)["already_unlocked"])
        self.assertEqual(device.calls, [])
        device.props.update({"ro.boot.flash.locked": "1", "ro.boot.verifiedbootstate": "green"})
        device.result = {"returncode": None, "timed_out": True, "stdout": ""}
        with patch.object(o, "lock_state", return_value=(1, BOOT)):
            with self.assertRaisesRegex(o.Stop, "uncertain"):
                o.unlock(device, True)
            with self.assertRaisesRegex(o.Stop, "no replay"):
                o.unlock(device, True)
        self.assertEqual(sum(c[0] == "unlock-once" for c in device.calls), 1)

    def test_root_entry_commands_refuse_row_before_reading_receipts_or_mutating(self):
        commands = flow.COMMANDS - {"disable-updates", "updates-status", "enable-updates"}
        for slot in ("_a", "_b"):
            device = Device(self.work / slot, slot)
            for command in sorted(commands):
                with self.subTest(slot=slot, command=command), patch.object(o, "verify_files"), \
                     self.assertRaisesRegex(o.Stop, "matching ROW firmware"):
                    flow.run(device, SimpleNamespace(command=command))
            self.assertEqual(device.calls, [])
            self.assertEqual(list(device.s.work.glob("devices/*/*attempt*")), [])

    def test_direct_preparation_and_flash_paths_refuse_row_without_inputs(self):
        device = Device(self.work)
        actions = [lambda: o.prepare_root(device, self.work / "missing.img", self.work / "missing.apk"),
                   lambda: o.fetch_inputs(device.s, device.binding),
                   lambda: flow.build_dsu(device, SimpleNamespace()),
                   lambda: flow.build_matches(device, {}),
                   lambda: flow.bound(device, {"binding": device.binding}),
                   lambda: flow.partition_guard(device.binding),
                   lambda: flow.flash_script(device.binding, BOOT, "/unused", "/unused", "a" * 64),
                   lambda: flow.reboot_verified(device, "install", {"boot_id": BOOT, "binding": device.binding})]
        for index, action in enumerate(actions):
            with self.subTest(action=index), self.assertRaisesRegex(o.Stop, "matching ROW firmware"):
                action()
        self.assertEqual(device.calls, [])

    def test_fetch_cli_honors_row_connection_and_explicit_target(self):
        device = Device(self.work)
        saved = connection.remember(device)
        for selector in (["--connection", saved], ["--target", OLD]):
            with self.subTest(selector=selector), patch.object(o, "ROOT", self.work), \
                 patch.object(o, "Device", return_value=device), redirect_stdout(StringIO()), \
                 redirect_stderr(StringIO()) as output:
                self.assertEqual(o.main(["--work", str(self.work), *selector, "fetch-inputs"]), 2)
                self.assertIn("matching ROW firmware", output.getvalue())
        self.assertFalse((self.work / "cache").exists())

    def test_builder_rejects_row_and_wrong_serial_before_probing_tools(self):
        for profile, serial in ((ROW["id"], "JCAROWTEST"), (o.PROFILE["id"], "JCAROWTEST")):
            args = SimpleNamespace(firmware_profile=profile, serial=serial, slot="_a")
            with self.subTest(profile=profile), self.assertRaises(o.Stop):
                build_dsu.build(args)

    def test_dsu_restart_refuses_row_before_backend_or_saved_state_changes(self):
        device = Device(self.work)
        saved = Path(connection.remember(device))
        checkpoint = saved.parent / "guide.json"
        checkpoint.write_text('{"preserve": "existing ROW evidence"}\n')
        before = checkpoint.read_bytes()
        messages = []
        ui = guide_core.UI(read=lambda _: "", emit=messages.append, choose=lambda: True, clear=lambda: None)
        for restart in (["--restart-dsu"],):
            args = o.parser().parse_args(["--work", str(self.work), "--connection", str(saved), "guide", *restart])
            with self.subTest(restart=restart), patch.object(guide.sys.stdin, "isatty", return_value=True), \
                 patch.object(o, "verify_files"), patch.object(o, "ROOT", self.work), \
                 patch.object(guide.shutil, "which", return_value="fake-adb"), \
                 patch.object(guide, "Backend", side_effect=AssertionError("ROW guide must stop before backend")):
                self.assertEqual(guide.start(args, ui), 2)
                self.assertEqual(checkpoint.read_bytes(), before)
        self.assertIn("matching ROW firmware", "\n".join(messages))
        self.assertIn("USB fastboot image preparation", "\n".join(messages))

    def test_guide_fetch_passes_selected_firmware_to_download_guard(self):
        device = Device(self.work)
        backend = guide.Backend(SimpleNamespace(work=self.work), Path(connection.remember(device)), None)
        with patch.object(o, "verify_files"):
            context = backend.prepare("fetch")
        self.assertIsNone(context.device)  # Downloads are deliberately host-only.
        with self.assertRaisesRegex(o.Stop, "matching ROW firmware"):
            backend.invoke("fetch", context, {})

    def test_us_guide_download_uses_saved_identity_without_device_access(self):
        device = Device(self.work)
        device.props, device.binding = props(profile=o.PROFILE), None
        device.identify()
        backend = guide.Backend(SimpleNamespace(work=self.work), Path(connection.remember(device)), None)
        with patch.object(o, "verify_files"), patch.object(o, "fetch_inputs", return_value={"download": "fixture"}) as fetch:
            context = backend.prepare("fetch")
            self.assertIsNone(context.device)
            self.assertEqual(backend.invoke("fetch", context, {}), {"download": "fixture"})
            fetch.assert_called_once_with(context.s, device.binding)

    def test_row_diagnostic_reports_unknown_stock_hash_and_scrubs_serials(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot):
                fake = FakeADB(slot=slot, readable=True)
                fake.values.update(props(slot))
                fake.values["ro.product.model"] = "echo JCAOTHER001"
                collector = slot_report.Collector("fake-adb", TARGET, o.PROFILE)
                with patch.object(slot_report.subprocess, "run", side_effect=fake):
                    report = collector.collect()
                self.assertEqual(report["collection_status"], "complete")
                comparison = report["profile_comparison"]
                self.assertEqual(comparison["compared_profile"], ROW["id"])
                self.assertTrue(comparison["firmware_matches"])
                self.assertTrue(comparison["bootloader_matches"])
                self.assertFalse(comparison["dsu_root_supported"])
                self.assertFalse(comparison["root_method_hardware_verified"])
                self.assertFalse(comparison["active_slot_matches_installer"])
                self.assertEqual(comparison["unlock_hardware_verified"], slot == "_a")
                self.assertEqual(comparison["fastboot_root_hardware_reported"], slot == "_a")
                for partition in ("init_boot_a", "init_boot_b"):
                    result = report["partitions"][partition]["sha256"]
                    self.assertFalse(result["reference_stock_image_available"])
                    self.assertIsNone(result["matches_reference_stock_image"])
                self.assertNotIn("JCA", json.dumps(report))
                self.assertIn(props()["ro.serialno"], json.dumps(collector.records))

    def test_row_notices_never_claim_dsu_verification(self):
        for slot in ("_a", "_b"):
            notice = guide_core.slot_notice(props(slot))
            self.assertIn("Guided DSU rooting is unavailable", notice)
            self.assertNotIn("root have been", notice)


if __name__ == "__main__":
    unittest.main()

"""Read-only transport allowlist, failure handling and report privacy."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import slot_report as sr

PROFILE = json.loads((sr.ROOT / "profile.json").read_text())
SERIAL = "JUSADIAGNOSTIC001"
BOOT = "11111111-2222-3333-4444-555555555555"
TARGET = "192.0.2.44:5555"
NODES = dict(zip(sr.PARTITIONS, (22, 23, 20, 21, 5, 6, 12, 13, 25, 26, 27, 28)))


class FakeADB:
    def __init__(self, slot="_a", readable=False):
        self.values = {key: "" for key in sr.PROPERTIES}
        self.values.update({
            "ro.serialno": SERIAL, "boot_id": BOOT,
            "ro.product.device": "jarvis2", "ro.product.model": "onn 4K Pro Streaming Device",
            "ro.build.fingerprint": PROFILE["fingerprint"], "ro.bootloader": PROFILE["bootloader"],
            "ro.boot.slot_suffix": slot, "ro.product.cpu.abilist": PROFILE["abi"],
            "ro.boot.flash.locked": "1", "ro.boot.vbmeta.device_state": "locked",
            "ro.boot.verifiedbootstate": "green", "sys.boot_completed": "1",
        })
        self.readable = readable
        self.calls = []
        self.queries = []
        self.identity_count = 0
        self.overrides = {}
        self.final_changes = {}
        self.guard_fails = False
        self.connect_unauthorized = False

    def __call__(self, argv, **kwargs):
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 10
        self.calls.append(argv)
        if argv == ["fake-adb", "connect", TARGET]:
            output = "failed to authenticate to " + TARGET if self.connect_unauthorized else "connected to " + TARGET
            return subprocess.CompletedProcess(argv, 0, output, "")
        assert argv[:4] == ["fake-adb", "-s", TARGET, "shell"], "Unselected target or non-shell ADB action"
        assert len(argv) == 5
        script = argv[4]
        if script == sr.IDENTITY_COMMAND:
            self.identity_count += 1
            values = dict(self.values)
            if self.identity_count > 1:
                values.update(self.final_changes)
            output = "\n".join(key + "=" + value for key, value in values.items()) + "\n"
            return subprocess.CompletedProcess(argv, 0, output, "")
        guard, command = script.rsplit("\n", 1)
        assert "|| exit 90" in guard and SERIAL in guard and BOOT in guard
        query = tuple(shlex.split(command))
        self.queries.append(query)
        if self.guard_fails:
            return subprocess.CompletedProcess(argv, 90, "", "")
        if query in self.overrides:
            value = self.overrides[query]
            if isinstance(value, BaseException):
                raise value
            return subprocess.CompletedProcess(argv, *value)
        if query == ("id", "-u"):
            response = (0, "2000\n", "")
        elif query == ("getenforce",):
            response = (0, "Enforcing\n", "")
        elif query == ("cat", "/proc/partitions"):
            response = (0, "179 22 8192 mmcblk0p22\n179 23 8192 mmcblk0p23\n", "") if self.readable else (
                1, "", "Permission denied " + SERIAL + " " + BOOT + " " + TARGET)
        elif query[0] == "readlink":
            assert query[:2] == ("readlink", "-f")
            name = query[2].removeprefix("/dev/block/by-name/")
            assert name in NODES and len(query) == 3
            response = (0, "/dev/block/mmcblk0p" + str(NODES[name]) + "\n", "")
        elif query[0] == "cat":
            assert len(query) == 2 and query[1] in {
                f"/sys/class/block/mmcblk0p{node}/size" for node in NODES.values()}
            response = (0, "16384\n", "") if self.readable else (1, "", "Permission denied")
        elif query[0] == "sha256sum":
            assert self.readable and query[1] in ("/dev/block/by-name/init_boot_a", "/dev/block/by-name/init_boot_b")
            assert len(query) == 2
            response = (0, PROFILE["init_boot_sha256"] + "  " + query[1] + "\n", "")
        elif query[0] == "bootctl":
            allowed = {("bootctl", command) for command in (
                "get-number-slots", "get-current-slot", "get-active-boot-slot", "get-snapshot-merge-status")}
            allowed |= {("bootctl", command, slot) for command in
                        ("is-slot-bootable", "is-slot-marked-successful") for slot in ("0", "1")}
            assert query in allowed, "Mutating or unknown bootctl command"
            if self.readable:
                output = {"get-number-slots": "2\n", "get-current-slot": "0\n",
                          "get-active-boot-slot": "0\n", "get-snapshot-merge-status": "none\n"}.get(query[1], "")
                response = (0, output, "")
            else:
                response = (127, "", "bootctl: not found " + TARGET + " " + SERIAL)
        elif query == ("update_engine_client", "--status"):
            response = (0, "CURRENT_OP=UPDATE_STATUS_IDLE\nUPDATE_URL=https://private.example/" + SERIAL, "")
        elif query == ("gsi_tool", "status"):
            response = (0, "normal\n", "")
        else:
            raise AssertionError("Unexpected or mutating device query: " + repr(query))
        return subprocess.CompletedProcess(argv, *response)


class SlotReportTests(unittest.TestCase):
    def collect(self, fake, **kwargs):
        collector = sr.Collector("fake-adb", TARGET, PROFILE, **kwargs)
        with patch.object(sr.subprocess, "run", side_effect=fake):
            return collector, collector.collect()

    def test_locked_slot_a_collects_without_installer_or_root_and_keeps_unknowns(self):
        fake = FakeADB()
        collector, report = self.collect(fake)
        self.assertEqual(report["collection_status"], "complete")
        self.assertTrue(report["profile_comparison"]["active_slot_a"])
        self.assertTrue(report["profile_comparison"]["active_slot_matches_installer"])
        self.assertEqual(report["profile_comparison"]["installer_supported_slots"], ["_a", "_b"])
        self.assertTrue(report["profile_comparison"]["root_method_hardware_verified"])
        self.assertTrue(report["profile_comparison"]["unlock_hardware_verified"])
        self.assertFalse(report["installation_support_established"])
        entry = report["partitions"]["init_boot_a"]
        self.assertEqual(entry["mapping"]["value"], "/dev/block/mmcblk0p22")
        self.assertEqual(entry["size"]["status"], "permission_denied")
        self.assertEqual(entry["sha256"]["status"], "size_unverified")
        self.assertEqual(report["boot_control"]["number_slots"]["status"], "unavailable")
        public = json.dumps(report) + sr.text_report(report)
        for private in (SERIAL, BOOT, TARGET, TARGET.split(":")[0], "UPDATE_URL", "private.example"):
            self.assertNotIn(private, public)
        self.assertIn(SERIAL, json.dumps(collector.records))
        self.assertFalse(any(query[0] == "sha256sum" for query in fake.queries))

    def test_slot_b_report_also_works(self):
        _, report = self.collect(FakeADB(slot="_b"))
        self.assertEqual(report["collection_status"], "complete")
        self.assertFalse(report["profile_comparison"]["active_slot_a"])
        self.assertTrue(report["profile_comparison"]["active_slot_matches_installer"])
        self.assertTrue(report["profile_comparison"]["root_method_hardware_verified"])

    def test_readable_metadata_and_only_bounded_init_boot_hashes(self):
        fake = FakeADB(readable=True)
        _, report = self.collect(fake)
        for slot in ("a", "b"):
            entry = report["partitions"]["init_boot_" + slot]
            self.assertEqual(entry["size"]["bytes"], 8388608)
            self.assertTrue(entry["sha256"]["matches_reference_stock_image"])
        self.assertEqual(sum(query[0] == "sha256sum" for query in fake.queries), 2)
        self.assertEqual(report["boot_control"]["get-snapshot-merge-status"]["value"], "none")

    def test_proc_sizes_fallback_and_unexpected_size_prevents_hash(self):
        fake = FakeADB(readable=True)
        fake.overrides[("cat", "/sys/class/block/mmcblk0p22/size")] = (1, "", "Permission denied")
        fake.overrides[("cat", "/sys/class/block/mmcblk0p23/size")] = (0, "999999999\n", "")
        _, report = self.collect(fake)
        self.assertEqual(report["partitions"]["init_boot_a"]["size"]["source"], "proc_partitions")
        self.assertEqual(report["partitions"]["init_boot_b"]["sha256"]["status"], "unexpected_size")
        self.assertNotIn(("sha256sum", "/dev/block/by-name/init_boot_b"), fake.queries)

    def test_unexpected_mapping_is_never_used_as_a_command_argument(self):
        fake = FakeADB(readable=True)
        fake.overrides[("readlink", "-f", "/dev/block/by-name/init_boot_a")] = (0, "/dev/block/mmcblk0; reboot\n", "")
        _, report = self.collect(fake)
        self.assertEqual(report["partitions"]["init_boot_a"]["mapping"]["status"], "unrecognized_output")
        self.assertNotIn(("sha256sum", "/dev/block/by-name/init_boot_a"), fake.queries)

    def test_bootctl_nonzero_is_unknown_not_a_false_bootability_claim(self):
        fake = FakeADB(readable=True)
        fake.overrides[("bootctl", "is-slot-bootable", "1")] = (70, "", "Error calling isSlotBootable()")
        _, report = self.collect(fake)
        self.assertEqual(report["boot_control"]["is-slot-bootable-1"], {"status": "query_failed"})

    def test_new_firmware_is_reported_without_enabling_installation(self):
        fake = FakeADB()
        fake.values["ro.build.fingerprint"] = "onn/jarvis2/jarvis2:14/OTHER/999:user/release-keys"
        _, report = self.collect(fake)
        self.assertEqual(report["collection_status"], "complete")
        self.assertFalse(report["profile_comparison"]["firmware_matches"])
        self.assertFalse(report["profile_comparison"]["root_method_hardware_verified"])
        self.assertFalse(report["profile_comparison"]["unlock_hardware_verified"])
        self.assertFalse(report["installation_support_established"])

    def test_wrong_model_or_expected_serial_stops_before_probes(self):
        fake = FakeADB()
        fake.values["ro.product.device"] = "SNA"
        _, report = self.collect(fake)
        self.assertEqual(report["collection_status"], "stopped")
        self.assertEqual(fake.queries, [])
        fake = FakeADB()
        _, report = self.collect(fake, expected_serial="JUSAOTHER")
        self.assertEqual(report["collection_status"], "stopped")
        self.assertEqual(fake.queries, [])

    def test_unauthorized_does_not_retry_or_read_device(self):
        fake = FakeADB()
        fake.connect_unauthorized = True
        _, report = self.collect(fake)
        self.assertEqual(report["collection_status"], "stopped")
        self.assertEqual(len(fake.calls), 1)
        self.assertNotIn(TARGET, json.dumps(report))

    def test_guard_failure_stops_immediately(self):
        fake = FakeADB()
        fake.guard_fails = True
        _, report = self.collect(fake)
        self.assertEqual(report["collection_status"], "stopped")
        self.assertEqual(len(fake.queries), 1)

    def test_final_identity_slot_firmware_and_boot_changes_reject_completion(self):
        for key, value in (("ro.serialno", "JUSAOTHER"), ("ro.boot.slot_suffix", "_b"),
                           ("ro.build.fingerprint", "other"), ("ro.bootloader", "other"),
                           ("boot_id", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")):
            with self.subTest(key=key):
                fake = FakeADB()
                fake.final_changes = {key: value}
                _, report = self.collect(fake)
                self.assertEqual(report["collection_status"], "stopped")

    def test_timeout_is_recorded_once_without_retry(self):
        fake = FakeADB()
        query = ("update_engine_client", "--status")
        fake.overrides[query] = subprocess.TimeoutExpired("fake-adb", 10, output=SERIAL.encode())
        _, report = self.collect(fake)
        self.assertEqual(report["update_engine"]["status"], "timeout")
        self.assertEqual(fake.queries.count(query), 1)
        self.assertNotIn(SERIAL, json.dumps(report))

    def test_private_binding_echo_in_properties_is_removed(self):
        fake = FakeADB()
        fake.values["ro.product.model"] = SERIAL + " " + BOOT + " " + TARGET
        _, report = self.collect(fake)
        for value in (SERIAL, BOOT, TARGET):
            self.assertNotIn(value, json.dumps(report))

    def test_invalid_targets_and_serials_cannot_be_shell_fragments(self):
        self.assertEqual(sr.network_target("192.0.2.44"), TARGET)
        for value in ("192.0.2.44; reboot", "$(id)", "-s", "192.0.2.44:99999", "hostname"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                sr.network_target(value)
        with self.assertRaises(argparse.ArgumentTypeError):
            sr.hardware_serial('"')

    def test_cli_private_files_shareable_reports_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "report"
            fake = FakeADB()
            args = ["--target", TARGET, "--output-dir", str(output), "--expect-serial", SERIAL]
            with patch.object(sr.shutil, "which", return_value="fake-adb"), \
                 patch.object(sr.subprocess, "run", side_effect=fake), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(sr.main(args), 0)
                original = (output / "private.json").read_bytes()
                call_count = len(fake.calls)
                with self.assertRaises(SystemExit):
                    sr.main(args)
                self.assertEqual(len(fake.calls), call_count)
                self.assertEqual((output / "private.json").read_bytes(), original)
            self.assertIn(SERIAL, original.decode())
            for name in ("slot-report.json", "slot-report.txt"):
                text = (output / name).read_text()
                for secret in (SERIAL, TARGET, BOOT, str(output)):
                    self.assertNotIn(secret, text)
                self.assertEqual((output / name).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

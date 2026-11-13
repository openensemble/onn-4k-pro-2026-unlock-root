"""Recovery transactions and restaging use a strict fake device, never live ADB."""
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import connection
import dsu_recovery as recovery
import guide as g
import onn2026 as o
import root_workflow as flow

BINDING = {"ro.serialno": "JUSARESTARTTEST", "ro.product.device": o.PROFILE["device"],
           "ro.build.fingerprint": o.PROFILE["fingerprint"], "ro.bootloader": o.PROFILE["bootloader"],
           "ro.boot.slot_suffix": "_b"}
ARCHIVE = "/storage/emulated/0/Download/onn26-stock-123456789abc.raw.gz"


class Android:
    def __init__(self, work):
        self.work = work
        self.binding = dict(BINDING)
        self.props = {**self.binding, **o.UNLOCKED, "ro.product.cpu.abilist": o.PROFILE["abi"],
                      "sys.boot_completed": "1"}
        self.s = o.Session(work, "restart-fixture")
        self.boot_number = 1
        self.android_id = "123456789abcdef"
        self.state = "installed\ndisabled"
        self.files = {ARCHIVE: b"part of an interrupted upload"}
        self.symlinks = set()
        self.root = False
        self.calls, self.writes = [], []
        self.hook = lambda label: None
        self.log = ""

    def prepare(self, phase):
        self.s = o.Session(self.work, "fixture-" + phase)
        return SimpleNamespace(s=self.s, device=self)

    def identify(self, unlocked=False):
        binding = o.validate_identity(self.props, unlocked)
        o.require(binding == self.binding, "Physical device or firmware differs from the saved binding")
        return self.props

    def boot_id(self):
        return f"11111111-2222-3333-4444-{self.boot_number:012d}"

    def text(self, label, *args, **kwargs):
        self.calls.append((label, args))
        self.hook(label)
        if args == ("gsi_tool", "status"):
            return self.state
        if args == ("settings", "get", "secure", "android_id"):
            return self.android_id
        if label == "restart-downloads":
            return "\n".join(p for p in self.files if p.startswith("/storage/emulated/0/Download/onn26-"))
        if args[:3] == ("stat", "-c", "%s"):
            return str(len(self.files[args[3]]))
        if args[0] == "sha256sum":
            return hashlib.sha256(self.files[args[1]]).hexdigest() + "  " + args[1]
        if args[0] == "logcat":
            return self.log
        raise AssertionError((label, args))

    def shell(self, label, *args, **kwargs):
        self.calls.append((label, args))
        self.hook(label)
        result = {"returncode": 0, "timed_out": False, "stdout": ""}
        if label == "restart-existing-root":
            result["returncode"] = 0 if self.root else 1
        elif label == "restart-download-type":
            result["returncode"] = int(any(p in args[-1] for p in self.symlinks))
        elif label == "staging-name-unused":
            result["returncode"] = int(args[-1] in self.files)
        elif label in ("stop-dsu-installer", "remove-abandoned-dsu", "remove-abandoned-upload"):
            assert recovery.pending(recovery.directory(self)), "Missing durable recovery intent"
            self.writes.append(label)
            if label == "remove-abandoned-dsu":
                assert args == ("gsi_tool", "wipe")
                self.state = "normal"
            if label == "remove-abandoned-upload":
                name = shlex.split(args[-1])[-1]
                assert recovery.DOWNLOAD.fullmatch(name)
                assert hashlib.sha256(self.files[name]).hexdigest() in args[-1], "Changed upload must not be deleted"
                del self.files[name]
        elif label == "stage-disabled-dsu":
            self.writes.append(label)
            self.state = "installed\ndisabled"
            name = args[args.index("-d") + 1].rsplit("/", 1)[1]
            self.log = name + "\nstatus: READY, cause: INSTALL_COMPLETED"
        elif label == "enable-single-use-dsu":
            self.writes.append(label)
            self.state = "installed\nenabled"
        elif label != "flush-single-use-state":
            raise AssertionError((label, args))
        return result

    def call(self, label, *args, **kwargs):
        self.calls.append((label, args))
        self.hook(label)
        self.writes.append(label)
        if label == "push-dsu":
            self.files[args[2]] = Path(args[1]).read_bytes()
        elif label == "single-dsu-reboot":
            self.boot_number += 1
            self.state = "running\ninstalled\ndisabled"
        else:
            raise AssertionError((label, args))
        return {"returncode": 0, "timed_out": False, "stdout": ""}


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.device = Android(self.work)
        self.base = recovery.directory(self.device)
        self.connection = self.base / "connection.json"
        self.record = {"schema": connection.SCHEMA, "binding": dict(BINDING),
                       "target": "192.0.2.41:5555", "name": "Fixture"}
        o.save(self.connection, self.record)
        self.path = self.base / "guide.json"
        self.state = g.initial_state(self.record, self.connection)
        self.state["consent"] = {"version": g.RISK_VERSION, "binding": dict(BINDING),
                                 "factory_reset_and_data_loss": True, "possible_permanent_brick": True}
        self.state["phase"] = "stock_stage"
        self.state["pending"] = {"phase": "stock_stage", "receipt": str(self.work / "lost-result.json")}
        o.save(self.path, self.state)
        self.attempt = self.base / "dsu-stock-stage-attempt.json"
        o.save(self.attempt, {"binding": dict(BINDING), "android_id": self.device.android_id,
                              "boot_id": self.device.boot_id(), "remote": ARCHIVE})
        self.messages = []
        self.ui = g.UI(read=lambda prompt: "", choose=lambda: True, emit=self.messages.append, clear=lambda: None)
        for owner, name in ((o.subprocess, "run"), (connection.socket, "create_connection")):
            guard = patch.object(owner, name, side_effect=AssertionError("Live I/O forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def restart(self):
        state = recovery.read(self.path) if self.path.exists() else self.state
        return recovery.restart(self.device, self.path, state, self.record, self.connection, self.ui)

    def test_restart_keeps_attempts_backups_and_old_progress(self):
        old_guide, old_attempt = self.path.read_bytes(), self.attempt.read_bytes()
        unlock = self.base / "unlock-attempt.json"
        backup = self.work / "stock-backup.img"
        o.save(unlock, {"consumed": True})
        backup.write_bytes(b"keep the original backup")
        self.device.files["/storage/emulated/0/Download/holiday.zip"] = b"personal file"
        result = self.restart()
        self.assertEqual(result["phase"], "consent")
        self.assertIsNone(result["pending"])
        self.assertEqual(self.device.state, "normal")
        self.assertNotIn(ARCHIVE, self.device.files)
        self.assertIn("/storage/emulated/0/Download/holiday.zip", self.device.files)
        self.assertEqual(self.attempt.read_bytes(), old_attempt)
        self.assertEqual(recovery.read(unlock), {"consumed": True})
        self.assertEqual(backup.read_bytes(), b"keep the original backup")
        archived = self.base / "dsu-restarts" / result["dsu_restart"]
        self.assertEqual((archived / "previous-guide.json").read_bytes(), old_guide)
        self.assertTrue(recovery.read(archived / "completed.json")["temporary_cleanup_verified"])
        self.assertEqual(self.device.writes, ["stop-dsu-installer", "remove-abandoned-dsu", "remove-abandoned-upload"])
        self.assertNotEqual(flow.journal(self.device, self.attempt.name), self.attempt)
        self.assertIsNone(recovery.pending(self.base))

    def test_fresh_clone_removes_disabled_image_and_partial_upload_without_receipts(self):
        self.path.unlink()
        self.attempt.unlink()
        self.state = g.initial_state(self.record, self.connection)
        self.assertEqual(self.restart()["phase"], "consent")
        self.assertFalse(self.device.files)
        self.assertEqual(self.device.state, "normal")

    def test_interrupted_upload_with_no_installed_dsu_can_be_discarded(self):
        self.device.state = "normal"
        self.restart()
        self.assertFalse(self.device.files)

    def test_declining_leaves_device_and_guide_untouched(self):
        self.ui.choose = lambda: False
        before = self.path.read_bytes()
        with self.assertRaises(g.Paused):
            self.restart()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.device.writes, [])
        self.assertIsNone(recovery.pending(self.base))

    def test_running_enabled_or_unknown_dsu_is_rejected(self):
        for state in ("running\ninstalled\ndisabled", "installed\nenabled", "", "unknown"):
            with self.subTest(state=state):
                self.device.state = state
                with self.assertRaises(o.Stop):
                    self.restart()
        self.assertFalse(self.device.writes)

    def test_permanent_attempt_or_completed_guide_is_never_cleared(self):
        for name in ("root-install-attempt.json", "normal-install-reboot-attempt.json"):
            with self.subTest(name=name):
                attempt = self.base / name
                o.save(attempt, {"uncertain": True})
                with self.assertRaisesRegex(o.Stop, "permanent-root installation was attempted"):
                    self.restart()
                self.assertTrue(attempt.exists())
                attempt.unlink()  # Only this synthetic fixture is reset between cases.
        self.state["phase"] = "install_reboot"
        o.save(self.path, self.state)
        with self.assertRaises(o.Stop):
            self.restart()
        self.assertFalse(self.device.writes)

    def test_failed_preflight_can_restart_but_firmware_attempt_cannot(self):
        self.state.update(phase="install", pending={"phase": "install", "receipt": str(self.work / "preflight.json")})
        o.save(self.path, self.state)
        self.assertEqual(self.restart()["phase"], "consent")
        # No root-install-attempt exists: the original stop was before firmware writing.
        self.assertFalse((self.base / "root-install-attempt.json").exists())

    def test_rooted_device_is_not_restarted_even_with_lost_host_records(self):
        self.device.root = True
        with self.assertRaisesRegex(o.Stop, "Permanent root is present"):
            self.restart()
        self.assertFalse(self.device.writes)

    def test_binding_and_original_android_changes_are_rejected(self):
        for field in o.IDENTITY:
            with self.subTest(field=field):
                old = self.device.props[field]
                self.device.props[field] = "other"
                with self.assertRaises(o.Stop):
                    self.restart()
                self.device.props[field] = old
        self.device.android_id = "ffff"
        with self.assertRaisesRegex(o.Stop, "Original Android differs"):
            self.restart()
        self.assertFalse(self.device.writes)

    def test_unresolved_reboot_on_same_boot_is_not_replayed_or_abandoned(self):
        o.save(self.base / "dsu-stock-reboot-attempt.json", {
            "binding": dict(BINDING), "boot_id_before": self.device.boot_id()})
        with self.assertRaisesRegex(o.Stop, "same boot and is unresolved"):
            self.restart()
        self.assertFalse(self.device.writes)
        self.device.boot_number += 1  # The owner has returned to original Android.
        self.restart()

    def test_changed_boot_after_confirmation_stops_before_cleanup(self):
        def accept():
            self.device.boot_number += 1
            return True
        self.ui.choose = accept
        with self.assertRaisesRegex(o.Stop, "Boot changed"):
            self.restart()
        self.assertFalse(self.device.writes)

    def test_changed_archive_after_confirmation_is_not_removed(self):
        def accept():
            self.device.files[ARCHIVE] = b"changed during prompt"
            return True
        self.ui.choose = accept
        with self.assertRaisesRegex(o.Stop, "downloads changed"):
            self.restart()
        self.assertFalse(self.device.writes)

    def test_symlink_or_unexpected_name_is_not_deleted(self):
        self.device.symlinks.add(ARCHIVE)
        with self.assertRaisesRegex(o.Stop, "regular file"):
            self.restart()
        self.device.symlinks.clear()
        self.device.files["/storage/emulated/0/Download/onn26-unrelated.raw.gz"] = b"unrecognized"
        with self.assertRaisesRegex(o.Stop, "Unexpected temporary download"):
            self.restart()
        self.assertFalse(self.device.writes)

    def test_wipe_timeout_keeps_guide_pending_and_blocks_new_staging(self):
        before = self.path.read_bytes()
        def fail(label):
            if label == "remove-abandoned-dsu":
                raise o.Stop("simulated wipe timeout")
        self.device.hook = fail
        with self.assertRaisesRegex(o.Stop, "wipe timeout"):
            self.restart()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(recovery.pending(self.base))
        self.assertIn(ARCHIVE, self.device.files)
        with self.assertRaisesRegex(o.Stop, "restart is unfinished"):
            flow.journal(self.device, "dsu-stock-stage-attempt.json")
        self.device.hook = lambda label: None
        self.ui.choose = lambda: False
        with self.assertRaises(g.Paused):
            self.restart()
        self.assertNotIn("remove-abandoned-dsu", self.device.writes)
        self.ui.choose = lambda: True
        self.restart()

    def test_disconnect_after_wipe_preserves_old_guide_and_requires_confirmation(self):
        def fail(label):
            if label == "remove-abandoned-upload":
                raise o.Stop("simulated connection loss")
        self.device.hook = fail
        with self.assertRaises(o.Stop):
            self.restart()
        self.assertEqual(self.device.state, "normal")
        self.assertEqual(recovery.read(self.path)["phase"], "stock_stage")
        self.assertTrue(self.attempt.exists())
        self.device.hook = lambda label: None
        self.restart()
        self.assertIsNone(recovery.pending(self.base))

    def test_host_interruption_after_cleanup_finishes_without_repeating_device_actions(self):
        replace = connection.replace_record
        for name in ("dsu-cycle.json", "guide.json", "dsu-restart.json"):
            with self.subTest(name=name):
                failed = False
                def fail(path, value):
                    nonlocal failed
                    if Path(path).name == name and (name != "dsu-restart.json" or value["complete"]):
                        failed = True
                        raise OSError("simulated host interruption")
                    replace(path, value)
                with patch.object(connection, "replace_record", side_effect=fail):
                    with self.assertRaisesRegex(OSError, "host interruption"):
                        self.restart()
                self.assertTrue(failed)
                before = list(self.device.writes)
                self.restart()
                self.assertEqual(self.device.writes, before)
                self.assertIsNone(recovery.pending(self.base))

    def test_pending_restart_can_resume_after_work_folder_moves(self):
        replace = connection.replace_record
        for phase in ("before_cleanup", "after_guide_update"):
            with self.subTest(phase=phase):
                def fail_record(path, value):
                    if Path(path).name == "dsu-restart.json" and value["complete"]:
                        raise OSError("simulated interruption after guide update")
                    replace(path, value)
                def fail_device(label):
                    if label == "remove-abandoned-dsu":
                        raise OSError("simulated interruption before cleanup")
                self.device.hook = fail_device if phase == "before_cleanup" else lambda label: None
                with patch.object(connection, "replace_record", side_effect=fail_record):
                    with self.assertRaisesRegex(OSError, "simulated interruption"):
                        self.restart()
                moved = self.work / "relocated"
                new_base = moved / "devices" / self.base.name
                new_base.parent.mkdir(parents=True)
                self.base.rename(new_base)
                self.work = self.device.work = moved
                self.base, self.path, self.connection = new_base, new_base / "guide.json", new_base / "connection.json"
                self.device.hook = lambda label: None
                before = list(self.device.writes)
                if phase == "after_guide_update":
                    with patch.object(connection, "replace_record", side_effect=fail_record):
                        with self.assertRaisesRegex(OSError, "after guide update"):
                            self.restart()
                result = self.restart()
                self.assertEqual(result["connection_file"], str(self.connection.resolve()))
                self.assertEqual(recovery.read(self.path)["connection_file"], str(self.connection.resolve()))
                self.assertIsNone(recovery.pending(self.base))
                if phase == "after_guide_update":
                    self.assertEqual(self.device.writes, before)

    def test_retired_receipts_cannot_boot_or_record_rehearsal(self):
        stage = self.work / "old-stage.json"
        o.save(stage, {"binding": dict(BINDING), "kind": "stock", "android_id": self.device.android_id})
        self.restart()
        before = list(self.device.writes)
        for operation in (flow.dsu_status, flow.boot_dsu, flow.stock_return):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(o.Stop, "retired"):
                operation(self.device, SimpleNamespace(stage=stage))
        with patch.object(flow, "patch_receipt", return_value=({}, None)):
            with self.assertRaisesRegex(o.Stop, "retired"):
                flow.install_root(self.device, SimpleNamespace(stage=stage, patch_result=None, accept_unproven_recovery=True))
        self.assertEqual(self.device.writes, before)

    def test_restart_then_reupload_rehearse_and_boot_root_on_both_slots(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot), tempfile.TemporaryDirectory() as work:
                device = Android(work)
                device.binding["ro.boot.slot_suffix"] = slot
                device.props["ro.boot.slot_suffix"] = slot
                record = {**self.record, "binding": dict(device.binding)}
                base = recovery.directory(device)
                selected = base / "connection.json"
                o.save(selected, record)
                recovery.restart(device, base / "guide.json", g.initial_state(record, selected), record, selected, self.ui)
                build = {"serial": device.binding["ro.serialno"], "profile": o.slot_profile(device.binding)["id"],
                         "fingerprint": o.PROFILE["fingerprint"], "bootloader": o.PROFILE["bootloader"], "slot": slot,
                         "single_use_required": True, "root_helper_autostart": False, "system_bytes": 100,
                         **{key: True for key in ("all_original_inode_metadata_preserved", "all_file_bytes_verified",
                                                  "policy_compiled", "avb_verified")}}
                for kind in ("stock", "root"):
                    archive = Path(work) / (kind + ".gz")
                    archive.write_bytes(kind.encode())
                    build[kind + "_gzip"], build[kind + "_gzip_sha256"] = str(archive), o.digest(archive)
                build_path = Path(work) / "build.json"
                o.save(build_path, build)
                rehearsal = None
                for kind in ("stock", "root"):
                    device.prepare(kind)
                    result = flow.stage_dsu(device, SimpleNamespace(build=build_path, kind=kind, rehearsal=rehearsal))
                    args = SimpleNamespace(stage=Path(result["stage"]), observed_setup=True, power_cycled_back=True)
                    self.assertTrue(flow.dsu_status(device, args)["installed_disabled_verified"])
                    self.assertEqual(flow.boot_dsu(device, args)["reboot_requests"], 1)
                    if kind == "stock":
                        device.boot_number += 1
                        device.state = "installed\ndisabled"
                        rehearsal = Path(flow.stock_return(device, args)["rehearsal"])
                self.assertEqual(device.state, "running\ninstalled\ndisabled")
                self.assertEqual(device.writes.count("push-dsu"), 2)
                self.assertEqual(device.writes.count("single-dsu-reboot"), 2)

    def test_guided_flag_and_interrupted_stage_offer_a_confirmed_restart(self):
        for explicit in (True, False):
            with self.subTest(explicit=explicit):
                o.save(self.path, self.state)
                self.device.state = "installed\ndisabled"
                self.device.files[ARCHIVE] = b"partial upload"
                args = o.parser().parse_args(["--work", str(self.work), "--connection", str(self.connection),
                                              "guide", *(["--restart-dsu"] if explicit else [])])
                entered = []
                original_run = g.Guide.run
                def run(guide):
                    entered.append(guide.state["phase"])
                    if guide.state["phase"] != "consent":
                        return original_run(guide)
                with patch.object(g.sys.stdin, "isatty", return_value=True), patch.object(o, "verify_files"), \
                     patch.object(o, "ROOT", self.work), patch.object(g.shutil, "which", return_value="fake-adb"), \
                     patch.object(g, "Backend", return_value=self.device), patch.object(g.Guide, "run", new=run):
                    self.assertEqual(g.start(args, self.ui), 0)
                self.assertEqual(entered[-1], "consent")
                self.assertEqual(self.device.state, "normal")
                self.assertIn("Remove temporary system and start again", "\n".join(self.messages))

    def test_r_menu_can_restart_before_a_preflight_failure_is_replayed(self):
        self.state.update(phase="install", pending={"phase": "install", "receipt": str(self.work / "missing.json")})
        o.save(self.path, self.state)
        self.ui.read = lambda prompt: "r"
        args = o.parser().parse_args(["--work", str(self.work), "--connection", str(self.connection), "guide"])
        with patch.object(g.sys.stdin, "isatty", return_value=True), patch.object(o, "verify_files"), \
             patch.object(o, "ROOT", self.work), patch.object(g.shutil, "which", return_value="fake-adb"), \
             patch.object(g, "Backend", return_value=self.device), patch.object(g.Guide, "run") as run:
            self.assertEqual(g.start(args, self.ui), 0)
        run.assert_called_once()
        self.assertEqual(recovery.read(self.path)["phase"], "consent")

    def test_pending_restart_is_offered_before_old_guide_can_run(self):
        def fail(label):
            if label == "remove-abandoned-dsu":
                raise o.Stop("simulated timeout")
        self.device.hook = fail
        with self.assertRaises(o.Stop):
            self.restart()
        self.device.hook = lambda label: None
        self.ui.choose = lambda: False
        before = list(self.device.writes)
        args = o.parser().parse_args(["--work", str(self.work), "--connection", str(self.connection), "guide"])
        with patch.object(g.sys.stdin, "isatty", return_value=True), patch.object(o, "verify_files"), \
             patch.object(o, "ROOT", self.work), patch.object(g.shutil, "which", return_value="fake-adb"), \
             patch.object(g, "Backend", return_value=self.device), \
             patch.object(g.Guide, "run", side_effect=AssertionError("Old guide must not run")):
            self.assertEqual(g.start(args, self.ui), 0)
        self.assertEqual(self.device.writes, before)

    def test_incomplete_wipe_does_not_delete_uploads_or_reset_guide(self):
        wiped = False
        def incomplete(label):
            nonlocal wiped
            if label == "remove-abandoned-dsu":
                wiped = True
            elif wiped and label == "restart-dsu-state":
                self.device.state = "installed\ndisabled"
        self.device.hook = incomplete
        with self.assertRaisesRegex(o.Stop, "removal is not confirmed"):
            self.restart()
        self.assertIn(ARCHIVE, self.device.files)
        self.assertEqual(recovery.read(self.path)["phase"], "stock_stage")

    def test_second_device_records_and_files_are_untouched(self):
        other = Android(self.work)
        other.binding["ro.serialno"] = "JUSAOTHERUNIT"
        other_base = recovery.directory(other)
        o.save(other_base / "root-install-attempt.json", {"consumed": True})
        self.restart()
        self.assertEqual(recovery.read(other_base / "root-install-attempt.json"), {"consumed": True})
        self.assertIn(ARCHIVE, other.files)

    def test_old_rehearsal_cannot_authorize_root_staging_in_new_cycle(self):
        self.restart()
        build = self.work / "build.json"
        rehearsal = self.work / "old-rehearsal.json"
        o.save(build, {})
        o.save(rehearsal, {"binding": dict(BINDING), "stock_single_use_return_verified": True,
                           "build_sha256": o.digest(build), "android_id": self.device.android_id})
        before = list(self.device.writes)
        with patch.object(flow, "build_matches"):
            with self.assertRaisesRegex(o.Stop, "retired"):
                flow.stage_dsu(self.device, SimpleNamespace(build=build, kind="root", rehearsal=rehearsal))
        self.assertEqual(self.device.writes, before)

    def test_changed_restart_template_is_rejected_before_further_cleanup(self):
        def fail(label):
            if label == "remove-abandoned-dsu":
                raise o.Stop("simulated timeout")
        self.device.hook = fail
        with self.assertRaises(o.Stop):
            self.restart()
        transaction = recovery.pending(self.base)
        template = self.base / "dsu-restarts" / transaction["cycle"] / "new-guide.json"
        replacement = recovery.read(template)
        replacement["phase"] = "install"
        o.save(template, replacement)
        before = list(self.device.writes)
        self.device.hook = lambda label: None
        with self.assertRaisesRegex(o.Stop, "restart guide.*changed"):
            self.restart()
        self.assertEqual(self.device.writes, before)

    def test_interrupted_completion_receipt_does_not_strand_recovery(self):
        save = o.save
        def interrupted(path, value, **kwargs):
            if "completed.json" in Path(path).name:
                Path(path).write_text("{")
                raise OSError("simulated interruption while saving completion")
            return save(path, value, **kwargs)
        with patch.object(o, "save", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "saving completion"):
                self.restart()
        self.assertTrue(recovery.pending(self.base))
        self.assertEqual(self.device.state, "normal")
        self.assertEqual(self.restart()["phase"], "consent")

    def test_missing_active_cycle_cannot_reactivate_legacy_receipts(self):
        self.restart()
        (self.base / "dsu-cycle.json").unlink()
        before = list(self.device.writes)
        with self.assertRaisesRegex(o.Stop, "cycle.*missing"):
            recovery.check_stage(self.device, {"binding": dict(BINDING)})
        self.assertEqual(self.device.writes, before)

    def test_missing_both_control_files_still_cannot_reactivate_legacy_receipts(self):
        self.restart()
        (self.base / "dsu-cycle.json").unlink()
        (self.base / "dsu-restart.json").unlink()
        with self.assertRaisesRegex(o.Stop, "cycle.*missing"):
            recovery.check_stage(self.device, {})

    def test_changed_cycle_during_interrupted_restart_is_rejected(self):
        self.restart()
        def fail(label):
            if label == "remove-abandoned-dsu":
                raise o.Stop("simulated timeout")
        self.device.hook = fail
        with self.assertRaises(o.Stop):
            self.restart()
        (self.base / "dsu-cycle.json").unlink()
        self.device.hook = lambda label: None
        before = list(self.device.writes)
        with self.assertRaisesRegex(o.Stop, "cycle changed"):
            self.restart()
        self.assertEqual(self.device.writes, before)

    def test_restarted_guide_completes_with_fresh_preparation_and_no_second_unlock(self):
        from test_guide import FakeBackend
        state = self.restart()
        backend = FakeBackend(self.work, locked=False, binding=self.device.binding)
        g.Guide(self.path, state, backend, self.ui).run()
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(backend.count("unlock"), 0)
        self.assertEqual(backend.count("unlock_reboot"), 0)
        for phase in ("patch", "build", "stock_stage", "stock_boot", "stock_return", "root_stage", "root_boot",
                      "install", "install_reboot", "verify", "cleanup"):
            self.assertEqual(backend.count(phase), 1)
        self.assertTrue(self.attempt.exists())


if __name__ == "__main__":
    unittest.main()

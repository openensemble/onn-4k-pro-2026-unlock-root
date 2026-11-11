"""Exercise the real guide state machine with a transport that cannot contact devices."""
from contextlib import redirect_stdout
from io import StringIO
import hashlib
import json
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import connection
import guide as g
import onn2026 as o

BINDING = {"ro.serialno": "JUSAGUIDEONLY", "ro.product.device": o.PROFILE["device"],
           "ro.build.fingerprint": o.PROFILE["fingerprint"], "ro.bootloader": o.PROFILE["bootloader"],
           "ro.boot.slot_suffix": o.PROFILE["slot"]}


class FakeBackend:
    def __init__(self, work, locked=True):
        self.work, self.locked = Path(work), locked
        self.calls, self.prepared = [], []
        self.failures, self.answers = {}, {}
        self.connection_failure = None
        self.interrupt = None

    def prepare(self, phase):
        self.prepared.append(phase)
        if phase == self.connection_failure:
            raise o.Stop("simulated disconnected device before action")
        return SimpleNamespace(s=o.Session(self.work, "fake-guide-" + phase), device=None)

    def invoke(self, phase, context, values):
        self.calls.append((phase, values))
        if phase in self.failures:
            raise self.failures[phase]
        if phase == self.interrupt:
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        if phase in self.answers:
            answer = self.answers[phase]
            return answer.pop(0) if isinstance(answer, list) else answer
        canned = {
            "host": {"ndk": "/example/ndk", "adb_public_key": "/example/adbkey.pub",
                     "mkfs_erofs": "mkfs.erofs", "fsck_erofs": "fsck.erofs", "secilc": "secilc"},
            "baseline": {"binding": BINDING, "boot_id": "11111111-2222-3333-4444-555555555555",
                         "secure_lock_state": int(self.locked), "unlocked_on_current_boot": not self.locked,
                         "write_invocations": 0},
            "unlock": {"binding": BINDING, "boot_id": "11111111-2222-3333-4444-555555555555",
                       "write_invocations": 1, "secure_lock_state": 0},
            "unlock_reboot": {"reboot_requests": 1, "already_rebooted": False, "transport": {"timed_out": True}},
            "install_reboot": {"reboot_requests": 1, "already_rebooted": False, "transport": {"timed_out": True}},
            "unlocked_check": {"secure_lock_state": 0, "unlocked_on_current_boot": True},
            "fetch": {"stock_init_boot": "/example/stock.img", "magisk_apk": "/example/Magisk.apk"},
            "patch": {"patch_result": "/example/patch.json"}, "build": {"build": "/example/build.json"},
            "stock_stage": {"stage": "/example/stock-stage.json"},
            "root_stage": {"stage": "/example/root-stage.json"},
            "stock_wait": {"installed_disabled_verified": True}, "root_wait": {"installed_disabled_verified": True},
            "stock_boot": {"reboot_requests": 1, "transport": {"timed_out": True}},
            "root_boot": {"reboot_requests": 1, "transport": {"timed_out": True}},
            "stock_return": {"rehearsal": "/example/return.json"}, "root_ready": {"temporary_system_ready": True},
            "install": {"verified_flash": True, "install_result": "/example/install.json", "boot_id": "root-guest"},
            "normal_check": {"boot_id": "original-before-magisk"}, "verify": {"root_verified": True},
            "cleanup": {"cleanup_completed": True}, "updates": {"automatic_system_updates_disabled": True},
            "updates_check": {"automatic_system_updates_disabled": True},
        }
        if phase not in canned:
            raise AssertionError("Unexpected guide operation: " + phase)
        return canned[phase]

    def count(self, phase):
        return sum(name == phase for name, _ in self.calls)


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.path = self.work / "guide.json"
        self.connection_file = self.work / "connection.json"
        self.record = {"schema": connection.SCHEMA, "binding": BINDING, "name": "Living room", "target": "192.0.2.71:5555"}
        o.save(self.connection_file, self.record)
        self.state = g.initial_state(self.record, self.connection_file)
        self.backend = FakeBackend(self.work)
        self.messages, self.prompts = [], []
        self.ui = g.UI(read=self.answer, emit=self.messages.append, choose=lambda: True)
        self.guide = g.Guide(self.path, self.state, self.backend, self.ui)
        self.guide.persist()
        for owner, name in ((o.subprocess, "run"), (connection.socket, "create_connection")):
            guard = patch.object(owner, name, side_effect=AssertionError("Live I/O forbidden in guide tests"))
            guard.start()
            self.addCleanup(guard.stop)

    def answer(self, prompt):
        self.prompts.append(prompt)
        return ""

    def resume(self):
        return g.Guide(self.path, g.load_state(self.path, self.record), self.backend, self.ui)

    def test_complete_walkthrough_manages_all_files_and_acceptances(self):
        self.guide.run()
        self.assertEqual(self.state["phase"], "complete")
        self.assertTrue(g.accepted(self.state))
        self.assertEqual(self.state["consent"]["method"], "accept_decline_buttons")
        self.assertTrue(self.state["install_accepted"])
        for phase in ("unlock", "unlock_reboot", "patch", "stock_stage", "stock_boot", "root_stage", "root_boot",
                      "install", "install_reboot", "cleanup", "updates"):
            self.assertEqual(self.backend.count(phase), 1, phase)
        inputs = dict(self.backend.calls)
        self.assertEqual(inputs["install"]["stage"], "/example/root-stage.json")
        self.assertEqual(inputs["install"]["patch_result"], "/example/patch.json")
        self.assertEqual(inputs["cleanup"]["install_result"], "/example/install.json")
        self.assertTrue(inputs["install"]["accept_unproven_recovery"])
        self.assertEqual(inputs["unlock_reboot"]["proof"], self.state["results"]["unlock"])
        self.assertEqual(inputs["install_reboot"]["proof"], self.state["results"]["install"])
        self.assertIn("factory-reset", "\n".join(self.messages))
        self.assertIn("brick", "\n".join(self.messages))
        self.assertFalse(any("/example/" in prompt for prompt in self.prompts))

    def test_declining_risks_stops_before_any_operation(self):
        self.ui.choose = lambda: False
        with self.assertRaises(g.Paused):
            self.guide.run()
        self.assertEqual(self.backend.prepared, [])
        self.assertEqual(self.backend.calls, [])
        self.assertFalse(g.accepted(self.state))
        self.assertEqual(self.state["phase"], "consent")

    def test_declining_unlock_starts_no_unlock_or_root_write(self):
        self.ui.choose = lambda: self.state["phase"] != "unlock"
        with self.assertRaises(g.Paused):
            self.guide.run()
        self.assertEqual(self.state["phase"], "unlock")
        self.assertEqual(self.backend.count("unlock"), 0)
        self.assertEqual(self.backend.count("install"), 0)

    def test_debugging_after_reset_waits_before_reconnecting_and_resumes_without_unlock(self):
        self.ui.read = lambda prompt: "q" if self.state["phase"] == "debugging_after_unlock" else ""
        with self.assertRaises(g.Paused):
            self.guide.run()
        self.assertEqual(self.state["phase"], "debugging_after_unlock")
        self.assertEqual(self.backend.count("unlock"), 1)
        self.assertEqual(self.backend.count("unlocked_check"), 0)
        instructions = "\n".join(self.messages)
        for required in ("Developer mode", "USB debugging", "Wi-Fi debugging", "seven times"):
            self.assertIn(required, instructions)
        self.ui.read = self.answer
        self.resume().run()
        self.assertEqual(self.backend.count("unlock"), 1)
        self.assertEqual(self.backend.count("unlocked_check"), 1)

    def test_first_debugging_screen_precedes_any_new_device_connection(self):
        self.ui.read = lambda prompt: "q"
        args = o.parser().parse_args(["--work", str(self.work), "guide"])
        with patch.object(o, "Device", side_effect=AssertionError("Connected before setup confirmation")):
            with self.assertRaises(g.Paused):
                g.select_connection(args, self.ui)
        for required in ("Developer mode", "USB debugging", "Wi-Fi debugging"):
            self.assertIn(required, "\n".join(self.messages))

    def test_new_question_and_acceptance_replace_old_question_and_answer(self):
        screen = []
        snapshots = []
        def choose():
            snapshots.append("\n".join(screen))
            return True
        ui = g.UI(read=lambda prompt: "", emit=screen.append, choose=choose, clear=screen.clear)
        ui.wait("First question about the TV.", title="First page")
        self.assertNotIn("First question", "\n".join(screen))
        ui.accept("Second question about unlocking.", title="Second page")
        self.assertIn("Second question", snapshots[-1])
        self.assertNotIn("First question", snapshots[-1])
        self.assertNotIn("First page", snapshots[-1])
        self.assertNotIn("Second question", "\n".join(screen))

    def test_both_risks_and_this_exact_device_must_be_accepted(self):
        for changes in ({"factory_reset_and_data_loss": False}, {"possible_permanent_brick": False},
                        {"binding": {**BINDING, "ro.serialno": "JUSAOTHER"}}):
            with self.subTest(changes=changes):
                self.state["consent"] = {"version": g.RISK_VERSION, "binding": BINDING,
                                         "factory_reset_and_data_loss": True, "possible_permanent_brick": True, **changes}
                with self.assertRaises(o.Stop):
                    self.guide.act("baseline")
        self.assertEqual(self.backend.prepared, [])

    def test_already_unlocked_unit_skips_unlock_and_its_restart_prompt(self):
        self.backend.locked = False
        self.guide.run()
        self.assertEqual(self.backend.count("unlock"), 0)
        self.assertEqual(self.backend.count("unlock_reboot"), 0)
        self.assertFalse(any("The unlock is confirmed" in line for line in self.messages))

    def test_bad_firmware_baseline_stops_before_unlock(self):
        self.backend.failures["baseline"] = o.Stop("Unsupported Android firmware")
        with self.assertRaises(o.Stop):
            self.guide.run()
        self.assertEqual(self.backend.count("unlock"), 0)

    def test_unsafe_interrupted_actions_are_never_replayed_on_resume(self):
        phases = ("unlock", "unlock_reboot", "patch", "stock_stage", "stock_boot", "root_stage", "root_boot",
                  "install", "install_reboot", "cleanup", "updates")
        for phase in phases:
            with self.subTest(phase=phase):
                backend = FakeBackend(self.work)
                backend.failures[phase] = o.Stop("simulated uncertain completion")
                path = self.work / (phase + ".json")
                guide = g.Guide(path, g.initial_state(self.record, self.connection_file), backend, self.ui)
                with self.assertRaises(o.Stop):
                    guide.run()
                before = list(backend.calls)
                backend.failures.clear()
                resumed = g.Guide(path, g.load_state(path, self.record), backend, self.ui)
                with self.assertRaisesRegex(o.Stop, "will not be repeated"):
                    resumed.run()
                self.assertEqual(backend.calls, before)
                self.assertEqual(backend.count(phase), 1)

    def test_result_saved_before_progress_interruption_is_adopted_without_replay(self):
        original = self.guide.finish_action
        def interrupted(receipt):
            if receipt["phase"] == "unlock":
                raise OSError("simulated loss after confirmed result")
            return original(receipt)
        with patch.object(self.guide, "finish_action", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.guide.run()
        self.assertEqual(self.backend.count("unlock"), 1)
        resumed = self.resume()
        resumed.run()
        self.assertEqual(resumed.state["phase"], "complete")
        self.assertEqual(self.backend.count("unlock"), 1)

    def test_interrupted_verification_resumes_check_without_reinstalling(self):
        self.backend.failures["verify"] = o.Stop("Shell permission not granted yet")
        with self.assertRaises(o.Stop):
            self.guide.run()
        self.backend.failures.clear()
        resumed = self.resume()
        resumed.run()
        self.assertEqual(resumed.state["phase"], "complete")
        self.assertEqual(self.backend.count("verify"), 2)
        self.assertEqual(self.backend.count("install"), 1)
        self.assertEqual(self.backend.count("root_boot"), 1)

    def test_connection_failure_before_write_does_not_consume_it(self):
        self.backend.connection_failure = "install"
        with self.assertRaises(o.Stop):
            self.guide.run()
        self.assertIsNone(self.state["pending"])
        self.assertEqual(self.backend.count("install"), 0)
        self.backend.connection_failure = None
        resumed = self.resume()
        resumed.run()
        self.assertEqual(resumed.state["phase"], "complete")
        self.assertEqual(self.backend.count("install"), 1)

    def test_installation_progress_can_be_checked_again_without_restarting_it(self):
        self.backend.answers["stock_wait"] = [{"installed_disabled_verified": False}, {"installed_disabled_verified": True}]
        self.guide.run()
        self.assertEqual(self.backend.count("stock_wait"), 2)
        self.assertEqual(self.backend.count("stock_stage"), 1)
        self.assertEqual(self.backend.count("stock_boot"), 1)

    def test_refusing_final_installation_never_flashes(self):
        self.ui.choose = lambda: self.state["phase"] != "flash_consent"
        with self.assertRaises(g.Paused):
            self.guide.run()
        self.assertEqual(self.backend.count("install"), 0)
        self.assertEqual(self.state["phase"], "flash_consent")

    def test_unverified_flash_never_reaches_restart_instructions(self):
        self.backend.answers["install"] = {"verified_flash": False}
        with self.assertRaisesRegex(o.Stop, "do not restart"):
            self.guide.run()
        self.assertEqual(self.backend.count("normal_check"), 0)
        self.assertEqual(self.backend.count("install_reboot"), 0)
        self.assertFalse(any("The installed image passed verification" in s for s in self.messages))
        with self.assertRaisesRegex(o.Stop, "will not be repeated"):
            self.resume().run()

    def test_failed_root_proof_cannot_cleanup_or_report_completion(self):
        self.backend.answers["verify"] = {"root_verified": False}
        with self.assertRaisesRegex(o.Stop, "not been verified"):
            self.guide.run()
        self.assertEqual(self.backend.count("cleanup"), 0)
        self.assertNotEqual(self.state["phase"], "complete")

    def test_update_setting_readback_is_required_for_completion(self):
        self.backend.answers["updates_check"] = {"automatic_system_updates_disabled": False}
        with self.assertRaises(o.Stop):
            self.guide.run()
        self.assertNotEqual(self.state["phase"], "complete")

    def test_ctrl_c_finishes_and_saves_critical_action_before_stopping(self):
        previous = signal.getsignal(signal.SIGINT)
        self.backend.interrupt = "unlock"
        with self.assertRaises(g.Paused):
            self.guide.run()
        self.assertIs(signal.getsignal(signal.SIGINT), previous)
        self.assertEqual(self.state["phase"], "unlock_reboot")
        self.assertIsNone(self.state["pending"])
        self.backend.interrupt = None
        self.resume().run()
        self.assertEqual(self.backend.count("unlock"), 1)

    def test_completed_guide_does_not_repeat_device_commands(self):
        self.guide.run()
        before = list(self.backend.calls)
        self.resume().run()
        self.assertEqual(self.backend.calls, before)

    def test_another_household_device_cannot_resume_these_records(self):
        other = {**self.record, "binding": {**BINDING, "ro.serialno": "JUSABEDROOM"}}
        with self.assertRaisesRegex(o.Stop, "another onn"):
            g.load_state(self.path, other)

    def test_preview_and_default_entry_do_not_dispatch_raw_device_commands(self):
        with patch.object(o, "Device", side_effect=AssertionError("No devices in preview")), \
             patch.object(o, "Session", side_effect=AssertionError("No session in preview")), redirect_stdout(StringIO()) as output:
            self.assertEqual(o.main(["guide", "--preview"]), 0)
        self.assertIn("factory-reset", output.getvalue())
        self.assertIn("brick", output.getvalue())
        with patch.object(g, "start", return_value=0) as start:
            self.assertEqual(o.main([]), 0)
        self.assertEqual(start.call_args.args[0].command, "guide")

    def test_real_backend_calls_guarded_installer_with_bound_receipts(self):
        args = SimpleNamespace(work=self.work, adb="unused")
        backend = g.Backend(args, self.connection_file, self.ui)
        context = SimpleNamespace(device=object(), s=None)
        values = {"stage": "/example/root-stage.json", "patch_result": "/example/patch.json", "accept_unproven_recovery": True}
        with patch.object(g.flow, "install_root", return_value={"verified_flash": True}) as install:
            backend.invoke("install", context, values)
        self.assertIs(install.call_args.args[0], context.device)
        passed = install.call_args.args[1]
        self.assertEqual(passed.stage, Path(values["stage"]))
        self.assertEqual(passed.patch_result, Path(values["patch_result"]))
        self.assertTrue(passed.accept_unproven_recovery)

    def test_real_backend_routes_both_restarts_through_verified_helper(self):
        backend = g.Backend(SimpleNamespace(work=self.work, adb="unused"), self.connection_file, self.ui)
        context = SimpleNamespace(device=object(), s=None)
        for kind in ("unlock", "install"):
            proof = {"binding": BINDING, "verified": kind}
            with self.subTest(kind=kind), patch.object(g.flow, "reboot_verified", return_value={}) as reboot:
                backend.invoke(kind + "_reboot", context, {"proof": proof})
                reboot.assert_called_once_with(context.device, kind, proof)

    def test_pending_unlock_restart_uses_trusted_baseline_without_unlock_write(self):
        self.backend.answers["baseline"] = {"binding": BINDING, "boot_id": "before-unlock-restart",
                                            "secure_lock_state": 0, "unlocked_on_current_boot": False,
                                            "write_invocations": 0}
        self.guide.run()
        self.assertEqual(self.backend.count("unlock"), 0)
        self.assertEqual(self.backend.count("unlock_reboot"), 1)
        self.assertEqual(dict(self.backend.calls)["unlock_reboot"]["proof"], self.state["results"]["baseline"])

    def test_legacy_manual_restart_pages_run_guarded_check_without_repeating_writes(self):
        self.guide.run()
        for phase, action, following in (("unlock_return", "unlock_reboot", "unlocked_check"),
                                         ("normal_return", "install_reboot", "normal_check")):
            with self.subTest(phase=phase):
                self.state["phase"] = phase
                self.state["results"].pop(action)
                self.guide.persist()
                self.backend.calls.clear()
                self.backend.connection_failure = following
                self.backend.answers[action] = {"reboot_requests": 0, "already_rebooted": True}
                with self.assertRaisesRegex(o.Stop, "disconnected"):
                    self.resume().run()
                self.assertEqual(self.backend.count(action), 1)
                self.assertEqual(self.backend.count("unlock"), 0)
                self.assertEqual(self.backend.count("install"), 0)

    def test_saved_restart_result_is_adopted_without_reboot_replay(self):
        for phase in ("unlock_reboot", "install_reboot"):
            with self.subTest(phase=phase):
                state = g.initial_state(self.record, self.connection_file)
                backend = FakeBackend(self.work)
                guide = g.Guide(self.path, state, backend, self.ui)
                original = guide.finish_action
                def interrupted(receipt):
                    if receipt["phase"] == phase:
                        raise OSError("lost progress after saved restart result")
                    original(receipt)
                with patch.object(guide, "finish_action", side_effect=interrupted):
                    with self.assertRaises(OSError):
                        guide.run()
                resumed = g.Guide(self.path, g.load_state(self.path, self.record), backend, self.ui)
                resumed.run()
                self.assertEqual(resumed.state["phase"], "complete")
                self.assertEqual(backend.count(phase), 1)

    def test_invalid_restart_result_cannot_advance_or_be_replayed(self):
        self.backend.answers["unlock_reboot"] = {"reboot_requests": 0, "already_rebooted": False}
        with self.assertRaisesRegex(o.Stop, "restart result is not confirmed"):
            self.guide.run()
        with self.assertRaisesRegex(o.Stop, "restart result is not confirmed"):
            self.resume().run()
        self.assertEqual(self.backend.count("unlock_reboot"), 1)
        self.assertEqual(self.backend.count("unlocked_check"), 0)

    def test_guided_entry_runs_and_reopens_completed_state_without_device_replay(self):
        args = o.parser().parse_args(["--work", str(self.work), "--connection", str(self.connection_file), "guide"])
        with patch.object(g.sys.stdin, "isatty", return_value=True), patch.object(o, "verify_files"), \
             patch.object(o, "ROOT", self.work), patch.object(g.shutil, "which", return_value="/fixture/adb"), \
             patch.object(g, "Backend", return_value=self.backend):
            self.assertEqual(g.start(args, self.ui), 0)
            before = list(self.backend.calls)
            self.assertEqual(g.start(args, self.ui), 0)
        self.assertEqual(self.backend.calls, before)
        path = self.work / "devices" / hashlib.sha256(BINDING["ro.serialno"].encode()).hexdigest() / "guide.json"
        self.assertEqual(json.loads(path.read_text())["phase"], "complete")

    def test_household_menu_selects_the_named_unit_without_contacting_others(self):
        for name, serial in (("Living room", BINDING["ro.serialno"]), ("Bedroom", "JUSABEDROOM")):
            path = self.work / "devices" / hashlib.sha256(serial.encode()).hexdigest() / "connection.json"
            o.save(path, {**self.record, "name": name, "binding": {**BINDING, "ro.serialno": serial}})
        rows = connection.devices(self.work)["saved_devices"]
        index = next(i for i, row in enumerate(rows, 1) if row["name"] == "Bedroom")
        self.ui.read = lambda prompt: str(index)
        args = o.parser().parse_args(["--work", str(self.work), "guide"])
        with patch.object(o, "Device", side_effect=AssertionError("Menu must not contact devices")):
            selected = g.select_connection(args, self.ui)
        self.assertEqual(connection.load(selected)["binding"]["ro.serialno"], "JUSABEDROOM")

    def test_a_second_open_guide_cannot_start_work(self):
        args = o.parser().parse_args(["--work", str(self.work), "--connection", str(self.connection_file), "guide"])
        lock_dir = self.work / "work"
        lock_dir.mkdir()
        with (lock_dir / "guide.lock").open("a") as lock:
            g.fcntl.flock(lock, g.fcntl.LOCK_EX | g.fcntl.LOCK_NB)
            with patch.object(g.sys.stdin, "isatty", return_value=True), patch.object(o, "verify_files"), \
                 patch.object(o, "ROOT", self.work), patch.object(g.shutil, "which", return_value="/fixture/adb"), \
                 patch.object(g, "select_connection", side_effect=AssertionError("Second guide reached device selection")):
                self.assertEqual(g.start(args, self.ui), 2)
        self.assertTrue(any("Another guide is already open" in message for message in self.messages))

    def test_existing_manual_attempt_is_not_replaced_by_a_new_guide(self):
        args = o.parser().parse_args(["--work", str(self.work), "--connection", str(self.connection_file), "guide"])
        directory = self.work / "devices" / hashlib.sha256(BINDING["ro.serialno"].encode()).hexdigest()
        attempt = directory / "unlock-attempt.json"
        o.save(attempt, {"consumed": True}, exclusive=True)
        before = attempt.read_bytes()
        with patch.object(g.sys.stdin, "isatty", return_value=True), patch.object(o, "verify_files"), \
             patch.object(o, "ROOT", self.work), patch.object(g.shutil, "which", return_value="/fixture/adb"), \
             patch.object(g, "Backend", side_effect=AssertionError("Should not start over")):
            self.assertEqual(g.start(args, self.ui), 2)
        self.assertEqual(attempt.read_bytes(), before)
        self.assertFalse((directory / "guide.json").exists())

    def test_saved_completion_cannot_hide_missing_root_evidence(self):
        self.guide.run()
        del self.state["results"]["verify"]
        self.guide.persist()
        before = list(self.backend.calls)
        with self.assertRaisesRegex(o.Stop, "completion proof is incomplete"):
            self.resume().run()
        self.assertEqual(self.backend.calls, before)


if __name__ == "__main__":
    unittest.main()

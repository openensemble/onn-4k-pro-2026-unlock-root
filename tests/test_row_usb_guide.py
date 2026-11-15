"""Exercise the Canadian menu, consent, workflow and interrupted-state recovery offline."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import connection
import guide as g
import onn2026 as o
import row_usb_guide as usb
from guide_core import Paused, RootAccessUnavailable, SCHEMA
from test_guide import FakeBackend
from test_row import props


class Backend(FakeBackend):
    def __init__(self, work, locked, binding):
        super().__init__(work, locked, binding)
        self.answers["host"] = {"fastboot": "/fixture/fastboot", "dsu_tools_required": False}
        self.answers["flash_consent"] = {"instructions": "OWNER USB COMMANDS — fixture only",
                                         "instructions_file": "/fixture/usb-instructions.txt",
                                         "firmware_written": False, "reboot_requested": False}


class CanadianUsbGuideTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.messages, self.prompts = [], []
        self.ui = g.UI(read=self.answer, emit=self.messages.append, choose=lambda: True, clear=lambda: None)
        for owner, name in ((o.subprocess, "run"), (connection.socket, "create_connection"),
                            (o.urllib.request, "urlopen")):
            guard = patch.object(owner, name, side_effect=AssertionError("Live I/O forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def answer(self, prompt):
        self.prompts.append(prompt)
        return ""

    def setup_guide(self, slot="_a", locked=False):
        directory = self.work / slot
        directory.mkdir(exist_ok=True)
        binding = o.validate_identity(props(slot))
        self.record = {"schema": connection.SCHEMA, "binding": binding,
                       "name": "Canadian fixture", "target": "192.0.2.71:5555"}
        self.selected = directory / "connection.json"
        o.save(self.selected, self.record)
        self.path = directory / "usb-guide.json"
        self.backend = Backend(directory, locked, binding)
        self.walk = usb.UsbGuide(self.path, usb.new_state(self.record, self.selected), self.backend, self.ui)
        self.walk.persist()
        return self.walk

    def resume(self):
        state = usb.validate(json.loads(self.path.read_text()), self.record)
        return usb.UsbGuide(self.path, state, self.backend, self.ui)

    def test_locked_both_slots_complete_without_any_dsu_or_automatic_usb_flash(self):
        for slot in ("_a", "_b"):
            with self.subTest(slot=slot):
                walk = self.setup_guide(slot, locked=True)
                legacy = self.path.with_name("guide.json")
                legacy.write_text('keep earlier DSU evidence\n')
                walk.run()
                self.assertEqual(walk.state["phase"], "complete")
                for step in ("unlock", "unlock_reboot", "fetch", "patch", "flash_consent", "verify"):
                    self.assertEqual(self.backend.count(step), 1, step)
                for step in ("build", "stock_stage", "stock_boot", "root_stage", "root_boot", "install", "install_reboot", "cleanup"):
                    self.assertEqual(self.backend.count(step), 0, step)
                self.assertEqual(legacy.read_text(), 'keep earlier DSU evidence\n')
                self.assertTrue(walk.state["consent"]["usb_reported_method_risk"])
                self.assertTrue(walk.state["results"]["verify"]["root_verified"])
                text = "\n".join(self.messages)
                self.assertIn("kushmint", text)
                self.assertIn("not independently tested", text)
                self.assertIn("factory reset in Android Recovery", text)

    def test_unlocked_units_skip_unlock_reboot_and_reset(self):
        walk = self.setup_guide()
        walk.run()
        self.assertEqual(self.backend.count("unlock"), 0)
        self.assertEqual(self.backend.count("unlock_reboot"), 0)
        self.assertTrue(walk.state["results"]["verify"]["root_verified"])

    def test_declining_community_risk_performs_no_backend_operation(self):
        walk = self.setup_guide()
        self.ui.choose = lambda: False
        with self.assertRaises(Paused):
            walk.run()
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.prepared, [])
        self.assertEqual(walk.state["phase"], "consent")
        self.assertNotIn("consent", walk.state)

    def test_declining_flash_consent_keeps_patch_and_does_not_show_commands(self):
        walk = self.setup_guide()
        self.ui.choose = lambda: self.ui.title != "Continue with the reported USB root method?"
        with self.assertRaises(Paused):
            walk.run()
        self.assertEqual(walk.state["phase"], "flash_consent")
        self.assertEqual(self.backend.count("patch"), 1)
        self.assertEqual(self.backend.count("flash_consent"), 0)
        self.assertNotIn("OWNER USB COMMANDS", "\n".join(self.messages))

    def test_resume_after_showing_commands_never_reissues_them_or_repatches(self):
        walk = self.setup_guide()
        self.ui.read = lambda _: "q" if self.ui.title == "Run the checked USB commands once" else ""
        with self.assertRaises(Paused):
            walk.run()
        self.assertEqual(json.loads(self.path.read_text())["phase"], "normal_return")
        self.assertIsNone(walk.state["pending"])
        shown = sum("OWNER USB COMMANDS" in message for message in self.messages)
        self.ui.read = self.answer
        resumed = self.resume()
        resumed.run()
        self.assertEqual(resumed.state["phase"], "complete")
        self.assertEqual(sum("OWNER USB COMMANDS" in message for message in self.messages), shown)
        self.assertEqual(self.backend.count("patch"), 1)
        self.assertEqual(self.backend.count("flash_consent"), 1)

    def test_uncertain_unlock_or_patch_is_never_automatically_repeated(self):
        for phase in ("unlock", "patch"):
            walk = self.setup_guide("_a" if phase == "unlock" else "_b", locked=True)
            self.backend.failures[phase] = o.Stop("uncertain fixture operation")
            with self.assertRaises(o.Stop):
                walk.run()
            del self.backend.failures[phase]
            with self.assertRaisesRegex(o.Stop, "will not be repeated"):
                self.resume().run()
            self.assertEqual(self.backend.count(phase), 1)
            self.assertEqual(self.backend.count("flash_consent"), 0)

    def test_root_denial_retries_only_verification_after_owner_continues(self):
        walk = self.setup_guide()
        self.backend.failures["verify"] = RootAccessUnavailable("Shell permission denied")
        continued = []
        def answer(_):
            if any("After correcting Shell access" in item for item in self.messages):
                self.backend.failures.pop("verify", None)
                continued.append(True)
            return ""
        self.ui.read = answer
        walk.run()
        self.assertTrue(continued)
        self.assertEqual(self.backend.count("verify"), 2)
        self.assertEqual(self.backend.count("patch"), 1)
        self.assertEqual(self.backend.count("flash_consent"), 1)
        self.assertEqual(len([r for r in walk.state["history"] if r["phase"] == "verify"]), 2)

    def test_completed_result_recovers_without_repeating_preparation(self):
        walk = self.setup_guide()
        self.ui.read = lambda _: "q" if self.ui.title == "Run the checked USB commands once" else ""
        with self.assertRaises(Paused):
            walk.run()
        state = json.loads(self.path.read_text())
        completed = state["history"][-1]
        self.assertEqual(completed["phase"], "flash_consent")
        state.update(phase="flash_consent", pending=completed)
        state["results"].pop("flash_consent")
        o.save(self.path, state)
        self.ui.read = self.answer
        self.resume().run()
        self.assertEqual(self.backend.count("flash_consent"), 1)

    def test_missing_usb_acceptance_and_foreign_method_states_are_rejected(self):
        walk = self.setup_guide()
        walk.run()
        for field, value in (("root_method", "dsu"), ("phase", "build")):
            with self.subTest(field=field), self.assertRaises(o.Stop):
                usb.validate({**walk.state, field: value}, self.record)
        state = {**walk.state, "consent": {**walk.state["consent"], "usb_reported_method_risk": False}}
        with self.assertRaises(o.Stop):
            usb.validate(state, self.record)
        receipt = {"schema": SCHEMA, "binding": self.record["binding"], "phase": "fetch", "next": "build", "result": {}}
        with self.assertRaises(o.Stop):
            walk.finish_action(receipt)

    def test_status_menu_does_not_require_risk_consent_or_create_root_progress(self):
        self.setup_guide()
        self.path.unlink()
        self.ui.read = lambda _: "2"
        self.ui.choose = lambda: self.fail("Status must not request root risk acceptance")
        with patch.object(usb, "Backend", return_value=self.backend), patch.object(o, "status", return_value={"read_only": True}):
            self.assertEqual(usb.start(SimpleNamespace(), self.selected, self.record, self.path, self.ui), 0)
        self.assertEqual(self.backend.calls, [])
        self.assertFalse(self.path.exists())

    def test_us_identity_never_gets_the_canadian_menu(self):
        self.setup_guide()
        record = {**self.record, "binding": o.validate_identity(props("_a", o.PROFILE))}
        with self.assertRaisesRegex(o.Stop, "No USB fastboot patch source"):
            usb.start(SimpleNamespace(), self.selected, record, self.path, self.ui)
        self.assertEqual(self.messages, [])

    def test_main_guide_dispatches_only_canadian_profile_to_usb_menu(self):
        for profile in (o.PROFILE, o.PROFILE["variants"]["jarvis2-row-15016191"]):
            binding = o.validate_identity(props("_a", profile))
            selected = self.work / (profile["id"] + ".json")
            o.save(selected, {"schema": connection.SCHEMA, "binding": binding, "target": "192.0.2.71:5555"})
            args = o.parser().parse_args(["--work", str(self.work), "--connection", str(selected), "guide"])
            with self.subTest(profile=profile["id"]), patch.object(g.sys.stdin, "isatty", return_value=True), \
                 patch.object(o, "verify_files"), patch.object(o, "ROOT", self.work), \
                 patch.object(g.shutil, "which", return_value="fake-adb"), patch.object(g, "Guide") as standard, \
                 patch.object(usb, "start", return_value=0) as canadian:
                self.assertEqual(g.start(args, self.ui), 0)
                self.assertEqual(canadian.call_count, int(profile is not o.PROFILE))
                self.assertEqual(standard.call_count, int(profile is o.PROFILE))

    def test_previous_permanent_installation_attempt_is_preserved_and_blocks_new_guide(self):
        self.setup_guide()
        self.path.unlink()
        attempt = self.path.parent / "root-install-attempt.json"
        attempt.write_text('"preserve me"\n')
        self.ui.read = lambda _: "1"
        with patch.object(usb, "Backend", return_value=self.backend), self.assertRaisesRegex(o.Stop, "Earlier installation attempts"):
            usb.start(SimpleNamespace(), self.selected, self.record, self.path, self.ui)
        self.assertEqual(attempt.read_text(), '"preserve me"\n')
        self.assertFalse(self.path.exists())
        self.assertEqual(self.backend.calls, [])


if __name__ == "__main__":
    unittest.main()

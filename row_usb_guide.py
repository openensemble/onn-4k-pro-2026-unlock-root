"""Canadian-only walkthrough for the contributor-reported manual USB root method."""
import json
from pathlib import Path
import platform
import shlex
import shutil
from types import SimpleNamespace

import connection
import guide
import onn2026 as o
import root_workflow as flow
from guide_core import (DEBUGGING_SETUP, MAGISK_OPEN, PAGE_TITLES, RISK_TEXT, RISK_VERSION,
                        ROOT_ACCESS_HELP, RootAccessUnavailable, initial_state, now, validate_state)

NOTICE_VERSION = 1
NOTICE = (
    "Canada/ROW USB root method — community hardware report\n\n"
    "This method was submitted in PR #4 by kushmint, who reports successful USB rooting\n"
    "on a Canadian onn, slot A. We have not independently tested this method on hardware.\n"
    "Canadian slot B is accepted by the script but has no separate hardware report.\n"
    "PR: https://github.com/openensemble/onn-4k-pro-2026-unlock-root/pull/4\n\n"
    "It uses the pinned US init_boot image, patched on your Canadian onn. That source\n"
    "is not an original Canadian backup. A working recovery path is not proven.\n"
    "You will run the USB fastboot flash commands yourself. This is separate from DSU.\n"
    "Continue only if you accept the risk of an unusable onn."
)
PHASES = {"consent", "host", "baseline", "unlock", "unlock_reboot", "unlock_return",
          "debugging_after_unlock", "unlocked_check", "fetch", "patch", "flash_consent",
          "normal_return", "magisk_setup", "verify", "updates", "updates_check", "complete"}


def validate(state, record):
    validate_state(state, record)
    o.patch_source_profile(record["binding"], "usb-fastboot")
    o.require(state.get("root_method") == "usb-fastboot" and state["phase"] in PHASES,
              "Saved progress is not this Canadian USB walkthrough; keep it for inspection")
    consent = state.get("consent", {})
    o.require(state["phase"] == "consent" or
              (consent.get("usb_reported_method_risk") is True and consent.get("usb_notice_version") == NOTICE_VERSION),
              "Acceptance of the contributor-reported USB method is missing")
    return state


def new_state(record, selected):
    return {**initial_state(record, selected), "root_method": "usb-fastboot"}


class Backend(guide.Backend):
    reconnect_help = ("Reconnect the original Android after its restart and finish setup on the TV.\n")

    def invoke(self, phase, context, values):
        o.patch_source_profile(self.binding, "usb-fastboot")
        o.require(phase in PHASES, "Unknown Canadian USB operation")
        device = context.device
        if phase == "host":
            o.require(platform.system() == "Linux", "This walkthrough requires a Linux computer")
            fastboot = shutil.which("fastboot")
            o.require(fastboot, "Install Android platform-tools with adb and fastboot. On Debian/Ubuntu: sudo apt-get install adb fastboot")
            for directory in (o.ROOT, self.args.work):
                o.require(shutil.disk_usage(directory).free >= 2 * 1024 ** 3,
                          "Free at least 2 GiB on the computer for the OTA, Magisk and receipts")
            return {"fastboot": fastboot, "dsu_tools_required": False}
        if phase in ("baseline", "unlocked_check"):
            result = o.status(device)
            o.require(flow.state(device) in ("normal", "installed\ndisabled"),
                      "Return to the original Android with DSU disabled before USB preparation")
            if phase == "baseline":
                root = device.shell("existing-root", "test", "-x", "/debug_ramdisk/su", check=False)
                o.require(root["returncode"] == 1 and not root["timed_out"],
                          "Root is already present, or could not be checked. This guide will not reinstall over it.")
            else:
                o.require(result["unlocked_on_current_boot"] and result["boot_completed"] == "1",
                          "Finish Android setup and a normal unlocked boot, then resume this check")
            return result
        if phase == "fetch":
            return o.fetch_inputs(context.s, self.binding, "usb-fastboot")
        if phase == "patch":
            return o.prepare_root(device, values["stock_init_boot"], values["magisk_apk"], "usb-fastboot")
        if phase == "flash_consent":
            patch, image = flow.patch_receipt(device, Path(values["patch_result"]), "usb-fastboot")
            o.require(device.boot_id() == patch["boot_id"]
                      and o.read_android_id(device, "usb-original-installation") == patch["android_id"]
                      and flow.state(device) in ("normal", "installed\ndisabled"),
                      "Android changed after preparation. Inspect the receipts before any USB flash")
            serial = self.binding["ro.serialno"]
            slot = self.binding["ro.boot.slot_suffix"][-1]
            adb = shlex.join([device.adb, "-s", device.target])
            fastboot_path = shutil.which("fastboot")
            o.require(fastboot_path, "fastboot is no longer available on this computer")
            fastboot = shlex.join([fastboot_path, "-s", serial])
            instructions = (
                "Use another terminal on this computer. Connect the onn's data-capable USB port.\n"
                "Expected physical serial: " + serial + " | active slot: " + slot.upper() + "\n"
                "Check the patched image before proceeding:\n\n"
                + shlex.join(["sha256sum", str(image.resolve())]) + "\n"
                "Expected SHA-256: " + patch["patched_image"]["sha256"] + "\n\n"
                "If the hash matches, request fastbootd once:\n\n"
                + adb + " reboot fastboot\n\n"
                "Inspect USB fastboot's identity before flashing:\n\n"
                + shlex.join([fastboot_path, "devices"]) + "\n"
                + fastboot + " getvar serialno\n" + fastboot + " getvar current-slot\n\n"
                "STOP if the physical serial or active slot differs from the expected values above.\n"
                "Do not change slots. Only after those checks match, flash once:\n\n"
                + fastboot + " flash init_boot_" + slot + " " + shlex.quote(str(image.resolve())) + "\n\n"
                "Only if fastboot reports a successful flash, reboot once:\n\n"
                + fastboot + " reboot\n\n"
                "If a flash or reboot fails or its result is uncertain, keep its output and inspect.\n"
                "Do not repeat an attempted flash or reboot. No USB command is run by this guide.\n"
                "After the successful flash and normal Android boot, continue here."
            )
            path = context.s.path / "usb-instructions.txt"
            path.write_text(instructions + "\n")
            return {"instructions": instructions, "instructions_file": str(path),
                    "firmware_written": False, "reboot_requested": False, "owner_runs_usb_commands": True}
        if phase == "verify":
            return flow.verify_root(device, SimpleNamespace(patch_result=Path(values["patch_result"]),
                                                           method="usb-fastboot"))
        o.require(phase in ("unlock", "unlock_reboot", "updates", "updates_check"),
                  "This operation does not apply to Canadian USB rooting")
        return super().invoke(phase, context, values)


class UsbGuide(guide.Guide):
    def finish_action(self, receipt):
        o.require(receipt.get("phase") in PHASES and receipt.get("next") in PHASES,
                  "An operation receipt cannot switch the Canadian USB guide to another root method")
        super().finish_action(receipt)

    def act(self, next_phase, **values):
        validate(self.state, {"binding": self.state["binding"]})
        return super().act(next_phase, **values)

    def verify(self):
        message = ROOT_ACCESS_HELP + "\n\nStay at the TV and grant Shell access when asked."
        while True:
            self.ui.wait(message, title="Verify permanent root")
            try:
                self.act("updates", patch_result=self.state["results"]["patch"]["patch_result"])
                return
            except RootAccessUnavailable as error:
                self.recover_pending()
                message = str(error) + "\n\nAfter correcting Shell access, continue to repeat only this read-only check."

    def run(self):
        validate(self.state, {"binding": self.state["binding"]})
        self.ui.device_label = (self.state["name"] + " | serial " + self.state["binding"]["ro.serialno"]
                                + " | slot " + self.state["binding"]["ro.boot.slot_suffix"][-1].upper())
        self.recover_pending()
        while True:
            phase, results = self.state["phase"], self.state["results"]
            o.require(phase in PHASES, "Unknown Canadian USB step; keep saved progress for inspection")
            self.ui.page(PAGE_TITLES.get(phase, "Canada/ROW USB rooting"))
            if phase == "consent":
                self.ui.accept(NOTICE + "\n\n" + RISK_TEXT, title="Accept the Canadian USB method and risks")
                self.state["consent"] = {"version": RISK_VERSION, "binding": dict(self.state["binding"]),
                                         "factory_reset_and_data_loss": True, "possible_permanent_brick": True,
                                         "usb_reported_method_risk": True, "usb_notice_version": NOTICE_VERSION,
                                         "method": "accept_decline_buttons", "at": now()}
                self.advance("host")
            elif phase == "host":
                self.ui.say("Checking Android platform-tools and download space. No DSU build tools are needed.")
                self.act("baseline")
            elif phase == "baseline":
                self.ui.say("Checking the selected Canadian onn and its unlock status...")
                self.act(lambda r: "unlock" if r["secure_lock_state"] == 1 else (
                    "unlocked_check" if r["unlocked_on_current_boot"] else "unlock_reboot"))
            elif phase == "unlock":
                self.ui.accept("Unlocking erases ALL apps, accounts, settings and other local data.\n"
                               "After the automatic restart, complete the factory reset in Android Recovery.\n"
                               "Back up anything you want to keep before accepting.")
                self.act(lambda r: "unlocked_check" if r.get("already_unlocked") else "unlock_reboot")
            elif phase == "unlock_reboot":
                self.act("unlock_return", proof=results.get("unlock", results.get("baseline")))
            elif phase == "unlock_return":
                self.ui.wait("The unlock restart has been handled once. Watch the TV.\n"
                             "Complete the factory reset in Android Recovery and finish Android setup.\n"
                             "Connect Ethernet or Wi-Fi, then continue. Do not repeat the unlock.")
                self.advance("debugging_after_unlock")
            elif phase == "debugging_after_unlock":
                self.ui.wait(DEBUGGING_SETUP)
                self.advance("unlocked_check")
            elif phase == "unlocked_check":
                self.act("fetch")
            elif phase == "fetch":
                self.ui.say("Downloading the pinned US init_boot source and Magisk from their official sources...")
                self.act("patch")
            elif phase == "patch":
                self.ui.say("Patching the image on this Canadian onn. This does not flash or reboot it...")
                self.act("flash_consent", **results["fetch"])
            elif phase == "flash_consent":
                self.ui.accept(NOTICE + "\n\nReady to show USB flash instructions for this onn's active slot.\n"
                               "Accept only when you are ready to run the commands yourself and inspect each result.",
                               title="Continue with the reported USB root method?")
                # Save progress before showing commands. Resuming proceeds to checks;
                # it cannot generate a new flash or reboot request.
                result = self.act("normal_return", patch_result=results["patch"]["patch_result"])
                self.ui.wait(result["instructions"] + "\n\nSaved instructions: " + result["instructions_file"],
                             title="Run the checked USB commands once")
            elif phase == "normal_return":
                self.ui.wait("Continue after the USB flash succeeded and original Android started normally.\n"
                             "If you have not attempted the USB commands, they are saved here:\n"
                             + results["flash_consent"]["instructions_file"] + "\n"
                             "Do not repeat any attempted flash or restart. If its result is uncertain, stop and inspect.\n"
                             "This walkthrough resumes with verification, never another USB flash.")
                self.advance("magisk_setup")
            elif phase == "magisk_setup":
                self.ui.wait(MAGISK_OPEN + "\nFinish Magisk's additional setup and allow its restart if requested.\n"
                             "Once the original Android is ready, continue to verify root.")
                self.advance("verify")
            elif phase == "verify":
                self.verify()
            elif phase == "updates":
                self.ui.say("Turning off Android's automatic system-update scheduling...")
                self.act("updates_check")
            elif phase == "updates_check":
                self.act("complete")
            elif phase == "complete":
                o.require(results.get("verify", {}).get("root_verified") is True
                          and results.get("updates_check", {}).get("automatic_system_updates_disabled") is True,
                          "Permanent root and update settings have not both been verified")
                self.ui.say("Saved successful result: permanent Magisk root was verified on this Canadian onn,\n"
                            "with SELinux Enforcing and the expected installed image. Automatic system updates\n"
                            "were confirmed disabled.\n"
                            "Keep the work folder and receipts. The US patch source is not an original ROW backup.")
                return


def start(args, selected, record, path, ui):
    o.patch_source_profile(record["binding"], "usb-fastboot")
    while True:
        ui.page("Canada/ROW options", NOTICE + "\n\n1. Continue with USB rooting\n2. Show status only\nQ. Leave")
        choice = ui.ask("Choose 1 or 2, or q to leave: ")
        if choice in ("1", "2"):
            break
    backend = Backend(args, selected, ui)
    if choice == "2":
        context = backend.prepare("baseline")
        ui.say(json.dumps(o.status(context.device), indent=2))
        return 0
    if path.exists():
        state = validate(json.loads(path.read_text()), record)
        o.require(Path(state["connection_file"]).resolve() == selected.resolve(),
                  "Saved USB progress uses another connection file; keep the original work folder")
    else:
        attempts = list(path.parent.glob("*-attempt.json"))
        o.require(all(p.name in ("unlock-attempt.json", "normal-unlock-reboot-attempt.json") for p in attempts),
                  "Earlier installation attempts need inspection. Keep all receipts; no USB workflow was started")
        state = new_state(record, selected)
    walkthrough = UsbGuide(path, state, backend, ui)
    walkthrough.persist()
    walkthrough.run()
    return 0

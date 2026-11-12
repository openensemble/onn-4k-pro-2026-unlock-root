"""Shared walkthrough for real and mock backends; contains no device transport."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import signal
import threading

from console_ui import accept_decline, clear_screen


class Stop(RuntimeError):
    pass


class RootAccessUnavailable(Stop):
    """A read-only root request needs the user's attention before another check."""


def require(condition, message):
    if not condition:
        raise Stop(message)


def now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = "onn26-guide-v1"
RISK_VERSION = 1
RISK_TEXT = (
    "Unlocking will factory-reset your onn and erase its apps, accounts, settings and other data.\n"
    "Back up anything you want to keep before continuing. An already-unlocked onn skips the unlock step.\n\n"
    "Unlocking or rooting could brick your onn: it may stop working and may not be recoverable.\n"
    "A proven way to restore a bad root installation is not available for this workflow.\n"
    "Saving the original image does not guarantee that it can be restored.\n\n"
    "Continue only if you accept the factory reset, data loss and possibility of a permanently unusable onn."
)
DEBUGGING_SETUP = (
    "Use the TV remote to enable all three settings:\n\n"
    "1. Open Settings > System > About.\n"
    "2. Select Android TV OS build (or Build) seven times to enable Developer mode.\n"
    "3. Return to System > Developer options and leave Developer options on.\n"
    "4. Turn on USB debugging.\n"
    "5. Turn on Wi-Fi debugging (sometimes named Network or Wireless debugging).\n\n"
    "Keep the onn and this computer on the same network.\n"
    "When the TV asks, allow this computer's debugging connection."
)
MAGISK_OPEN = (
    "On the TV, open Settings > Apps > See all apps > Magisk > Open.\n"
    "Magisk may not appear in the regular Google TV app list."
)
ROOT_ACCESS_HELP = (
    MAGISK_OPEN + "\n\n"
    "If a previous root prompt expired or no prompt appears, open Magisk's Superuser screen.\n"
    "Find Shell and allow its root access. To get a fresh prompt instead, remove only Shell's saved entry.\n"
    "If Shell is already allowed, verification can succeed without another prompt."
)
PHASES = (
    "consent", "host", "baseline", "unlock", "unlock_reboot", "unlock_return", "debugging_after_unlock", "unlocked_check", "fetch", "patch", "build",
    "stock_stage", "stock_wait", "stock_boot", "stock_seen", "stock_return_prompt", "stock_return",
    "root_stage", "root_wait", "root_boot", "root_setup", "root_ready", "flash_consent", "install",
    "install_reboot", "normal_return", "normal_check", "magisk_setup", "verify", "cleanup", "updates", "updates_check", "complete",
)
PAGE_TITLES = {
    "consent": "Read and accept the risks", "host": "Check your computer", "baseline": "Check your onn",
    "unlock": "Unlock your onn", "unlock_reboot": "Restart after unlocking", "unlock_return": "Finish Android setup",
    "debugging_after_unlock": "Turn debugging back on", "unlocked_check": "Confirm the unlock",
    "fetch": "Download the matching files", "patch": "Prepare root for this onn",
    "build": "Prepare the temporary Android systems", "stock_stage": "Install the first temporary system",
    "stock_wait": "Check the first installation", "stock_boot": "Start the first temporary system",
    "stock_seen": "Wait for the Android setup screen", "stock_return_prompt": "Return to your original Android",
    "stock_return": "Check that your original Android returned", "root_stage": "Prepare the installation system",
    "root_wait": "Check the installation system", "root_boot": "Start the installation system",
    "root_setup": "Connect the temporary system to Wi-Fi", "root_ready": "Check that the system is ready",
    "flash_consent": "Confirm the root installation", "install": "Back up and install root",
    "install_reboot": "Restart into your original Android", "normal_return": "Wait for your original Android",
    "normal_check": "Check the original Android",
    "magisk_setup": "Finish Magisk setup", "verify": "Verify permanent root",
    "cleanup": "Remove the temporary system", "updates": "Turn off automatic system updates",
    "updates_check": "Check the update setting", "complete": "Setup complete",
}
# Only checks and host-only preparation can be repeated after an interruption.
REPEATABLE = {"host", "baseline", "unlocked_check", "fetch", "build", "stock_wait", "root_wait",
              "stock_return", "root_ready", "normal_check", "verify", "updates_check"}
PREVIEW = """2026 onn unlock and root guide

The exact supported firmware may run on active slot A or B. The guide keeps
that slot throughout. Slot B has hardware verification; slot A awaits a
successful complete hardware run.

1. Enable Developer mode, USB debugging and Wi-Fi/network debugging, then choose your onn.
2. Read and accept the factory-reset, data-loss and bricking warning.
3. Check the computer, Android version and selected onn.
4. Unlock once if needed and restart automatically. Finish Android setup and enable debugging again.
5. Download the matching files and prepare root for this specific onn.
6. Try a temporary Android system and use one power cycle to prove the original returns.
7. Start the temporary system used for installation; reconnect its Wi-Fi on the TV.
8. Review the final warning, back up the original image, and install root once.
9. Restart automatically into normal Android, then finish Magisk's setup and its restart.
10. Verify permanent root, remove the temporary system, and turn off automatic system updates.

The guide runs the commands and remembers the files for you. It checks the same
serial after IP changes. Type q at a prompt to leave and run the same command to
return. An uncertain write or reboot is never automatically repeated.
"""


class Paused(Exception):
    pass


class UI:
    def __init__(self, read=input, emit=print, choose=accept_decline, clear=clear_screen):
        self.read, self.emit, self.choose = read, emit, choose
        self.clear = clear
        self.title = "Get started"
        self.device_label = ""

    def page(self, title, message=""):
        self.title = title
        self.clear()
        self.say("onn 2026 | Unlock and root\n")
        self.say(title)
        if self.device_label:
            self.say(self.device_label)
        self.say("-" * 72)
        if message:
            self.say(message + "\n")

    def say(self, message):
        self.emit(message)

    def ask(self, message):
        try:
            answer = self.read(message).strip()
        except (EOFError, KeyboardInterrupt):
            raise Paused() from None
        self.page(self.title)
        if answer.lower() == "q":
            raise Paused()
        return answer

    def wait(self, message, title=None):
        self.page(title or self.title, message)
        while self.ask("Press Enter when ready, or q to leave: "):
            self.page(title or self.title, message + "\n\nPress Enter to continue, or type q to leave.")

    def accept(self, message, title=None):
        self.page(title or self.title, message)
        try:
            approved = self.choose()
        except (EOFError, KeyboardInterrupt):
            raise Paused() from None
        self.page(self.title)
        if approved is not True:
            self.say("Declined. This step was not started.")
            raise Paused()


def show_debugging_setup(ui):
    ui.wait(DEBUGGING_SETUP, title="Enable debugging on your onn")


def ask_device_name(ui, serial):
    ui.page("Name your onn", "Found supported 2026 onn | Android 14 | build 15640252\nHardware serial: " + serial)
    return ui.ask("Give this onn a name, such as Living room (Enter uses its serial): ") or serial


def confirm_device(ui, name, serial):
    ui.accept("Use this onn for the guided unlock and root?\nName: " + name + " | serial " + serial,
              title="Confirm your onn")


def show_stopped(ui):
    ui.page("Walkthrough stopped", "Run the script again when you are ready to continue.")


def slot_notice(binding):
    slot = binding.get("ro.boot.slot_suffix")
    require(slot in ("_a", "_b"), "The selected onn has no supported active slot")
    if slot == "_a":
        return ("Active slot A: installer support is implemented, but a complete unlock and root\n"
                "installation on slot A has not yet been verified on hardware.\n\n")
    return "Active slot B: unlock and root have been verified on hardware for this firmware.\n\n"


@contextmanager
def working(ui, protect=False):
    """Keep long operations understandable; Ctrl-C cannot interrupt a critical write."""
    stopped = threading.Event()
    cancellation = {"requested": False}
    def heartbeat():
        while not stopped.wait(20):
            ui.say("Still working on this step. Keep the onn powered on and leave this window open.")
    def interrupt(signum, frame):
        cancellation["requested"] = True
        ui.say("This will finish the current step, save its result, and stop before the next step.")
    old = None
    if protect and threading.current_thread() is threading.main_thread():
        old = signal.signal(signal.SIGINT, interrupt)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield cancellation
    finally:
        stopped.set()
        thread.join()
        if old is not None:
            signal.signal(signal.SIGINT, old)


def accepted(state):
    consent = state.get("consent", {})
    return (consent.get("version") == RISK_VERSION and consent.get("factory_reset_and_data_loss") is True
            and consent.get("possible_permanent_brick") is True and consent.get("binding") == state["binding"])


def validate_state(state, record):
    require(isinstance(state, dict) and state.get("schema") == SCHEMA and state.get("phase") in PHASES,
              "Saved guide progress is not recognized. Keep it for inspection; no operation was started.")
    require(state.get("binding") == record["binding"] and isinstance(state.get("results"), dict),
              "This saved guide belongs to another onn or firmware. No operation was started.")
    require(state["phase"] == "consent" or accepted(state), "The required risk acceptance is missing.")
    return state


def initial_state(record, connection_file):
    return {"schema": SCHEMA, "binding": record["binding"], "name": record.get("name", record["binding"]["ro.serialno"]),
            "connection_file": str(Path(connection_file).resolve()), "phase": "consent", "created_at": now(),
            "results": {}, "pending": None, "history": []}


class Guide:
    def __init__(self, path, state, backend, ui, store):
        self.path, self.state, self.backend, self.ui = Path(path), state, backend, ui
        self.store = store

    def persist(self):
        self.state["updated_at"] = now()
        self.store.replace(self.path, self.state)

    def advance(self, phase):
        require(phase in PHASES, "Unknown guide step")
        self.state["phase"] = phase
        self.persist()

    def finish_action(self, receipt):
        require(receipt.get("schema") == SCHEMA and receipt.get("binding") == self.state["binding"]
                  and receipt.get("phase") == self.state["phase"] and receipt.get("next") in PHASES,
                  "The saved operation result does not belong to this step or onn.")
        required = {"install": "verified_flash", "verify": "root_verified", "cleanup": "cleanup_completed",
                    "updates_check": "automatic_system_updates_disabled"}.get(receipt["phase"])
        require(not required or receipt["result"].get(required) is True,
                  "The saved operation result does not prove that this step completed.")
        if receipt["phase"] in ("unlock_reboot", "install_reboot"):
            result = receipt["result"]
            require(type(result.get("reboot_requests")) is int and result["reboot_requests"] in (0, 1)
                    and result.get("already_rebooted") is (result["reboot_requests"] == 0),
                    "The saved restart result is not confirmed. It will not be repeated.")
        self.state["results"][receipt["phase"]] = receipt["result"]
        self.state["phase"] = receipt["next"]
        self.state["pending"] = None
        self.persist()

    def recover_pending(self):
        pending = self.state.get("pending")
        if not pending:
            return
        require(pending.get("phase") == self.state["phase"], "Interrupted step does not match saved progress")
        receipt = Path(pending["receipt"])
        completed = self.store.read_optional(receipt)
        if completed is not None:
            self.finish_action(completed)
            self.ui.say("The previous step finished and saved a confirmed result. Continuing after it.")
        elif pending["phase"] in REPEATABLE:
            self.ui.say("The last check or computer-only preparation was interrupted. It can be checked again.")
            self.state["pending"] = None
            self.persist()
        else:
            raise Stop("The last device operation has no confirmed completion. It will not be repeated. "
                         "Keep the onn in its current state; do not restart it or delete the saved records. "
                         "Review the receipts in " + str(receipt.parent))

    def act(self, next_phase, **values):
        phase = self.state["phase"]
        require(accepted(self.state), "Accept factory reset, data loss and possible bricking before continuing")
        # Reconnect first. A connection failure must not consume a device-write attempt.
        context = self.backend.prepare(phase)
        result_path = context.s.path / "guide-result.json"
        pending = {"phase": phase, "receipt": str(result_path), "started_at": now()}
        self.state["pending"] = pending
        self.state["history"].append(dict(pending))
        self.persist()
        with working(self.ui, protect=phase not in REPEATABLE) as cancellation:
            result = self.backend.invoke(phase, context, values)
            if phase == "install":
                require(result.get("verified_flash") is True, "Root installation was not verified; do not restart.")
            if phase == "verify":
                require(result.get("root_verified") is True, "Permanent root has not been verified.")
            if phase == "updates_check":
                require(result.get("automatic_system_updates_disabled") is True,
                          "Automatic system updates are not confirmed disabled.")
            following = next_phase(result) if callable(next_phase) else next_phase
            receipt = {"schema": SCHEMA, "binding": self.state["binding"], "phase": phase,
                       "next": following, "result": result, "completed_at": now()}
            self.store.write_once(result_path, receipt)
            self.finish_action(receipt)
        if cancellation["requested"]:
            raise Paused()
        return result

    def verify(self):
        message = ("Stay at the TV with its remote before starting this check.\n"
                   "Magisk's permission prompt expires quickly. Select Grant for Shell when it appears.\n\n"
                   + ROOT_ACCESS_HELP)
        while True:
            self.ui.wait(message, title=PAGE_TITLES["verify"])
            self.ui.say("Checking permanent root. Watch the TV and grant Shell access if asked...")
            try:
                results = self.state["results"]
                self.act("cleanup", patch_result=results["patch"]["patch_result"],
                         before=results["normal_check"]["boot_id"])
                return
            except RootAccessUnavailable as error:
                # Only this read-only request can return here. Keep every failed
                # session and history entry; the next request waits for Enter.
                self.recover_pending()
                message = (str(error) + "\n\nAfter checking Shell's permission, press Enter to retry verification.\n"
                           "This repeats only the root check. You can also type q and resume later.")

    def run(self):
        support_notice = slot_notice(self.state["binding"])
        self.ui.device_label = (self.state["name"] + " | serial " + self.state["binding"]["ro.serialno"] +
                                " | slot " + self.state["binding"]["ro.boot.slot_suffix"][-1].upper())
        self.ui.page("Continue your walkthrough")
        self.recover_pending()
        while True:
            phase, results = self.state["phase"], self.state["results"]
            self.ui.page(PAGE_TITLES.get(phase, "Check saved progress"))
            if phase == "consent":
                self.ui.accept(support_notice + RISK_TEXT)
                self.state["consent"] = {"version": RISK_VERSION, "binding": dict(self.state["binding"]),
                                         "factory_reset_and_data_loss": True, "possible_permanent_brick": True,
                                         "method": "accept_decline_buttons",
                                         "at": now()}
                self.advance("host")
            elif phase == "host":
                self.ui.say("\nChecking the computer and the tools needed to prepare this onn...")
                self.act("baseline")
            elif phase == "baseline":
                self.ui.say("\nChecking this onn's Android version, free space and unlock status...")
                self.act(lambda r: "unlock" if r["secure_lock_state"] == 1 else (
                    "unlocked_check" if r["unlocked_on_current_boot"] else "unlock_reboot"))
            elif phase == "unlock":
                self.ui.accept(support_notice + "Ready to unlock " + self.state["name"] + ".\n"
                               "This will factory-reset it and erase its data. This step is performed only once.\n"
                               "Once the unlock is confirmed, this will restart the onn automatically.")
                self.ui.say("Unlocking once and checking the result...")
                self.act(lambda r: "unlocked_check" if r.get("already_unlocked") else "unlock_reboot")
            elif phase == "unlock_reboot":
                self.ui.say("Checking the confirmed unlock and requesting one automatic restart...")
                self.act("unlock_return", proof=results.get("unlock", results.get("baseline")))
            elif phase == "unlock_return":
                if "unlock_reboot" not in results:
                    # Older saved guides waited here for a manual restart.
                    self.advance("unlock_reboot")
                    continue
                restart = ("Android has already restarted." if results["unlock_reboot"]["already_rebooted"]
                           else "The automatic restart was requested once. Wait for Android to start.")
                self.ui.wait(restart + "\n"
                             "Finish the fresh Android setup after the factory reset and reconnect Wi-Fi.\n"
                             "The next screen will help you turn debugging back on.")
                self.advance("debugging_after_unlock")
            elif phase == "debugging_after_unlock":
                self.ui.wait("After the factory reset, enable Developer mode and both debugging settings again.\n\n"
                             + DEBUGGING_SETUP)
                self.advance("unlocked_check")
            elif phase == "unlocked_check":
                self.ui.say("\nFinding the same onn and confirming that it is unlocked...")
                self.act("fetch")
            elif phase == "fetch":
                self.ui.say("\nDownloading the matching Android files and Magisk. This may take several minutes...")
                self.act("patch")
            elif phase == "patch":
                self.ui.say("\nPreparing the root image on your selected onn and installing the Magisk app...")
                self.act("build", **results["fetch"])
            elif phase == "build":
                self.ui.say("\nPreparing and checking two temporary Android systems for this onn. This can take a while...")
                self.act("stock_stage", **results["host"])
            elif phase in ("stock_stage", "root_stage"):
                kind = "stock" if phase == "stock_stage" else "root"
                self.ui.say("\nCopying the temporary Android system to the onn. It stays off until the next restart step...")
                self.act(kind + "_wait", build=results["build"]["build"], kind=kind,
                         rehearsal=results.get("stock_return", {}).get("rehearsal"))
            elif phase in ("stock_wait", "root_wait"):
                kind = "stock" if phase == "stock_wait" else "root"
                self.ui.say("\nChecking whether the temporary Android installation has finished...")
                result = self.act(lambda r: kind + "_boot" if r["installed_disabled_verified"] else phase,
                                  stage=results[kind + "_stage"]["stage"])
                if not result["installed_disabled_verified"]:
                    self.ui.wait("It is still installing. Keep the onn on and wait for installation to finish.\n"
                                 "Press Enter to check again. This does not restart installation.")
            elif phase in ("stock_boot", "root_boot"):
                kind = "stock" if phase == "stock_boot" else "root"
                self.ui.wait("The temporary system is ready. The onn will restart once and show an Android setup screen.\n"
                             "This temporary system is set to run for one boot. Have the TV remote ready.")
                self.ui.say("Requesting the one restart. A lost connection during restart is expected.")
                self.act("stock_seen" if kind == "stock" else "root_setup", stage=results[kind + "_stage"]["stage"])
            elif phase == "stock_seen":
                self.ui.wait("Watch the TV. Wait until the temporary Android setup screen appears.\n"
                             "You do not need to sign in. Continue only after you have seen that setup screen.")
                self.advance("stock_return_prompt")
            elif phase == "stock_return_prompt":
                self.ui.wait("Now unplug the onn's power and plug it back in once. Wait for your original Android to return.\n"
                             "If you have already done this, leave it running. Continue when the original system is back.")
                self.advance("stock_return")
            elif phase == "stock_return":
                self.ui.say("\nChecking that your original Android returned...")
                self.act("root_stage", stage=results["stock_stage"]["stage"], observed_setup=True, power_cycled_back=True)
            elif phase == "root_setup":
                self.ui.wait("The TV should show temporary Android setup again. Use the remote to connect it to Wi-Fi.\n"
                             "Leave the onn on this temporary system. You do not need to sign in to Google.\n"
                             "Continue when Wi-Fi is connected.")
                self.advance("root_ready")
            elif phase == "root_ready":
                self.ui.say("\nChecking the temporary system before installation...")
                self.act("flash_consent", stage=results["root_stage"]["stage"])
            elif phase == "flash_consent":
                self.ui.accept(support_notice + "Ready to install permanent root on " + self.state["name"] + ".\n"
                               "This will save and check the original image, install the prepared image once, and check it.\n"
                               "Once verification passes, this will restart the onn automatically.\n"
                               "This write could brick the onn. A backup does not guarantee recovery.\n"
                               "Keep its power and network connection steady until the result is shown.")
                self.state["install_accepted"] = True
                self.advance("install")
            elif phase == "install":
                require(self.state.get("install_accepted") is True, "The final installation was not accepted")
                self.ui.say("\nSaving the original image, installing root once, and verifying the installed image...")
                self.act("install_reboot", stage=results["root_stage"]["stage"], patch_result=results["patch"]["patch_result"],
                         accept_unproven_recovery=True)
            elif phase == "install_reboot":
                require(results["install"].get("verified_flash") is True, "Installation not verified; do not restart")
                self.ui.say("Checking the installed image again and requesting one automatic restart...")
                self.act("normal_return", proof=results["install"])
            elif phase == "normal_return":
                require(results["install"].get("verified_flash") is True, "Installation not verified; do not restart")
                if "install_reboot" not in results:
                    self.advance("install_reboot")
                    continue
                restart = ("Your original Android has already restarted." if results["install_reboot"]["already_rebooted"]
                           else "The installed image passed verification and the automatic restart was requested once.")
                self.ui.wait(restart + "\nWait for your original Android to return. Leave Magisk setup for the next step.")
                self.advance("normal_check")
            elif phase == "normal_check":
                self.ui.say("\nConfirming that the original Android has returned...")
                self.act("magisk_setup", patch_result=results["patch"]["patch_result"], install=results["install"])
            elif phase == "magisk_setup":
                self.ui.wait(MAGISK_OPEN + "\n\nAccept its 'Requires additional setup' message and let it restart.\n"
                             "When Android returns, use the same Settings path to open Magisk again.\n"
                             "If that setup restart already finished, continue without repeating it.")
                self.advance("verify")
            elif phase == "verify":
                self.verify()
            elif phase == "cleanup":
                self.ui.say("\nRoot is verified. Saving the stock backup and removing the temporary Android system...")
                self.act("updates", install_result=results["install"]["install_result"])
            elif phase == "updates":
                self.ui.say("\nTurning off automatic system updates to protect this setup. App updates are separate...")
                self.act("updates_check")
            elif phase == "updates_check":
                self.act("complete")
            elif phase == "complete":
                require(all(results.get(step, {}).get(flag) is True for step, flag in (
                    ("install", "verified_flash"), ("verify", "root_verified"), ("cleanup", "cleanup_completed"),
                    ("updates_check", "automatic_system_updates_disabled"))), "Saved completion proof is incomplete.")
                self.ui.say("\nSaved successful result for " + self.state["name"] + ":\n"
                            "  Unlocked. Permanent Magisk 30.7 root verified after restart.\n"
                            "  Temporary Android removed. Automatic system updates turned off.\n"
                            "Keep this computer's work folder: it contains your backups and progress.\n"
                            "To work on another onn, run the guide again and choose that device.")
                return
            else:
                raise Stop("Unknown saved guide step. No operation was started.")

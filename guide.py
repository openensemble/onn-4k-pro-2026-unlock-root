"""Live-device adapters for the shared guided walkthrough."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
from types import SimpleNamespace

import connection
import onn2026 as o
import root_workflow as flow
from tools.erofs_tools import check_erofs_tools
from guide_core import (Guide as CoreGuide, Paused, UI, accepted, initial_state,
                        validate_state, working, PREVIEW, RISK_TEXT, RISK_VERSION,
                        show_debugging_setup, ask_device_name, confirm_device, show_stopped)


class HostStore:
    @staticmethod
    def replace(path, value):
        connection.replace_record(path, value)

    @staticmethod
    def write_once(path, value):
        o.save(path, value, exclusive=True)

    @staticmethod
    def read_optional(path):
        return json.loads(Path(path).read_text()) if Path(path).is_file() else None


def load_state(path, record):
    return validate_state(json.loads(Path(path).read_text()), record)


class Guide(CoreGuide):
    def __init__(self, path, state, backend, ui):
        super().__init__(path, state, backend, ui, HostStore())


def find_ndk(explicit=None):
    if explicit:
        return Path(explicit).expanduser()
    candidates = [os.environ.get("ANDROID_NDK_HOME"), os.environ.get("ANDROID_NDK_ROOT")]
    sdk_roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                 str(Path.home() / "Android/Sdk"), str(Path.home() / "android-sdk")]
    candidates += [str(Path(root) / "ndk" / o.PROFILE["ndk_version"]) for root in sdk_roots if root]
    return next((Path(p) for p in candidates if p and (Path(p) / "source.properties").is_file()), None)


def host_setup(args, ui):
    o.require(platform.system() == "Linux" and platform.machine() in ("x86_64", "AMD64"),
              "This release needs an x86-64 Linux computer.")
    missing = [name for name in ("ip", args.mkfs_erofs, args.fsck_erofs, args.secilc) if not shutil.which(name)]
    o.require(not missing, "Install these computer tools before continuing: " + ", ".join(missing) +
              ". On Debian/Ubuntu: sudo apt-get install adb iproute2 erofs-utils secilc")
    check_erofs_tools(args.mkfs_erofs, args.fsck_erofs)
    ndk = find_ndk(args.ndk)
    if ndk is None:
        ui.page("Find the Android build tools", "The Android build tools (NDK " + o.PROFILE["ndk_version"] +
                ") are needed once on this computer.")
        value = ui.ask("Enter the folder containing that NDK, or q to leave: ")
        o.require(value, "No Android NDK folder was selected")
        ndk = Path(value).expanduser()
    o.require((ndk / "source.properties").is_file() and
              re.search(r"^Pkg\.Revision\s*=\s*" + re.escape(o.PROFILE["ndk_version"]) + r"\s*$",
                        (ndk / "source.properties").read_text(), re.M),
              "That folder is not Android NDK " + o.PROFILE["ndk_version"])
    compiler = ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang"
    o.require(compiler.is_file() and os.access(compiler, os.X_OK), "The NDK's Linux compiler is missing")
    key = args.adb_public_key.expanduser().resolve()
    o.require(key.is_file(), "Authorize this computer for debugging first; its ADB public key is missing")
    contents = key.read_bytes()
    o.require(len(contents.splitlines()) == 1 and b"PRIVATE" not in contents and
              len(base64.b64decode(contents.split()[0], validate=True)) == 524,
              "Use this computer's ADB public key (adbkey.pub), never its private key")
    for path in (o.ROOT, args.work):
        o.require(shutil.disk_usage(path).free >= 12 * 1024 ** 3, "Free at least 12 GiB on the computer before continuing")
    return {"ndk": str(ndk.resolve()), "adb_public_key": str(key), "mkfs_erofs": args.mkfs_erofs,
            "fsck_erofs": args.fsck_erofs, "secilc": args.secilc}


class Backend:
    def __init__(self, args, connection_file, ui):
        self.args, self.path, self.ui = args, Path(connection_file).resolve(), ui
        self.binding = connection.load(self.path)["binding"]

    def prepare(self, phase):
        o.verify_files()
        session = o.Session(self.args.work, "guide-" + phase)
        if phase in ("host", "fetch"):
            return SimpleNamespace(s=session, device=None)
        candidate = None
        task_title = self.ui.title
        while True:
            record = connection.load(self.path)
            o.require(record["binding"] == self.binding, "The saved device identity changed; no action was started")
            device = o.Device(session, record["target"], self.args.adb)
            device.binding, device.connection_path = dict(self.binding), self.path
            try:
                found = connection.reconnect(device, self.path, candidate=candidate, scan_lan=candidate is None)
                if found["ip_changed"]:
                    self.ui.say("Found your same onn at its new address: " + found["target"])
                if self.ui.title != task_title:
                    self.ui.page(task_title, "The selected onn is connected. Continuing this step...")
                return SimpleNamespace(s=session, device=device)
            except (o.Stop, OSError, ValueError) as error:
                self.ui.page("Reconnect your onn", "The selected onn could not be reached and verified.\n"
                             "Check Developer mode, USB debugging and Wi-Fi/network debugging.\n"
                             "Accept this computer on the TV if asked.\n\nConnection details: " + str(error))
                value = self.ui.ask("Press Enter to look again, enter its new IP address, or q to leave: ")
                candidate = connection.network_target(value) if value else None

    def invoke(self, phase, context, values):
        device = context.device
        args = SimpleNamespace(**{key: Path(value) if key in (
            "ndk", "adb_public_key", "build", "rehearsal", "stage", "patch_result", "install_result") and value else value
            for key, value in values.items()})
        if phase == "host":
            return host_setup(self.args, self.ui)
        if phase in ("baseline", "unlocked_check"):
            result = o.status(device)
            o.require(flow.state(device) == "normal", "The onn already has a temporary Android installation. Preserve its records.")
            if phase == "baseline":
                root = device.shell("existing-root", "test", "-x", "/debug_ramdisk/su", check=False)
                o.require(root["returncode"] == 1 and not root["timed_out"],
                          "Root is already present, or could not be checked. This guide will not reinstall over it.")
                space = device.text("free-space", "df", "-k", "/data").splitlines()[-1].split()
                o.require(len(space) >= 4 and space[3].isdigit() and int(space[3]) >= 8 * 1024 ** 2,
                          "Free at least 8 GiB on the onn before continuing.")
            else:
                o.require(result["unlocked_on_current_boot"] and result["boot_completed"] == "1",
                          "Android has not finished an unlocked boot. Finish setup, then resume this check.")
            return result
        if phase == "unlock":
            return o.unlock(device, True)
        if phase in ("unlock_reboot", "install_reboot"):
            return flow.reboot_verified(device, phase.removesuffix("_reboot"), values["proof"])
        if phase == "fetch":
            return o.fetch_inputs(context.s)
        if phase == "patch":
            return o.prepare_root(device, values["stock_init_boot"], values["magisk_apk"])
        if phase == "build":
            return flow.build_dsu(device, args)
        if phase in ("stock_stage", "root_stage"):
            return flow.stage_dsu(device, args)
        if phase in ("stock_wait", "root_wait"):
            return flow.dsu_status(device, args)
        if phase in ("stock_boot", "root_boot"):
            return flow.boot_dsu(device, args)
        if phase == "stock_return":
            return flow.stock_return(device, args)
        if phase == "root_ready":
            stage = flow.read(args.stage)
            flow.bound(device, stage)
            o.require(stage["kind"] == "root" and flow.state(device) == "running\ninstalled\ndisabled"
                      and device.boot_id() != stage["boot_id"], "The expected one-boot temporary system is not ready.")
            return {"temporary_system_ready": True}
        if phase == "install":
            return flow.install_root(device, args)
        if phase == "normal_check":
            patch, _ = flow.patch_receipt(device, args.patch_result)
            boot = device.boot_id()
            o.require(boot != values["install"]["boot_id"] and flow.state(device) == "installed\ndisabled"
                      and device.text("original-android", "settings", "get", "secure", "android_id") == patch["android_id"],
                      "The original Android has not returned yet. Resume this check once it has started.")
            return {"boot_id": boot, "original_android_verified": True}
        if phase == "verify":
            o.require(device.boot_id() != values["before"],
                      "No restart after Magisk setup has been observed. Finish that setup, then resume the check.")
            return flow.verify_root(device, args)
        if phase == "cleanup":
            return flow.cleanup_root(device, args)
        if phase in ("updates", "updates_check"):
            return flow.automatic_updates(device, SimpleNamespace(command="disable-updates" if phase == "updates" else "updates-status"))
        raise o.Stop("Unknown guide operation")


def select_connection(args, ui):
    if args.connection:
        connection.load(args.connection)
        return args.connection.resolve()
    rows = connection.devices(args.work)["saved_devices"]
    if rows and not args.target:
        while True:
            ui.page("Choose your onn", "Each unit keeps its own progress and backups.")
            for number, row in enumerate(rows, 1):
                ui.say(f"  {number}. {row['name']} | serial {row['serial']} | last address {row['last_target']}")
            ui.say("  N. Add another onn\n")
            choice = ui.ask("Enter a number, N for another onn, or q to leave: ")
            if choice.lower() == "n":
                break
            if choice.isdigit() and 1 <= int(choice) <= len(rows):
                return Path(rows[int(choice) - 1]["connection_file"])
            ui.say("Choose one of the listed numbers, N, or q.")
    show_debugging_setup(ui)
    ui.page("Connect your onn", "Find the onn's IP address in its network settings.")
    target = args.target or ui.ask("Enter the onn's IP address from its network settings, or q to leave: ")
    session = o.Session(args.work, "guide-select")
    while True:
        try:
            ui.page("Connect your onn", "Connecting to the selected address and checking this onn...")
            target = connection.network_target(target)
            device = o.Device(session, target, args.adb)
            session.run("adb-connect", [device.adb, "connect", target], timeout=5, check=False)
            current = connection.adb_devices(session.run("adb-devices", [device.adb, "devices", "-l"], timeout=5)["stdout"])
            o.require(current.get(target) == "device", "Accept this computer's debugging request on the TV first.")
            props = device.identify()
            break
        except (o.Stop, OSError, ValueError) as error:
            ui.page("Check the connection", "This onn is not connected and verified yet: " + str(error))
            value = ui.ask("Press Enter to check that address again, enter a different IP, or q to leave: ")
            target = value or target
    name = ask_device_name(ui, props["ro.serialno"])
    confirm_device(ui, name, props["ro.serialno"])
    return Path(connection.remember(device, name))


def start(args, ui=None):
    ui = ui or UI()
    if args.preview:
        ui.say(PREVIEW + "\n" + RISK_TEXT + "\n\nPreview only. No device was contacted or changed.")
        return 0
    path = None
    try:
        o.require(sys.stdin.isatty(), "Open a terminal to use the interactive guide. Use guide --preview for an offline overview.")
        o.verify_files()
        o.require(shutil.which(args.adb or "adb"), "ADB is missing. Install Android platform-tools or use --adb /path/to/adb.")
        args.work = args.work.resolve()
        args.work.mkdir(parents=True, exist_ok=True)
        # The cache belongs to this checkout, even when a custom receipts folder is used.
        lock_directory = o.ROOT / "work"
        lock_directory.mkdir(parents=True, exist_ok=True)
        with (lock_directory / "guide.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise o.Stop("Another guide is already open. Use that window or finish it before starting another.") from None
            ui.page("Get started", "This will run the commands and keep track of the files. Keep the TV where you can see it.")
            selected = select_connection(args, ui)
            record = connection.load(selected)
            directory = args.work / "devices" / hashlib.sha256(record["binding"]["ro.serialno"].encode()).hexdigest()
            path = directory / "guide.json"
            if path.exists():
                state = load_state(path, record)
                o.require(Path(state["connection_file"]).resolve() == selected.resolve(),
                          "Saved progress uses another connection file; select its original saved device.")
                ui.say("Resuming this onn's saved progress.")
            else:
                o.require(not list(directory.glob("*-attempt.json")),
                          "This onn has records from earlier work outside the guide. Preserve those records; do not start over.")
                state = initial_state(record, selected)
            guide = Guide(path, state, Backend(args, selected, ui), ui)
            guide.persist()
            guide.run()
            return 0
    except (Paused, KeyboardInterrupt):
        show_stopped(ui)
        if path:
            ui.say("Saved progress: " + str(path))
        return 0
    except (o.Stop, OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as error:
        ui.page("Stopped before the next step", "No failed action will be automatically repeated.\n" + str(error))
        if path:
            ui.say("Keep your saved progress and receipts: " + str(path))
        return 2


def add_parser(sub):
    parser = sub.add_parser("guide", help="Complete guided unlock-to-root walkthrough; manages commands and files for you")
    parser.add_argument("--preview", action="store_true", help="Explain the steps without device access")
    parser.add_argument("--ndk", type=Path, help="Optional NDK folder; otherwise detected or requested")
    parser.add_argument("--adb-public-key", type=Path, default=Path.home() / ".android/adbkey.pub")
    parser.add_argument("--mkfs-erofs", default="mkfs.erofs")
    parser.add_argument("--fsck-erofs", default="fsck.erofs")
    parser.add_argument("--secilc", default="secilc")

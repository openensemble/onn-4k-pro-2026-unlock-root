#!/usr/bin/env python3
"""Pinned jarvis2 workflow. No startup edits or automatic write/reboot retries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from connection_identity import FIELDS
from guide_core import Stop, require, now

ROOT = Path(__file__).resolve().parent
PROFILE = json.loads((ROOT / "profile.json").read_text())
IDENTITY = FIELDS
UNLOCKED = {"ro.boot.flash.locked": "0", "ro.boot.vbmeta.device_state": "unlocked",
            "ro.boot.verifiedbootstate": "orange"}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def save(path, value, exclusive=False):
    """Durable receipt; exclusive attempts cannot silently replace earlier ones."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def parse_props(text):
    return dict(re.findall(r"^\[([^\]]+)\]: \[(.*)\]$", text, re.M))


def parse_events(text):
    try:
        events = [json.loads(line) for line in text.splitlines()]
    except (TypeError, ValueError) as error:
        raise Stop("Malformed native response; inspect receipts, no retry") from error
    require(events and all(isinstance(row, dict) for row in events),
            "Unexpected native response; inspect receipts, no retry")
    return events


def parse_lock_state(text):
    rows = [event for event in parse_events(text) if event.get("stage") == "lock_state"]
    require(len(rows) == 1 and rows[0].get("rc") == 0 and rows[0].get("ret") == "0x00000000"
            and type(rows[0].get("a")) is int and rows[0]["a"] in (0, 1),
            "No valid trusted-state response")
    return rows[0]["a"]


def validate_identity(props, unlocked=False):
    expected = {"ro.product.device": PROFILE["device"],
                "ro.build.fingerprint": PROFILE["fingerprint"],
                "ro.bootloader": PROFILE["bootloader"],
                "ro.boot.slot_suffix": PROFILE["slot"]}
    for key, value in expected.items():
        require(props.get(key) == value, "Unsupported or changed device: " + key)
    require(re.fullmatch(r"JUSA[A-Za-z0-9]+", props.get("ro.serialno", "")),
            "Missing or unexpected jarvis2 hardware serial")
    require(PROFILE["abi"] in props.get("ro.product.cpu.abilist", "").split(","),
            "Unsupported userspace ABI")
    if unlocked:
        require(all(props.get(k) == v for k, v in UNLOCKED.items()),
                "Post-reboot Android unlock properties are not proved")
        require(props.get("sys.boot_completed") == "1", "Android boot is incomplete")
    return {key: props[key] for key in IDENTITY}


def verify_files():
    entries = json.loads((ROOT / "NATIVE_SHA256.json").read_text())
    for relative, expected in entries.items():
        require(digest(ROOT / relative) == expected, "Packaged input changed: " + relative)


class Session:
    def __init__(self, work, purpose):
        self.work = Path(work).resolve()
        self.path = self.work / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                 + "-" + purpose + "-" + uuid.uuid4().hex[:8])
        self.path.mkdir(parents=True, mode=0o700)
        self.counter = 0

    def run(self, label, argv, timeout=15, check=True, start_new_session=False):
        self.counter += 1
        record = {"at": now(), "argv": [str(x) for x in argv], "timed_out": False}
        try:
            p = subprocess.run(record["argv"], capture_output=True, text=True,
                               errors="replace", timeout=timeout, check=False,
                               start_new_session=start_new_session)
            record.update(returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
        except subprocess.TimeoutExpired as error:
            decode = lambda x: x.decode(errors="replace") if isinstance(x, bytes) else x or ""
            record.update(returncode=None, timed_out=True,
                          stdout=decode(error.stdout), stderr=decode(error.stderr))
        except OSError as error:
            record.update(returncode=None, stdout="", stderr=str(error))
        save(self.path / f"{self.counter:03d}-{label}.json", record)
        if check:
            require(record["returncode"] == 0 and not record["timed_out"],
                    f"{label} failed or timed out. No retry. Receipts: {self.path}")
        return record


class Device:
    def __init__(self, session, target, adb):
        require(target and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:%\[\]-]{0,199}", target),
                "Supply one explicit --target ADB address or serial")
        self.s, self.target = session, target
        self.adb = shutil.which(adb or "adb")
        require(self.adb, "ADB not found; supply --adb /path/to/adb")
        self.binding = None
        self.connection_path = None

    def call(self, label, *args, **kwargs):
        # Terminal Ctrl+C must reach the guide's deferred-stop handler without
        # killing ADB in the middle of a protected write. Outside a protected
        # step, subprocess.run still cancels its child on KeyboardInterrupt.
        return self.s.run(label, [self.adb, "-s", self.target, *args], start_new_session=True, **kwargs)

    def shell(self, label, *args, **kwargs):
        return self.call(label, "shell", shlex.join([str(x) for x in args]), **kwargs)

    def text(self, label, *args, **kwargs):
        return self.shell(label, *args, **kwargs)["stdout"].strip()

    def identify(self, unlocked=False):
        props = parse_props(self.text("identity", "getprop"))
        binding = validate_identity(props, unlocked)
        require(self.binding is None or self.binding == binding,
                "Physical device or firmware differs from the saved binding; stop and use reconnect")
        self.binding = binding
        return props

    def boot_id(self):
        value = self.text("boot-id", "cat", "/proc/sys/kernel/random/boot_id")
        require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value),
                "Invalid boot ID")
        return value

    def push_checked(self, source, remote):
        self.call("push", "push", str(source), remote, timeout=30)
        actual = self.text("staged-hash", "sha256sum", remote).split()
        require(actual and actual[0] == digest(source), "Staged file hash mismatch")


def lock_state(device):
    verify_files()
    device.identify()
    boot = device.boot_id()
    remote = "/data/local/tmp/oe26-avb-read-" + uuid.uuid4().hex
    device.shell("mkdir-reader", "mkdir", "-m", "700", remote)
    device.push_checked(ROOT / "bin/armeabi-v7a/read_state", remote + "/read_state")
    device.shell("chmod-reader", "chmod", "700", remote + "/read_state")
    result = device.shell("read-secure-state", remote + "/read_state", timeout=10, check=False)
    device.identify()
    require(device.boot_id() == boot, "Boot changed during trusted-state read")
    device.shell("remove-reader", "rm", remote + "/read_state")
    device.shell("remove-reader-dir", "rmdir", remote)
    require(result["returncode"] == 0 and not result["timed_out"], "Trusted read failed; no write")
    return parse_lock_state(result["stdout"]), boot


def status(device, name=None):
    import connection
    props = device.identify()
    state, boot = lock_state(device)
    result = {"at": now(), "binding": device.binding, "boot_id": boot,
              "secure_lock_state": state, "boot_completed": props.get("sys.boot_completed"),
              "boot_properties": {k: props.get(k) for k in UNLOCKED},
              "unlocked_on_current_boot": state == 0 and all(props.get(k) == v for k, v in UNLOCKED.items()),
              "write_invocations": 0, "connection_file": connection.remember(device, name)}
    save(device.s.path / "status.json", result)
    return result


def unlock(device, accepted):
    require(accepted, "Unlocking will factory-reset the onn and erase its data. Read the instructions and supply --accept-data-loss.")
    props = device.identify()
    state, boot = lock_state(device)
    if state == 0:
        result = {"at": now(), "already_unlocked": True, "write_invocations": 0,
                  "next": "Verify current boot properties; do not repeat unlock."}
        save(device.s.path / "unlock-result.json", result)
        return result
    require(props.get("ro.boot.flash.locked") == "1" and props.get("ro.boot.verifiedbootstate") == "green",
            "Locked boot baseline does not match trusted state")
    identity_hash = hashlib.sha256(props["ro.serialno"].encode()).hexdigest()
    attempt = device.s.work / "devices" / identity_hash / "unlock-attempt.json"
    require(not attempt.exists(), "An unlock attempt already exists. Preserve it; no replay.")
    remote = "/data/local/tmp/oe26-avb-" + uuid.uuid4().hex
    device.shell("mkdir-writer", "mkdir", "-m", "700", remote)
    device.push_checked(ROOT / "bin/armeabi-v7a/unlock_once", remote + "/unlock_once")
    device.shell("chmod-writer", "chmod", "700", remote + "/unlock_once")
    device.identify()
    require(device.boot_id() == boot, "Boot changed before unlock; no write")
    save(attempt, {"at": now(), "binding": device.binding, "boot_id": boot,
                   "session": str(device.s.path), "remote": remote,
                   "max_write_invocations": 1, "automatic_retry": False}, exclusive=True)
    args = [remote + "/unlock_once", "--unlock-once", props["ro.serialno"],
            PROFILE["fingerprint"], PROFILE["bootloader"], PROFILE["slot"], boot,
            remote + "/attempt-consumed"]
    result = device.shell("unlock-once", *args, timeout=15, check=False)
    save(device.s.path / "unlock-execution.json", result)
    # Only remove the executable after confirming the selected hardware again.
    # The consumed marker and host attempt record are retained; neither schedules execution.
    device.identify()
    removed = device.shell("remove-writer", "rm", remote + "/unlock_once", check=False)
    require(result["returncode"] == 0 and not result["timed_out"],
            "Unlock result uncertain or failed. Do not rerun it or reboot automatically.")
    events = parse_events(result["stdout"])
    finish = [e for e in events if e.get("stage") == "finished"]
    require(len(finish) == 1 and finish[0].get("outcome") == 0 and finish[0].get("write_invocations") == 1,
            "Missing exact one-write proof; inspect receipts")
    readback, final_boot = lock_state(device)
    require(readback == 0 and final_boot == boot, "Independent unlock readback failed")
    output = {"at": now(), "binding": device.binding, "boot_id": boot,
              "secure_lock_state": 0, "write_invocations": 1,
              "writer_removed": removed["returncode"] == 0,
              "reboot_requested": False, "persistence_verified": False}
    save(device.s.path / "unlock-result.json", output)
    return output


def download(url, destination, expected, expected_size=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        require(digest(destination) == expected, "Existing download hash mismatch: " + str(destination))
        return destination
    temporary = destination.with_name(destination.name + ".partial-" + uuid.uuid4().hex)
    size = 0
    with urllib.request.urlopen(url, timeout=30) as source, temporary.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
            size += len(chunk)
            if size % (32 * 1024 * 1024) == 0:
                print(f"Downloaded {size // (1024 * 1024)} MiB: {destination.name}", flush=True)
        output.flush()
        os.fsync(output.fileno())
    require(expected_size is None or size == expected_size, "Download size mismatch; partial file retained")
    require(digest(temporary) == expected, "Download hash mismatch; partial file retained")
    require(not destination.exists(), "Download destination appeared; refusing replacement")
    temporary.rename(destination)
    return destination


def fetch_inputs(session):
    verify_files()
    cache = ROOT / "cache"
    apk = download(PROFILE["magisk_url"], cache / "Magisk-v30.7.apk", PROFILE["magisk_apk_sha256"])
    stock = cache / "firmware" / "init_boot.img"
    if not stock.exists():
        ota = download(PROFILE["ota_url"], cache / "jarvis2-15640252-full-ota.zip",
                       PROFILE["ota_sha256"], PROFILE["ota_size"])
        # Fixed full OTA only. The extractor rejects delta operations and checks
        # payload, operation and partition hashes before returning an image.
        session.run("extract-init-boot", [sys.executable, ROOT / "tools/extract_full_ota.py",
                    ota, stock.parent, "--partitions", "init_boot"], timeout=180)
    require(digest(stock) == PROFILE["init_boot_sha256"], "Extracted stock image mismatch")
    return {"stock_init_boot": str(stock), "magisk_apk": str(apk)}


def validate_image(path, stock=False):
    path = Path(path)
    require(path.is_file() and path.stat().st_size == PROFILE["init_boot_size"], "Wrong init_boot image size")
    with path.open("rb") as stream:
        header = stream.read(4096)
    require(header[:8] == b"ANDROID!", "Not an Android boot image")
    kernel, ramdisk = struct.unpack_from("<II", header, 8)
    version = struct.unpack_from("<I", header, 40)[0]
    require(kernel == 0 and version == 4 and 0 < ramdisk <= path.stat().st_size - 4096,
            "Wrong init_boot layout; kernel must be absent, header v4, ramdisk bounded")
    if stock:
        require(digest(path) == PROFILE["init_boot_sha256"], "Stock init_boot differs from the pinned OTA")
    return {"bytes": path.stat().st_size, "sha256": digest(path), "header_version": version,
            "kernel_size": kernel, "ramdisk_size": ramdisk}


def extract_magisk(apk, directory):
    require(digest(apk) == PROFILE["magisk_apk_sha256"], "Magisk APK differs from official pinned release")
    directory.mkdir()
    with zipfile.ZipFile(apk) as archive:
        for name in archive.namelist():
            dest = None
            if name.startswith("lib/armeabi-v7a/lib") and name.endswith(".so"):
                dest = name.rsplit("/", 1)[1][3:-3]
            elif name.startswith("assets/") and (name.endswith(".sh") or name == "assets/stub.apk"):
                dest = name.split("/", 1)[1]
            if dest is not None:
                require(re.fullmatch(r"[A-Za-z0-9_.-]+", dest), "Unexpected archive path")
                (directory / dest).write_bytes(archive.read(name))
    require(all((directory / name).is_file() for name in
                ["busybox", "magisk", "magiskinit", "magiskboot", "init-ld",
                 "stub.apk", "boot_patch.sh", "util_functions.sh"]), "Incomplete Magisk inputs")


def read_android_id(device, label):
    """Keep the exact installation token; Android's hexadecimal long is unpadded."""
    value = device.text(label, "settings", "get", "secure", "android_id")
    require(value not in ("", "null"),
            "Android installation identity unavailable: Android returned no ID. "
            "Finish Android setup and check the saved settings-read receipt. "
            "Keep the work folder; do not reset the onn or repeat the unlock.")
    require(re.fullmatch(r"[0-9a-fA-F]{1,16}", value),
            "Android installation identity unavailable: expected 1 to 16 hexadecimal "
            f"characters; received an invalid {len(value)}-character response. "
            "Keep the work folder and inspect the saved settings-read receipt.")
    return value


def prepare_root(device, stock, apk):
    props = device.identify(unlocked=True)
    state, boot = lock_state(device)
    require(state == 0, "Secure AVB unlock is not proved")
    android_id = read_android_id(device, "android-installation-id")
    stock, apk = Path(stock), Path(apk)
    original = validate_image(stock, stock=True)
    tools = device.s.path / "patch-tools"
    extract_magisk(apk, tools)
    remote = "/data/local/tmp/onn26-patch-" + uuid.uuid4().hex
    script = f'''#!/system/bin/sh
cd {remote} || exit 1
[ "$(id -u)" = 2000 ] || exit 2
export BOOTMODE=true
. ./util_functions.sh
TMPDIR={remote}/work-tmp
mkdir "$TMPDIR" || exit 1
api_level_arch_detect
export SOURCEDMODE=true KEEPVERITY=true KEEPFORCEENCRYPT=true
export PATCHVBMETAFLAG=false RECOVERYMODE=false LEGACYSAR=false
. ./boot_patch.sh ./stock-init_boot.img
'''
    (tools / "patch.sh").write_text(script)
    receipt = {"at": now(), "binding": device.binding, "android_id": android_id,
               "boot_id": boot, "remote": remote, "stock_image": original,
               "magisk_apk_sha256": PROFILE["magisk_apk_sha256"],
               "firmware_written": False, "startup_registration": False, "root_installed": False}
    save(device.s.path / "patch-request.json", receipt)
    device.shell("mkdir-patch", "mkdir", "-m", "700", remote)
    for path in sorted(tools.iterdir()):
        device.push_checked(path, remote + "/" + path.name)
    device.push_checked(stock, remote + "/stock-init_boot.img")
    device.shell("chmod-busybox", "chmod", "700", remote + "/busybox")
    device.identify(unlocked=True)
    require(device.boot_id() == boot, "Boot changed before image preparation")
    device.shell("patch-local-image-only", "sh", "-c",
                 f"cd {remote} && ASH_STANDALONE=1 ./busybox sh ./patch.sh", timeout=50)
    patched = device.s.path / "patched-init_boot.img"
    device.call("pull-patched-image", "pull", remote + "/new-boot.img", str(patched), timeout=30)
    patched_info = validate_image(patched)
    require(patched_info["sha256"] != original["sha256"], "Image was not changed by the patcher")
    shutil.copyfile(stock, device.s.path / "stock-init_boot.img")
    device.shell("mkdir-verify", "mkdir", remote + "/verify")
    device.shell("unpack-patch-check", "sh", "-c",
                 f"cd {remote}/verify && ../magiskboot unpack ../new-boot.img", timeout=20)
    test = device.shell("cpio-test", "sh", "-c",
                        f"cd {remote}/verify && ../magiskboot cpio ramdisk.cpio test", check=False)
    require(test["returncode"] == 1 and not test["timed_out"], "Ramdisk is not identified as Magisk")
    device.shell("extract-config", "sh", "-c",
                 f'cd {remote}/verify && ../magiskboot cpio ramdisk.cpio "extract .backup/.magisk config"')
    # The embedded .magisk file legitimately has mode 000. This is our temporary copy.
    device.shell("read-own-config-mode", "chmod", "600", remote + "/verify/config")
    config = device.text("read-patch-config", "cat", remote + "/verify/config")
    for expected in ["KEEPVERITY=true", "KEEPFORCEENCRYPT=true", "RECOVERYMODE=false", "VENDORBOOT=false"]:
        require(expected in config.splitlines(), "Unexpected patch configuration: " + expected)
    existing = device.shell("manager-presence", "pm", "path", "com.topjohnwu.magisk", check=False)
    require(not existing["timed_out"], "Manager query timed out")
    if not existing["stdout"].strip():
        install = device.call("install-manager", "install", str(apk), timeout=50)
        require("Success" in install["stdout"].splitlines(), "Magisk manager installation incomplete")
    device.identify(unlocked=True)
    require(device.boot_id() == boot, "Boot changed during image preparation")
    receipt.update(completed_at=now(), patched_on_target=True, patched_image=patched_info,
                   magisk_configuration=config, ramdisk_verified=True,
                   patch_result=str(device.s.path / "patch-result.json"),
                   next="Build DSU inputs and follow the stock-return rehearsal before root installation.")
    save(device.s.path / "patch-result.json", receipt)
    require(re.fullmatch(r"/data/local/tmp/onn26-patch-[0-9a-f]{32}", remote), "Unexpected cleanup path")
    device.shell("remove-own-staging", "rm", "-rf", remote)
    return receipt


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    address = p.add_mutually_exclusive_group()
    address.add_argument("--target", help="Initial explicit ADB address or serial")
    address.add_argument("--connection", type=Path,
                         help="Saved connection file from status; pins the physical device across commands")
    p.add_argument("--adb", help="Path to ADB, otherwise use PATH")
    p.add_argument("--work", type=Path, default=ROOT / "work")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-files", help="Verify packaged native inputs; no device access")
    status_parser = sub.add_parser("status", help="Identity, boot properties and trusted lock-state read only")
    status_parser.add_argument("--name", help="Optional local name, such as Living room, for this saved onn")
    sub.add_parser("devices", help="List saved onn names, serials and connection files; no device access")
    reconnect = sub.add_parser("reconnect", help="Find the saved onn after an IP change; no device writes or reboot")
    discovery = reconnect.add_mutually_exclusive_group()
    discovery.add_argument("--scan-lan", action="store_true", help="If needed, scan port 5555 on bounded local LANs")
    discovery.add_argument("--candidate", help="Check only this IPv4 address, optionally followed by :port")
    u = sub.add_parser("unlock", help="One direct trusted-service unlock; never reboots")
    u.add_argument("--accept-data-loss", action="store_true")
    sub.add_parser("fetch-inputs", help="Download pinned official OTA/Magisk and extract init_boot")
    root = sub.add_parser("prepare-root", help="Patch init_boot on the selected device; no firmware flash")
    root.add_argument("--stock-init-boot", type=Path, default=ROOT / "cache/firmware/init_boot.img")
    root.add_argument("--magisk-apk", type=Path, default=ROOT / "cache/Magisk-v30.7.apk")
    import root_workflow
    root_workflow.add_parsers(sub)
    import guide
    guide.add_parser(sub)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(argv or ["guide"])
    os.umask(0o077)
    if args.command == "guide":
        import guide
        return guide.start(args)
    session = Session(args.work, args.command)
    try:
        if args.command == "verify-files":
            verify_files()
            result = {"packaged_inputs_verified": True}
        elif args.command == "fetch-inputs":
            result = fetch_inputs(session)
        elif args.command == "devices":
            import connection
            result = connection.devices(args.work)
        else:
            import connection
            saved = connection.load(args.connection) if args.connection else None
            device = Device(session, saved["target"] if saved else args.target, args.adb)
            if saved:
                device.binding = saved["binding"]
                device.connection_path = args.connection.resolve()
            if args.command == "status":
                result = status(device, args.name)
            elif args.command == "reconnect":
                verify_files()
                result = connection.reconnect(device, args.connection, args.candidate, args.scan_lan)
            elif args.command == "unlock":
                result = unlock(device, args.accept_data_loss)
            elif args.command == "prepare-root":
                result = prepare_root(device, args.stock_init_boot, args.magisk_apk)
            else:
                import root_workflow
                result = root_workflow.run(device, args)
        print(json.dumps(result, indent=2))
        print("Receipts:", session.path)
        return 0
    except (Stop, OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as error:
        save(session.path / "stopped.json", {"at": now(), "reason": str(error), "automatic_retry": False})
        print("STOP:", error, "\nReceipts:", session.path, file=sys.stderr)
        return 2


if __name__ == "__main__":
    # The workflow module must share these guard/exception classes with the CLI.
    sys.modules["onn2026"] = sys.modules[__name__]
    sys.exit(main())

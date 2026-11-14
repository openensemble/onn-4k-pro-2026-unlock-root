"""Network-only DSU workflow for the single pinned firmware profile."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
import uuid

import onn2026 as o
import dsu_recovery
import host_build
from guide_core import MAGISK_OPEN, ROOT_ACCESS_HELP, RootAccessUnavailable, TemporarySystemPresent

COMMANDS = {"build-dsu", "stage-dsu", "dsu-status", "boot-dsu", "confirm-stock-return",
            "install-root", "verify-root", "cleanup-root", "disable-updates", "updates-status", "enable-updates"}


def read(path):
    return json.loads(Path(path).read_text())


def bound(device, record):
    device.identify(unlocked=True)
    o.require(record["binding"] == device.binding, "Receipt belongs to another device, build, or slot")


def journal(device, name):
    if name.startswith("dsu-"):
        return dsu_recovery.journal_directory(device) / name
    key = hashlib.sha256(device.binding["ro.serialno"].encode()).hexdigest()
    return device.s.work / "devices" / key / name


def state(device):
    return device.text("dsu-state", "gsi_tool", "status")


def automatic_updates(device, args):
    device.identify(unlocked=True)
    o.require(state(device) in ("normal", "installed\ndisabled"), "Change the original Android setting, not a DSU guest")
    boot = device.boot_id()
    key = "ota_disable_automatic_update"
    before = device.text("automatic-update-setting-before", "settings", "get", "global", key)
    o.require(before in ("null", "0", "1"), "Unexpected automatic-update setting; inspect before changing it")
    value = {"disable-updates": "1", "enable-updates": "0"}.get(args.command)
    changed = value is not None and before != value
    if changed:
        o.save(device.s.path / "update-setting-request.json",
               {"at": o.now(), "binding": device.binding, "boot_id": boot, "before": before,
                "requested": value, "key": key, "automatic_retry": False}, exclusive=True)
        device.shell("set-automatic-system-updates", "settings", "put", "global", key, value)
    after = device.text("automatic-update-setting-readback", "settings", "get", "global", key)
    o.require(value is None or after == value, "Automatic-update setting readback failed")
    device.identify(unlocked=True)
    o.require(device.boot_id() == boot, "Boot changed while applying update preference")
    report = {"at": o.now(), "binding": device.binding, "boot_id": boot, "key": key,
              "before": before, "after": after, "changed": changed,
              "automatic_system_updates_disabled": after == "1", "boot_script_installed": False,
              "scope": "Android automatic system-update scheduling; manual updates remain available"}
    o.save(device.s.path / "automatic-updates.json", report)
    return report


def root(device, label, command, helper="/system/bin/onn26-su", **kwargs):
    return device.shell(label, helper, "-c", command, **kwargs)


def partition_guard(binding):
    """Shell preconditions for every privileged read of the bound partition.

    Callers use set -e. The flash template independently repeats these checks
    immediately before its sole write, alongside the image and identity guards.
    """
    slot = o.slot_profile(binding)
    partition = shlex.quote(o.root_partition(binding))
    block = shlex.quote(slot["root_block_path"])
    size_path = shlex.quote("/sys/class/block/" + Path(slot["root_block_path"]).name + "/size")
    return ("test \"$(getprop ro.boot.slot_suffix)\" = " + shlex.quote(binding["ro.boot.slot_suffix"]) + "; "
            "test -b " + partition + "; "
            "test \"$(readlink -f " + partition + ")\" = " + block + "; "
            "test \"$(cat " + size_path + ")\" = " + str(o.PROFILE["init_boot_size"] // 512) + "; ")


def partition_hash_command(binding):
    return "set -e; " + partition_guard(binding) + "sha256sum " + shlex.quote(o.root_partition(binding))


def reboot_verified(device, kind, proof):
    """Request one normal restart after verified work; never replay a request."""
    o.require(kind in ("unlock", "install"), "Unknown restart step")
    o.require(isinstance(proof, dict) and isinstance(proof.get("boot_id"), str)
              and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", proof["boot_id"]),
              "The completed step has no valid boot record; no restart")
    device.identify(unlocked=kind == "install")
    o.require(proof.get("binding") == device.binding, "Restart proof belongs to another onn, build, or slot")
    boot = device.boot_id()
    if kind == "unlock":
        o.require(type(proof.get("secure_lock_state")) is int and proof["secure_lock_state"] == 0
                  and type(proof.get("write_invocations")) is int and proof["write_invocations"] in (0, 1),
                  "Unlock was not verified; no restart")
        secure, observed_boot = o.lock_state(device)
        o.require(secure == 0 and observed_boot == boot and state(device) == "normal",
                  "Unlock or Android state changed; no restart")
    else:
        o.require(proof.get("verified_flash") is True
                  and re.fullmatch(r"[0-9a-f]{64}", proof.get("image_sha256", ""))
                  and isinstance(proof.get("readback"), dict)
                  and proof["readback"].get("sha256") == proof["image_sha256"]
                  and proof["readback"].get("bytes") == o.PROFILE["init_boot_size"],
                  "Installed image was not verified; no restart")
        o.require(isinstance(proof.get("patch_result"), str), "Missing patch receipt; no restart")
        patch, _ = patch_receipt(device, Path(proof["patch_result"]))
        o.require(patch["patched_image"]["sha256"] == proof["image_sha256"]
                  and patch["android_id"] == proof.get("original_android_id"),
                  "The installed image or original Android record changed; no restart")

    record = {"at": o.now(), "binding": dict(device.binding), "kind": kind,
              "boot_id_before": boot, "verified_step_boot_id": proof["boot_id"],
              "automatic_retry": False}
    if boot != proof["boot_id"]:
        # A user may already have restarted while the guide was closed.
        device.identify(unlocked=True)
        if kind == "install":
            o.require(state(device) == "installed\ndisabled"
                      and device.text("returned-android-before-reboot", "settings", "get", "secure", "android_id")
                      == proof["original_android_id"],
                      "The original Android has not returned; no additional restart")
        o.require(device.boot_id() == boot, "Boot changed during the restart check")
        result = {**record, "reboot_requests": 0, "already_rebooted": True}
    else:
        attempt = journal(device, "normal-" + kind + "-reboot-attempt.json")
        o.require(not attempt.exists(), "A restart request already exists. It will not be repeated.")
        if kind == "install":
            o.require(state(device) == "running\ninstalled\ndisabled",
                      "The installation system must be running with its next boot disabled; no restart")
            readback = root(device, "before-root-reboot-readback", partition_hash_command(device.binding))
            o.require(readback["stdout"].strip() == proof["image_sha256"] + "  " + o.root_partition(device.binding),
                      "Installed image changed after verification; do not restart")
        device.shell("flush-before-normal-reboot", "sync", timeout=20)
        device.identify(unlocked=kind == "install")
        expected_state = "normal" if kind == "unlock" else "running\ninstalled\ndisabled"
        o.require(device.boot_id() == boot and state(device) == expected_state,
                  "Boot or temporary-system state changed before restart")
        # This durable exclusive journal is consumed before the only reboot call.
        o.save(attempt, record, exclusive=True)
        response = device.call("single-" + kind + "-reboot", "reboot", timeout=8, check=False)
        result = {**record, "reboot_requests": 1, "already_rebooted": False, "transport": response}
    o.save(device.s.path / (kind + "-reboot-result.json"), result, exclusive=True)
    return result


def build_matches(device, build):
    device.identify(unlocked=True)
    expected = {"serial": device.binding["ro.serialno"], "profile": o.slot_profile(device.binding)["id"],
                "fingerprint": o.PROFILE["fingerprint"], "bootloader": o.PROFILE["bootloader"],
                "slot": device.binding["ro.boot.slot_suffix"]}
    o.require(all(build.get(k) == v for k, v in expected.items()), "DSU build binding mismatch")
    o.require(build.get("single_use_required") is True and build.get("root_helper_autostart") is False,
              "Unexpected DSU boot behavior")
    o.require(all(build.get(k) is True for k in ["all_original_inode_metadata_preserved",
                  "all_file_bytes_verified", "policy_compiled", "avb_verified"]), "Incomplete DSU validation")


def build_dsu(device, args):
    device.identify(unlocked=True)
    o.verify_files()
    print("Preparing the temporary Android systems on this computer (15-minute build limit). "
          "Ctrl-C stops the build; the guide can resume it.", flush=True)
    cache = o.ROOT / "cache"
    ota = o.download(o.PROFILE["ota_url"], cache / "jarvis2-15640252-full-ota.zip",
                     o.PROFILE["ota_sha256"], o.PROFILE["ota_size"])
    images = cache / "dsu-images"
    if not images.exists():
        device.s.run("extract-dsu-inputs", [sys.executable, o.ROOT / "tools/extract_dsu_ota.py",
                     ota, images, "--partitions", *o.PROFILE["dsu_partitions"]], timeout=600)
    for name, pin in o.PROFILE["dsu_partitions"].items():
        path = images / (name + ".img")
        o.require(path.is_file() and path.stat().st_size == pin["size"] and o.digest(path) == pin["sha256"],
                  "Incomplete or changed cached DSU input: " + name)
    avbtool = cache / "avbtool.py"
    if not avbtool.exists():
        with urllib.request.urlopen(o.PROFILE["avbtool"]["url"], timeout=30) as response:
            data = base64.b64decode(b"".join(response.read().split()), validate=True)
        o.require(hashlib.sha256(data).hexdigest() == o.PROFILE["avbtool"]["sha256"], "AVB source changed")
        with avbtool.open("xb") as stream:
            stream.write(data)
    output = device.s.path / "dsu-build"
    host_build.run(device.s, "build-offline-dsu", [sys.executable, o.ROOT / "tools/build_dsu.py",
                 "--serial", device.binding["ro.serialno"], "--slot", device.binding["ro.boot.slot_suffix"],
                 "--images", images,
                 "--ndk", args.ndk.resolve(), "--adb-public-key", args.adb_public_key.resolve(),
                 "--avbtool", avbtool, "--output", output,
                 "--mkfs-erofs", args.mkfs_erofs, "--fsck-erofs", args.fsck_erofs,
                 "--secilc", args.secilc])
    result = read(output / "build.json")
    build_matches(device, result)
    return {"build": str(output / "build.json"), "firmware_written": False,
            "next": "Stage the stock DSU first and demonstrate its single-use return."}


def stage_dsu(device, args):
    build = read(args.build)
    build_matches(device, build)
    cycle = dsu_recovery.cycle(device)
    current = state(device)
    if args.kind == "stock":
        if current != "normal":
            raise TemporarySystemPresent("An existing DSU must not be overwritten. " + dsu_recovery.HINT)
    else:
        o.require(args.rehearsal, "Supply this device's successful stock-return receipt")
        rehearsal = read(args.rehearsal)
        bound(device, rehearsal)
        dsu_recovery.check_stage(device, rehearsal)
        o.require(rehearsal.get("stock_single_use_return_verified") is True,
                  "Stock single-use return was not demonstrated")
        o.require(rehearsal["build_sha256"] == o.digest(args.build), "Rehearsal used another DSU build")
        o.require(current == "installed\ndisabled", "Expected the disabled rehearsal DSU")
    device.identify(unlocked=True)
    boot = device.boot_id()
    android_id = o.read_android_id(device, "original-android-id")
    if args.kind == "root":
        o.require(android_id == rehearsal["android_id"], "Original Android installation changed")
    archive = Path(build[args.kind + "_gzip"])
    expected = build[args.kind + "_gzip_sha256"]
    o.require(o.digest(archive) == expected, "DSU archive changed")
    attempt = journal(device, "dsu-" + args.kind + "-stage-attempt.json")
    if attempt.exists():
        raise TemporarySystemPresent("This staging action was already attempted; inspect its receipt. " + dsu_recovery.HINT)
    remote = "/storage/emulated/0/Download/onn26-" + args.kind + "-" + expected[:12] + ".raw.gz"
    missing = device.shell("staging-name-unused", "test", "!", "-e", remote, check=False)
    o.require(not missing["timed_out"] and missing["returncode"] in (0, 1), "Remote staging path check failed")
    if missing["returncode"] == 1:
        raise TemporarySystemPresent("Remote staging path already exists. " + dsu_recovery.HINT)
    request = {"at": o.now(), "binding": device.binding, "boot_id": boot, "android_id": android_id,
               "kind": args.kind, "build": str(args.build.resolve()), "build_sha256": o.digest(args.build),
               "remote": remote, "archive_sha256": expected, "dsu_cycle": cycle,
               "enabled": False, "automatic_retry": False}
    o.save(attempt, request, exclusive=True)
    # A transfer timeout never initiates installation and is never automatically retried.
    # 600 s allows for slower connections (VPN, international users).
    device.call("push-dsu", "push", str(archive), remote, timeout=600)
    o.require(device.text("archive-readback", "sha256sum", remote, timeout=30).split()[0] == expected,
              "Uploaded DSU archive changed")
    device.identify(unlocked=True)
    o.require(device.boot_id() == boot, "Boot changed before DSU install")
    o.save(device.s.path / "stage.json", request, exclusive=True)
    device.shell("stage-disabled-dsu", "am", "start-activity", "-n", "com.android.dynsystem/.VerificationActivity",
                 "-a", "android.os.image.action.START_INSTALL", "-d", "file://" + remote,
                 "--el", "KEY_SYSTEM_SIZE", str(build["system_bytes"]),
                 "--el", "KEY_USERDATA_SIZE", "4294967296", "--ez", "KEY_ENABLE_WHEN_COMPLETED", "false",
                 "--ez", "KEY_ONE_SHOT", "true", "--es", "KEY_DSU_SLOT", "dsu", timeout=20)
    return {"stage": str(device.s.path / "stage.json"), "enable_requested": False,
            "next": "Run dsu-status with this stage receipt; installation is asynchronous."}


def dsu_status(device, args):
    stage = read(args.stage)
    bound(device, stage)
    dsu_recovery.check_stage(device, stage)
    current = state(device)
    log = device.text("installation-log", "logcat", "-d", "-v", "brief", "-s",
                      "DynamicSystemInstallationService", "InstallationAsyncTask", "gsid", "DynamicSystemService",
                      timeout=20)
    tail = log.rsplit(stage["remote"].rsplit("/", 1)[1], 1)
    ready = (current == "installed\ndisabled" and len(tail) == 2 and
             re.search(r"status: READY, cause: INSTALL_COMPLETED\b", tail[-1]) is not None)
    result = {"at": o.now(), "binding": device.binding, "boot_id": device.boot_id(), "state": current,
              "stage_sha256": o.digest(args.stage), "installed_disabled_verified": ready}
    if ready:
        o.require(result["boot_id"] == stage["boot_id"], "Boot changed since staging")
        o.save(args.stage.parent / "installed.json", result)
    return result


def boot_dsu(device, args):
    stage = read(args.stage)
    bound(device, stage)
    dsu_recovery.check_stage(device, stage)
    installed = read(args.stage.parent / "installed.json")
    o.require(installed.get("installed_disabled_verified") is True and
              installed["stage_sha256"] == o.digest(args.stage), "Run dsu-status and verify completed installation first")
    o.require(device.boot_id() == stage["boot_id"] and state(device) == "installed\ndisabled",
              "Boot or DSU state changed")
    attempt = journal(device, "dsu-" + stage["kind"] + "-enable-attempt.json")
    record = {"at": o.now(), "binding": device.binding, "boot_id_before": stage["boot_id"],
              "stage": str(args.stage.resolve()), "stage_sha256": o.digest(args.stage),
              "one_shot": True, "automatic_retry": False}
    o.save(attempt, record, exclusive=True)
    device.shell("enable-single-use-dsu", "gsi_tool", "enable", "--single-boot", "--dsuslot", "dsu")
    device.shell("flush-single-use-state", "sync", timeout=20)
    o.require(state(device) == "installed\nenabled", "DSU enable readback failed; do not repeat")
    device.identify(unlocked=True)
    o.require(device.boot_id() == stage["boot_id"], "Boot changed before reboot")
    o.save(journal(device, "dsu-" + stage["kind"] + "-reboot-attempt.json"), record, exclusive=True)
    o.save(args.stage.parent / "boot-request.json", record, exclusive=True)
    response = device.call("single-dsu-reboot", "reboot", timeout=8, check=False)
    return {**record, "reboot_requests": 1, "transport": response,
            "next": "Observe the TV. Never repeat the reboot because ADB timed out."}


def stock_return(device, args):
    stage = read(args.stage)
    bound(device, stage)
    dsu_recovery.check_stage(device, stage)
    o.require(stage["kind"] == "stock" and args.observed_setup and args.power_cycled_back,
              "Confirm that stock DSU reached setup and one physical power cycle returned original Android")
    boot_request = read(args.stage.parent / "boot-request.json")
    o.require(boot_request["stage_sha256"] == o.digest(args.stage), "Missing bound DSU boot request")
    boot = device.boot_id()
    o.require(boot != stage["boot_id"] and state(device) == "installed\ndisabled", "No demonstrated stock return")
    android_id = device.text("returned-android-id", "settings", "get", "secure", "android_id")
    o.require(android_id == stage["android_id"], "This is not the original Android installation")
    result = {"at": o.now(), "binding": device.binding, "boot_id": boot, "android_id": android_id,
              "dsu_cycle": stage.get("dsu_cycle"),
              "build_sha256": stage["build_sha256"], "stock_single_use_return_verified": True,
              "owner_observed_setup_and_power_cycle": True,
              "modified_init_boot_recovery_proven": False}
    o.save(device.s.path / "stock-return.json", result, exclusive=True)
    return {**result, "rehearsal": str(device.s.path / "stock-return.json")}


def patch_receipt(device, path):
    receipt = read(path)
    bound(device, receipt)
    o.require(receipt.get("patched_on_target") is True and receipt.get("ramdisk_verified") is True,
              "Only a verified on-target patch is accepted")
    o.require(receipt["magisk_apk_sha256"] == o.PROFILE["magisk_apk_sha256"], "Magisk version changed")
    for flag in ["KEEPVERITY=true", "KEEPFORCEENCRYPT=true", "RECOVERYMODE=false", "VENDORBOOT=false"]:
        o.require(flag in receipt["magisk_configuration"].splitlines(), "Unexpected patch flags")
    image = path.parent / "patched-init_boot.img"
    o.require(o.validate_image(image) == receipt["patched_image"], "Patched image no longer matches its receipt")
    return receipt, image


def pull_block(device, helper, dest):
    partition = o.root_partition(device.binding)
    command = shlex.join([helper, "-c", "set -e; " + partition_guard(device.binding) +
                         "head -c " + str(o.PROFILE["init_boot_size"]) + " " + shlex.quote(partition)])
    argv = [device.adb, "-s", device.target, "exec-out", command]
    with dest.open("xb") as out:
        response = subprocess.run(argv, stdout=out, stderr=subprocess.PIPE, timeout=30, check=False,
                                  start_new_session=True)
        out.flush()
        os.fsync(out.fileno())
    result = {"argv": argv, "returncode": response.returncode, "stderr": response.stderr.decode(errors="replace"),
              "file": str(dest), "bytes": dest.stat().st_size, "sha256": o.digest(dest),
              "partition": partition, "binding": dict(device.binding)}
    o.save(dest.with_suffix(".receipt.json"), result, exclusive=True)
    o.require(response.returncode == 0 and result["bytes"] == o.PROFILE["init_boot_size"], "Incomplete binary backup")
    return result


def flash_script(binding, boot, remote, attempt, patch_hash):
    slot = o.slot_profile(binding)
    values = {"SERIAL": binding["ro.serialno"], "FINGERPRINT": o.PROFILE["fingerprint"],
              "BOOTLOADER": o.PROFILE["bootloader"], "BOOT_ID": boot,
              "SLOT": binding["ro.boot.slot_suffix"], "PARTITION": o.root_partition(binding),
              "BLOCK_PATH": slot["root_block_path"],
              "SIZE_PATH": "/sys/class/block/" + Path(slot["root_block_path"]).name + "/size",
              "PATCH_PATH": remote + "/patched-init_boot.img", "STOCK_PATH": remote + "/stock-init_boot.img",
              "PATCH_SHA256": patch_hash, "STOCK_SHA256": o.PROFILE["init_boot_sha256"], "ATTEMPT_PATH": attempt}
    template = (o.ROOT / "native/flash_init_boot.sh.in").read_text()
    for name, value in values.items():
        template = template.replace("@@" + name + "@@", shlex.quote(value))
    o.require("@@" not in template, "Unexpanded flash script token")
    return template


def install_root(device, args):
    o.require(args.accept_unproven_recovery,
              "Read the recovery limitation. This release requires explicit --accept-unproven-recovery to flash.")
    patch, image = patch_receipt(device, args.patch_result)
    stage = read(args.stage)
    bound(device, stage)
    dsu_recovery.check_stage(device, stage)
    o.require(stage["kind"] == "root" and stage["android_id"] == patch["android_id"], "Not this installation's root DSU")
    build = read(Path(stage["build"]))
    o.require(o.digest(stage["build"]) == stage["build_sha256"], "Build receipt changed")
    build_matches(device, build)
    request = read(args.stage.parent / "boot-request.json")
    o.require(request["stage_sha256"] == o.digest(args.stage), "Root DSU boot request missing")
    o.require(state(device) == "running\ninstalled\ndisabled", "Root DSU is not running disabled")
    # Bypass the Google TV setup wizard if it is still running.
    # The DSU image pre-embeds the ADB key so the connection works before setup
    # completes, but the wizard blocks activation on slow or VPN connections.
    # These two settings skip it without requiring any TV interaction.
    device.shell("bypass-setup-provisioned", "settings", "put", "global",
                 "device_provisioned", "1", check=False)
    device.shell("bypass-setup-complete", "settings", "put", "secure",
                 "user_setup_complete", "1", check=False)
    boot = device.boot_id()
    o.require(boot != stage["boot_id"], "DSU did not reboot")
    o.require(device.text("dsu-helper-hash", "sha256sum", "/system/bin/onn26-su").split()[0] == build["helper_sha256"],
              "Unexpected root helper")
    attempt = journal(device, "root-install-attempt.json")
    o.require(not attempt.exists(), "An installation attempt already exists; do not replay it")
    partition = o.root_partition(device.binding)
    proof = root(device, "root-and-stock-proof", "id; " + partition_hash_command(device.binding))
    o.require("uid=0(root)" in proof["stdout"] and "u:r:onn26_root:s0" in proof["stdout"] and
              o.PROFILE["init_boot_sha256"] + "  " + partition in proof["stdout"].splitlines(),
              "DSU root or stock partition proof failed")
    backup = pull_block(device, "/system/bin/onn26-su", device.s.path / "stock-init_boot.img")
    o.require(backup["sha256"] == o.PROFILE["init_boot_sha256"], "Device backup differs from pinned stock")
    token = uuid.uuid4().hex
    remote = "/data/local/tmp/onn26-root-install-" + token
    device_attempt = "/metadata/onn26-root-install-" + token
    local_script = device.s.path / "flash-once.sh"
    local_script.write_text(flash_script(device.binding, boot, remote, device_attempt, patch["patched_image"]["sha256"]))
    device.shell("mkdir-root-staging", "mkdir", "-m", "755", remote)
    for source, name in [(image, "patched-init_boot.img"), (Path(backup["file"]), "stock-init_boot.img"),
                         (local_script, "flash-once.sh")]:
        device.push_checked(source, remote + "/" + name)
        device.shell("readable-root-input", "chmod", "644", remote + "/" + name)
    preflight = root(device, "native-preflight-no-write", "sh " + shlex.quote(remote + "/flash-once.sh") + " --check-only")
    o.require(preflight["stdout"].strip() == "PREFLIGHT_VERIFIED", "Native preflight incomplete")
    record = {"at": o.now(), "binding": device.binding, "boot_id": boot, "partition": partition,
              "original_android_id": patch["android_id"], "patch_result": str(args.patch_result.resolve()),
              "patch_result_sha256": o.digest(args.patch_result), "image_sha256": patch["patched_image"]["sha256"],
              "stock_backup": backup, "stage": str(args.stage.resolve()), "remote": remote,
              "device_attempt": device_attempt, "max_write_invocations": 1, "automatic_retry": False,
              "accept_unproven_recovery": True, "reboot_requested": False}
    o.save(attempt, record, exclusive=True)
    result = root(device, "flash-once", "sh " + shlex.quote(remote + "/flash-once.sh") + " --write-once",
                  timeout=30, check=False)
    o.require(result["returncode"] == 0 and not result["timed_out"] and
              result["stdout"].splitlines() == ["FLASH_READBACK_VERIFIED", record["image_sha256"]],
              "Flash failed or is uncertain. Keep Android running; do not retry or reboot.")
    readback = pull_block(device, "/system/bin/onn26-su", device.s.path / "installed-init_boot.img")
    o.require(readback["sha256"] == record["image_sha256"], "Independent readback mismatch; do not reboot")
    device.identify(unlocked=True)
    o.require(device.boot_id() == boot and state(device) == "running\ninstalled\ndisabled", "Post-write state changed")
    device.shell("remove-used-flasher", "rm", remote + "/flash-once.sh")
    record.update(readback=readback, verified_flash=True)
    o.save(device.s.path / "install-result.json", record, exclusive=True)
    return {**record, "install_result": str(device.s.path / "install-result.json"),
            "next": "Reboot normally once. " + MAGISK_OPEN +
                    " Finish Magisk's additional setup and its restart, then run verify-root and grant Shell access."}


def verify_root(device, args):
    patch, _ = patch_receipt(device, args.patch_result)
    boot = device.boot_id()
    o.require(boot != patch["boot_id"], "No reboot after image preparation")
    aid = device.text("original-installation", "settings", "get", "secure", "android_id")
    o.require(aid == patch["android_id"], "Not the original Android installation")
    current = state(device)
    o.require(current in ("normal", "installed\ndisabled"), "DSU is still running or enabled")
    partition = o.root_partition(device.binding)
    command = ("set -e; " + partition_guard(device.binding) +
               "id; /debug_ramdisk/magisk -v; /debug_ramdisk/magisk -V; getenforce; "
               "sha256sum " + shlex.quote(partition) + "; "
               "test -s /data/adb/magisk/busybox; test -s /data/adb/magisk/magiskboot; "
               "test -s /data/adb/magisk/magisk; echo MAGISK_ENVIRONMENT_COMPLETE")
    result = root(device, "verify-permanent-root", command, helper="/debug_ramdisk/su", timeout=35, check=False)
    receipts = "\nReceipts: " + str(device.s.path)
    if result["timed_out"]:
        raise RootAccessUnavailable("The root verification command timed out. Root has not been verified.\n\n"
                                    + ROOT_ACCESS_HELP + receipts)
    if (result["returncode"] != 0 and "uid=0(root)" not in result["stdout"] and
            (result["returncode"] == 13 or "Permission denied" in result["stderr"] + result["stdout"])):
        raise RootAccessUnavailable("Magisk denied Shell root access. Root has not been verified.\n\n"
                                    + ROOT_ACCESS_HELP + receipts)
    o.require(result["returncode"] == 0, "Root verification command failed. Inspect the saved command output." + receipts)
    lines = result["stdout"].splitlines()
    o.require("uid=0(root)" in result["stdout"] and "u:r:magisk:s0" in result["stdout"] and
              "30.7:MAGISK:R" in lines and "30700" in lines and "Enforcing" in lines and
              patch["patched_image"]["sha256"] + "  " + partition in lines and
              "MAGISK_ENVIRONMENT_COMPLETE" in lines, "Permanent root/environment verification failed" + receipts)
    report = {"at": o.now(), "binding": device.binding, "boot_id": boot, "android_id": aid,
              "root_verified": True, "magisk_version": "30.7", "selinux_enforcing": True,
              "partition": partition, "partition_sha256": patch["patched_image"]["sha256"], "environment_complete": True,
              "dsu_state": current, "firmware_written": False}
    o.save(device.s.path / "root-verification.json", report)
    return report


def cleanup_root(device, args):
    installed = read(args.install_result)
    bound(device, installed)
    o.require(installed.get("verified_flash") is True, "Unverified installation")
    o.require(o.digest(installed["patch_result"]) == installed["patch_result_sha256"], "Patch receipt changed")
    args.patch_result = Path(installed["patch_result"])
    verified = verify_root(device, args)
    stage = read(Path(installed["stage"]))
    bound(device, stage)
    stock = Path(installed["stock_backup"]["file"])
    o.require(o.digest(stock) == o.PROFILE["init_boot_sha256"], "Stock backup changed")
    compressed = device.s.path / "stock-init_boot.img.gz"
    with stock.open("rb") as source, compressed.open("xb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as stream:
            shutil.copyfileobj(source, stream)
    remote = "/data/local/tmp/onn26-backup-" + uuid.uuid4().hex + ".gz"
    device.push_checked(compressed, remote)
    directory = "/data/magisk_backup_" + o.PROFILE["root_stock_sha1"]
    command = ("set -e; test ! -e " + directory + "/boot.img.gz; mkdir -p " + directory +
               "; chmod 700 " + directory + "; cp " + shlex.quote(remote) + " " + directory +
               "/boot.img.gz; chown 0:0 " + directory + " " + directory + "/boot.img.gz; chmod 600 " +
               directory + "/boot.img.gz; sync; /data/adb/magisk/busybox gzip -dc " + directory +
               "/boot.img.gz | sha256sum")
    result = root(device, "save-magisk-stock-backup", command, helper="/debug_ramdisk/su")
    o.require(result["stdout"].split()[0] == o.PROFILE["init_boot_sha256"], "On-device stock backup mismatch")
    device.shell("remove-backup-transfer", "rm", remote)
    if state(device) == "installed\ndisabled":
        # This is the package's own guest: original Android identity and its
        # successful installation receipt were checked before removing it.
        device.shell("remove-used-dsu", "gsi_tool", "wipe", timeout=30)
    o.require(state(device) == "normal", "DSU cleanup incomplete")
    candidates = [(stage["remote"], stage["archive_sha256"])]
    # The stock rehearsal download is tracked by its own immutable stage receipt.
    stock_stage_attempt = journal(device, "dsu-stock-stage-attempt.json")
    if stock_stage_attempt.exists():
        stock_stage = read(stock_stage_attempt)
        bound(device, stock_stage)
        candidates.append((stock_stage["remote"], stock_stage["archive_sha256"]))
    for path, expected in candidates:
        o.require(re.fullmatch(r"/storage/emulated/0/Download/onn26-(stock|root)-[0-9a-f]{12}\.raw\.gz", path),
                  "Unexpected cleanup path")
        exists = device.shell("known-download-present", "test", "-f", path, check=False)
        o.require(not exists["timed_out"] and exists["returncode"] in (0, 1), "Download state uncertain")
        if exists["returncode"] == 0:
            o.require(device.text("known-download-digest", "sha256sum", path, timeout=30).split()[0] == expected,
                      "Staged file changed; leaving it in place")
            device.shell("remove-own-download", "rm", path)
    return {**verified, "dsu_state": "normal", "cleanup_completed": True,
            "stock_backup_on_device": directory + "/boot.img.gz"}


def add_parsers(sub):
    build = sub.add_parser("build-dsu", help="Build device-bound stock/root DSU inputs offline; no firmware write")
    build.add_argument("--ndk", type=Path, required=True)
    build.add_argument("--adb-public-key", type=Path, default=Path.home() / ".android/adbkey.pub")
    build.add_argument("--mkfs-erofs", default="mkfs.erofs")
    build.add_argument("--fsck-erofs", default="fsck.erofs")
    build.add_argument("--secilc", default="secilc")
    stage = sub.add_parser("stage-dsu", help="Install a separate DSU guest with activation disabled")
    stage.add_argument("--build", type=Path, required=True)
    stage.add_argument("--kind", choices=("stock", "root"), required=True)
    stage.add_argument("--rehearsal", type=Path)
    for name, help_text in [("dsu-status", "Read installation progress; never enables or reboots"),
                            ("boot-dsu", "Explicitly enable a completed DSU for one boot and reboot once")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--stage", type=Path, required=True)
    returned = sub.add_parser("confirm-stock-return", help="Record observed setup, physical power cycle and original return")
    returned.add_argument("--stage", type=Path, required=True)
    returned.add_argument("--observed-setup", action="store_true")
    returned.add_argument("--power-cycled-back", action="store_true")
    install = sub.add_parser("install-root", help="Back up and flash the bound active init_boot once from the disabled root DSU")
    install.add_argument("--stage", type=Path, required=True)
    install.add_argument("--patch-result", type=Path, required=True)
    install.add_argument("--accept-unproven-recovery", action="store_true")
    verify = sub.add_parser("verify-root", help="Check permanent Magisk, original Android, SELinux and partition hash")
    verify.add_argument("--patch-result", type=Path, required=True)
    cleanup = sub.add_parser("cleanup-root", help="After root verification, save stock backup and remove this workflow's DSU")
    cleanup.add_argument("--install-result", type=Path, required=True)
    sub.add_parser("disable-updates", help="Persist Android's automatic system-update preference as disabled")
    sub.add_parser("updates-status", help="Read the automatic system-update preference")
    sub.add_parser("enable-updates", help="Explicitly re-enable automatic system-update scheduling")


def run(device, args):
    o.verify_files()
    handlers = {"build-dsu": build_dsu, "stage-dsu": stage_dsu, "dsu-status": dsu_status,
                "boot-dsu": boot_dsu, "confirm-stock-return": stock_return, "install-root": install_root,
                "verify-root": verify_root, "cleanup-root": cleanup_root,
                "disable-updates": automatic_updates, "updates-status": automatic_updates,
                "enable-updates": automatic_updates}
    return handlers[args.command](device, args)

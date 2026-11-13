"""Explicit DSU restart transactions. Never delete a firmware attempt or reboot."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import uuid

import connection
import onn2026 as o
from guide_core import initial_state, working

SCHEMA = "onn26-dsu-restart-v1"
RESTART_PHASES = {"consent", "host", "baseline", "unlocked_check", "fetch", "patch", "build",
                  "stock_stage", "stock_wait", "stock_boot", "stock_seen", "stock_return_prompt",
                  "stock_return", "root_stage", "root_wait", "root_boot", "root_setup", "root_ready",
                  "flash_consent", "install"}
DOWNLOAD = re.compile(r"/storage/emulated/0/Download/onn26-(stock|root)-[0-9a-f]{12}\.raw\.gz")
HINT = "Keep the work folder. Use guide --restart-dsu to review removal and start temporary-system setup again."


def read(path):
    record = json.loads(Path(path).read_text())
    o.require(isinstance(record, dict), "Invalid DSU record: " + str(path))
    return record


def directory(device):
    return device.s.work / "devices" / hashlib.sha256(device.binding["ro.serialno"].encode()).hexdigest()


def pending(directory):
    path = Path(directory) / "dsu-restart.json"
    if not path.exists():
        return None
    record = read(path)
    o.require(record.get("schema") == SCHEMA and type(record.get("complete")) is bool
              and isinstance(record.get("cycle"), str)
              and re.fullmatch(r"[0-9a-f]{32}", record.get("cycle", "")), "Invalid DSU restart record")
    return None if record["complete"] else record


def cycle(device, allow_pending=False):
    base = directory(device)
    restart = pending(base)
    o.require(allow_pending or not restart, "A temporary-system restart is unfinished. " + HINT)
    path = base / "dsu-cycle.json"
    if not path.exists():
        o.require(restart or not ((base / "dsu-restart.json").exists()
                                 or any((base / "dsu-restarts").glob("*/completed.json"))),
                  "The active DSU cycle record is missing. Preserve the restart records; legacy receipts cannot be reused.")
        return None  # Original releases used journals directly in the device directory.
    record = read(path)
    value = record.get("cycle", "")
    o.require(record.get("schema") == SCHEMA and record.get("binding") == device.binding
              and isinstance(value, str)
              and re.fullmatch(r"[0-9a-f]{32}", value), "Invalid DSU cycle binding")
    completed = base / "dsu-restarts" / value / "completed.json"
    o.require(completed.is_file() and o.digest(completed) == record.get("completed_sha256"),
              "The DSU restart completion receipt is missing or changed")
    proof = read(completed)
    o.require(proof.get("schema") == SCHEMA and proof.get("binding") == device.binding
              and proof.get("cycle") == value and proof.get("temporary_cleanup_verified") is True
              and proof.get("dsu_state") == "normal", "The DSU cycle has no verified cleanup proof")
    return value


def journal_directory(device, allow_pending=False):
    value = cycle(device, allow_pending)
    return directory(device) / "dsu-cycles" / value if value else directory(device)


def check_stage(device, record):
    o.require(record.get("dsu_cycle") == cycle(device),
              "This temporary-system receipt was retired by a restart. Use the new guide receipts.")


def downloads(device):
    # Expand only our filename prefix; validate every returned name before using it.
    names = device.text("restart-downloads", "sh", "-c",
                        'for f in /storage/emulated/0/Download/onn26-*.raw.gz; do '
                        'if [ -e "$f" ] || [ -L "$f" ]; then printf "%s\\n" "$f"; fi; done').splitlines()
    o.require(len(names) <= 64 and len(set(names)) == len(names), "Unexpected number of DSU downloads")
    result = []
    for name in sorted(names):
        o.require(DOWNLOAD.fullmatch(name), "Unexpected temporary download name; leaving files in place")
        kind = device.shell("restart-download-type", "sh", "-c",
                            "test -f " + shlex.quote(name) + " && test ! -L " + shlex.quote(name), check=False)
        o.require(kind["returncode"] == 0 and not kind["timed_out"],
                  "Temporary download is not a regular file; leaving it in place")
        size = device.text("restart-download-size", "stat", "-c", "%s", name)
        digest = device.text("restart-download-hash", "sha256sum", name, timeout=60).split()
        o.require(size.isdigit() and len(digest) == 2 and re.fullmatch(r"[0-9a-f]{64}", digest[0])
                  and digest[1] == name, "Could not identify the temporary download")
        result.append({"path": name, "bytes": int(size), "sha256": digest[0]})
    return result


def check_host(device, state):
    base = directory(device)
    o.require(not (base / "root-install-attempt.json").exists()
              and not (base / "normal-install-reboot-attempt.json").exists(),
              "A permanent-root installation was attempted. DSU restart cannot clear or repeat it; inspect its receipts.")
    if state:
        o.require(state["binding"] == device.binding and state["phase"] in RESTART_PHASES
                  and "install" not in state["results"],
                  "This guide is not at a restartable temporary-system step. Preserve the saved installation records.")
        o.require(not state.get("pending") or state["pending"]["phase"] in RESTART_PHASES,
                  "An uncertain unlock or firmware operation cannot be abandoned by DSU restart.")


def original_android(device, expected_id=None, expected_boot=None):
    props = device.identify(unlocked=True)
    o.require(props.get("sys.boot_completed") == "1", "Wait for original Android to finish starting before DSU restart.")
    current = device.text("restart-dsu-state", "gsi_tool", "status")
    o.require(current in ("normal", "installed\ndisabled"),
              "DSU restart requires original Android with the temporary system disabled. "
              "A running or enabled temporary system cannot be removed here. Keep the receipts.")
    boot = device.boot_id()
    android_id = o.read_android_id(device, "restart-original-android")
    o.require(expected_id is None or android_id == expected_id, "Original Android installation changed; restart refused.")
    o.require(expected_boot is None or boot == expected_boot, "Boot changed during DSU restart; no further cleanup.")
    root = device.shell("restart-existing-root", "test", "-e", "/debug_ramdisk/su", check=False)
    o.require(root["returncode"] == 1 and not root["timed_out"],
              "Permanent root is present or could not be excluded. Use the existing root-verification/cleanup flow.")
    return {"boot_id": boot, "android_id": android_id, "dsu_state": current}


def inspect(device, state):
    device.identify(unlocked=True)
    check_host(device, state)
    current = original_android(device)
    for path in sorted(journal_directory(device, allow_pending=True).glob("dsu-*-attempt.json")):
        record = read(path)
        o.require(record.get("binding") == device.binding, "A DSU attempt belongs to another device or firmware")
        if "android_id" in record:
            o.require(record["android_id"] == current["android_id"], "Original Android differs from the DSU attempt")
        if path.name.endswith("-reboot-attempt.json"):
            o.require(record.get("boot_id_before") != current["boot_id"],
                      "A temporary-system reboot was requested on this same boot and is unresolved. "
                      "Inspect the TV and receipts before restarting temporary-system setup.")
    return {**current, "downloads": downloads(device)}


def restart_guide(location, plan):
    path = location / "new-guide.json"
    o.require(path.is_file() and o.digest(path) == plan.get("new_guide_sha256"),
              "The saved restart guide is missing or changed; preserve the restart records.")
    result = read(path)
    o.require(result.get("binding") == plan["binding"] and result.get("dsu_restart") == plan["cycle"]
              and result.get("phase") == "consent" and result.get("pending") is None and result.get("results") == {},
              "The saved restart guide is not a fresh walkthrough")
    return result


def restart(backend, guide_path, state, record, selected, ui):
    """Review, remove and commit a fresh cycle; interruptions retain all evidence."""
    context = backend.prepare("restart_dsu")
    device, guide_path = context.device, Path(guide_path)
    base = directory(device)
    transaction = pending(base)
    current = inspect(device, state)
    if transaction:
        o.require(transaction["binding"] == device.binding, "DSU restart belongs to another device")
        location = base / "dsu-restarts" / transaction["cycle"]
        plan = read(location / "plan.json")
        o.require(o.digest(location / "plan.json") == transaction["plan_sha256"], "DSU restart plan changed")
        o.require(plan.get("schema") == SCHEMA and plan.get("binding") == device.binding
                  and plan.get("cycle") == transaction["cycle"], "DSU restart plan has a different binding")
        template = restart_guide(location, plan)
        o.require(cycle(device, allow_pending=True) in (plan["previous_cycle"], transaction["cycle"]),
                  "The DSU cycle changed during recovery; preserve the restart records")
        o.require(plan["android_id"] == current["android_id"], "Original Android changed since restart was requested")
        guide_digest = o.digest(guide_path) if guide_path.exists() else None
        published = read(guide_path) if guide_path.exists() else None
        # A host interruption may have already published the fresh guide. Only
        # its connection path may differ after moving the preserved work folder.
        fresh_guide = isinstance(published, dict) and {
            **published, "connection_file": template["connection_file"]} == template
        o.require(guide_digest == plan["guide_sha256"] or fresh_guide,
                  "Guide progress changed during DSU restart; keep both sets of receipts")
    ui.accept("Remove the temporary Android image and its temporary data, then upload fresh images\n"
              "and repeat both temporary boots? Original Android must be running.\n\n"
              "Selected onn: " + record.get("name", device.binding["ro.serialno"]) +
              " | serial " + device.binding["ro.serialno"] + "\n"
              "Temporary-system state: " + current["dsu_state"].replace("\n", ", ") + "\n"
              "Uploads to remove:\n" + ("\n".join("  " + item["path"] for item in current["downloads"]) or "  None") +
              "\n\nPrevious progress, attempt records and backups will be kept.\n"
              "This removes the current DSU even if its original host records are unavailable.\n"
              "The guide will prepare a fresh attempt and ask again before installing permanent root.",
              title="Remove temporary system and start again")
    with working(ui, protect=True) as cancellation:
        # Recheck after the owner has reviewed the exact device and deletion list.
        check_host(device, state)
        original_android(device, current["android_id"], current["boot_id"])
        o.require(downloads(device) == current["downloads"], "Temporary downloads changed during confirmation")
        if transaction:
            restart_guide(location, plan)
        if not transaction:
            value = uuid.uuid4().hex
            location = base / "dsu-restarts" / value
            plan = {"schema": SCHEMA, "cycle": value, "binding": dict(device.binding), "at": o.now(),
                    "previous_cycle": cycle(device), **current,
                    "guide_sha256": o.digest(guide_path) if guide_path.exists() else None}
            if guide_path.exists():
                o.save(location / "previous-guide.json", read(guide_path), exclusive=True)
            new_state = initial_state(record, selected)
            new_state["dsu_restart"] = value
            o.save(location / "new-guide.json", new_state, exclusive=True)
            plan["new_guide_sha256"] = o.digest(location / "new-guide.json")
            o.save(location / "plan.json", plan, exclusive=True)
            transaction = {"schema": SCHEMA, "binding": dict(device.binding), "cycle": value,
                           "plan_sha256": o.digest(location / "plan.json"), "complete": False}
            connection.replace_record(base / "dsu-restart.json", transaction)
        completed = location / "completed.json"
        if not completed.exists():
            # A new explicit confirmation is required after any failed cleanup.
            # Stop the asynchronous installer before removing its source archive or DSU.
            o.save(context.s.path / "restart-attempt.json", {**transaction, **current}, exclusive=True)
            device.shell("stop-dsu-installer", "am", "force-stop", "com.android.dynsystem")
            original_android(device, current["android_id"], current["boot_id"])
            device.shell("remove-abandoned-dsu", "gsi_tool", "wipe", timeout=60)
            after = original_android(device, current["android_id"], current["boot_id"])
            o.require(after["dsu_state"] == "normal", "Temporary-system removal is not confirmed. " + HINT)
            for item in current["downloads"]:
                original_android(device, current["android_id"], current["boot_id"])
                name = shlex.quote(item["path"])
                command = ("set -e; test -f " + name + "; test ! -L " + name +
                           '; test "$(stat -c %s ' + name + ')" = ' + str(item["bytes"]) +
                           '; test "$(sha256sum ' + name + ')" = ' +
                           shlex.quote(item["sha256"] + "  " + item["path"]) +
                           "; rm -- " + name + "; test ! -e " + name)
                device.shell("remove-abandoned-upload", "sh", "-c", command, timeout=60)
            after = original_android(device, current["android_id"], current["boot_id"])
            o.require(after["dsu_state"] == "normal" and not downloads(device), "Temporary cleanup incomplete. " + HINT)
            connection.create_record(completed, {
                "schema": SCHEMA, "binding": dict(device.binding), "cycle": transaction["cycle"],
                "at": o.now(), **after, "temporary_cleanup_verified": True,
                "firmware_write_invocations": 0, "reboot_invocations": 0})
        else:
            # Device cleanup finished before a host update was interrupted. Finish
            # only those host updates, without repeating even the DSU removal.
            result = read(completed)
            o.require(result.get("temporary_cleanup_verified") is True and result.get("binding") == device.binding
                      and result.get("cycle") == transaction["cycle"] and current["dsu_state"] == "normal"
                      and not current["downloads"], "Saved cleanup no longer matches the device")
        new_state = restart_guide(location, plan)
        new_state["connection_file"] = str(Path(selected).resolve())
        connection.replace_record(base / "dsu-cycle.json", {
            "schema": SCHEMA, "binding": dict(device.binding), "cycle": transaction["cycle"],
            "completed_sha256": o.digest(completed)})
        connection.replace_record(guide_path, new_state)
        connection.replace_record(base / "dsu-restart.json", {**transaction, "complete": True})
    ui.say("Temporary system removed. Previous records are preserved in " + str(location) +
           ". Starting a fresh walkthrough; an already-unlocked onn skips unlocking.")
    if cancellation["requested"]:
        from guide_core import Paused
        raise Paused()
    return new_state

#!/usr/bin/env python3
"""Offline, device-bound DSU builder. It never contacts or flashes a device."""
import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import onn2026 as o
from tools.erofs_metadata import Metadata
from tools.erofs_tools import check_erofs_tools

POLICY = '''
; DSU-only owner console; stock Android policy is not changed.
(type onn26_root)
(roletype object_r onn26_root)
(typeattributeset domain (onn26_root))
(typeattributeset coredomain (onn26_root))
(type onn26_root_exec)
(roletype object_r onn26_root_exec)
(typeattributeset file_type (onn26_root_exec))
(typeattributeset exec_type (onn26_root_exec))
(typeattributeset system_file_type (onn26_root_exec))
(allow shell onn26_root_exec (file (getattr open read execute map)))
(allow shell onn26_root (process (transition siginh rlimitinh)))
(allow onn26_root onn26_root_exec (file (entrypoint getattr open read execute map)))
(allow onn26_root shell (process (sigchld)))
(typetransition shell onn26_root_exec process onn26_root)
(typepermissive onn26_root)
'''
ADB_INIT = '''# Network ADB in the isolated, single-use DSU only. No root command or reboot.
on property:sys.boot_completed=1
    setprop service.adb.tcp.port 5555
    start adbd
'''


def progress(message):
    print("[build] " + message, flush=True)


def command(argv, log, description):
    progress(description + "...")
    with Path(log).open("x") as stream:
        try:
            # The supervising runner enforces the overall 15-minute deadline
            # and stops this process group on timeout or cancellation.
            subprocess.run([str(v) for v in argv], check=True, stdout=stream,
                           stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as error:
            raise o.Stop(f"{description} failed (exit {error.returncode}). Tool log: {log}") from error


def metadata(path):
    reader = Metadata(path)
    try:
        reader.walk()
        return reader.rows
    finally:
        reader.data.close()
        reader.file.close()


def compress(source, dest):
    label = "Compressing " + Path(dest).name
    progress(label + "...")
    total = Path(source).stat().st_size
    copied, next_update = 0, 64 * 1024 * 1024
    with Path(source).open("rb") as inp, Path(dest).open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0,
                          compresslevel=1) as out:
            while chunk := inp.read(1024 * 1024):
                out.write(chunk)
                copied += len(chunk)
                if copied >= next_update:
                    progress(f"{label}: {copied * 100 // total}%")
                    next_update += 64 * 1024 * 1024
    progress(label + ": complete")


def build(args):
    o.require(__debug__, "Python optimization disables parser assertions; omit -O")
    progress("Checking the computer's EROFS tools...")
    check_erofs_tools(args.mkfs_erofs, args.fsck_erofs)
    o.require(re.fullmatch(r"JUSA[A-Za-z0-9]+", args.serial), "Invalid hardware serial")
    slot = o.slot_profile({"ro.boot.slot_suffix": args.slot})
    o.require(o.PROFILE["ndk_version"] in (args.ndk / "source.properties").read_text(),
              "Use the recorded Android NDK revision")
    o.require(o.digest(args.avbtool) == o.PROFILE["avbtool"]["sha256"], "AVB tool hash mismatch")
    public_key = args.adb_public_key.read_bytes()
    o.require(len(public_key.splitlines()) == 1 and b"PRIVATE" not in public_key,
              "Supply the owner's ADB PUBLIC key, never a private key")
    o.require(len(base64.b64decode(public_key.split()[0], validate=True)) == 524,
              "Unexpected Android ADB public-key encoding")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    roots = {}
    for name, pin in o.PROFILE["dsu_partitions"].items():
        progress("Checking the stock " + name + " image...")
        image = args.images / (name + ".img")
        o.require(image.stat().st_size == pin["size"] and o.digest(image) == pin["sha256"],
                  "Stock partition does not match the pinned OTA: " + name)
        roots[name] = out / (name + "-stock")
        command([args.fsck_erofs, "--extract=" + str(roots[name]), "--no-preserve", image],
                out / (name + "-extract.log"), "Extracting " + name)
    progress("Reading the original filesystem metadata...")
    rows = metadata(args.images / "system.img")
    o.save(out / "stock-metadata.json", rows)
    tree = out / "tree"
    progress("Copying the temporary system's files...")
    shutil.copytree(roots["system"], tree, symlinks=True)

    header = {"ONN26_SERIAL": args.serial, "ONN26_FINGERPRINT": o.PROFILE["fingerprint"],
              "ONN26_BOOTLOADER": o.PROFILE["bootloader"], "ONN26_SLOT": args.slot}
    (out / "onn26_binding.h").write_text("".join(
        "#define " + k + " " + json.dumps(v) + "\n" for k, v in header.items()))
    compiler = args.ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang"
    command([compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIE", "-pie",
             "-I", out, o.ROOT / "native/dsu_console.c", "-o", out / "onn26-su"], out / "native-build.log",
             "Compiling the temporary root helper")

    added = {
        "system/bin/onn26-su": ((out / "onn26-su").read_bytes(), 0o4750,
                                "u:object_r:onn26_root_exec:s0", 2000),
        "system/etc/init/onn26-dsu-adb.rc": (ADB_INIT.encode(), 0o644,
                                           "u:object_r:system_file:s0", 0),
        "adb_keys": (public_key, 0o644, "u:object_r:adb_keys_file:s0", 0),
    }
    tar_rows = dict(rows)
    for name, (data, mode, label, gid) in added.items():
        path = tree / name
        o.require(not path.exists() and not path.is_symlink(), "Unexpected existing path: " + name)
        path.write_bytes(data)
        tar_rows[name] = {"mode": stat.S_IFREG | mode, "uid": 0, "gid": gid,
                          "xattrs": {"security.selinux": base64.b64encode(label.encode() + b"\0").decode()}}
    caps = rows["system/bin/run-as"]["xattrs"]["security.capability"]
    o.require(base64.b64decode(caps).hex() == "01000002c0000000000000000000000000000000",
              "Stock run-as capability encoding changed")
    tar_rows["system/bin/onn26-su"]["xattrs"]["security.capability"] = caps

    policy = tree / "system/etc/selinux/plat_sepolicy.cil"
    policy.write_bytes(policy.read_bytes() + POLICY.encode())
    contexts = tree / "system/etc/selinux/plat_file_contexts"
    contexts.write_bytes(contexts.read_bytes() + b"\n/system/bin/onn26-su u:object_r:onn26_root_exec:s0\n")
    mapping = tree / "system/etc/selinux/mapping/34.0.cil"
    marker = tree / "system/etc/selinux/plat_sepolicy_and_mapping.sha256"
    marker.write_text(hashlib.sha256(policy.read_bytes() + mapping.read_bytes()).hexdigest() + "\n")
    components = [policy, mapping]
    for name in ["system_ext", "product", "vendor", "odm"]:
        directory = roots[name] / "etc/selinux"
        components.extend(sorted(directory.glob("*.cil")))
        components.extend(sorted((directory / "mapping").glob("34.0*.cil")))
    o.require(len(components) == 7, "Unexpected stock SELinux components")
    command([args.secilc, "-m", "-M", "true", "-G", "-N", "-c", "30", "-o",
             out / "compiled-sepolicy", "-f", out / "compiled-file-contexts", *components],
            out / "policy-build.log", "Compiling the temporary system's security policy")

    # The original EROFS metadata is authoritative. Linux host symlinks cannot
    # preserve the original link modes; PAX records can. Raw SCHILY values avoid
    # erofs-utils 1.7.1's incompatible LIBARCHIVE base64 decoding.
    progress("Packing the temporary filesystem...")
    with tarfile.open(out / "system.tar", "w", format=tarfile.PAX_FORMAT) as archive:
        for name, row in sorted(tar_rows.items()):
            item = tarfile.TarInfo(name or ".")
            item.mode = stat.S_IMODE(row["mode"])
            item.uid, item.gid, item.mtime = row["uid"], row["gid"], 1230768000
            item.pax_headers = {"SCHILY.xattr." + key: base64.b64decode(value).decode("utf-8", "surrogateescape")
                                for key, value in row["xattrs"].items()}
            path = tree / name
            if stat.S_ISDIR(row["mode"]):
                item.type = tarfile.DIRTYPE
                archive.addfile(item)
            elif stat.S_ISLNK(row["mode"]):
                item.type, item.linkname = tarfile.SYMTYPE, os.readlink(path)
                archive.addfile(item)
            elif stat.S_ISREG(row["mode"]):
                item.size = path.stat().st_size
                with path.open("rb") as stream:
                    archive.addfile(item, stream)
            else:
                raise o.Stop("Unexpected stock inode type: " + name)
    erofs = out / "system.erofs"
    command([args.mkfs_erofs, "--tar=f", "-zlz4hc,9", "-C4096", "-T1230768000",
             erofs, out / "system.tar"], out / "mkfs.log", "Building the temporary Android filesystem")
    command([args.fsck_erofs, "--extract=" + str(out / "verify-tree"), "--no-preserve", erofs],
            out / "verify-extract.log", "Checking the rebuilt filesystem")
    progress("Verifying every filesystem entry and file...")
    actual = metadata(erofs)
    changed = {"system/etc/selinux/plat_sepolicy.cil", "system/etc/selinux/plat_file_contexts",
               "system/etc/selinux/plat_sepolicy_and_mapping.sha256"}
    o.require(actual.keys() == tar_rows.keys(), "Round-trip filesystem paths changed")
    for name, expected in tar_rows.items():
        o.require(all(actual[name][key] == expected[key] for key in ("mode", "uid", "gid", "xattrs")),
                  "Round-trip inode metadata changed: " + name)
        reference = tree if name in changed or name in added else roots["system"]
        if stat.S_ISREG(expected["mode"]):
            o.require(o.digest(out / "verify-tree" / name) == o.digest(reference / name),
                      "Round-trip file bytes changed: " + name)
        elif stat.S_ISLNK(expected["mode"]):
            o.require(os.readlink(out / "verify-tree" / name) == os.readlink(reference / name),
                      "Round-trip symlink changed: " + name)
    with erofs.open("rb") as stream:
        stream.seek(1104)
        incompat = int.from_bytes(stream.read(4), "little")
    o.require(incompat & ~1 == 0, "Unsupported EROFS feature flags")

    image = out / "system.img"
    shutil.copyfile(erofs, image)
    avb = [sys.executable, args.avbtool]
    command([*avb, "add_hashtree_footer", "--image", image, "--partition_name", "system",
             "--partition_size", str(o.PROFILE["dsu_partitions"]["system"]["size"]),
             "--algorithm", "NONE", "--hash_algorithm", "sha256", "--block_size", "4096",
             "--do_not_generate_fec", "--salt", "9c36d1cb9303ad93fda0f340d31b24ccf8892ee3a1d9198bb6b94acf0772d625",
             "--prop", "com.android.build.system.os_version:14", "--prop",
             "com.android.build.system.fingerprint:" + o.PROFILE["fingerprint"], "--prop",
             "com.android.build.system.security_patch:2026-06-01"], out / "avb-footer.log",
             "Adding Android verification data")
    command([*avb, "verify_image", "--image", image], out / "avb-verify.log",
            "Verifying the rebuilt Android image")
    compress(image, out / "system.root.raw.gz")
    compress(args.images / "system.img", out / "system.stock.raw.gz")
    result = {"at": o.now(), "profile": slot["id"], "serial": args.serial,
              "fingerprint": o.PROFILE["fingerprint"], "bootloader": o.PROFILE["bootloader"],
              "slot": args.slot, "system_bytes": image.stat().st_size,
              "root_image_sha256": o.digest(image), "helper_sha256": o.digest(out / "onn26-su"),
              "root_gzip": str(out / "system.root.raw.gz"),
              "root_gzip_sha256": o.digest(out / "system.root.raw.gz"),
              "stock_gzip": str(out / "system.stock.raw.gz"),
              "stock_gzip_sha256": o.digest(out / "system.stock.raw.gz"),
              "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
              "all_original_inode_metadata_preserved": True, "all_file_bytes_verified": True,
              "policy_compiled": True, "avb_verified": True, "erofs_incompat_features": incompat,
              "single_use_required": True, "root_helper_autostart": False,
              "changed": sorted(changed), "added": sorted(added), "stock_paths": len(rows),
              "firmware_written": False}
    o.save(out / "build.json", result, exclusive=True)
    progress("Both temporary Android images are built and verified.")
    print(json.dumps(result, indent=2))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--serial", required=True)
    p.add_argument("--slot", required=True, choices=("_a", "_b"),
                   help="The verified active slot from the selected device; no default")
    p.add_argument("--images", type=Path, required=True)
    p.add_argument("--ndk", type=Path, required=True)
    p.add_argument("--adb-public-key", type=Path, required=True)
    p.add_argument("--avbtool", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mkfs-erofs", default="mkfs.erofs")
    p.add_argument("--fsck-erofs", default="fsck.erofs")
    p.add_argument("--secilc", default="secilc")
    return p


if __name__ == "__main__":
    try:
        build(parser().parse_args())
    except (o.Stop, OSError, subprocess.SubprocessError) as error:
        progress("FAILED: " + str(error))
        sys.exit(2)

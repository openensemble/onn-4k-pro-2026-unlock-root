"""Check the EROFS features used by the DSU builder without device access."""
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

import onn2026 as o

HELP = ("Use compatible erofs-utils tools (1.7.1 tested). Older packages may lack "
        "directory extraction or tar input. See docs/HOST_TOOLS.md for upgrade "
        "instructions, then resume with the existing work folder.")


def run_probe(argv, description):
    try:
        result = subprocess.run([str(value) for value in argv], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise o.Stop(f"EROFS host check could not {description}: {error}.\n{HELP}") from error
    o.require(result.returncode == 0,
              f"EROFS host check could not {description} using {argv[0]} "
              f"(exit {result.returncode}):\n{result.stdout[-4096:].strip()}\n{HELP}")


def check_erofs_tools(mkfs_erofs, fsck_erofs):
    """Build and extract a tiny compressed tar image with the actual DSU flags.

    Test capabilities rather than version strings: distributions can backport
    features, and mkfs.erofs and fsck.erofs can come from different installations.
    The short probe limit does not change the full build's 15-minute deadline.
    """
    resolved = []
    for tool in (mkfs_erofs, fsck_erofs):
        path = shutil.which(tool)
        o.require(path, f"EROFS tool is missing or not executable: {tool}.\n{HELP}")
        resolved.append(path)
    with tempfile.TemporaryDirectory(prefix="onn26-erofs-check-") as directory:
        root = Path(directory)
        archive, image, extracted = root / "probe.tar", root / "probe.erofs", root / "extracted"
        payload = b"onn2026 EROFS host compatibility check\n" * 2048
        with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as stream:
            folder = tarfile.TarInfo(".")
            folder.type, folder.mode = tarfile.DIRTYPE, 0o755
            stream.addfile(folder)
            item = tarfile.TarInfo("probe.txt")
            item.mode, item.size = 0o644, len(payload)
            stream.addfile(item, BytesIO(payload))
            link = tarfile.TarInfo("probe-link")
            link.type, link.mode, link.linkname = tarfile.SYMTYPE, 0o777, "probe.txt"
            stream.addfile(link)
        run_probe([resolved[0], "--tar=f", "-zlz4hc,9", "-C4096", "-T1230768000", image, archive],
                  "build a compressed image from tar input")
        run_probe([resolved[1], "--extract=" + str(extracted), "--no-preserve", image],
                  "extract files into a directory")
        file, link = extracted / "probe.txt", extracted / "probe-link"
        o.require(file.is_file() and not file.is_symlink() and file.read_bytes() == payload and
                  link.is_symlink() and os.readlink(link) == "probe.txt",
                  "EROFS tools exited successfully but did not extract the expected files.\n" + HELP)

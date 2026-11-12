"""Host tool compatibility and early refusal; no ADB or device access."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import guide
import onn2026 as o
from tools import build_dsu, erofs_tools

MKFS, FSCK = shutil.which("mkfs.erofs"), shutil.which("fsck.erofs")


class ErofsToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def tool(self, name, source):
        path = self.folder / name
        path.write_text("#!" + sys.executable + "\n" + source)
        path.chmod(0o755)
        return str(path)

    @unittest.skipUnless(MKFS and FSCK, "Install erofs-utils for the actual tool round trip")
    def test_actual_tools_build_and_extract_compressed_files_and_symlinks(self):
        temporary = tempfile.TemporaryDirectory
        with patch.object(erofs_tools.tempfile, "TemporaryDirectory",
                          side_effect=lambda **kwargs: temporary(dir=self.folder, **kwargs)):
            erofs_tools.check_erofs_tools(MKFS, FSCK)
        self.assertEqual(list(self.folder.iterdir()), [])

    @unittest.skipUnless(MKFS, "Install erofs-utils for the extraction fixtures")
    def test_legacy_extract_option_error_includes_selected_tool_and_upgrade(self):
        old = self.tool("old-fsck", "import sys\n"
                        "print('option --extract does not allow an argument', file=sys.stderr)\nsys.exit(1)\n")
        with self.assertRaisesRegex(o.Stop, "--extract does not allow an argument") as stopped:
            erofs_tools.check_erofs_tools(MKFS, old)
        self.assertIn(old, str(stopped.exception))
        self.assertIn("docs/HOST_TOOLS.md", str(stopped.exception))

    @unittest.skipUnless(MKFS, "Install erofs-utils for the extraction fixtures")
    def test_successful_integrity_check_without_extracted_files_is_rejected(self):
        no_extract = self.tool("check-only-fsck", "print('image integrity checked')\n")
        with self.assertRaisesRegex(o.Stop, "did not extract the expected files"):
            erofs_tools.check_erofs_tools(MKFS, no_extract)

    def test_missing_tar_support_stops_before_extraction(self):
        old = self.tool("old-mkfs", "import sys\nprint('unrecognized option --tar=f', file=sys.stderr)\nsys.exit(1)\n")
        marker = self.folder / "fsck-ran"
        unused = self.tool("unused-fsck", f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        with self.assertRaisesRegex(o.Stop, "unrecognized option --tar=f"):
            erofs_tools.check_erofs_tools(old, unused)
        self.assertFalse(marker.exists())

    def test_guide_rejects_incompatible_tools_during_host_setup(self):
        args = SimpleNamespace(mkfs_erofs="mkfs.erofs", fsck_erofs="fsck.erofs", secilc="secilc")
        with patch.object(guide.platform, "system", return_value="Linux"), \
             patch.object(guide.platform, "machine", return_value="x86_64"), \
             patch.object(guide.shutil, "which", return_value="/fixture/tool"), \
             patch.object(guide, "check_erofs_tools", side_effect=o.Stop("incompatible EROFS tools")):
            # No NDK selection or other setup may be attempted after this fails.
            with self.assertRaisesRegex(o.Stop, "incompatible EROFS tools"):
                guide.host_setup(args, None)

    def test_direct_or_resumed_builder_checks_tools_before_reading_inputs(self):
        args = SimpleNamespace(mkfs_erofs="mkfs.erofs", fsck_erofs="fsck.erofs")
        with patch.object(build_dsu, "check_erofs_tools", side_effect=o.Stop("incompatible EROFS tools")), \
             redirect_stdout(StringIO()):
            with self.assertRaisesRegex(o.Stop, "incompatible EROFS tools"):
                build_dsu.build(args)

    def test_stuck_probe_has_a_bounded_wait_and_upgrade_diagnostic(self):
        def timeout(argv, **kwargs):
            self.assertEqual(kwargs["timeout"], 30)
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        with patch.object(erofs_tools.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(o.Stop, "docs/HOST_TOOLS.md"):
                erofs_tools.run_probe(["fixture-tool"], "check a tiny image")


if __name__ == "__main__":
    unittest.main()

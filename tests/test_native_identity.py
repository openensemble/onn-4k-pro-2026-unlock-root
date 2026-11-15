"""Compile the actual unlock entry point with a host property stub that stops before device I/O."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "profile.json").read_text())
ROW = PROFILE["variants"]["jarvis2-row-15016191"]


class NativeIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc"):
            raise unittest.SkipTest("A host C compiler is needed for native identity guards")
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "sys").mkdir()
        (root / "sys/system_properties.h").write_text(
            "#define PROP_VALUE_MAX 92\nint __system_property_get(const char *, char *);\n")
        stub = root / "android-stub.c"
        stub.write_text(
            "#include <stdlib.h>\n#include <sys/types.h>\n"
            "uid_t getuid(void) { const char *v = getenv(\"ONN26_FIXTURE_UID\"); return v ? atoi(v) : 2000; }\n"
            "uid_t geteuid(void) { return getuid(); }\n"
            # No property ever matches: execution stops before boot-ID, marker or TEE access.
            "int __system_property_get(const char *key, char *out) { (void)key; out[0] = 0; return 0; }\n")
        cls.binary = root / "unlock-entry-fixture"
        subprocess.run(["cc", "-std=c11", "-D_GNU_SOURCE", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I", str(root), str(ROOT / "native/unlock_once.c"), str(stub),
                        "-o", str(cls.binary)], check=True, capture_output=True, text=True, timeout=30)

    def entry(self, serial, profile, slot="_a", uid="2000", marker="/data/local/tmp/oe26-avb-fixture/consumed"):
        result = subprocess.run([str(self.binary), "--unlock-once", serial, profile["fingerprint"],
                                 profile["bootloader"], slot, "11111111-2222-3333-4444-555555555555", marker],
                                env={**os.environ, "ONN26_FIXTURE_UID": uid}, capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.stdout, "", "No trusted-state request may reach a real device")
        return result

    def test_us_and_row_reach_exact_property_checks_on_both_slots(self):
        for profile in (PROFILE, ROW):
            for slot in ("_a", "_b"):
                with self.subTest(profile=profile["id"], slot=slot):
                    result = self.entry(profile["serial_prefixes"][0] + "NATIVETEST001", profile, slot)
                    self.assertEqual(result.returncode, 22)
                    self.assertIn("Identity mismatch: ro.product.device", result.stderr)

    def test_missing_malformed_and_cross_region_serials_fail_before_properties(self):
        cases = [("JCA", ROW), ("JUSA", PROFILE), ("", ROW), ("JCATEST_001", ROW),
                 ("JCATEST;id", ROW), ("JCATEST001", PROFILE), ("JUSATEST001", ROW),
                 ("SNATEST001", ROW), ("JCATest\n", ROW)]
        for serial, profile in cases:
            with self.subTest(serial=serial, profile=profile["id"]):
                self.assertEqual(self.entry(serial, profile).returncode, 21)

    def test_caller_and_marker_guards_still_apply_to_both_regions(self):
        for profile in (PROFILE, ROW):
            serial = profile["serial_prefixes"][0] + "NATIVETEST001"
            for uid in ("0", "1000"):
                with self.subTest(profile=profile["id"], uid=uid):
                    self.assertEqual(self.entry(serial, profile, uid=uid).returncode, 21)
            for marker in ("/tmp/fixture", "/data/local/tmp/oe26-avb-fixture/../consumed"):
                with self.subTest(profile=profile["id"], marker=marker):
                    self.assertEqual(self.entry(serial, profile, marker=marker).returncode, 21)


if __name__ == "__main__":
    unittest.main()

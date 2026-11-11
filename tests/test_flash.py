"""Exercise the real flash template against disposable regular-file fixtures."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import onn2026 as o
import root_workflow as flow

BOOT = "11111111-2222-3333-4444-555555555555"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FlashTests(unittest.TestCase):
    def exercise(self, case):
        with tempfile.TemporaryDirectory(prefix="onn26-fixture-") as directory:
            root = Path(directory)
            commands = root / "commands"
            commands.mkdir()
            target, stock, image = (root / name for name in ["partition.fixture", "stock-init_boot.img", "patched-init_boot.img"])
            stock.write_bytes(b"S" * 8388608)
            image.write_bytes(b"P" * 8388608)
            shutil.copyfile(stock, target)
            stock_hash, image_hash = digest(stock), digest(image)
            attempt = root / "attempt"
            values = {
                "ro.serialno": "JUSA123456", "ro.build.fingerprint": o.PROFILE["fingerprint"],
                "ro.bootloader": o.PROFILE["bootloader"], "ro.boot.slot_suffix": "_b",
                "ro.boot.flash.locked": "0", "ro.gsid.image_running": "1",
                "/proc/sys/kernel/random/boot_id": BOOT,
                "/metadata/gsi/dsu/install_status": "disabled", "/metadata/gsi/dsu/one_shot_boot": "1",
                "/sys/class/block/mmcblk0p23/size": "16384",
            }
            if case == "wrong-unit":
                values["ro.serialno"] = "JUSA999999"
            elif case == "wrong-slot":
                values["ro.boot.slot_suffix"] = "_a"
            elif case == "changed-boot":
                values["/proc/sys/kernel/random/boot_id"] = "another-boot"
            elif case == "armed-dsu":
                values["/metadata/gsi/dsu/install_status"] = "ok"
            elif case == "wrong-image":
                with image.open("r+b") as stream:
                    stream.write(b"BAD")
            elif case == "changed-partition":
                with target.open("r+b") as stream:
                    stream.write(b"BAD")
            elif case == "consumed":
                attempt.mkdir()
            data = root / "values.json"
            data.write_text(json.dumps(values))
            dispatch = '''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
name=Path(sys.argv[0]).name
if name == 'id': print('0')
elif name == 'sync': pass
else:
    values=json.loads(Path(os.environ['ONN26_FIXTURE_VALUES']).read_text())
    if sys.argv[1] in values: print(values[sys.argv[1]])
    elif name == 'cat': os.execv('/bin/cat', ['cat', *sys.argv[1:]])
    else: sys.exit(1)
'''
            for command in ["id", "getprop", "cat", "sync"]:
                path = commands / command
                path.write_text(dispatch)
                path.chmod(0o700)
            if case == "partial-write":
                path = commands / "dd"
                path.write_text('''#!/usr/bin/python3
import sys
args=dict(arg.split('=',1) for arg in sys.argv[1:])
with open(args['if'],'rb') as src, open(args['of'],'r+b') as dst:
    dst.write(src.read(4096))
sys.exit(1)
''')
                path.chmod(0o700)
            with patch.dict(o.PROFILE, {"init_boot_sha256": stock_hash}):
                script = flow.flash_script({"ro.serialno": "JUSA123456"}, BOOT, str(root), str(attempt), image_hash)
            # Test-only conversion. The shipped template has no fixture mode and
            # accepts only its pinned block node, mapping and partition length.
            script = script.replace('[ -b "$partition" ]', '[ -f "$partition" ]')
            script = script.replace("/dev/block/by-name/init_boot_b", str(target))
            script = script.replace("/dev/block/mmcblk0p23", str(target))
            file = root / "fixture.sh"
            file.write_text(script)
            env = dict(os.environ, PATH=str(commands) + ":/usr/bin:/bin", ONN26_FIXTURE_VALUES=str(data))
            args = ["/bin/sh", str(file)]
            if case != "missing-action":
                args.append("--check-only" if case == "check-only" else "--write-once")
            before = digest(target)
            result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=20)
            if case == "success":
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(digest(target), image_hash)
                self.assertTrue((attempt / "readback-verified").is_file())
                shutil.copyfile(stock, target)
                again = subprocess.run(args, env=env, capture_output=True, text=True, timeout=20)
                self.assertNotEqual(again.returncode, 0)
                self.assertEqual(digest(target), stock_hash)
            elif case == "check-only":
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "PREFLIGHT_VERIFIED")
                self.assertEqual(digest(target), before)
                self.assertFalse(attempt.exists())
            elif case == "partial-write":
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue((attempt / "attempt-consumed").is_file())
                self.assertFalse((attempt / "readback-verified").exists())
                self.assertNotEqual(digest(target), image_hash)
                self.assertNotEqual(digest(target), before)
            else:
                self.assertNotEqual(result.returncode, 0, case)
                self.assertEqual(digest(target), before, case)

    def test_flash_behavior_and_guards(self):
        for case in ["success", "check-only", "wrong-unit", "wrong-slot", "changed-boot", "armed-dsu",
                     "wrong-image", "changed-partition", "consumed", "partial-write", "missing-action"]:
            with self.subTest(case=case):
                self.exercise(case)


if __name__ == "__main__":
    unittest.main()

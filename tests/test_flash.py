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
    def exercise(self, case, slot):
        with tempfile.TemporaryDirectory(prefix="onn26-fixture-") as directory:
            root = Path(directory)
            commands = root / "commands"
            commands.mkdir()
            target, stock, image = (root / name for name in ["partition.fixture", "stock-init_boot.img", "patched-init_boot.img"])
            inactive = root / "inactive.fixture"
            by_name = root / ("init_boot" + slot)
            by_name.symlink_to(target)
            stock.write_bytes(b"S" * 8388608)
            image.write_bytes(b"P" * 8388608)
            shutil.copyfile(stock, target)
            shutil.copyfile(stock, inactive)
            stock_hash, image_hash = digest(stock), digest(image)
            attempt = root / "attempt"
            values = {
                "ro.serialno": "JUSA123456", "ro.build.fingerprint": o.PROFILE["fingerprint"],
                "ro.bootloader": o.PROFILE["bootloader"], "ro.boot.slot_suffix": slot,
                "ro.boot.flash.locked": "0", "ro.gsid.image_running": "1",
                "/proc/sys/kernel/random/boot_id": BOOT,
                "/metadata/gsi/dsu/install_status": "disabled", "/metadata/gsi/dsu/one_shot_boot": "1",
                "/sys/class/block/mmcblk0p22/size": "16384",
                "/sys/class/block/mmcblk0p23/size": "16384",
            }
            if case == "wrong-unit":
                values["ro.serialno"] = "JUSA999999"
            elif case == "wrong-slot":
                values["ro.boot.slot_suffix"] = "_b" if slot == "_a" else "_a"
            elif case == "wrong-firmware":
                values["ro.build.fingerprint"] = "another-build"
            elif case == "wrong-mapping":
                by_name.unlink()
                by_name.symlink_to(inactive)
            elif case in ("wrong-size", "size-unavailable"):
                size_path = "/sys/class/block/" + Path(o.PROFILE["slots"][slot]["root_block_path"]).name + "/size"
                values[size_path] = "8192" if case == "wrong-size" else ""
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
            path = commands / "dd"
            path.write_text('''#!/usr/bin/python3
import os, sys
from pathlib import Path
args=dict(arg.split('=',1) for arg in sys.argv[1:])
counter=Path(os.environ['ONN26_FIXTURE_VALUES']).parent / 'write-count'
counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
if os.environ['ONN26_FIXTURE_CASE'] in ('partial-write', 'readback-failure'):
    with open(args['if'],'rb') as src, open(args['of'],'r+b') as dst:
        dst.write(src.read(4096))
    sys.exit(1 if os.environ['ONN26_FIXTURE_CASE'] == 'partial-write' else 0)
os.execv('/bin/dd', ['dd', *sys.argv[1:]])
''')
            path.chmod(0o700)
            binding = {"ro.serialno": "JUSA123456", "ro.boot.slot_suffix": slot}
            with patch.dict(o.PROFILE, {"init_boot_sha256": stock_hash}):
                script = flow.flash_script(binding, BOOT, str(root), str(attempt), image_hash)
            # Test-only conversion. The shipped template has no fixture mode and
            # accepts only its pinned block node, mapping and partition length.
            script = script.replace('[ -b "$partition" ]', '[ -f "$partition" ]')
            script = script.replace(o.root_partition(binding), str(by_name))
            script = script.replace(o.slot_profile(binding)["root_block_path"], str(target))
            file = root / "fixture.sh"
            file.write_text(script)
            env = dict(os.environ, PATH=str(commands) + ":/usr/bin:/bin", ONN26_FIXTURE_VALUES=str(data),
                       ONN26_FIXTURE_CASE=case)
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
            elif case in ("partial-write", "readback-failure"):
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue((attempt / "attempt-consumed").is_file())
                self.assertFalse((attempt / "readback-verified").exists())
                self.assertNotEqual(digest(target), image_hash)
                self.assertNotEqual(digest(target), before)
            else:
                self.assertNotEqual(result.returncode, 0, case)
                self.assertEqual(digest(target), before, case)
            self.assertEqual(digest(inactive), stock_hash, "Inactive slot was modified")
            count = root / "write-count"
            if case in ("success", "partial-write", "readback-failure"):
                self.assertEqual(count.read_text(), "1")
            else:
                self.assertFalse(count.exists(), "A refused operation reached dd")

    def test_flash_behavior_and_guards(self):
        for slot in ("_a", "_b"):
            for case in ["success", "check-only", "wrong-unit", "wrong-slot", "wrong-firmware",
                         "wrong-mapping", "wrong-size", "size-unavailable", "changed-boot", "armed-dsu",
                         "wrong-image", "changed-partition", "consumed", "partial-write", "readback-failure",
                         "missing-action"]:
                with self.subTest(case=case, slot=slot):
                    self.exercise(case, slot)


if __name__ == "__main__":
    unittest.main()

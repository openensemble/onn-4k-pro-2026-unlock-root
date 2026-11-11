"""Exercise real process-group interrupts using an offline transport fixture."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InterruptTests(unittest.TestCase):
    def test_ctrl_c_does_not_kill_protected_command_or_binary_backup(self):
        for operation in ("command", "backup"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ready = root / "ready"
                transport = root / "offline-transport"
                transport.write_text("#!/usr/bin/python3\n" +
                    "from pathlib import Path\nimport sys, time\n" +
                    "Path(" + repr(str(ready)) + ").write_text('ready')\n" +
                    "time.sleep(0.5)\n" +
                    "if 'exec-out' in sys.argv:\n    sys.stdout.buffer.write(b'S' * 8388608)\n" +
                    "else:\n    print('COMMAND_FINISHED')\n")
                transport.chmod(0o700)
                worker = r'''
from pathlib import Path
import sys
import onn2026 as o
import root_workflow as flow
from guide_core import UI, working

directory, transport, operation = sys.argv[1:]
device = o.Device(o.Session(Path(directory) / 'receipts', 'interrupt-fixture'),
                  '192.0.2.71:5555', transport)
with working(UI(emit=lambda message: None), protect=True) as cancellation:
    if operation == 'command':
        result = device.call('critical-command-fixture', 'fixture-only', timeout=5)
        assert result['stdout'].strip() == 'COMMAND_FINISHED'
    else:
        result = flow.pull_block(device, '/fixture/helper', Path(directory) / 'backup.fixture')
        assert result['bytes'] == o.PROFILE['init_boot_size']
    assert result['returncode'] == 0
assert cancellation['requested'] is True
Path(directory, 'completed').write_text('confirmed after interrupt')
'''
                process = subprocess.Popen([sys.executable, '-B', '-c', worker, directory,
                                            str(transport), operation], cwd=ROOT,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, start_new_session=True)
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(ready.exists(), 'Offline child did not start')
                    # Only this isolated test worker's process group receives
                    # the signal, exactly as a terminal sends Ctrl+C.
                    os.killpg(process.pid, signal.SIGINT)
                    out, error = process.communicate(timeout=8)
                    self.assertEqual(process.returncode, 0, out + error)
                    self.assertTrue((root / 'completed').is_file())
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()

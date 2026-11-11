"""Exercise the actual buttons through a local pseudo-terminal; never uses ADB."""
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import termios
import time
import unittest


class TerminalButtons(unittest.TestCase):
    def choose(self, keys, expected):
        master, slave = pty.openpty()
        original = termios.tcgetattr(slave)
        process = None
        try:
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", "from console_ui import accept_decline; print('RESULT=' + str(accept_decline()), flush=True)"],
                stdin=slave, stdout=slave, stderr=slave, cwd=Path(__file__).resolve().parents[1], close_fds=True)
            output = b""
            deadline = time.monotonic() + 5
            sent = False
            while b"RESULT=" not in output and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    output += os.read(master, 4096)
                if not sent and b"Decline" in output:
                    os.write(master, keys)
                    sent = True
            process.wait(timeout=3)
            self.assertEqual(process.returncode, 0, output)
            self.assertIn(("RESULT=" + str(expected)).encode(), output)
            self.assertEqual(termios.tcgetattr(slave), original, "Terminal settings were not restored")
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)

    def test_enter_defaults_to_decline(self):
        self.choose(b"\r", False)

    def test_left_then_enter_accepts(self):
        self.choose(b"\x1b[D\r", True)

    def test_tab_then_enter_accepts(self):
        self.choose(b"\t\r", True)

    def test_right_returns_to_decline(self):
        self.choose(b"\x1b[D\x1b[C\r", False)

    def test_two_tabs_return_to_decline(self):
        self.choose(b"\t\t\r", False)

    def test_escape_declines(self):
        self.choose(b"\x1b", False)

    def test_old_confirmation_text_does_not_count_as_acceptance(self):
        self.choose(b"I ACCEPT\r", False)


if __name__ == "__main__":
    unittest.main()

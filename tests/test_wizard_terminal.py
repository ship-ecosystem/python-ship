"""Actual terminal back/cancel behavior and terminal-mode restoration."""
import os
import subprocess
import sys
import time
import unittest

from tests.helpers import ProjectTestCase


@unittest.skipUnless(sys.platform == "linux", "PTY verification uses Linux terminal controls")
class WizardTerminalTests(ProjectTestCase):
    def test_back_and_cancel_restore_terminal_and_write_nothing(self):
        import pty
        import select
        import termios
        master, slave = pty.openpty()
        before = termios.tcgetattr(slave)
        process = subprocess.Popen([sys.executable, "-m", "python_ship", "--project-directory", str(self.root), "init"],
            stdin=slave, stdout=slave, stderr=slave, env={**os.environ, "TERM": "xterm-256color", "CI": ""})
        output = b""
        def until(fragment, occurrences=1):
            nonlocal output
            deadline = time.monotonic() + 20
            while output.count(fragment) < occurrences:
                if time.monotonic() > deadline:
                    self.fail(f"Terminal did not show {fragment!r}: {output[-2000:]!r}")
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    output += os.read(master, 65536)
        try:
            until(b"Project name")
            os.write(master, b"\r")
            until(b"Select a framework")
            os.write(master, b"\x15")
            until(b"Project name", 2)
            os.write(master, b"\x03")
            self.assertEqual(130, process.wait(timeout=10))
            self.assertEqual(before, termios.tcgetattr(slave))
            self.assertEqual(["main.py"], [path.name for path in self.root.iterdir()])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)

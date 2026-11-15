"""A plain Python callable must serve HTTP through Ship's default server."""

from pathlib import Path
import asyncio
import importlib.util
import socket
import subprocess
import sys
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen


FIXTURE = Path(__file__).parent / "fixtures" / "plain_asgi"


class PlainAsgiHttpTests(unittest.TestCase):
    def test_plain_asgi_response_events(self):
        spec = importlib.util.spec_from_file_location("ship_plain_asgi_fixture", FIXTURE / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        events = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(event):
            events.append(event)

        asyncio.run(module.app({"type": "http"}, receive, send))
        self.assertEqual(200, events[0]["status"])
        self.assertEqual(b"hello from plain ASGI", events[1]["body"])

    def test_plain_asgi_http_response(self):
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        except PermissionError:
            self.skipTest("local sockets are not permitted in this sandbox")
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=FIXTURE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail(f"Uvicorn exited early: {process.stderr.read().decode()}")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=0.5) as response:
                        self.assertEqual(200, response.status)
                        self.assertEqual(b"hello from plain ASGI", response.read())
                        return
                except (OSError, URLError):
                    time.sleep(0.1)
            self.fail("Uvicorn did not serve the plain ASGI fixture within 10 seconds")
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)

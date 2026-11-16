"""A real Vite project and a minimal HMR WebSocket client, for the live Docker suites."""

import base64
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import sys
import unittest
from urllib.request import Request, urlopen

from python_ship.init import VITE_SNIPPET
from tests.helpers import captured, wait_http

VITE = "8.3.8"


def write_project(root: Path) -> None:
    (root / "package.json").write_text(json.dumps(
        {"name": "hmr", "private": True, "type": "module", "scripts": {"dev": "vite"}, "devDependencies": {"vite": VITE}}))
    # Exactly the snippet `ship init` prints.
    (root / "vite.config.js").write_text("import { defineConfig } from 'vite';\n" + VITE_SNIPPET.replace("    // ...\n", ""))
    (root / "index.html").write_text('<!doctype html><div id="app"></div><script type="module" src="/main.js"></script>\n')
    (root / "main.js").write_text("import './style.css';\nimport logo from './logo.svg';\nconsole.log(logo);\n")
    (root / "style.css").write_text("body { color: red; }\n")
    # Over Vite's 4 KB inline limit, so it is served by URL rather than as a data: URI.
    (root / "logo.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg"><!-- {"x" * 5000} --></svg>\n')


def socket_to(port: int, token: str) -> socket.socket:
    """Open Vite's HMR WebSocket the way its browser client does."""
    connection = socket.create_connection(("127.0.0.1", port), timeout=60)
    key = base64.b64encode(os.urandom(16)).decode()
    connection.sendall((f"GET /?token={token} HTTP/1.1\r\nHost: localhost:{port}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                        "Sec-WebSocket-Protocol: vite-hmr\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = connection.recv(1)
        if not chunk:
            raise AssertionError(f"Vite closed the HMR handshake: {head!r}")
        head += chunk
    if not head.startswith(b"HTTP/1.1 101"):
        raise AssertionError(f"Vite refused the HMR WebSocket: {head!r}")
    return connection


def message(connection: socket.socket) -> dict:
    """The next text frame from the server (unmasked, unfragmented), as JSON."""
    def exactly(count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = connection.recv(count - len(data))
            if not chunk:
                raise AssertionError("Vite closed the HMR WebSocket")
            data += chunk
        return data

    while True:
        first, second = exactly(2)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", exactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", exactly(8))[0]
        payload = exactly(length)
        if first & 0x0F == 1:
            return json.loads(payload)


def stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def assert_hmr(test: unittest.TestCase, root: Path, port: int, backend_port: int, ship) -> None:
    """`ship npm install` and `ship npm run dev`, then everything a browser needs from Vite through `port`."""
    with captured() as output:
        test.assertEqual(0, ship("npm", "install", "--no-audit", "--no-fund"), output.stderr)
    process = subprocess.Popen([sys.executable, "-m", "python_ship", "--project-directory", str(root), "npm", "run", "dev"],
                               env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    test.addCleanup(stop, process)
    test.assertIn(b"/@vite/client", wait_http(port, "/", timeout=90))
    client = wait_http(port, "/@vite/client").decode()
    token = re.search(r'const wsToken = "([^"]+)"', client)
    test.assertIsNotNone(token, client[:2000])

    # A page the Python app renders loads Vite's modules cross-origin, with asset URLs on Vite's origin.
    backend = f"http://localhost:{backend_port}"
    with urlopen(Request(f"http://127.0.0.1:{port}/main.js", headers={"Origin": backend}), timeout=10) as response:
        test.assertEqual(backend, response.headers["Access-Control-Allow-Origin"])
    test.assertIn(f"http://localhost:{port}/logo.svg".encode(), wait_http(port, "/logo.svg?import"))

    connection = socket_to(port, token.group(1))
    test.addCleanup(connection.close)
    test.assertEqual("connected", message(connection)["type"])
    (root / "style.css").write_text("body { color: blue; }\n")
    update = message(connection)
    test.assertEqual("update", update["type"], update)
    test.assertEqual(["/style.css"], [change["path"] for change in update["updates"]])
    (root / "index.html").write_text((root / "index.html").read_text() + "<p>edited</p>\n")
    test.assertEqual("full-reload", message(connection)["type"])

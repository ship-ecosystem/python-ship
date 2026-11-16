"""`ship up` readiness: container state, healthchecks and a real HTTP answer."""

import http.server
import json
import subprocess
import threading
import unittest
from unittest.mock import patch

from python_ship.config import ConfigError
from python_ship.runtime import records, responds, status, wait_for_stack


def ps(*rows):
    return subprocess.CompletedProcess([], 0, "\n".join(json.dumps(row) for row in rows), "")


APP_READY = {"Service": "app", "State": "running", "Publishers": [{"TargetPort": 8000, "PublishedPort": 18000, "Protocol": "tcp"}]}


class StatusTests(unittest.TestCase):
    def test_records_accept_both_compose_output_shapes(self):
        lines = "\n".join(json.dumps(row) for row in (APP_READY, {"Service": "db", "State": "running"}))
        self.assertEqual(records(lines), records(json.dumps([APP_READY, {"Service": "db", "State": "running"}])))
        self.assertEqual({}, records(""))
        with self.assertRaisesRegex(ConfigError, "Could not read"):
            records("{broken")

    def test_fatal_pending_and_port(self):
        found = records("\n".join(json.dumps(row) for row in (
            APP_READY,
            {"Service": "db", "State": "exited", "ExitCode": 1},
            {"Service": "cache", "State": "running", "Health": "unhealthy"},
            {"Service": "search", "State": "running", "Health": "starting"},
            {"Service": "worker", "State": "restarting", "ExitCode": 7},
            {"Service": "mail", "State": "created"},
        )))
        fatal, pending, port = status(found, {"app", "db", "cache", "search", "worker", "mail", "missing"})
        self.assertEqual(18000, port)
        self.assertEqual(3, len(fatal))
        self.assertIn("db is exited (health: none, exit code: 1)", fatal)
        self.assertIn("worker is restarting (health: none, exit code: 7)", fatal)
        self.assertIn("missing has no container", pending)
        self.assertIn("search is running (health: starting)", pending)
        self.assertIn("mail is created (health: none)", pending)

    def test_unpublished_and_renamed_app(self):
        _, pending, port = status({"app": {"Service": "app", "State": "running", "Publishers": []}}, {"app"})
        self.assertIsNone(port)
        self.assertIn("app port 8000 is not published", pending)
        _, pending, port = status({"web": {**APP_READY, "Service": "web"}}, {"web"}, "web")
        self.assertEqual(([], 18000), (pending, port))


class WaitTests(unittest.TestCase):
    def test_waits_for_health_then_http(self):
        results = [ps({**APP_READY, "Health": "starting"}), ps(APP_READY), ps(APP_READY)]
        with patch("python_ship.runtime.subprocess.run", side_effect=results) as run, \
                patch("python_ship.runtime.time.sleep"), \
                patch("python_ship.runtime.responds", side_effect=[False, True]) as probe:
            self.assertEqual(18000, wait_for_stack(["docker", "compose"], {"app"}))
        self.assertEqual(3, run.call_count)
        self.assertEqual(2, probe.call_count)

    def test_crash_fails_fast_with_guidance(self):
        with patch("python_ship.runtime.subprocess.run", return_value=ps({**APP_READY, "State": "exited", "ExitCode": 3})):
            with self.assertRaisesRegex(ConfigError, r"app is exited .* Run `ship logs SERVICE`"):
                wait_for_stack(["docker", "compose"], {"app"})

    def test_timeout_reports_what_was_pending(self):
        with patch("python_ship.runtime.subprocess.run", return_value=ps(APP_READY)), \
                patch("python_ship.runtime.responds", return_value=False), patch("python_ship.runtime.time.sleep"), \
                patch("python_ship.runtime.time.monotonic", side_effect=[0, 0, 1000]):
            with self.assertRaisesRegex(ConfigError, "not answering HTTP on port 18000"):
                wait_for_stack(["docker", "compose"], {"app"})

    def test_compose_failure(self):
        with patch("python_ship.runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "daemon down")):
            with self.assertRaisesRegex(ConfigError, "daemon down"):
                wait_for_stack(["docker", "compose"], {"app"})


class ProbeTests(unittest.TestCase):
    def test_any_http_status_counts_and_silence_does_not(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(500)
                self.end_headers()

            def log_message(self, *args):
                pass

        try:
            server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("local sockets are not permitted here")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertTrue(responds(server.server_address[1]))
        finally:
            server.shutdown()
            server.server_close()
        self.assertFalse(responds(server.server_address[1]))


if __name__ == "__main__":
    unittest.main()

"""Opt-in live service catalog checks with isolated temporary projects."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ShipConfig


FIXTURE = Path(__file__).parent / "fixtures" / "plain_asgi" / "main.py"


@unittest.skipUnless(os.environ.get("SHIP_DOCKER_TEST") == "1", "set SHIP_DOCKER_TEST=1 for real Docker integration")
class ServiceCatalogDockerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ship-catalog-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.addCleanup(self._cleanup)
        (self.root / "main.py").write_text(FIXTURE.read_text())
        self.app_port = self._free_port()
        self.mailpit_port = self._free_port()
        self.hmr_port = self._free_port()
        self.silo_port = self._free_port()
        (self.root / ".env").write_text(
            f"SHIP_APP_PORT={self.app_port}\nMAILPIT_WEB_PORT={self.mailpit_port}\n"
            f"SHIP_HMR_PORT={self.hmr_port}\nSILO_CONSOLE_PORT={self.silo_port}\n"
        )
        (self.root / ".env.production").write_text(
            "DB_PASSWORD=fixture-production-password\n"
            "DB_ROOT_PASSWORD=fixture-production-root-password\n"
            "AWS_SECRET_ACCESS_KEY=fixture-production-secret\n"
            "MEILISEARCH_KEY=fixture-production-meili-key\n"
            "GARAGE_ADMIN_TOKEN=fixture-production-admin\n"
            f"GARAGE_RPC_SECRET={'1' * 64}\n"
        )

    @staticmethod
    def _free_port():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def _ship(self, *args):
        return main(["--project-directory", str(self.root), *args])

    def _init_up(self, *args):
        with patch("python_ship.cli.require_uv", return_value="/integration/uv"):
            self.assertEqual(0, self._ship("init", "--app", "main:app", *args))
        self.assertEqual(0, self._ship("config:test"))
        self.assertEqual(0, self._ship("up"))

    def _prefix(self):
        compose = self.root / "ship" / "compose.generated.json"
        return ["docker", "compose", "--project-directory", str(self.root), "-f", str(compose)]

    def _cleanup(self):
        if (self.root / "ship.json").is_file():
            self._ship("down", "--volumes")
            name = build_compose(ShipConfig.read(self.root), self.root)["name"]
            subprocess.run(["docker", "image", "rm", f"{name}-app:latest"], capture_output=True, check=False)

    def test_mysql_application_user_and_separate_root(self):
        self._init_up("--database", "mysql")
        result = subprocess.run(self._prefix() + ["exec", "-T", "mysql", "mysql", "-uapp", "-pship", "app", "-e", "SELECT 1"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("1", result.stdout)
        result = subprocess.run(self._prefix() + ["exec", "-T", "mysql", "mysql", "-uroot", "-prootship", "-e", "SELECT 1"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_mysql_data_survives_container_restart(self):
        self._init_up("--database", "mysql")
        command = self._prefix() + ["exec", "-T", "mysql", "mysql", "-uapp", "-pship", "app", "-e"]
        created = subprocess.run(command + ["CREATE TABLE ship_persistence (value VARCHAR(30)); INSERT INTO ship_persistence VALUES ('kept');"], capture_output=True, text=True)
        self.assertEqual(0, created.returncode, created.stderr)
        self.assertEqual(0, self._ship("down"))
        self.assertEqual(0, self._ship("up"))
        result = subprocess.run(command + ["SELECT value FROM ship_persistence"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("kept", result.stdout)

    def test_postgres_and_redis_data_survive_container_restart(self):
        self._init_up("--database", "postgres", "--cache", "redis")
        postgres = self._prefix() + ["exec", "-T", "postgres", "psql", "-U", "app", "-d", "app", "-tAc"]
        redis = self._prefix() + ["exec", "-T", "redis", "redis-cli"]
        created = subprocess.run(postgres + ["CREATE TABLE ship_persistence (value text); INSERT INTO ship_persistence VALUES ('kept');"], capture_output=True, text=True)
        self.assertEqual(0, created.returncode, created.stderr)
        saved = subprocess.run(redis + ["SET", "ship:persistence", "kept"], capture_output=True, text=True)
        self.assertEqual(0, saved.returncode, saved.stderr)
        self.assertEqual(0, self._ship("down"))
        self.assertEqual(0, self._ship("up"))
        row = subprocess.run(postgres + ["SELECT value FROM ship_persistence"], capture_output=True, text=True)
        self.assertEqual(0, row.returncode, row.stderr)
        self.assertIn("kept", row.stdout)
        value = subprocess.run(redis + ["GET", "ship:persistence"], capture_output=True, text=True)
        self.assertEqual(0, value.returncode, value.stderr)
        self.assertEqual("kept", value.stdout.strip())

    def test_mailpit_smtp_and_web_ui(self):
        self._init_up("--mail", "mailpit")
        command = (
            "import os,smtplib; "
            "from email.message import EmailMessage; "
            "m=EmailMessage(); m['From']='ship@example.test'; m['To']='user@example.test'; "
            "m['Subject']='Ship catalog'; m.set_content('hello'); "
            "s=smtplib.SMTP(os.environ['MAIL_HOST'],int(os.environ['MAIL_PORT'])); s.send_message(m); s.quit()"
        )
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", command))
        deadline = time.monotonic() + 15
        while True:
            with urlopen(f"http://127.0.0.1:{self.mailpit_port}/api/v1/messages", timeout=5) as response:
                messages = json.load(response)
            if messages.get("total", 0) >= 1:
                break
            if time.monotonic() >= deadline:
                self.fail("Mailpit did not capture the SMTP message")
            time.sleep(0.5)

    def test_meilisearch_authenticated_api(self):
        self._init_up("--search", "meilisearch")
        command = (
            "import os,urllib.request,json; "
            "base=os.environ['MEILISEARCH_URL']; key=os.environ['MEILISEARCH_KEY']; "
            "assert json.load(urllib.request.urlopen(base+'/health'))['status']=='available'; "
            "request=urllib.request.Request(base+'/indexes',headers={'Authorization':'Bearer '+key}); "
            "assert urllib.request.urlopen(request).status==200"
        )
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", command))

    def test_node_npm_and_hmr_port(self):
        (self.root / "package.json").write_text('{"scripts":{"dev":"node dev.js"}}')
        (self.root / "dev.js").write_text("require('http').createServer((req,res)=>res.end('ship hmr')).listen(5173,'0.0.0.0')\n")
        self._init_up("--frontend", "node")
        self.assertEqual(0, self._ship("npm", "--version"))
        command = [sys.executable, "-m", "python_ship", "--project-directory", str(self.root), "npm", "run", "dev"]
        process = subprocess.Popen(
            command,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with urlopen(f"http://127.0.0.1:{self.hmr_port}", timeout=2) as response:
                        self.assertEqual(b"ship hmr", response.read())
                        break
                except OSError:
                    time.sleep(0.5)
            else:
                self.fail("Node HMR port did not become reachable")
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    def test_playwright_python_client_opens_app(self):
        (self.root / "requirements.txt").write_text("playwright==1.64.1\n")
        self._init_up("--testing", "playwright")
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{self.app_port}/", timeout=2) as response:
                    if response.read() == b"hello from plain ASGI":
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                self.fail("Application HTTP did not become ready for browser tests")
            time.sleep(0.5)
        direct_check = (
            "require('http').get('http://app:8000/',r=>{let body='';"
            "r.on('data',part=>body+=part);r.on('end',()=>process.exit(body==='hello from plain ASGI'?0:1))"
            "}).on('error',()=>process.exit(1))"
        )
        self.assertEqual(0, self._ship("exec", "playwright", "node", "-e", direct_check))
        command = (
            "import os; "
            "from playwright.sync_api import sync_playwright; "
            "p=sync_playwright().start(); "
            "browser=p.chromium.connect(os.environ['PLAYWRIGHT_WS_URL']); "
            "page=browser.new_page(); page.goto(os.environ['APP_URL']); "
            "assert page.text_content('body')=='hello from plain ASGI'; "
            "browser.close(); p.stop()"
        )
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", command))

    def test_selenium_python_client_opens_app(self):
        (self.root / "requirements.txt").write_text("selenium>=4.35,<5\n")
        self._init_up("--testing", "selenium")
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{self.app_port}/", timeout=2) as response:
                    if response.read() == b"hello from plain ASGI":
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                self.fail("Application HTTP did not become ready for browser tests")
            time.sleep(0.5)
        command = (
            "import os; "
            "from selenium import webdriver; "
            "from selenium.webdriver.chrome.options import Options; "
            "options=Options(); options.add_argument('--headless=new'); "
            "driver=webdriver.Remote(command_executor=os.environ['SELENIUM_REMOTE_URL'],options=options); "
            "driver.get(os.environ['APP_URL']); "
            "assert driver.find_element('tag name','body').text=='hello from plain ASGI'; "
            "driver.quit()"
        )
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", command))

    def test_hypercorn_serves_plain_asgi_and_reloads(self):
        self._init_up("--server", "hypercorn")
        endpoint = f"http://127.0.0.1:{self.app_port}/"
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(endpoint, timeout=2) as response:
                    if response.read() == b"hello from plain ASGI":
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                self.fail("Hypercorn did not serve the plain ASGI app")
            time.sleep(0.5)
        source = (self.root / "main.py").read_text()
        (self.root / "main.py").write_text(source.replace("hello from plain ASGI", "hello after reload"))
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(endpoint, timeout=2) as response:
                    if response.read() == b"hello after reload":
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                self.fail("Hypercorn did not reload a bind-mounted app edit")
            time.sleep(0.5)

    def test_uv_dependency_modes_and_host_owned_files(self):
        self._init_up()
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", "import uvicorn"))
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", "from pathlib import Path; Path('/app/generated.txt').write_text('ship')"))
        if hasattr(os, "getuid") and sys.platform.startswith("linux"):
            self.assertEqual(os.getuid(), (self.root / "generated.txt").stat().st_uid)
            self.assertEqual(os.getgid(), (self.root / "generated.txt").stat().st_gid)

        (self.root / "requirements.txt").write_text("httpx==0.28.1\n")
        self.assertEqual(0, self._ship("up"))
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", "import httpx; assert httpx.__version__ == '0.28.1'"))

        (self.root / "requirements.txt").unlink()
        (self.root / "appmark.py").write_text("VALUE = 'installed'\n")
        (self.root / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools>=77"]\nbuild-backend = "setuptools.build_meta"\n'
            '[project]\nname = "ship-phase5-fixture"\nversion = "0.1.0"\nrequires-python = ">=3.13"\n'
            '[tool.setuptools]\npy-modules = ["appmark"]\n'
        )
        self.assertEqual(0, self._ship("up"))
        check = "import importlib.metadata; assert importlib.metadata.version('ship-phase5-fixture') == '0.1.0'; import uvicorn"
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", check))

        self.assertEqual(0, self._ship("uv", "lock"))
        self.assertTrue((self.root / "uv.lock").is_file())
        self.assertEqual(0, self._ship("up"))
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", check))
        self.assertEqual(0, self._ship("uv", "add", "h11==0.16.0"))
        self.assertFalse((self.root / ".venv").exists())
        self.assertEqual(0, self._ship("up"))
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", check + "; import h11"))

    def _storage_object_round_trip(self, choice, *options):
        (self.root / "requirements.txt").write_text("boto3>=1.35,<2\n")
        self._init_up("--storage", choice, *options)
        command = (
            "import os,boto3,botocore; "
            "from botocore.config import Config; "
            "c=boto3.client('s3',endpoint_url=os.environ['AWS_ENDPOINT'],"
            "aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],"
            "aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],"
            "region_name=os.environ['AWS_DEFAULT_REGION'],"
            "config=Config(s3={'addressing_style':'path'})); "
            "bucket=os.environ['AWS_BUCKET']; "
            "exists=True\n"
            "try:\n c.head_bucket(Bucket=bucket)\n"
            "except botocore.exceptions.ClientError as error:\n"
            " if error.response['ResponseMetadata']['HTTPStatusCode']!=404: raise\n"
            " exists=False\n"
            "if not exists: c.create_bucket(Bucket=bucket)\n"
            "c.put_object(Bucket=bucket,Key='ship-check.txt',Body=b'storage works')\n"
            "assert c.get_object(Bucket=bucket,Key='ship-check.txt')['Body'].read()==b'storage works'\n"
        )
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", command))

    def test_silo_s3_object_round_trip(self):
        self._storage_object_round_trip("silo")

    def test_silo_standard_s3_object_round_trip(self):
        self._storage_object_round_trip("silo", "--silo-image", "standard")

    def test_rustfs_s3_object_round_trip(self):
        self._storage_object_round_trip("rustfs")

    def test_seaweedfs_s3_object_round_trip(self):
        self._storage_object_round_trip("seaweedfs")

    def test_seaweedfs_escaped_secret_round_trip(self):
        with patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": 'ship"\\secret'}):
            self._storage_object_round_trip("seaweedfs")

    def test_seaweedfs_rejects_unknown_credentials(self):
        (self.root / "requirements.txt").write_text("boto3>=1.35,<2\n")
        self._init_up("--storage", "seaweedfs")
        command = (
            "import os,boto3,botocore; "
            "from botocore.config import Config; "
            "c=boto3.client('s3',endpoint_url=os.environ['AWS_ENDPOINT'],"
            "aws_access_key_id='unknown',aws_secret_access_key='wrongsecret',"
            "region_name=os.environ['AWS_DEFAULT_REGION'],"
            "config=Config(s3={'addressing_style':'path'})); "
            "blocked=False\n"
            "try: c.list_buckets()\n"
            "except botocore.exceptions.ClientError as error:\n"
            " blocked=error.response['ResponseMetadata']['HTTPStatusCode']==403\n"
            "assert blocked\n"
        )
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", command))

    def test_garage_s3_object_round_trip(self):
        self._storage_object_round_trip("garage")


if __name__ == "__main__":
    unittest.main()

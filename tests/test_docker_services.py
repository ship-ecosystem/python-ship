"""Every built-in service, live: persistence, credentials, browsers, storage and npm.

Opt in with SHIP_DOCKER_TEST=1.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from tests.helpers import DockerProjectTestCase, captured, flag, free_port, wait_http

S3_ROUND_TRIP = (
    "import os,boto3,botocore\n"
    "from botocore.config import Config\n"
    "c=boto3.client('s3',endpoint_url=os.environ['AWS_ENDPOINT'],aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],"
    "aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],region_name=os.environ['AWS_DEFAULT_REGION'],"
    "config=Config(s3={'addressing_style':'path'}))\n"
    "bucket=os.environ['AWS_BUCKET']\n"
    "try:\n c.head_bucket(Bucket=bucket)\n"
    "except botocore.exceptions.ClientError as error:\n"
    " assert error.response['ResponseMetadata']['HTTPStatusCode']==404, error\n"
    " c.create_bucket(Bucket=bucket)\n"
    "c.put_object(Bucket=bucket,Key='ship-check.txt',Body=b'storage works')\n"
    "assert c.get_object(Bucket=bucket,Key='ship-check.txt')['Body'].read()==b'storage works'\n"
)


@unittest.skipUnless(flag("SHIP_DOCKER_TEST"), "set SHIP_DOCKER_TEST=1 for real Docker integration")
class ServiceCatalogDockerTests(DockerProjectTestCase):
    def setUp(self):
        super().setUp()
        self.ports = {name: free_port() for name in ("APP_PORT", "MAILPIT_WEB_PORT", "VITE_PORT", "SILO_CONSOLE_PORT")}
        (self.root / ".env").write_text("".join(f"{name}={port}\n" for name, port in self.ports.items()))

    def up(self, *arguments):
        self.assertEqual(0, self.init("--app", "main:app", *arguments), self.init_output.stderr)
        with captured() as output:
            code = self.ship("up")
        self.assertEqual(0, code, output.stderr + output.stdout[-3000:])

    def restart(self):
        with captured():
            self.assertEqual(0, self.ship("down"))
            self.assertEqual(0, self.ship("up"))

    def run_in(self, service, *command):
        result = self.compose("exec", "-T", service, *command)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def test_postgres_and_redis_keep_data_across_restarts(self):
        self.up("--database", "postgres", "--cache", "redis")
        self.run_in("postgres", "psql", "-U", "app", "-d", "app", "-c", "CREATE TABLE kept (v text); INSERT INTO kept VALUES ('yes');")
        self.run_in("redis", "redis-cli", "SET", "ship:kept", "yes")
        self.restart()
        self.assertIn("yes", self.run_in("postgres", "psql", "-U", "app", "-d", "app", "-tAc", "SELECT v FROM kept"))
        self.assertEqual("yes", self.run_in("redis", "redis-cli", "GET", "ship:kept").strip())

    def test_mysql_application_user_separate_root_and_persistence(self):
        self.up("--database", "mysql")
        self.run_in("mysql", "mysql", "-uapp", "-pship", "app", "-e", "CREATE TABLE kept (v VARCHAR(5)); INSERT INTO kept VALUES ('yes');")
        self.run_in("mysql", "mysql", "-uroot", "-prootship", "-e", "SELECT 1")
        self.restart()
        self.assertIn("yes", self.run_in("mysql", "mysql", "-uapp", "-pship", "app", "-e", "SELECT v FROM kept"))

    def test_mailpit_captures_smtp(self):
        self.up("--mail", "mailpit")
        send = ("import os,smtplib; from email.message import EmailMessage; m=EmailMessage(); "
                "m['From']='a@example.test'; m['To']='b@example.test'; m['Subject']='Ship'; m.set_content('hi'); "
                "s=smtplib.SMTP(os.environ['MAIL_HOST'],int(os.environ['MAIL_PORT'])); s.send_message(m); s.quit()")
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", send))
        deadline = time.monotonic() + 20
        while json.load(urlopen(f"http://127.0.0.1:{self.ports['MAILPIT_WEB_PORT']}/api/v1/messages", timeout=5)).get("total", 0) < 1:
            self.assertLess(time.monotonic(), deadline, "Mailpit did not capture the message")
            time.sleep(0.5)

    def test_meilisearch_is_healthy_and_requires_its_key(self):
        self.up("--search", "meilisearch")
        check = ("import os,json,urllib.request,urllib.error; base=os.environ['MEILISEARCH_URL']; "
                 "assert json.load(urllib.request.urlopen(base+'/health'))['status']=='available'; "
                 "r=urllib.request.Request(base+'/indexes',headers={'Authorization':'Bearer '+os.environ['MEILISEARCH_KEY']}); "
                 "assert urllib.request.urlopen(r).status==200\n"
                 "try:\n urllib.request.urlopen(base+'/indexes'); raise SystemExit('unauthenticated request succeeded')\n"
                 "except urllib.error.HTTPError as e:\n assert e.code==401, e.code")
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", check))
        self.assertIn("healthy", self.compose("ps", "meilisearch").stdout)

    def test_node_npm_and_vite_port(self):
        (self.root / "package.json").write_text('{"scripts":{"dev":"node dev.js"}}')
        (self.root / "dev.js").write_text(
            "require('http').createServer((q,s)=>s.end('ship hmr')).listen(Number(process.env.VITE_PORT),'0.0.0.0')\n")
        self.up("--frontend", "node")
        with captured():
            self.assertEqual(0, self.ship("npm", "--version"))
        process = subprocess.Popen([sys.executable, "-m", "python_ship", "--project-directory", str(self.root), "npm", "run", "dev"],
                                   env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            self.assertEqual(b"ship hmr", wait_http(self.ports["VITE_PORT"], timeout=30))
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)

    def test_playwright_browser_opens_the_app(self):
        (self.root / "requirements.txt").write_text("playwright==1.64.1\n")
        self.up("--testing", "playwright")
        wait_http(self.ports["APP_PORT"])
        script = ("import os; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); "
                  "b=p.chromium.connect(os.environ['PLAYWRIGHT_WS_URL']); page=b.new_page(); page.goto(os.environ['APP_URL']); "
                  "assert page.text_content('body')=='hello from plain ASGI', page.content(); b.close(); p.stop()")
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", script))

    def test_selenium_browser_opens_the_app(self):
        (self.root / "requirements.txt").write_text("selenium>=4.35,<5\n")
        self.up("--testing", "selenium")
        wait_http(self.ports["APP_PORT"])
        script = ("import os; from selenium import webdriver; from selenium.webdriver.chrome.options import Options; "
                  "o=Options(); o.add_argument('--headless=new'); "
                  "d=webdriver.Remote(command_executor=os.environ['SELENIUM_REMOTE_URL'],options=o); d.get(os.environ['APP_URL']); "
                  "assert d.find_element('tag name','body').text=='hello from plain ASGI'; d.quit()")
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", script))

    def storage_round_trip(self, *arguments):
        (self.root / "requirements.txt").write_text("boto3>=1.35,<2\n")
        self.up(*arguments)
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", S3_ROUND_TRIP))

    def test_garage(self):
        self.storage_round_trip("--storage", "garage")

    def test_seaweedfs_with_an_escaped_secret_and_rejected_credentials(self):
        with patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": 'ship"\\secret'}):
            self.storage_round_trip("--storage", "seaweedfs")
            reject = ("import os,boto3,botocore; from botocore.config import Config; "
                      "c=boto3.client('s3',endpoint_url=os.environ['AWS_ENDPOINT'],aws_access_key_id='unknown',"
                      "aws_secret_access_key='wrong',region_name='us-east-1',config=Config(s3={'addressing_style':'path'}))\n"
                      "try:\n c.list_buckets(); raise SystemExit('unknown credentials were accepted')\n"
                      "except botocore.exceptions.ClientError as e:\n assert e.response['ResponseMetadata']['HTTPStatusCode']==403")
            self.assertEqual(0, self.ship("exec", "app", "python", "-c", reject))

    def test_rustfs(self):
        self.storage_round_trip("--storage", "rustfs")

    def test_silo_standard_with_console(self):
        self.storage_round_trip("--storage", "silo", "--silo-image", "standard")
        with urlopen(Request(f"http://127.0.0.1:{self.ports['SILO_CONSOLE_PORT']}/"), timeout=10) as response:
            self.assertEqual(200, response.status)

    def test_silo_distroless(self):
        self.storage_round_trip("--storage", "silo", "--silo-image", "distroless")

    def celery_project(self):
        (self.root / "requirements.txt").write_text("celery[redis]==5.7.0\n")
        (self.root / "tasks.py").write_text(
            "import os\n"
            "from celery import Celery\n"
            "celery = Celery('tasks', broker=os.environ['CELERY_BROKER_URL'], backend=os.environ['CELERY_RESULT_BACKEND'])\n"
            "celery.conf.beat_schedule = {'tick': {'task': 'tasks.add', 'schedule': 2.0, 'args': (1, 1)}}\n"
            "@celery.task\n"
            "def add(x, y):\n"
            "    return x + y\n"
            "@celery.task\n"
            "def total(values):\n"
            "    return sum(values)\n")
        with (self.root / ".env").open("a") as stream:
            stream.write("CELERY_APP=tasks\n")

    def wait_for_beat(self):
        deadline = time.monotonic() + 60
        while "Sending due task tick" not in self.compose("logs", "celery-beat").stdout:
            self.assertLess(time.monotonic(), deadline, self.compose("logs", "celery-beat").stdout[-2000:])
            time.sleep(2)

    def test_celery_with_redis_runs_a_chord_and_beat(self):
        self.celery_project()
        self.up("--cache", "redis", "--tasks", "celery")
        chord = ("from celery import chord; from tasks import add, total; "
                 "assert chord(add.s(i, i) for i in range(4))(total.s()).get(timeout=60) == 12")
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", chord))
        self.wait_for_beat()

    def test_celery_with_rabbitmq_runs_a_chain(self):
        self.ports["RABBITMQ_MANAGEMENT_PORT"] = free_port()
        self.celery_project()
        with (self.root / ".env").open("a") as stream:
            stream.write(f"RABBITMQ_MANAGEMENT_PORT={self.ports['RABBITMQ_MANAGEMENT_PORT']}\n")
        self.up("--broker", "rabbitmq", "--tasks", "celery")
        chain = "from tasks import add; assert (add.s(1, 2) | add.s(3)).delay().get(timeout=60) == 6"
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", chain))
        with urlopen(f"http://127.0.0.1:{self.ports['RABBITMQ_MANAGEMENT_PORT']}/", timeout=10) as response:
            self.assertEqual(200, response.status)
        self.wait_for_beat()

    def test_dependency_modes_and_ship_uv(self):
        self.up()
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", "import uvicorn"))
        (self.root / "requirements.txt").write_text("httpx==0.28.1\n")
        with captured():
            self.assertEqual(0, self.ship("up"))
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", "import httpx; assert httpx.__version__ == '0.28.1'"))
        (self.root / "requirements.txt").unlink()
        (self.root / "appmark.py").write_text("VALUE = 'installed'\n")
        (self.root / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools>=77"]\nbuild-backend = "setuptools.build_meta"\n'
            '[project]\nname = "ship-fixture"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n'
            '[tool.setuptools]\npy-modules = ["appmark"]\n')
        check = "import importlib.metadata as m; assert m.version('ship-fixture') == '0.1.0'; import uvicorn"
        with captured():
            self.assertEqual(0, self.ship("up"))
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", check))
        with captured():
            self.assertEqual(0, self.ship("uv", "lock"))
        self.assertTrue((self.root / "uv.lock").is_file())
        with captured():
            self.assertEqual(0, self.ship("uv", "add", "h11==0.16.0"))
            self.assertEqual(0, self.ship("up"))
        self.assertFalse((self.root / ".venv").exists())
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", check + "; import h11"))


if __name__ == "__main__":
    unittest.main()

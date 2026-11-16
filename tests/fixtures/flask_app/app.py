"""Flask (WSGI) integration fixture using Ship's injected connection values."""

import os

from flask import Flask, jsonify
import psycopg
import redis

app = Flask(__name__)
MESSAGE = "flask initial"


@app.get("/")
def index():
    return jsonify(framework=MESSAGE)


@app.get("/dependencies")
def dependencies():
    with psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    ) as connection:
        database = connection.execute("SELECT 1").fetchone()[0]
    cache = redis.from_url(os.environ["REDIS_URL"]).ping()
    return jsonify(database=database, redis=cache)


@app.cli.command("hello")
def hello():
    """A custom command, for `ship flask hello`."""
    print("hello from flask")

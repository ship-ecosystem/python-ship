"""FastAPI integration fixture using Ship's injected connection values."""

import os

from fastapi import FastAPI
import psycopg
import redis

app = FastAPI()
MESSAGE = "fastapi initial"


@app.get("/")
def index():
    return {"framework": MESSAGE}


@app.get("/dependencies")
def dependencies():
    with psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            database = cursor.fetchone()[0]
    cache = redis.from_url(os.environ["REDIS_URL"]).ping()
    return {"database": database, "redis": cache}

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
import redis


def index(request):
    return JsonResponse({"framework": "django"})


def dependencies(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        database = cursor.fetchone()[0]
    cache = redis.from_url(settings.REDIS_URL).ping()
    return JsonResponse({"database": database, "redis": cache})

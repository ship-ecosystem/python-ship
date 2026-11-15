"""The application must explicitly consume Ship's injected connection values."""

import os

SECRET_KEY = "ship-django-integration-fixture-only"
DEBUG = True
ROOT_URLCONF = "project.urls"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
INSTALLED_APPS = []
MIDDLEWARE = []
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "NAME": os.environ.get("DB_NAME", "app"),
        "USER": os.environ.get("DB_USER", "app"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "ship"),
    }
}
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

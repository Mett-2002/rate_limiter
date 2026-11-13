"""Local demo settings. The demo accesses the limiter only over HTTP."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DEMO_SECRET_KEY", "local-rate-limiter-demo-only-not-for-production")
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "support",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
# Cookies are shared across localhost ports; keep the demo token separate from API Admin.
CSRF_COOKIE_NAME = "demo_csrftoken"
ROOT_URLCONF = "demo_config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.csrf",
    ]},
}]
WSGI_APPLICATION = "demo_config.wsgi.application"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"
LIMITER_BASE_URL = os.environ.get("LIMITER_BASE_URL", "http://127.0.0.1:8200").rstrip("/")
DEMO_API_KEY = os.environ.get("DEMO_API_KEY", "")
DEMO_CREDENTIALS_FILE = BASE_DIR / ".runtime" / "credentials.json"

"""Production settings with hardened security headers and HTTPS enforcement."""

import os
from .base import *  # noqa: F401,F403

DEBUG = False
IS_VERCEL = bool(os.environ.get("VERCEL"))

if IS_VERCEL:
    if ".vercel.app" not in ALLOWED_HOSTS:  # noqa: F405
        ALLOWED_HOSTS = [*ALLOWED_HOSTS, ".vercel.app"]  # noqa: F405
    WHITENOISE_USE_FINDERS = True
    STORAGES["staticfiles"] = {  # noqa: F405
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    }

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])  # noqa: F405
if IS_VERCEL:
    for _host_var in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        _host_val = os.environ.get(_host_var, "").strip()
        if _host_val:
            _origin = f"https://{_host_val}"
            if _origin not in CSRF_TRUSTED_ORIGINS:
                CSRF_TRUSTED_ORIGINS.append(_origin)
    if "https://*.vercel.app" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append("https://*.vercel.app")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"

DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0 if IS_VERCEL else 60)  # noqa: F405

"""Local development settings."""

from .base import *  # noqa: F401,F403

DEBUG = env.bool("DEBUG", default=True)  # noqa: F405

STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}

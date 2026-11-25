from django.apps import AppConfig
from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security, deploy=True)
def check_production_secret_key(app_configs, **kwargs):
    errors = []
    if not settings.DEBUG:
        sk = getattr(settings, "SECRET_KEY", "") or ""
        if "change-me" in sk or len(sk) < 32:
            errors.append(
                checks.Warning(
                    "SECRET_KEY appears to be a default placeholder or shorter than 32 characters.",
                    hint="Set a strong random SECRET_KEY (>= 50 chars) in .env before pilot or production launch.",
                    id="marina_shoes.W001",
                )
            )
    return errors


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Core"

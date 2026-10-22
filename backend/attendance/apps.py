from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'attendance'

    def ready(self):
        # The ICT officer's admin access, and forgetting offices looked up
        # this request when one is given or taken.
        from . import signals  # noqa: F401

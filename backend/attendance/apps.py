from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'attendance'

    def ready(self):
        # Registers the receiver that bills a student as they are enrolled.
        from . import signals  # noqa: F401

"""What the system must do for itself: an office carries its own access.

Charges used to follow enrollment here — a student added to a module was
billed for the year. Since 20 October 2026 they do not: admission's finance
desk raises the year's bill once, and nothing else bills the year again.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ICTOfficerProfile

logger = logging.getLogger(__name__)


# A student added to a module is no longer billed here (said 20 October 2026):
# the year's bill is raised once, at the finance desk, when they are admitted.
# This hook re-ran the fee generator on every new enrollment and billed a part
# twice when the fee setup or the schedule had changed since admission.


@receiver(post_save, sender=ICTOfficerProfile,
          dispatch_uid='attendance.ict_officer_opens_django_admin')
def ict_officer_opens_django_admin(sender, instance, **kwargs):
    """The ICT officer runs the system, so Django's own admin site is theirs,
    with every table in it: the role makes the account a superuser.

    What the ICT officer may do inside the app is still decided by roles.py.
    Django's admin will happily attach an ICT officer profile without touching
    the flags, so the profile sets them itself. Grants only; taking the role
    away goes through set_roles.
    """
    user = instance.user
    if instance.is_active and not (user.is_staff and user.is_superuser):
        user.is_staff = user.is_superuser = True
        user.save(update_fields=['is_staff', 'is_superuser'])
        logger.info('Opened the admin site to %s on becoming ICT officer', user.username)

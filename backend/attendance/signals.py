"""Two things the system must do for itself.

Charges follow enrollment, and an office carries its own access.

A student used to be registered and then owe nothing at all until somebody
remembered to run "generate charges" for their whole level. Anyone admitted
after that run — a late admission, a transfer, a student added when a tutor
corrected a roster — sat there with an empty ledger. They looked fully paid up
on the portal, they cleared every exam check, and the college only found out
when the accountant reconciled at the end of term.

Enrolling somebody is the moment the college decides to charge them, so that is
where the charges are raised. `generate_charges` is idempotent on
(person, charge type, year, instalment), so a student enrolled in four modules
is still billed once, and the accountant's bulk run remains safe to repeat.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from . import finance
from .models import ICTOfficerProfile, Student

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Student, dispatch_uid='attendance.bill_new_enrollment')
def bill_new_enrollment(sender, instance, created, **kwargs):
    """Raise a newly enrolled student's charges for the year they joined.

    Deliberately inside the enrolling transaction rather than on commit: if the
    registration rolls back, the charges must go with it.
    """
    if not created or getattr(instance, 'skip_auto_billing', False):
        return

    module = instance.module
    if module is None or module.semester_id is None:
        return
    academic_year = module.semester.academic_year

    try:
        profile = finance.profile_for_student(instance)
        # No level passed in: the student's registration decides it. This used
        # to pass the module's own level, which billed a level 5 student at
        # level 4 the moment they were enrolled in a level 4 module to repeat —
        # and since a charge is raised only once, the level 5 amount never
        # replaced it.
        raised = finance.generate_charges(profile, academic_year)
    except ValueError as exc:
        # The fee structure exists but has no due dates yet. Registration must
        # not fail over that — the accountant's bulk run will catch this
        # student up once the schedule is set.
        logger.warning(
            'Could not bill %s on enrollment: %s', instance.nactvet_reg_no, exc)
        return

    if raised:
        logger.info('Billed %s for %s on enrollment: %d charge(s)',
                    instance.nactvet_reg_no, academic_year, len(raised))


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

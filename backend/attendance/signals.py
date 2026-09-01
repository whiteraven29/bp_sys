"""Charges follow enrollment.

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
from .models import Student

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
        raised = finance.generate_charges(
            profile, academic_year, class_level=module.class_level)
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

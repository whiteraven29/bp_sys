"""Helpers shared by the test modules."""

from .models import ClassLevel, ResultRelease, Semester


def show_all_results():
    """Every semester's results shown to every level's students, as the
    examination office does with *Show to students* (ResultRelease) — for
    tests about what the portal shows once results are out."""
    for semester in Semester.objects.all():
        for level in ClassLevel.objects.all():
            ResultRelease.objects.update_or_create(semester=semester, class_level=level,
                                                   defaults={'visible': True})

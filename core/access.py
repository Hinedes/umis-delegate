"""Object-level access rules.

Views ask these helpers instead of checking role names. A helper carries one
rule; the system that owns the object extends it when its data model lands.

Visit scope: a user with the view permission sees every visit, otherwise only
visits assigned to them. Patient scope mirrors that through assigned visits.
"""
from .models import Patient, Visit


def visible_visits(user):
    """Visits the user may list."""
    if user.has_perm('core.view_visit'):
        return Visit.objects.all()
    return Visit.objects.filter(assigned_doctor=user)


def can_view_visit(user, visit):
    return user.has_perm('core.view_visit') or visit.assigned_doctor_id == user.id


def can_change_visit(user, visit):
    return user.has_perm('core.change_visit') or visit.assigned_doctor_id == user.id


def can_start_visit(user, visit):
    return user.has_perm('core.start_visit') or can_change_visit(user, visit)


def visible_patients(user):
    """Patients the user may list."""
    if user.has_perm('core.view_patient'):
        return Patient.objects.all()
    return Patient.objects.filter(visits__assigned_doctor=user).distinct()


def can_view_patient(user, patient):
    """Whether the user may open this patient record."""
    if user.has_perm('core.view_patient'):
        return True
    return patient.visits.filter(assigned_doctor=user).exists()

"""Canonical phone normalization and strict phone identity.

Search and identity are deliberately different operations:
- staff discovery is tolerant and lives in ``core.search``;
- identity is exact to E.164 and never guesses.
"""

import phonenumbers
from django.conf import settings


class AmbiguousPhoneIdentity(Exception):
    """Several patients share one canonical number; refuse to choose one."""

    def __init__(self, patients):
        self.patients = patients
        super().__init__('Multiple patients share this phone number')


def normalize_phone(raw, region=None):
    """Return canonical E.164 for a valid number, else ``None``.

    An explicit ``region`` wins; otherwise ``PHONENUMBER_DEFAULT_REGION`` is
    used. There is no guessing beyond that setting: a national number with no
    configured region does not normalize.
    """
    text = (raw or '').strip()
    if not text:
        return None
    region = (region or settings.PHONENUMBER_DEFAULT_REGION or '').strip().upper() or None
    try:
        parsed = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def resolve_patient_by_phone(raw, region=None):
    """Strict identity lookup: one exact canonical match or nothing.

    Raises ``ValueError`` when the input is not a valid number (including
    suffix fragments), ``AmbiguousPhoneIdentity`` when several patients share
    the number, and returns ``None`` when no patient matches.
    """
    canonical = normalize_phone(raw, region)
    if canonical is None:
        raise ValueError('Enter a valid phone number')
    from .models import Patient

    matches = Patient.objects.filter(phone=canonical)
    rows = list(matches[:2])
    if len(rows) > 1:
        raise AmbiguousPhoneIdentity(matches)
    return rows[0] if rows else None

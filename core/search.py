"""Staff-facing registry search.

Search is tolerant because the result is presented for a human to choose.
Identity is strict and lives in ``core.phones``.
"""

from django.db.models import Q

from .phones import normalize_phone

PHONE_FRAGMENT_MIN_DIGITS = 4


def phone_search_q(raw, region=None):
    """Valid number -> exact E.164; otherwise a digit fragment contains match."""
    text = (raw or '').strip()
    if not text:
        return None
    canonical = normalize_phone(text, region)
    if canonical:
        return Q(phone=canonical)
    digits = ''.join(ch for ch in text if ch.isdigit())
    if len(digits) >= PHONE_FRAGMENT_MIN_DIGITS:
        return Q(phone__icontains=digits)
    return None


def patient_search_q(raw, region=None):
    """The one patient-matching rule every registry search uses.

    Name substring and trigram similarity, national ID substring, and the
    tolerant phone rule. Callers apply their own authorization scope first.
    """
    text = (raw or '').strip()
    if not text:
        return Q()
    q = Q(full_name__icontains=text) | Q(national_id__icontains=text)
    q |= Q(full_name__trigram_similar=text)
    phone_q = phone_search_q(text, region)
    if phone_q:
        q |= phone_q
    return q

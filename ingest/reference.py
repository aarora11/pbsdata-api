"""Canonical PBS code vocabularies.

Single source of truth shared by the ingest pipeline and the API layer. Both sides
import from here so a value written by the ingest is always a value the API knows
how to interpret.

Historically the ingest expanded PBS benefit-type codes into long-form strings
("authority_required") while the API matched on the raw PBS letters ("A"). The two
never agreed, and because the mismatch produced empty aggregates rather than errors
it went unnoticed. Storage is now the PBS letter; expansion to a human label happens
only at the response layer. See docs/AUDIT_FINDINGS.md [B3].
"""

# ── Benefit type ──────────────────────────────────────────────────────────────
# Stored in items.benefit_type as the PBS letter code. Values are from the PBS v3
# data dictionary; the label is ours and is presentation-only.
BENEFIT_TYPE_LABELS: dict[str, str] = {
    "U": "Unrestricted",
    "R": "Restricted",
    "S": "Streamlined Authority",
    "A": "Authority Required",
    "O": "Hospital",
    "B": "Brand Equivalent",
    "C": "Combination",
    "D": "Doctor's Bag",
    "F": "Mnemonics",
    "G": "General Schedule",
    "H": "Highly Specialised",
    "N": "Nursing Home",
    "P": "Private",
    "T": "In Hospital",
    "W": "In Hospital Only",
}

# Long-form values written by earlier ingest runs, mapped back to the PBS letter.
# Retained so migration 009 and any un-migrated caller can normalise old data.
LEGACY_BENEFIT_TYPE_TO_CODE: dict[str, str] = {
    "unrestricted": "U",
    "restricted": "R",
    "authority_streamlined": "S",
    "authority_required": "A",
    "hospital": "O",
    "brand_equivalent": "B",
    "combination": "C",
    "doctors_bag": "D",
    "mnemonics": "F",
    "general_schedule": "G",
    "highly_specialised": "H",
    "nursing_home": "N",
    "private": "P",
    "in_hospital": "T",
    "in_hospital_only": "W",
}


def canonical_benefit_type(raw: str | None) -> str:
    """Return the PBS letter code for a benefit type in any form we have written.

    Accepts a PBS letter ("A"), a legacy long-form value ("authority_required"), or
    None. Unknown values are passed through uppercased rather than silently mapped,
    so a new PBS code surfaces as itself instead of being coerced to "U".
    """
    if not raw:
        return "U"
    value = str(raw).strip()
    if value in BENEFIT_TYPE_LABELS:
        return value
    upper = value.upper()
    if upper in BENEFIT_TYPE_LABELS:
        return upper
    return LEGACY_BENEFIT_TYPE_TO_CODE.get(value.lower(), upper)


def benefit_type_label(code: str | None) -> str | None:
    """Human label for a benefit type code. Unknown codes return themselves."""
    if code is None:
        return None
    return BENEFIT_TYPE_LABELS.get(code, code)


# ── Formulary ─────────────────────────────────────────────────────────────────
# F1/F2 describe how many brands of a medicine are listed, which in turn drives
# price-disclosure obligations. They are NOT a cost-effectiveness ranking — earlier
# labels described them that way and were wrong. See docs/AUDIT_FINDINGS.md [F6].
#
# PENDING DOMAIN CONFIRMATION: the substitutability direction (which of F1/F2 is
# brand-substitutable) is on the list for external PBS review. These labels
# deliberately describe brand count only and make no substitutability claim.
FORMULARY_LABELS: dict[str, str] = {
    "F1": "Formulary 1 — single-brand listing",
    "F2": "Formulary 2 — multiple-brand listing, subject to price disclosure",
    "CDL": "Combination Drug List",
}

FORMULARY_NOTE = (
    "Formulary labels describe brand-count status, not cost-effectiveness. "
    "Substitutability rules are not asserted here — consult the PBS Schedule."
)


def formulary_label(code: str | None) -> str | None:
    """Human label for a formulary code. Unknown or absent codes return None."""
    if not code:
        return None
    return FORMULARY_LABELS.get(code, code)


# ── Authority method ──────────────────────────────────────────────────────────
# Populated from the PBS restrictions feed. NOTE: this field is currently null for
# every ingested restriction; the correct PBS source field is an open question for
# domain review. See docs/AUDIT_FINDINGS.md [F3].
AUTHORITY_METHOD_LABELS: dict[str, str] = {
    "S": "Streamlined (self-assess)",
    "T": "Telephone approval",
    "W": "Written application",
    "O": "Online application",
}


def authority_method_label(code: str | None) -> str | None:
    """Human label for an authority method code. Unknown codes return themselves."""
    if code is None:
        return None
    return AUTHORITY_METHOD_LABELS.get(code, code)


# ── summary_of_changes.section ────────────────────────────────────────────────
# The PBS feed supplies `changed_endpoint`, e.g. "/items" or
# "/item-restriction-relationships". Stored without the leading slash so equality
# comparisons against a table name work. See docs/AUDIT_FINDINGS.md [B4].
def canonical_section(raw: str | None) -> str | None:
    """Strip the leading slash from a PBS changed_endpoint value."""
    if raw is None:
        return None
    return str(raw).strip().lstrip("/") or None

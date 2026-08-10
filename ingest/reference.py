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
# `/restrictions.authority_method` — no longer an open question. The data dictionary
# defines it as "Determines if a benefit is restricted or not and if so the level of
# authority required", with values R / A / S, and states that "authority_method and
# benefit_type_code fields hold the same information in other endpoints". Measured on
# 2026-06 that holds exactly: A↔AUTHORITY_REQUIRED (2,201 items), R↔RESTRICTED (1,414),
# S↔STREAMLINED (2,111), with no item carrying a mismatched pair.
#
# The dictionary (v3.6.5) documents the single letters; the live API (v3.7.8) sends the
# long form. Both are accepted on input and stored as the PBS letter, matching how
# benefit_type is handled above.
AUTHORITY_METHOD_LABELS: dict[str, str] = {
    "R": "Restricted Benefit",
    "A": "Authority Required",
    "S": "Authority Required (Streamlined)",
}

_AUTHORITY_METHOD_TO_CODE: dict[str, str] = {
    "RESTRICTED": "R",
    "AUTHORITY_REQUIRED": "A",
    "STREAMLINED": "S",
}

# Methods that constitute an authority listing. A Restricted Benefit carries clinical
# criteria but needs no authority approval, so it is deliberately excluded.
AUTHORITY_REQUIRED_METHODS: frozenset[str] = frozenset({"A", "S"})


def canonical_authority_method(raw: str | None) -> str | None:
    """Return the PBS letter for an authority method in either published form.

    Accepts a letter ("A"), the long form the live API sends ("AUTHORITY_REQUIRED"),
    or None. Unknown values pass through uppercased rather than being mapped to a
    guess, so a new PBS value surfaces as itself instead of silently becoming wrong.
    """
    if raw is None:
        return None
    value = str(raw).strip().upper()
    if not value:
        return None
    return _AUTHORITY_METHOD_TO_CODE.get(value, value)


def authority_method_label(code: str | None) -> str | None:
    """Human label for an authority method code. Unknown codes return themselves."""
    if code is None:
        return None
    return AUTHORITY_METHOD_LABELS.get(canonical_authority_method(code), code)


def is_authority_required(method: str | None) -> bool:
    """Whether an authority method means the prescriber needs an authority."""
    return canonical_authority_method(method) in AUTHORITY_REQUIRED_METHODS


# ── Restriction kind ──────────────────────────────────────────────────────────
# `/restrictions` returns three different things in one feed and PBS distinguishes
# them three independent ways, all of which agree exactly (3,030 / 444 / 84 on
# 2026-06): the res_code suffix, the note_indicator/caution_indicator flags, and
# whether li_html_text is populated. The data dictionary ties the first two together
# — note_indicator "Relates to N suffix in res_code" — and explains the third:
# li_html_text "is Null for Notes and Cautions as they are not legal".
#
# Only the restrictions are true restrictions. Serving all three as restrictions
# overstated /v1/drugs/{code}/restrictions by 2.3x. See docs/AUDIT_FINDINGS.md.
RESTRICTION_KIND_RESTRICTION = "restriction"
RESTRICTION_KIND_NOTE = "note"
RESTRICTION_KIND_CAUTION = "caution"

RESTRICTION_KINDS: frozenset[str] = frozenset({
    RESTRICTION_KIND_RESTRICTION, RESTRICTION_KIND_NOTE, RESTRICTION_KIND_CAUTION,
})

_RES_CODE_SUFFIX_TO_KIND: dict[str, str] = {
    "R": RESTRICTION_KIND_RESTRICTION,
    "N": RESTRICTION_KIND_NOTE,
    "C": RESTRICTION_KIND_CAUTION,
}


def restriction_kind(raw: dict) -> str:
    """Classify a /restrictions row as a restriction, note or caution.

    Prefers the explicit indicator flags and falls back to the res_code suffix, which
    the data dictionary documents as carrying the same meaning. Anything unclassified
    is treated as a restriction, matching the feed's own default (a row is a note or
    caution only when it says so).
    """
    def flag(name: str) -> bool:
        return str(raw.get(name) or "").strip().upper() == "Y"

    if flag("note_indicator"):
        return RESTRICTION_KIND_NOTE
    if flag("caution_indicator"):
        return RESTRICTION_KIND_CAUTION
    code = str(raw.get("res_code") or "")
    suffix = code.rsplit("_", 1)[-1].strip().upper() if "_" in code else ""
    return _RES_CODE_SUFFIX_TO_KIND.get(suffix, RESTRICTION_KIND_RESTRICTION)


# ── summary_of_changes.section ────────────────────────────────────────────────
# The PBS feed supplies `changed_endpoint`, e.g. "/items" or
# "/item-restriction-relationships". Stored without the leading slash so equality
# comparisons against a table name work. See docs/AUDIT_FINDINGS.md [B4].
def canonical_section(raw: str | None) -> str | None:
    """Strip the leading slash from a PBS changed_endpoint value."""
    if raw is None:
        return None
    return str(raw).strip().lstrip("/") or None

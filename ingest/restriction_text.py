"""Read PBS restriction text: plain paragraphs, and the condition a restriction covers.

Shared by the ingest (to pair 30-day and 60-day listings) and the public site (to label
listings). Kept in one place because both depend on reading the same markup the same way.
"""
import html
import re


def paragraphs(markup):
    """PBS restriction text is loose HTML; keep its words, drop its markup."""
    if not markup:
        return []
    text = re.sub(r"</p>|<br\s*/?>|</li>|</h\d>|</div>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return [" ".join(line.split()) for line in text.split("\n") if line.strip()]


# Lines that open a restriction without naming a condition: the legislative instrument,
# the prescriber-type heading, and the clause PBS adds to every 60-day listing. Treating
# them as conditions left 108 of 115 unpaired 60-day listings unpaired (2026-06): a
# 60-day listing opens with the stability clause where its 30-day twin opens with the
# prescriber heading, so the two never matched.
NOT_A_CONDITION = (
    "Listing of Pharmaceutical Benefits",
    "Schedule",
    "For prescribing by",
    "The condition must be stable",
)


def condition_of(paras):
    """The condition a restriction covers, e.g. "Epileptic seizures", or None.

    PBS restriction text opens with the legislative instrument it sits in, then usually
    names the condition. Returns None when the restriction names no condition, or when
    the first substantive paragraph is too long to be a condition name.
    """
    for para in paras:
        if para.startswith(NOT_A_CONDITION):
            continue
        # The first substantive line either names the condition or is criteria text.
        if para.startswith(("Must ", "Patient must", "The treatment must")):
            return None
        return para if len(para) <= 80 else None
    return None

# Verification against pbs.gov.au — 100 items, August 2026

An independent check of the data we serve *after* all joins, against the public PBS
medicine pages. Distinct from the Tier A sweep: that compares us to `/item-overview`,
another API surface. This compares us to what PBS **displays to the public**, which is
the closest thing to an outside auditor this project has.

## Result

**658 of 659 comparable checks passed — 99.8%.**

| Field | Pass | Fail | N/A | Rate |
|---|---:|---:|---:|---:|
| Ingredient | 99 | 1 | 0 | 99.0% |
| Brands | 100 | 0 | 0 | 100% |
| Sponsor | 100 | 0 | 0 | 100% |
| Max quantity | 99 | 0 | 1 | 100% |
| Repeats | 100 | 0 | 0 | 100% |
| Patient charge | 100 | 0 | 0 | 100% |
| Listing type | 60 | 0 | 40 | 100% |

`N/A` means the site did not publish a comparable value — those are excluded rather
than counted as passes, because scoring unknowns as successes would defeat the exercise.

## Method

`scripts/verify_against_pbs_site.py` draws a seeded, stratified sample (seed 20260811)
and exports our canonical facts. Each item's page is then read and compared field by
field.

- **100 items across 15 programs** — 49 General Schedule, plus deliberate coverage of
  Section 100 hospital (HB/HS/CA), chemotherapy (IN/IP/CT), RPBS, palliative care,
  growth hormone, botulinum toxin, IVF, Prescriber Bag and special authority.
- **All four benefit types**: 33 Authority Required, 33 Streamlined, 21 Unrestricted,
  13 Restricted.
- **Extemporaneous (EP) and Paraplegic/Quadriplegic (PQ) excluded by design.**
  pbs.gov.au publishes no medicine page for them — verified on 11025G, 3250C and 100G,
  all of which 404 while the live API returns them in the current schedule. They cannot
  be compared, and including them would only manufacture failures.
- The current schedule (2026-08) was ingested first so prices compare like for like.
  That ingest passed every gate on a schedule the pipeline had never seen: Tier A joins
  matched across 14,942 items, and the patient charge formula recomposed on 99.57% of
  38,816 pricing rows.

## What the first pass flagged, and what it turned out to be

The raw first run scored 97.1% with 19 disagreements. All but one were artefacts of the
comparison rather than defects, and each was tested rather than assumed.

**Twelve "max quantity" mismatches — the site quotes packs, we store units.**
Confirmed on all twelve: `max_quantity ÷ pack_size` equals the site's figure exactly,
including 12046B where the site shows `0.5` against our 10 units in a pack of 20.
Nothing but packs explains a half. Zero defects.

**Five "patient charge" mismatches — schedule drift.** Every one was an item priced
*under* the copayment, where the charge tracks the price and therefore moves between
schedules. Re-run against 2026-08 all five matched to the cent. Items sitting at the
copayment cap matched in both runs, which is what you would expect if drift, not
mapping, were the cause.

**One "sponsor" mismatch — an extraction error on our side of the comparison.** For
12565H the page shows *Hikma* as the brand, with the manufacturer only reachable through
the brand link, which resolves to *Link Medical Products Pty Ltd* — our stored value.

## The one genuine finding

**We display the legal name where PBS displays the schedule name**, on roughly 15% of
items (2,228 of 14,782 have both fields present and different).

The `/items` feed carries two names and the data dictionary distinguishes them:

- `drug_name` — "PBS Schedule drug name… Medicinal Product Pack – Name"
- `li_drug_name` — "**Legally determined** name of a drug used in **legislative
  instruments**"

`ingest/normaliser.py` prefers `li_drug_name`, falling back to `drug_name`. So for 9357L
we show *Ibandronic Acid* where pbs.gov.au shows *ibandronate*. Others in the same class:
*Senna standardised* vs *Sennoside B*, *Dolutegravir with lamivudine* vs
*Dolutegravir + lamivudine*.

Neither field is wrong — they are different PBS concepts. But a customer rendering a
drug name to a clinician will expect the name PBS itself displays, and today they get
the other one.

**Recommended:** store both, expose `drug_name` as the display name and keep
`li_drug_name` for legal-instrument matching. That is additive and lets the caller
choose, rather than us choosing wrongly for one audience or the other.

## Reproducing

```
python scripts/verify_against_pbs_site.py --month 2026-08 --n 100 --out sample.json
# read each https://www.pbs.gov.au/medicine/item/{code} into site_results.json
python compare.py sample.json
```

The sample is seeded, so the same 100 codes are drawn every time and results are
comparable across runs.

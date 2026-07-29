# PBSdata.io — Data & Join Correctness Audit

**Date:** 2026-07-28
**Scope:** All 99 endpoints across 31 routers, 41 tables, 8 migrations, and the ingest pipeline.
**Method:** Source inspection of `api/routers/`, `ingest/`, `migrations/`, cross-checked against
`docs/pbs-endpoint-audit-spec.md` (Tier A/B/C framework) and verified empirically against the
live development database where data existed.
**Verification database:** `postgresql://localhost:5432/pbsapi`, schedules `2026-03` (6,883 items,
`ingest_status='failed'`) and `2026-04` (0 items, `ingest_status='complete'`).

---

## 0. Executive summary

The join *topology* is largely sound — most relationships use the PBS-documented keys and are
correctly schedule-scoped. The defects are concentrated in three areas:

1. **A contract mismatch between the ingest layer and the API layer.** The ingest writes expanded
   enum values (`authority_required`) where the API matches on single letters (`'A'`). This silently
   zeroes out several headline metrics rather than erroring.
2. **Aggregate queries that count join fan-out rather than entities.** Measured at **3.12× inflation**
   on live data. This affects the flagship T4 Market endpoints.
3. **Fields that are fabricated, aliased, or derived from the wrong source column** and then presented
   as PBS-official data.

Separately, and most urgently: **the only schedule marked `complete` contains zero items.** Every
endpoint that resolves a default schedule (`WHERE ingest_status='complete' ORDER BY month DESC LIMIT 1`)
currently returns 404 or empty. The 6,883-item schedule is marked `failed` and is invisible to the API.

**Counts:** 6 blocking defects, 9 correctness defects, 8 mislabelled/fabricated fields,
5 population/operational gaps, 4 undisclosed heuristics.

**Recommendation:** Do not put T3/T4 in front of a paying customer until §1 and §2 are closed.
The T1/T2 surface is closer, but §3.1–§3.5 must be resolved first because they are wrong in a way a
domain expert will notice immediately.

---

## 0.1 Remediation status — updated 2026-07-28

Remediation was carried out against `docs/FIX_PLAN.md`. Test count went from **221 to 268**;
`tests/test_audit_regressions.py` pins each fixed finding so it cannot silently return.

### Root cause found during remediation

**Three client methods existed but `ingest/runner.py` never called them.** This accounts for a large
share of the "empty table" and "100% NULL" findings below — they were not field-mapping errors:

| Never called | Left empty |
|---|---|
| `get_all_item_dispensing_rule_relationships` | `item_pricing`, and hence every price statistic |
| `get_all_item_amt` | `item_amt_relationships` (`raw_item_amt = []` was hardcoded) |
| `get_all_item_overviews` | `biosimilar`, `artg_id`, `sponsor`, `caution` (`raw_item_overviews = []` hardcoded) |

The runner called `get_all_item_dispensing_rules` — a **different method with a nearly identical
name** that feeds the rule-link table — while the pricing method sat unused beside it.

### Status by finding

| ID | Status | Notes |
|---|---|---|
| `[B1]` `is_primary` missing | **Fixed** | Column added and populated in migration 009 and at ingest; one primary ATC per item, ties broken deterministically |
| `[B2]` undefined alias | **Fixed** | ATC filter now built per table alias |
| `[B3]` benefit_type enum | **Fixed** | Canonicalised on PBS letter codes via `ingest/reference.py`; migration 009 converts existing rows. Authority counts are non-zero for the first time |
| `[B4]` `section` mismatch | **Fixed** | Slash stripped at ingest, backfilled by migration, and comparisons normalise defensively |
| `[B5]` delisting enrichment | **Fixed** | Names and ATC resolved against the previous completed schedule |
| `[B6]` primary ATC inverted | **Fixed** | `DISTINCT ON` replaces the dict comprehension |
| `[C1]` join fan-out | **Fixed** | Metrics computed per grain via CTEs; invariant test added |
| `[C2]` two price sources | **Partially addressed** | Endpoints now declare `price_basis`; choosing a canonical source remains a domain question |
| `[C3]` `medicines` not versioned | **Open — needs decision** | Claim corrected in `OFFERING.md`; fix deferred pending domain input on whether ingredient ATC actually churns |
| `[C4]` restriction lookup | **Fixed** | Deterministic ordering plus `applies_to_pbs_codes` |
| `[C5]` falsy-zero charge | **Fixed** | Shared `patient_charge()` helper; recomposition test added |
| `[C6]` concessional mismatch | **Fixed** | Both endpoints use the shared helper |
| `[C7]` truncated statistics | **Fixed** | Population statistics computed in a separate unlimited query |
| `[C8]` price pressure index | **Fixed** | Counting bases aligned, window upper-bounded, signals and weights published |
| `[C9]` AMT join key | **Open — blocked** | Requires `li_item_id` to be populated (now fetched) and a domain answer on grain |
| `[F1]`–`[F4]` field mappings | **Open — blocked** | Await PBS domain review; the quality gate now reports them every ingest |
| `[F5]` HTML in text field | **Open** | Reported by the quality gate |
| `[F6]` formulary semantics | **Fixed** | Cost-effectiveness framing removed everywhere; labels describe brand count only and make no substitutability claim pending confirmation |
| `[F7]` originator inference | **Open** | `originator_brand_indicator` now ingested; endpoint still infers |
| `[F8]` fabricated brand data | **Fixed** | `brand_count` no longer coerced to 1; `brand_name_note` on all three brand-returning routes |
| `[D1]` empty serving schedule | **Fixed** | Quality gate blocks completion; `/health/data` exposes the condition |
| `[D2]`/`[D3]` empty tables | **Fixed at source** | Three fetches wired; re-ingest required to confirm |
| `[D4]` dead market tables | **Fixed** | Dropped in migration 009 |
| `[D5]` cache | **Fixed** | TTL enforced, LRU bound added; multi-worker invalidation still requires Redis |
| `[H1]`–`[H4]` disclosure | **Fixed** | `meta` blocks, interpretation notes, and safety caveats added; asserted by tests |

### Still requires a re-ingest

Every "empty table" finding is fixed **at the source**, but the fix cannot be confirmed until a
schedule is re-ingested — the local database still holds the pre-fix `2026-03` data. Run the ingest,
then re-run the verification queries in the appendix.

---

## 0.2 Second audit pass — 2026-07-29

A full re-audit was run over the remediated code, **including the remediation itself**. It produced
seven new findings, two corrections to round one, and two regressions introduced by the round-one fixes.

Companion document: **`JOIN_CONFIDENCE.md`** — per-join confidence ratings with the evidence behind
each, and the plan to raise the low ones.

### Corrections to the first audit

| # | Round-one claim | Correction |
|---|---|---|
| **X1** | *"Idempotency ✅ — tables lacking unique constraints (`markup_bands`, `item_pricing_events`, `summary_of_changes`, `changes`) are all loaded DELETE … WHERE schedule_id first."* | **Wrong for `changes`.** `insert_changes` has neither a DELETE nor an `ON CONFLICT`, and `changes` has no unique constraint. Verified: **87,983 rows collapse to 13,813 distinct — 6.37× duplication**, worst case 96 copies of one change. `/v1/changes` serves this table directly. It is the **only** non-idempotent loader of 35. |
| **X2** | *"What is well populated and worth reviewing now: … prescriber authorisations (10,324), criteria and parameters."* | **Misleading.** Those rows exist but are attached to a **different schedule** than the items — see N1. They are unusable, not usable. |

### New findings

#### N1 — The database holds a split-schedule state (root cause of several "empty" findings)

Two partial ingests wrote to two different schedule rows. Neither is complete:

| Schedule | Status | items | atc_codes | prescribers | pricing_events | criteria | markup_bands |
|---|---|---|---|---|---|---|---|
| `2026-03` | failed | **6,883** | **7,891** | 0 | 0 | 0 | 0 |
| `2026-04` | complete | **0** | 0 | **10,324** | **1,796** | **2,820** | **125** |

Every core table lives on `2026-03`; every migration-007 table lives on `2026-04`. Because joins are
correctly schedule-scoped, **nothing joins to anything across that boundary** — which is why
`item_prescribers → items` shows a 100% orphan rate (10,324 of 10,324) despite the PBS codes matching
perfectly on both sides.

This is a data-state artifact, not a query defect. It is also precisely the failure mode the
transactional loader and quality gate added in round one now prevent. It cannot be cleaned up by
query changes — it needs the re-ingest.

#### N2 — `item_pricing_events.pbs_code` is 100% NULL (1,796 of 1,796)

`ingest/normaliser.py:624` maps `"pbs_code": e.get("pbs_code")`. The PBS pricing-events feed evidently
does not supply that field under that name — the same problem `summary_of_changes` already works
around by deriving the code from `table_keys.li_item_id`.

Consequence: every pricing-event join matches on NULL. `/drugs/{code}/pricing-events`,
`/market/price-reduction-events`, and the `recent_pricing_events` block of `/drugs/{code}/formulary-status`
return nothing regardless of the 1,796 rows present. Round one saw the row count and assumed the
endpoint worked.

#### N3 — Recursive ATC CTEs have no cycle protection

All four recursive CTEs (`market.py`, `atc_codes.py` ×2, `drugs.py`) use `UNION ALL` with no `CYCLE`
clause and no depth limit. A cycle in `atc_codes.atc_parent_code` — or a self-referencing row — makes
the query run forever, holding a pool connection until the client disconnects.

Current data is clean (0 self-parents of 7,891), so this is latent rather than active. But the ATC tree
is ingested from an external feed with no structural validation, and the T4 endpoints have a
concurrency semaphore but **no statement timeout**. `UNION` instead of `UNION ALL` terminates on a
cycle; a `statement_timeout` bounds it regardless.

#### N4 — `item_pricing.pbs_code` is a string-split heuristic used as a join key

`ingest/normaliser.py:357`:
```python
pbs_code = p.get("pbs_code") or (p.get("li_item_id", "").split("_")[0] if p.get("li_item_id") else "")
```
Every pricing join in the API keys on this column, and it is **nullable**. A `li_item_id` whose format
differs from `PBSCODE_n` yields a wrong or empty `pbs_code`, and the rows silently vanish from an
INNER JOIN. `summary_of_changes.pbs_code` uses the same split.

This is a Tier C heuristic sitting underneath Tier A joins — the highest-leverage single point of
failure in the pricing chain, and currently unverifiable because `item_pricing` is empty.

#### N5 — Rate limiter is per-process and its monthly counter races

`api/middleware/rate_limit.py`:
- `_per_minute_store` is a module-level dict. With `WEB_CONCURRENCY=2` the effective burst limit is
  **2× the documented value**, and the dict grows per API key forever with no eviction.
- The monthly counter does read-then-write (`requests_this_month` is read in `require_api_key`, then
  `UPDATE ... + 1`). Concurrent requests both read *N* and both write *N+1*, so usage **under-counts**.
  That is a billing-integrity issue, not just a limit-accuracy one.

Fix for both: `UPDATE ... SET requests_this_month = requests_this_month + 1 RETURNING` for atomicity,
and Redis for the burst window.

#### N6 — `price-pressure-index` DPMQ ratio is still pricing-row weighted

Round one fixed the count bases but left
`AVG(ip.commonwealth_price / NULLIF(i.government_price, 0))` averaging over the `items ⋈ item_pricing`
join, so an item with four pricing rows contributes four times. The signal carrying 20% of the index
weight remains grain-inconsistent with the other two. Deferred alongside the open `[C2]` decision on
which price is canonical — the ratio's meaning is undefined until that is settled.

#### N7 — `benefit_type` appears in the differ's change-detection fields

`ingest/differ.py:META_FIELDS` includes `benefit_type`, compared as `str(old) != str(new)`. Migration 009
converted stored values from long form to PBS letters, so the first ingest after that migration would
have flagged a spurious `benefit_type_changed` for **every item** had the migration not backfilled
existing rows first. It did, so this is closed — recorded because the same hazard applies to any future
vocabulary change.

### Regressions introduced by the round-one fixes

Both found by this pass and fixed:

| # | Regression | Detail |
|---|---|---|
| **R1** | `/market/manufacturer-landscape` threshold/field mismatch | Round one moved `HAVING` onto `COUNT(DISTINCT ior.pbs_code)` but left the response field `item_count` bound to `COUNT(DISTINCT ip.li_item_id)`. `?min_item_count=10` could return rows showing `item_count: 0`. Now `item_count` reports PBS codes (matching what the threshold gates on) and brands are reported separately as `brand_count`. |
| **R2** | `/restrictions/{code}` unbounded array | Round one added `applies_to_pbs_codes` with no limit. For the measured worst-case restriction that is 2,124 codes in every response. Now capped at 200 with `applies_to_truncated`, and the true total comes from a `COUNT` rather than `len()`. |

A third was caught during the fix itself: a global string replace intended for
`manufacturer-landscape` also rewrote `item_count` in `formulary-landscape` and `authority-landscape`,
breaking both. Caught immediately by the existing test suite and reverted.

### Round-two status summary

| Finding | Severity | Status |
|---|---|---|
| X1 `changes` duplication (6.37×) | High | **Open** — one-line loader fix + dedupe migration |
| N1 split-schedule state | High | **Open** — resolved by re-ingest |
| N2 pricing-events `pbs_code` NULL | High | **Open** — needs the PBS field name |
| N3 recursive CTE cycles | Medium | **Open** — latent |
| N4 `pbs_code` split heuristic | Medium | **Open** — unverifiable until pricing loads |
| N5 rate limiter | Medium | **Open** — billing integrity |
| N6 DPMQ ratio weighting | Low | **Deferred** behind `[C2]` |
| N7 differ vocabulary hazard | Info | **Closed** |
| R1, R2 regressions | Medium | **Fixed** |

---

## 1. Blocking defects — endpoints that error or return nothing

### B1. `is_primary` column does not exist — `/market/atc-summary?primary_atc_only=true` returns 500

`api/routers/market.py:63` appends `AND iar.is_primary = TRUE` to the subtree join. No migration
ever adds `is_primary` to `item_atc_relationships`.

Verified:
```
\d item_atc_relationships
→ columns: id, schedule_id, pbs_code, atc_code, atc_priority_pct, created_at
```
Any request with `primary_atc_only=true` raises `UndefinedColumnError` → HTTP 500.
`docs/join_business_rules.md` §5.1 documents this filter as if it works.

**Fix:** either add the column and populate it at ingest (highest `atc_priority_pct` per `pbs_code`),
or rewrite the filter as a correlated subquery on `atc_priority_pct`.

---

### B2. Undefined table alias — `/market/schedule-comparison?atc_prefix=…` returns 500

`market.py:435-439` builds `atc_filter_base` referencing alias `i`:
```sql
AND EXISTS (SELECT 1 FROM item_atc_relationships iar
            WHERE iar.pbs_code = i.pbs_code AND iar.schedule_id = i.schedule_id …)
```
It is injected into the *added* and *removed* queries (which do alias `items AS i` — fine), but also
into the price-change query at `market.py:486`, whose only aliases are `b` and `t`. PostgreSQL raises
`missing FROM-clause entry for table "i"`.

The endpoint works without `atc_prefix` and fails with it — so it will pass a smoke test and fail in use.

**Fix:** parameterise the alias, or write a separate filter string for the price-change query using `b`.

---

### B3. `benefit_type` enum mismatch — authority metrics are silently always zero

The normaliser (`ingest/normaliser.py:195-196`) maps raw PBS codes through `BENEFIT_TYPE_MAP` into
long-form values. The API matches on single letters.

Verified:
```
SELECT benefit_type, COUNT(*) FROM items GROUP BY 1;
 authority_required    | 2170
 authority_streamlined | 2090
 restricted            | 1460
 unrestricted          | 1163
```

Consequences — every one of these silently returns 0 or falls through, with no error:

| Location | Code | Effect |
|---|---|---|
| `market.py:78` | `COUNT(*) FILTER (WHERE i.benefit_type = 'A')` | `authority_required_count` always **0** |
| `market.py:79` | `FILTER (WHERE i.benefit_type = 'S')` | `streamlined_count` always **0** |
| `market.py:344` | `FILTER (WHERE i.benefit_type = 'A')` | `authority_count` always **0** |
| `drugs.py:75` | `i.benefit_type = $n` from `?benefit_type=A` | `/drugs/search` returns **nothing** for documented values |
| 6 sites | `benefit_labels = {"U":…,"R":…,"A":…,"S":…}` | `benefit_type_label` emits the raw value, not the label |

This is the single highest-value fix in the audit: one decision (canonicalise on letters or on long
form) repairs five endpoints.

---

### B4. `section` matched exactly against `'items'` — new-listings and delistings always empty

`ingest/normaliser.py:548` sets `section` from the PBS `changed_endpoint` field. Migration 006's own
comment confirms the shape:
> `-- summary_of_changes: section holds endpoint names (e.g. "/item-restriction-relationships" = 31 chars)`

These values carry a leading slash. Two consequences:

- `schedule_changes.py:263` and `:344` filter `AND sc.section = 'items'`. `'/items' ≠ 'items'` →
  **`/schedule-changes/{code}/new-listings` and `/delistings` return zero rows for every schedule.**
- `_classify_change` (`schedule_changes.py:37`) tests `if sec == "items"`, so `NEW_LISTING`,
  `DELISTING` and `FORMULARY_CHANGE` are **never emitted**. All item changes fall through to
  `OTHER_MODIFICATION / LOW`. A delisting — documented as severity `HIGH` — is reported as `LOW`.

The substring tests in the same function (`"pricing-event" in sec`, `"restriction" in sec`) are
unaffected, which is why this was not caught.

**Note:** `summary_of_changes` is currently empty (0 rows), so this cannot be confirmed against live
data. Verify with `SELECT DISTINCT section FROM summary_of_changes;` after the next successful ingest.

**Fix:** normalise `section` at ingest (strip leading `/`), or change both comparisons to
`sec.strip('/') == 'items'`.

---

### B5. Delisting enrichment looks up removed items in the schedule they were removed from

`/schedule-changes/{code}/delistings` resolves both the drug name and the therapeutic alternatives
against the **current** schedule:

- `_fetch_drug_names` (`schedule_changes.py:71`): `WHERE i.pbs_code = ANY($1) AND i.schedule_id = $2`
- ATC lookup (`schedule_changes.py:364`): `WHERE iar.pbs_code = ANY($1) AND iar.schedule_id = $2`

A delisted item is by definition absent from that schedule. Both return nothing, so:
- `drug_name` is **always `null`** for every delisting
- `pbs_to_atc_l4` is **always empty**, so `therapeutic_alternatives` is **always `[]`**

`OFFERING.md` sells this endpoint on exactly the feature that cannot fire:
> *"surface therapeutic alternatives for delistings"*

**Fix:** resolve delisted items against the **previous** completed schedule.

---

### B6. "Primary ATC" selection returns the *lowest* priority code

`schedule_changes.py:288`:
```python
atc_rows = await db.fetch("… ORDER BY iar.atc_priority_pct DESC NULLS LAST", …)
primary_atc = {r["pbs_code"]: r["atc_code"] for r in atc_rows}
```
The query orders correctly, but a dict comprehension keeps the **last** value written per key. For
split-ATC items the last row is the *lowest*-priority ATC. So `primary_atc_code` is inverted, and
`is_first_in_atc_class` is computed against the wrong class.

32 items in the current schedule have split ATC — a small but non-zero blast radius that grows with
data volume.

**Fix:** build the dict from the first occurrence only (`if pc not in primary_atc`), or `DISTINCT ON`.

---

## 2. Correctness defects

### C1. Aggregate counts measure join fan-out, not entities — **measured 3.12× inflation**

The T4 aggregates join `items` to `item_pricing` (one row per `li_item_id` × `dispensing_rule_mnem`)
and to `item_organisation_relationships` (one row per sponsor), producing a cartesian product, then
count with `COUNT(*) FILTER (…)` instead of `COUNT(DISTINCT i.pbs_code) FILTER (…)`.

Verified on the 50 items that currently have more than one sponsor:
```
true_item_count | rows_counted_by_COUNT_STAR | inflation_factor
             50 |                        156 |             3.12
```

Affected fields — all inflated, all sitting next to correctly-computed `COUNT(DISTINCT …)` fields in
the same response, so the numbers are internally contradictory (`f1_count + f2_count` can exceed
`unique_prescribing_rules`):

| Endpoint | Inflated fields |
|---|---|
| `/market/atc-summary` | `authority_required_count`, `streamlined_count`, `f1_count`, `f2_count`, `sixty_day_items`, `biosimilar_items`; `mean_dpmq` and `median_dpmq` are sponsor-weighted |
| `/market/manufacturer-landscape` | `f1_count`, `f2_count`, `biosimilar_count`, `authority_count` |
| `/market/price-pressure-index` | `total_f2` (reported to the user as `total_f2_items`) |
| `/market/formulary-landscape` | `mean_dpmq` (pricing-row-weighted; min/max unaffected) |
| `/market/biosimilar-landscape` | `biosimilar_mean_dpmq`, `originator_mean_dpmq` |

Currently masked because `item_pricing` is empty and sponsor coverage is only 3%. **It will surface
the moment pricing data lands.**

**Fix:** `COUNT(DISTINCT i.pbs_code) FILTER (…)` throughout; compute price statistics in a separate
CTE aggregated to item level before joining.

---

### C2. Two competing price sources with no canonical rule *(open item P2)*

| Source | Semantics | Consumed by |
|---|---|---|
| `items.government_price` / `items.general_charge` | one snapshot value per item per schedule | `price-history`, `safety-net`, `60-day-pair`, `market/safety-net-burden`, `market/schedule-comparison`, `schedule-changes/price-changes` |
| `item_pricing.commonwealth_price` | varies per dispensing rule **and** per brand | `items/{code}/price`, `patient-cost`, `dispensing-context`, all T4 DPMQ statistics |

These are different quantities at different grain. Nothing decides which is canonical for a
patient-facing cost. Two concrete symptoms today:

- `/items/{code}/patient-cost` uses `item_pricing`, falling back to `items.government_price`
  (`items.py:341`) — so a single endpoint silently switches price basis depending on data presence,
  with no indication in the response.
- `/market/price-pressure-index` computes `AVG(ip.commonwealth_price / NULLIF(i.government_price,0))`
  — it *divides the two sources by each other* and treats the ratio as a pressure signal. If they
  represent the same quantity the ratio is ~1 and carries no information; if they differ, the
  semantics of the ratio are undefined. Either way the 20% weight it carries is not defensible.

**Fix:** pick one as canonical for patient cost, document the other as a snapshot, and make every
endpoint state which basis it used in `meta`.

---

### C3. `medicines` is not schedule-versioned, and the upsert rewrites history

`medicines` has **no `schedule_id`** (verified: 0 columns matching `%schedule%`). It is keyed
`UNIQUE(ingredient_lower)` and upserted destructively (`ingest/loader.py:12-19`):
```sql
ON CONFLICT (ingredient_lower) DO UPDATE
  SET atc_code = EXCLUDED.atc_code, therapeutic_group = …, therapeutic_subgroup = …
```
Every ingest overwrites `atc_code`, `therapeutic_group` and `therapeutic_subgroup` **globally, for
every schedule, retroactively**.

So `GET /drugs/search?schedule=2025-01` returns the ATC code as of the *most recent* ingest, not as of
January 2025. This contradicts the product's core claim in `OFFERING.md`:
> *"Every schedule stored independently; any endpoint queryable against any historical schedule"*

Affected: `/drugs/search` (`medicine_atc_code`), `/drugs/{code}` and `/full-profile` (ATC fallback),
`/items/{code}` (`m.atc_code`), `/market/biosimilar-landscape` (`GROUP BY m.ingredient, m.atc_code`).

**Fix:** either version `medicines` per schedule, or stop serving medicine-level ATC/therapeutic
fields from historical queries and source ATC only from `item_atc_relationships` (which *is* versioned).

---

### C4. `restriction_code` is not unique — `/restrictions/{code}` returns one arbitrary row of up to 2,124

Verified:
```
distinct_codes | total_rows | max_items_sharing_one_code
          3536 |      22769 |                       2124
```

`restrictions.py:130` uses `fetchrow` with **no `ORDER BY`**:
```sql
SELECT r.*, i.pbs_code FROM restrictions r JOIN items i ON i.id = r.item_id
WHERE r.restriction_code = $1 AND i.schedule_id = $2
```
For a code shared by 2,124 items this returns a non-deterministic single row and reports **that item's
`pbs_code`** as though it were *the* item for the restriction. Two identical requests can return
different `pbs_code` values.

**Fix:** either return the restriction with the full list of affected `pbs_code`s, or add a
deterministic `ORDER BY` and label `pbs_code` explicitly as "one example item".

---

### C5. Falsy-zero bug collapses a legitimate $0 patient charge to full price

`items.py:244` and `items.py:350`:
```python
gen_patient = round(min(dpmq, max_charge or dpmq), 2)
```
When `max_general_patient_charge` is `0` — a legitimate value (e.g. CTG-eligible or fully subsidised
items) — Python's `or` treats it as falsy and substitutes `dpmq`. The patient charge is reported as
the **full dispensed price** instead of **zero**, and `government_pays` becomes 0 instead of the full
subsidy.

**Fix:** `max_charge if max_charge is not None else dpmq`.

---

### C6. The same quantity is computed two different ways in two endpoints

For the concessional patient charge:

- `/items/{code}/price` (`items.py:261`): `"concessional_patient_charge": con_cp` — the raw schedule
  copayment, uncapped by the dispensed price.
- `/items/{code}/patient-cost` (`items.py:351`): `you_pay_con = min(dpmq, con_cp)` — correctly capped.

For an item whose DPMQ is below the concessional copayment, `/price` overstates what the patient pays.
`/price` also omits `government_pays` for the concessional case entirely.

**Fix:** extract one shared patient-charge function and call it from both.

---

### C7. `/market/safety-net-burden` reports the mean of the top 500, described as the mean of all items

`market.py:889-921`: the SQL applies `ORDER BY scripts_to_safety_net ASC NULLS LAST LIMIT 500`, and
the `min`/`max`/`mean` are then computed **in Python over that truncated list**. The endpoint
description claims:
> *"Returns min/max/mean scripts-to-safety-net across all items"*

Because the sort is ascending, the 500 retained rows are the *lowest*-script items. The reported
`mean` is therefore systematically biased downward and the reported `max` is meaningless.

**Fix:** compute the aggregate statistics in a separate un-limited query, or drop the claim.

---

### C8. `/market/price-pressure-index` — three independent defects in one composite score

1. **Mismatched numerator/denominator.** `reduction_signal = recent_reductions / max(total_f2, 1)`
   where `recent_reductions` is `COUNT(DISTINCT pe.pbs_code)` but `total_f2` is a fan-out-inflated
   `COUNT(*)` (see C1). The ratio mixes two different counting bases.
2. **No upper time bound.** `market.py:1094` filters `s.month >= (target − 3 months)` with **no
   upper bound**. Querying a historical schedule counts price reductions from schedules *after* it —
   temporal leakage that makes historical index values non-reproducible.
3. **Undocumented weights.** The 50/30/20 split appears nowhere outside the code. It is a Tier C
   opinion presented as an index without a published methodology.

---

### C9. AMT relationships keyed on `pbs_code`, PBS keys them on `li_item_id` *(open item P1)*

`item_amt_relationships` is `UNIQUE(pbs_code, amt_id, schedule_id)`; PBS `ITEM_AMT_T` uses
`li_item_id` (brand/TPP level). `/drugs/{code}/amt` and `/full-profile` therefore aggregate AMT
concepts at drug level when PBS models them at brand level.

This is the one defect already tracked in `docs/pbs-t3-t4-audit-approach.md`. Note that `items.li_item_id`
is **100% NULL** in the current data, so the corrected join has nothing to key on yet — the ingest fix
must land first.

---

## 3. Fabricated, aliased, or mislabelled fields

### F1. `streamlined_code` is a copy of `restriction_code` — 100% of rows

`ingest/normaliser.py:239`: `"streamlined_code": res_code,  # backward compat alias`

Verified:
```
identical | differing | total
    22769 |         0 | 22769
```

A PBS streamlined authority code is a distinct short numeric code a prescriber writes on the script.
This field is not that. Yet it is exposed as `streamlined_code` on `/restrictions`,
`/restrictions/{code}`, `/drugs/{code}/restrictions` and `/drugs/{code}/authority-workflow`, and is
offered as a **query filter** (`/restrictions?streamlined_code=`) that in reality filters on
restriction code.

**Fix:** remove the field until the real PBS value is ingested. Do not ship a filter on it.

---

### F2. `authority_required` is derived from the *complex* authority indicator

`ingest/normaliser.py:249`:
```python
"authority_required": yn(r.get("complex_authority_rqrd_ind")) or bool(r.get("authority_required", False)),
```
Complex authority is a **subset** of authority. An item requiring ordinary telephone or written
authority — but not complex authority — is recorded as `authority_required = False` unless PBS
happens to supply a separate field.

Verified: 2,950 of 22,769 restrictions (13%) are flagged. Against 2,170 `authority_required` +
2,090 `authority_streamlined` items, this is not a credible authority population.

This flows into `/drugs/{code}/prescribers.requires_authority`,
`/drugs/{code}/authority-workflow.requires_any_authority`, and the authority-workflow checklist
branch. **This is the most clinically material derivation error in the audit.**

---

### F3. `authority_method` is NULL in 100% of rows — the authority workflow has no data

Verified: `COUNT(*) FILTER (WHERE authority_method IS NOT NULL) = 0` of 22,769.

Consequences:
- `/drugs/{code}/authority-workflow`: the branch `if r["authority_method"] == "S"` never fires, so
  **every** authority restriction receives the generic "Obtain authority approval before prescribing"
  checklist. `authority_method_label` is always `null`.
- `/market/authority-landscape.authority_method_distribution` filters
  `WHERE authority_method IS NOT NULL` → **always an empty array**. One third of that endpoint returns
  nothing.

`OFFERING.md` sells `/authority-workflow` as the flagship T3 example.

---

### F4. `indication` is NULL in 100% of rows, but is documented as populated

Verified: `COUNT(*) FILTER (WHERE indication IS NOT NULL) = 0` of 22,769.
`ingest/normaliser.py:246` marks it `# legacy compat`.

`TIERS.md` tells Base subscribers they receive "restriction summary (indication, prescriber type,
authority flag)"; `docs/join_business_rules.md` §1.1 says the same. The field is always null.

---

### F5. `restriction_text` contains raw HTML in 45% of rows

`ingest/normaliser.py:245`: `"restriction_text": r.get("li_html_text") or r.get("restriction_text")`

Verified: 10,141 of 22,769 rows contain `<`. A field named `restriction_text`, served alongside a
separate `li_html_text` field, delivers markup to callers who will render it as plain text.

---

### F6. F1/F2 formulary semantics are stated backwards, and the labels describe the wrong concept

Two different incorrect descriptions coexist:

1. **Backwards.** `drugs.py:317`, `market.py:295`, `OFFERING.md`, `TIERS.md` all say
   *"F1 (brand-substitutable), F2 (not substitutable)"*. In the PBS, **F1 is the single-brand
   formulary** and **F2 holds multiple-brand medicines subject to price disclosure** — substitution
   applies to F2. The statement is inverted.
2. **Wrong concept.** `drugs.py:1466-1470` labels them as a cost-effectiveness ranking:
   ```python
   "F1": "Formulary 1 — highest cost-effectiveness",
   "F2": "Formulary 2 — medium cost-effectiveness",
   "F3": "Formulary 3 — lowest cost-effectiveness / price disclosure items",
   ```
   F1/F2 is not a cost-effectiveness ranking, and F3 is not a current PBS formulary.

Additionally the data contains a fourth value, `CDL` (208 items), which `formulary_labels` does not
handle → rendered as "Unknown formulary".

This is customer-facing documentation and a T3 response field. A PBS domain expert will flag it on
first read.

---

### F7. "Originator" is inferred as "not a biosimilar" while a real field sits unused

`/market/biosimilar-landscape` defines originator as `FILTER (WHERE i.biosimilar = FALSE)`. That
bucket contains every non-biosimilar item sharing the ingredient — generics included — not the
originator brand.

`items.originator_brand_indicator` and `items.innovator_indicator` **are ingested**
(`normaliser.py:289-290`) and **never read by any endpoint**.

Also note `biosimilar = TRUE` for **0 of 6,883** items, so the endpoint's
`HAVING … biosimilar_count > 0` currently returns an empty array unconditionally.

---

### F8. Brand-level responses fabricate a count and repeat one name across all brands

- `drugs.py:209`: `brand_count = … or 1` — when `item_pricing` is empty (i.e. always, currently) the
  response asserts `brand_count: 1` for an item with no known brands.
- `drugs.py:230`, `drugs.py:720`: every entry in the `brands` array is given
  `"brand_name": item["brand_name"]` — the item-level name. An item with five `li_item_id`s returns
  five "brands" with identical names.

`/drugs/{code}/brands` discloses this via `brand_name_note` (good). **`/drugs/{code}?include_brands=true`
and `/drugs/{code}/full-profile` return the same fabricated structure with no note.**

---

## 4. Data population and operational gaps

### D1. The only `complete` schedule is empty — the API serves nothing by default

```
  month  | ingest_status | items
 2026-03 | failed        |  6883
 2026-04 | complete      |     0
```
Every endpoint resolves its default schedule with
`WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1` → `2026-04` → zero rows. Without an
explicit `?schedule=2026-03`, the API returns 404/empty across the board. And `2026-03` is `failed`,
so it is excluded from `/schedules`, from price-history, and from the previous-schedule lookups in
`/schedule-changes/price-changes`.

### D2. Core tables are empty

| Table | Rows | Endpoints returning nothing as a result |
|---|---|---|
| `item_pricing` | **0** | `/items/{c}/price`, `/patient-cost`, `/dispensing-context`, `/drugs/{c}/brands`, all T4 DPMQ statistics |
| `summary_of_changes` | **0** | all 6 `/schedule-changes/*`, `/market/listings-pipeline` |
| `amt_items`, `item_amt_relationships` | **0** | `/drugs/{c}/amt`, `/amt` |
| `prescribing_texts`, `restriction_prescribing_text_relationships` | **0** | `/restrictions/{c}` T2 enrichment, `/drugs/{c}/restriction-full` components, `/items/{c}/prescribing-texts` |
| `fees`, `indications` | **0** | `/fees`, `/indications` |
| `item_restriction_relationships` | **0** | *(ingested but never read by any router — see §5)* |

### D3. Price and brand-key columns are 100% NULL

Of 6,883 items: `government_price` 0 set, `general_charge` 0 set, `li_item_id` 0 set.
Combined with D2, **there is no price data anywhere in the system.** Every pricing, safety-net,
patient-cost and market-pricing endpoint returns nulls.

### D4. Migration 008's pre-computed market tables are dead code

`market_atc_summary` and `market_manufacturer_landscape` are created by migration 008, are **never
written by the ingest**, and are **never read by any router**. `OFFERING.md` claims T4 metrics are
*"Pre-computed at ingest time where possible"* — they are not; every T4 request runs the full
aggregation live.

### D5. Response cache is per-process, unbounded, and has no TTL

`api/cache.py` stores entries in a module-level dict. `cache_get` reads the stored timestamp and
discards it — there is no expiry, despite `CACHE_TTL_SCHEDULE_SECONDS` / `CACHE_TTL_META_SECONDS`
being documented in `README.md` (dead config). Entries are only ever evicted by
`cache_invalidate_schedule`.

Deployment runs `gunicorn -w ${WEB_CONCURRENCY:-2}` (`scripts/start.sh`), so invalidation reaches
only the worker that handled the ingest callback. Because cache keys embed `schedule_id`, a *new*
schedule is unaffected — but a **re-ingest of a corrected schedule** (PBS does republish) keeps the
same `schedule_id`, so other workers serve the superseded data indefinitely. Memory growth is unbounded.

---

## 5. Undisclosed heuristics — Tier C logic not labelled as required

`docs/pbs-endpoint-audit-spec.md` §0.1 requires Tier C logic to be "clearly labelled" and never
described as PBS-official. Outstanding:

| # | Location | Issue |
|---|---|---|
| H1 | `/items/{code}/patient-cost` | Returns a bare `{"data": …}` with **no `meta` block at all** — unlike every sibling endpoint. The representative-price selection (`ORDER BY commonwealth_price DESC LIMIT 1`) and the scripts-to-safety-net estimate are both Tier C and both undisclosed. Spec §1.3 explicitly requires this labelling. |
| H2 | `/drugs/{code}/authority-workflow` | The generated checklist is an internal interpretation. Spec §3.4: *"Ensure documentation clearly states workflow is an internal interpretation."* No note field is present. |
| H3 | `/drugs/{code}/substitution`, `/60-day-pair` | Match on ingredient only — **form, strength and pack size are ignored**. A 500 mg immediate-release tablet will be listed as a substitute for a 1 g modified-release tablet. The note says "same-ingredient matches only" but does not warn that strength and form differ. For a tier sold into clinical decision support this caveat must be explicit. |
| H4 | `/items/{code}`, `/drugs/{code}`, `_gather_drug_data` | `item_organisation_relationships … LIMIT 1` with **no `ORDER BY`** — the "primary manufacturer" is non-deterministic across identical requests. 50 items currently have >1 sponsor. The accompanying note discloses that it is one of several, but not that the choice is arbitrary. |

**Also noted:** `item_restriction_relationships` is the PBS-documented Tier A relationship
(`pbs_code + restriction_code + schedule_id`) and is populated by the ingest, but **no router reads
it**. The API instead uses the denormalised `restrictions.item_id` FK. That FK *is* derived from the
PBS relationship in the normaliser, so this is acceptable under spec §0.2 — but it means the
authoritative table is never used as a cross-check. `container_organisation_relationships` is
likewise ingested and never read.

---

## 6. What is correct

Recording this explicitly, because the audit above is unbalanced by design:

- **Schedule scoping is genuinely good.** Every cross-table join in the API carries a matching
  `schedule_id` on both sides. I found **no Tier A scoping violations**. The one table without a
  `schedule_id` column (`restrictions`) is scoped transitively through `items.schedule_id`, correctly,
  everywhere it is used.
- **Join keys match the PBS data dictionary** for items↔ATC, items↔programs, items↔organisations,
  items↔prescribers, items↔dispensing-rules, restrictions↔prescribing-texts. The single key
  divergence is AMT (C9), already tracked.
- **Coverage is honest** on the endpoints that claim completeness: `/items/{c}/prescribing-texts`,
  `/items/{c}/dispensing-rules`, `/drugs/{c}/restrictions`, `/drugs/{c}/atc` and
  `/drugs/{c}/restriction-full` apply no hidden `LIMIT`.
- **Idempotent re-ingest.** Tables lacking unique constraints (`markup_bands`, `item_pricing_events`,
  `summary_of_changes`, `changes`) are all loaded `DELETE … WHERE schedule_id` first, inside a
  transaction. Re-ingest will not duplicate.
- **Zero-division is guarded** everywhere it matters — `scripts_to_safety_net` (both variants),
  `price_delta_pct`, `NULLIF` on price ratios.
- **The T1/T2 audit fixes landed properly.** `/drugs/{code}/prescribers` now uses a deterministic
  `ORDER BY authority_required DESC, written_authority_required DESC` with an `authority_note`
  explaining the selection — exactly what spec §2.3 asked for. Manufacturer notes and brand-name notes
  are present where that pass touched.

---

## 7. Suggested remediation order

**Before any customer sees T3/T4:**
1. B3 (benefit_type enum) — one decision, repairs five endpoints
2. B1, B2 — two runtime 500s, both one-line fixes
3. C1 (fan-out) — mechanical `COUNT(DISTINCT …)` change across 5 queries
4. F2, F3 (authority derivation and null `authority_method`) — needs a PBS field-mapping review
5. D1 (re-run the March ingest to `complete`) — nothing else can be validated until this is done

**Before the join documentation is published as accurate:**
6. F6 (formulary semantics) — correct in code, `OFFERING.md`, `TIERS.md`, `join_business_rules.md`
7. F1, F4, F5 (streamlined_code, indication, HTML leakage) — remove or fix the fields
8. B4, B5, B6 (schedule-changes) — the whole sub-router
9. H1–H4 — add the Tier C disclosures the spec already mandates

**Requires a product decision, not just a fix:**
10. C2 (canonical price source) — the open P2 question
11. C3 (medicines versioning) — affects the historical-accuracy claim in `OFFERING.md`
12. C9 (AMT key) — the open P1 question; blocked on `li_item_id` being populated

---

## Appendix — verification queries

```sql
-- B1
\d item_atc_relationships

-- B3
SELECT benefit_type, COUNT(*) FROM items GROUP BY 1 ORDER BY 2 DESC;

-- B4 (run after next successful ingest)
SELECT DISTINCT section FROM summary_of_changes ORDER BY 1;

-- C1
SELECT COUNT(DISTINCT i.pbs_code) AS true_count, COUNT(*) AS counted,
       ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT i.pbs_code),0), 2) AS inflation
FROM items i
JOIN item_organisation_relationships ior
  ON ior.pbs_code = i.pbs_code AND ior.schedule_id = i.schedule_id;

-- C4
SELECT COUNT(*) AS distinct_codes, SUM(n) AS rows, MAX(n) AS worst
FROM (SELECT restriction_code, COUNT(*) n FROM restrictions GROUP BY 1) x;

-- F1 / F2 / F3 / F4 / F5
SELECT COUNT(*) FILTER (WHERE streamlined_code = restriction_code) AS aliased,
       COUNT(*) FILTER (WHERE authority_required)                  AS auth_true,
       COUNT(*) FILTER (WHERE authority_method IS NOT NULL)        AS method_set,
       COUNT(*) FILTER (WHERE indication IS NOT NULL)              AS indication_set,
       COUNT(*) FILTER (WHERE restriction_text LIKE '%<%')         AS html_leak,
       COUNT(*)                                                    AS total
FROM restrictions;

-- D2 / D3
SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;
SELECT COUNT(government_price), COUNT(general_charge), COUNT(li_item_id), COUNT(*) FROM items;
```

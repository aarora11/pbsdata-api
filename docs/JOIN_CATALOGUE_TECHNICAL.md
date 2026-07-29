# PBSdata.io — Technical Join Catalogue

**Purpose:** A complete, verifiable inventory of every cross-table join in the API, its keys, its
risk tier, and its known defects. Written for independent technical review against the PBS v3 Data
Model / Data Dictionary.

**Date:** 2026-07-28 · **Source:** `api/routers/*.py`, `ingest/`, `migrations/`
**Companion documents:** `AUDIT_FINDINGS.md` (defects), `JOIN_CATALOGUE_PLAIN_ENGLISH.md` (non-technical), `JOIN_CONFIDENCE.md` (per-join confidence ratings and the uplift plan)

---

## How to read this document

### Risk tiers (from `docs/pbs-endpoint-audit-spec.md`)

| Tier | Meaning | Review standard |
|---|---|---|
| **A** | Mechanical join on a relationship PBS documents explicitly | Keys must match the PBS data dictionary exactly. Deviations are **errors**. |
| **B** | Derivation computed only from PBS fields, reversible back to them | Must be reversible or documented. Inconsistency is a **high-risk defect**. |
| **C** | Opinionated heuristic — representative selection, composite score, estimate | Must never be described as PBS-official, and must be labelled in the response. |

### Defect markers

`⛔ BLOCKING` — endpoint errors or returns nothing · `⚠️ DEFECT` — returns wrong values
`🏷️ LABELLING` — Tier C logic not disclosed · `✅` — no finding

Cross-references such as `[B3]` point to numbered findings in `AUDIT_FINDINGS.md`.

> ### Remediation status — 2026-07-28
>
> **The defect markers below describe the state at the time of the audit and are kept
> as the record of what was found.** Most have since been fixed —
> see `AUDIT_FINDINGS.md` §0.1 for the per-finding status table, and
> `tests/test_audit_regressions.py` for the tests that pin each fix.
>
> Still open at the time of writing: `[C2]` (canonical price source), `[C3]`
> (`medicines` versioning), `[C9]` (AMT grain), `[F1]`–`[F5]` and `[F7]` — all
> blocked on the PBS domain review in `FIX_PLAN.md` Phase 4.
>
> One correction to the audit itself: the empty tables in §4 were **not** primarily a
> field-mapping problem. `ingest/runner.py` never called
> `get_all_item_dispensing_rule_relationships`, `get_all_item_amt` or
> `get_all_item_overviews`, so those feeds were never fetched at all.

---

## Part 1 — Schema reference

### 1.1 Grain of each table

Understanding grain is prerequisite to reviewing the aggregate joins.

| Table | Grain (one row per…) | Unique constraint |
|---|---|---|
| `schedules` | schedule month | `month` |
| `medicines` | **active ingredient, globally — NOT per schedule** | `ingredient_lower` |
| `items` | PBS item (prescribing rule) per schedule | `(pbs_code, schedule_id)` |
| `restrictions` | restriction **per item** (denormalised copy) | *none* |
| `item_pricing` | brand × dispensing rule per schedule | `(li_item_id, dispensing_rule_mnem, schedule_id)` |
| `item_atc_relationships` | item × ATC code per schedule | `(pbs_code, atc_code, schedule_id)` |
| `item_organisation_relationships` | item × sponsor per schedule | `(pbs_code, organisation_id, schedule_id)` |
| `item_prescribers` | item × prescriber code per schedule | `(pbs_code, prescriber_code, schedule_id)` |
| `item_amt_relationships` | item × AMT concept per schedule | `(pbs_code, amt_id, schedule_id)` |
| `item_restriction_relationships` | item × restriction code per schedule | `(pbs_code, restriction_code, schedule_id)` |
| `item_dispensing_rules` | item × rule code per schedule | `(pbs_code, rule_code, schedule_id)` |
| `restriction_prescribing_text_relationships` | restriction × text per schedule | `(restriction_code, prescribing_text_id, schedule_id)` |
| `item_prescribing_text_relationships` | item × text per schedule | `(pbs_code, prescribing_text_id, schedule_id)` |
| `organisations` | sponsor per schedule | `(organisation_id, schedule_id)` |
| `programs` | program per schedule | `(program_code, schedule_id)` |
| `atc_codes` | ATC node per schedule | `(atc_code, schedule_id)` |
| `copayments` | **exactly one row per schedule** | `schedule_id` |
| `markup_bands` | band × program × rule per schedule | *none* |
| `item_pricing_events` | pricing event per schedule | *none* |
| `summary_of_changes` | change record per schedule | *none* |

**Two grain facts drive most of the defects in this catalogue:**

1. `medicines` has **no `schedule_id`**. It is the only non-versioned dimension in the model.
   → see `[C3]`.
2. `restrictions` has **no `schedule_id`** and **no unique key**. It holds one denormalised copy per
   item, so a single `restriction_code` appears up to **2,124 times**. Scoping is transitive through
   `items.schedule_id`, which every query does correctly — but any `fetchrow` on `restriction_code`
   alone is non-deterministic. → see `[C4]`.

### 1.2 Key-conformance summary vs PBS v3

| Relationship | PBS documented key | Implemented key | Verdict |
|---|---|---|---|
| Items ↔ Medicines | *(local)* | `medicines.id = items.medicine_id` | ✅ local FK, acceptable |
| Items ↔ ATC | `pbs_code + schedule_id` | `pbs_code + schedule_id` | ✅ conforms |
| Items ↔ Programs | `program_code + schedule_id` | `program_code + schedule_id` | ✅ conforms |
| Items ↔ Organisations | `pbs_code + schedule_id` | `pbs_code + schedule_id` | ✅ conforms |
| Items ↔ Prescribers | `pbs_code + schedule_id` | `pbs_code + schedule_id` | ✅ conforms |
| Items ↔ Restrictions | `pbs_code + res_code + schedule_id` | `restrictions.item_id = items.id` | ✅ derived FK, permitted by spec §0.2 |
| Items ↔ Pricing | `pbs_code + schedule_id` | `pbs_code + schedule_id` | ✅ conforms |
| Items ↔ Dispensing rules | `rule_code + schedule_id` | `rule_code + schedule_id` | ✅ conforms |
| Restrictions ↔ Prescribing texts | `restriction_code + schedule_id` | `restriction_code + schedule_id` | ✅ conforms |
| Markup bands | `schedule + program + rule mnem` | `schedule + program + rule mnem` | ✅ conforms |
| **Items ↔ AMT** | **`li_item_id + schedule_id`** | **`pbs_code + schedule_id`** | **⚠️ Tier A violation `[C9]`** |

**Schedule scoping: no violations found.** Every cross-table join in the API carries a matching
`schedule_id` on both sides. This is the strongest part of the implementation.

---

## Part 2 — Joins by endpoint

### 2.1 `GET /items/{pbs_code}` — Base raw / T2 enriched

| # | From → To | Condition | Tier | Notes |
|---|---|---|---|---|
| 1 | `items` → `medicines` | `m.id = i.medicine_id` | A | Ingredient + medicine-level ATC. ATC is **not schedule-versioned** `[C3]` |
| 2 | `items` → `restrictions` | `r.item_id = i.id` | A | Derived FK. No LIMIT ✅ |
| 3 | `items` → `item_organisation_relationships` | `ior.pbs_code = i.pbs_code AND ior.schedule_id = i.schedule_id` | A | T2+ only |
| 4 | `ior` → `organisations` | `o.organisation_id = ior.organisation_id AND o.schedule_id = ior.schedule_id` | **C** | `LIMIT 1` **without `ORDER BY`** → non-deterministic 🏷️ `[H4]` |
| 5 | `items` → `programs` | `program_code + schedule_id` | A | ✅ |
| 6 | `items` → `item_atc_relationships` | `pbs_code + schedule_id` | **C** | `ORDER BY atc_priority_pct DESC NULLS LAST LIMIT 1` — representative selection, ties arbitrary |
| 7 | `iar` → `atc_codes` | `atc_code + schedule_id` | A | ✅ |
| 8 | `items` → `item_prescribers` | `pbs_code + schedule_id` | A | No LIMIT ✅ |

**Derived:** none beyond selection. **Disclosure:** `primary_manufacturer_note` present ✅;
`primary_atc` selection not labelled as representative 🏷️.

---

### 2.2 `GET /items/{pbs_code}/price` — T2

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` → `medicines` | `m.id = i.medicine_id` | A |
| 2 | `items` → `item_pricing` | `pbs_code + schedule_id`, `ORDER BY li_item_id, dispensing_rule_mnem` | A — no LIMIT ✅ |
| 3 | *(schedule)* → `copayments` | `schedule_id` | A |

**Derived fields:**

| Field | Formula | Tier | Finding |
|---|---|---|---|
| `general_patient_charge` | `MIN(commonwealth_price, max_general_patient_charge)` | B | ⚠️ `max_charge or dpmq` collapses a legitimate `0` to full price `[C5]` |
| `government_pays` | `commonwealth_price − general_patient_charge` | B | Reversible ✅ (given C5 fixed) |
| `concessional_patient_charge` | `copayments.concessional` **raw, uncapped** | B | ⚠️ Inconsistent with `/patient-cost`, which caps it `[C6]` |
| `total_fees` | *(not computed here)* | — | — |

---

### 2.3 `GET /items/{pbs_code}/patient-cost` — T2

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` → `medicines` | `m.id = i.medicine_id` | A |
| 2 | `items` → `item_pricing` | `pbs_code + schedule_id ORDER BY commonwealth_price DESC NULLS LAST **LIMIT 1**` | **C** — highest-price row chosen as representative |
| 3 | *(schedule)* → `copayments` | `schedule_id` | A |

**Derived fields:**

| Field | Formula | Tier | Finding |
|---|---|---|---|
| `you_pay` (general) | `MIN(dpmq, max_general_patient_charge)` | B | ⚠️ falsy-zero `[C5]` |
| `you_pay` (concessional) | `MIN(dpmq, concessional_copay)` | B | ✅ |
| `government_pays` | `dpmq − you_pay` | B | Returns `null` rather than `0` when `dpmq == 0` |
| `estimated_scripts_to_safety_net` | `ROUND(safety_net / you_pay)` | **C** | Zero-guarded ✅ |
| price basis fallback | `item_pricing.commonwealth_price` **else** `items.government_price` | **C** | ⚠️ Silently switches source `[C2]` |

🏷️ **`[H1]` This endpoint returns no `meta` block at all** — no tier, no schedule, no Tier C
disclosure. Audit spec §1.3 explicitly requires the representative-price and estimate labelling.

---

### 2.4 `GET /items/{pbs_code}/prescribing-texts` — all tiers

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `prescribing_texts` → `item_prescribing_text_relationships` | `prescribing_text_id + schedule_id` | A |
| — | filter | `rel.pbs_code = $1 AND rel.schedule_id = $2` | A |

Ordered by `prescribing_text_id`, no LIMIT. **Fully Tier A ✅** — one of the cleanest endpoints.
Currently returns empty: `prescribing_texts` has 0 rows `[D2]`.

---

### 2.5 `GET /items/{pbs_code}/dispensing-rules` — all tiers

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `program_dispensing_rules` → `item_dispensing_rules` | `rule_code + schedule_id` | A |
| — | filter | `idr.pbs_code = $1 AND idr.schedule_id = $2` | A |

No LIMIT, no aggregation. **Fully Tier A ✅**

---

### 2.6 `GET /items/{li_item_id}/dispensing-context` — T3

Path is keyed on `li_item_id` (brand/TPP level) — correct per PBS semantics for dispensing and markup.

| # | From → To | Condition | Tier | Notes |
|---|---|---|---|---|
| 1 | `item_pricing` → `items` | `i.pbs_code = ip.pbs_code AND i.schedule_id = ip.schedule_id` | A | ⚠️ `item_pricing.pbs_code` is **nullable**; INNER JOIN silently drops rows where it is null |
| 2 | `item_pricing` → `markup_bands` | `mb.schedule_id = ip.schedule_id AND mb.program_code = i.program_code AND mb.dispensing_rule_mnem = ip.dispensing_rule_mnem` | A | Ordered `limit_amount NULLS LAST` ✅ |

**Derived:** `total_fees = fee_dispensing + (fee_dispensing_dangerous_drug if item.dangerous_drug) + fee_container_other`
— Tier **B**, but **excludes `fee_container_injectable`** with no stated rationale. Not reversible
against any PBS total. 🏷️ undocumented.

**Structural:** `program_code` is taken from `pricing_rows[0]` and reused for every markup-band lookup.
Correct only if all rows for the `li_item_id` share a program. Not enforced. N+1 query pattern.

---

### 2.7 `GET /drugs/{pbs_code}` — T2

| # | From → To | Condition | Tier | Notes |
|---|---|---|---|---|
| 1 | `items` → `medicines` | `m.id = i.medicine_id` | A | |
| 2 | `items` → `programs` | `program_code + schedule_id` | A | |
| 3 | `items` → `ior` → `organisations` | `pbs_code + schedule_id`; `organisation_id + schedule_id`; **`LIMIT 1` no `ORDER BY`** | **C** | 🏷️ `[H4]` non-deterministic; note present |
| 4 | `items` → `item_atc_relationships` → `atc_codes` | `pbs_code + schedule_id`; `atc_code + schedule_id`; `ORDER BY atc_priority_pct DESC` | A join / **C** selection | `atc_rows[0]` taken as primary |
| 5 | `items` → `item_prescribers` | `pbs_code + schedule_id` | A | ✅ |
| 6 | `items` → `item_pricing` | `pbs_code + schedule_id GROUP BY li_item_id` | A join / **C** aggregation | `MAX()` per brand across dispensing rules |

**Derived fields:**

| Field | Formula | Tier | Finding |
|---|---|---|---|
| `has_split_atc` | `len(atc_rows) > 1` | B | ✅ |
| `brand_count` | `COUNT(DISTINCT li_item_id) **or 1**` | **C** | ⚠️ fabricates `1` when no pricing rows `[F8]` |
| `brands[].pricing.*` | `MAX(commonwealth_price)` etc. per `li_item_id` | **C** | Worst-case representative; not labelled here 🏷️ |
| `brands[].brand_name` | item-level `brand_name` repeated for every brand | **C** | ⚠️ `[F8]` — no `brand_name_note` on this route |
| `benefit_type_label` | dict lookup on `'U'/'R'/'A'/'S'` | B | ⛔ never matches the data `[B3]` |

---

### 2.8 `GET /drugs/{pbs_code}/brands` — T2

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` *(existence check)* | `pbs_code + schedule_id` | A |
| 2 | `items` → `item_pricing` | `pbs_code + schedule_id GROUP BY li_item_id` | A join / **C** aggregation |

`MAX()` over `commonwealth_price`, `max_general_patient_charge`, `brand_premium`, `fee_dispensing`.
🏷️ Disclosure: `brand_name_note` **is** present ✅ — this route is the disclosure model the others
should follow. Aggregation itself still unlabelled as worst-case.

---

### 2.9 `GET /drugs/{pbs_code}/prescribers` — T2

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` → `medicines` | `m.id = i.medicine_id` | A |
| 2 | `items` → `item_prescribers` | `pbs_code + schedule_id ORDER BY prescriber_code` | A — no LIMIT ✅ |
| 3 | `items` → `restrictions` | `r.item_id = i.id ORDER BY authority_required DESC NULLS LAST, written_authority_required DESC NULLS LAST **LIMIT 1**` | **C** |

**Disclosure ✅** — `authority_note` states this reflects the most restrictive restriction and points
to the full list. This is the corrected pattern from the T1/T2 audit pass.

**But:** `authority_required` is mis-derived at ingest `[F2]` and `authority_method` is 100% null
`[F3]`, so the ordering key and the returned method are both unreliable.

---

### 2.10 `GET /drugs/{pbs_code}/atc` — T2

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` → `item_atc_relationships` | `pbs_code + schedule_id ORDER BY atc_priority_pct DESC NULLS LAST` | A — no LIMIT ✅ |
| 2 | `iar` → `atc_codes` | `atc_code + schedule_id` | A |
| 3 | `atc_codes` → `atc_codes` **recursive** | `p.atc_code = c.atc_parent_code WHERE p.schedule_id = $2` | A joins / **B** derivation |

**Derived:** `is_primary = (len(classifications) == 0)` — first row wins → **B** ✅.
`has_split_atc = len > 1` → **B** ✅. `breadcrumb` = joined ancestor codes → **B** ✅.
N+1: one recursive CTE per ATC code. Correct, not efficient.

---

### 2.11 `GET /drugs/{pbs_code}/amt` — T2

| # | From → To | Condition | Tier | Notes |
|---|---|---|---|---|
| 1 | `items` → `item_amt_relationships` | **`iar.pbs_code = $1`** + `schedule_id` | **⚠️ Tier A violation** | PBS `ITEM_AMT_T` keys on `li_item_id` `[C9]` |
| 2 | `iar` → `amt_items` | `amt_id + schedule_id ORDER BY concept_type, amt_id` | A — no LIMIT ✅ |

**Derived:** grouping into `concepts_by_type` — presentational, **B** ✅.
**Impact:** AMT concepts are aggregated at drug level; PBS models them at brand level, so concepts
belonging to one brand are attributed to all brands of the PBS code.
Currently moot — `item_amt_relationships` has 0 rows and `items.li_item_id` is 100% null `[D2][D3]`.

---

### 2.12 `GET /drugs/{pbs_code}/restrictions` — T2

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` → `medicines` | `m.id = i.medicine_id` | A |
| 2 | `restrictions` → `items` | `i.id = r.item_id WHERE i.pbs_code = $1 AND i.schedule_id = $2 ORDER BY restriction_code` | A — no LIMIT ✅ |

**Coverage ✅.** Field-level defects inherited from ingest: `streamlined_code` `[F1]`,
`authority_required` `[F2]`, `authority_method` `[F3]`, `continuation_only` (substring heuristic).

---

### 2.13 `GET /drugs/search` — T3

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` → `medicines` | `m.id = i.medicine_id` | A |
| 2 | `items` → `item_atc_relationships` *(EXISTS)* | `pbs_code + schedule_id AND atc_code LIKE $n` | A |

**Search rule (Tier C):** `ILIKE '%q%'` OR'd across `m.ingredient`, `i.brand_name`, `i.pbs_code`.
No ranking; ordered `m.ingredient, i.pbs_code`. Unanchored `ILIKE` cannot use the `gin_trgm` index
built in migration 002 — full scan.
⛔ `?benefit_type=A` matches nothing `[B3]`. Returns non-versioned `medicine_atc_code` `[C3]`.

---

### 2.14 `GET /drugs/{pbs_code}/full-profile` — T3

Superset of §2.7 via `_gather_drug_data`. Same joins 1–6, plus:

| # | From → To | Condition | Tier |
|---|---|---|---|
| 7 | `restrictions` → `items` | `i.id = r.item_id ORDER BY restriction_code` | A — **all** rows, no LIMIT ✅ |
| 8 | `item_amt_relationships` → `amt_items` | `amt_id + schedule_id` | A join, keyed on `pbs_code` ⚠️ `[C9]` |

Inherits `[F8]` (repeated brand names, `brand_count … or 1`) with **no note** on this route 🏷️.

---

### 2.15 `GET /drugs/{pbs_code}/restriction-full` — T3

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `restrictions` → `items` | `i.id = r.item_id`, optional `AND r.restriction_code = $n` | A |
| 2 | `restrictions` → `restriction_prescribing_text_relationships` | `restriction_code + schedule_id` | A |
| 3 | `rel` → `prescribing_texts` | `prescribing_text_id + schedule_id ORDER BY prescribing_text_id` | A |

**No fragments dropped ✅.** Ordering is by `prescribing_text_id`, not by a PBS sequence column —
acceptable per spec §1.4 only because no sequence column is ingested. Verify PBS does not supply one.
N+1: one text query per restriction. Currently returns empty components `[D2]`.

---

### 2.16 `GET /drugs/{pbs_code}/authority-workflow` — T3

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `restrictions` → `items` | `i.id = r.item_id ORDER BY restriction_code` | A |
| 2 | `items` → `item_prescribers` | `pbs_code + schedule_id` | A |

**Derived — the entire `checklist` array is Tier C**, generated in application code from
`authority_method`, `written_authority_required`, `complex_authority_required`, `continuation_only`.

🏷️ **`[H2]` No disclaimer present.** Spec §3.4 requires the response to state this is an internal
interpretation.
⚠️ `authority_method` is 100% null `[F3]` → the `== "S"` branch never fires; every authority
restriction receives the same generic checklist and a null `authority_method_label`.

---

### 2.17 `GET /drugs/{pbs_code}/substitution` — T3

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` → `medicines` | `m.id = i.medicine_id` (resolve source ingredient) | A |
| 2 | `medicines` → `items` | `m.ingredient = $source AND i.schedule_id = $2 AND i.pbs_code != $3` | A join / **C** semantics |

🏷️ **`[H3]`** Matches on **ingredient only** — `form`, `strength` and `pack_size` are ignored. A
500 mg IR tablet is returned as a substitute for a 1 g MR tablet. The `note` says "same-ingredient
matches only" but does not warn that strength and form differ. **For a tier sold into clinical
decision support this caveat must be explicit.**

---

### 2.18 `GET /drugs/{pbs_code}/price-history` — T3

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` → `schedules` | `s.id = i.schedule_id WHERE i.pbs_code = $1 AND s.ingest_status = 'complete' ORDER BY s.month DESC LIMIT $months` | A |

Completed-ingest filter ✅ (spec §3.6). **Derived trend** (`delta`, `delta_pct`, `direction`) — **C**.

⚠️ `oldest_price` is taken from the last **non-null** price, but `oldest_month` is taken from the last
**snapshot** — the two can refer to different months. Uses `items.government_price` `[C2]`, which is
100% null `[D3]`, so `trend` is currently always `null`.

---

### 2.19 `GET /drugs/{pbs_code}/pricing-events` — T3

No joins — direct read of `item_pricing_events` on `pbs_code + schedule_id`.
**Derived:** `price_change = new_price − previous_price` → **B**, reversible ✅.
Cleanest T3 endpoint in the codebase.

---

### 2.20 `GET /drugs/{pbs_code}/safety-net` — T3

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` *(direct)* | `pbs_code + schedule_id` | A |
| 2 | *(schedule)* → `copayments` | `schedule_id` | A |

**Derived:**

| Field | Formula | Tier | Finding |
|---|---|---|---|
| `actual_general` | `MIN(items.general_charge, copayments.general)` | B | ✅ null-guarded |
| `estimated_scripts_to_safety_net` | `ROUND(safety_net / actual)` | **C** | Zero-guarded ✅; ignores household/family aggregation |

⚠️ Uses `items.general_charge` while `/items/{code}/patient-cost` uses
`item_pricing.commonwealth_price` — the two endpoints will disagree on the same item `[C2]`.

---

### 2.21 `GET /drugs/{pbs_code}/60-day-pair` — T3

Same join shape as §2.17 plus `AND i.sixty_day_eligible = TRUE`.
`sixty_day_eligible` is a PBS-sourced flag → Tier **A/B** ✅.
🏷️ Same form/strength blindness as substitution `[H3]`.

---

### 2.22 `GET /drugs/{pbs_code}/formulary-status` — T3

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items` → `medicines` | `m.id = i.medicine_id` | A |
| 2 | `items` → `item_pricing_events` | `pbs_code + schedule_id ORDER BY effective_date DESC **LIMIT 5**` | A join / **C** truncation |

⚠️ **`[F6]`** `formulary_labels` describes F1/F2/F3 as a cost-effectiveness ranking. This is not the
PBS meaning. The endpoint docstring separately states "F1 = brand-substitutable; F2 = not
substitutable", which is **inverted**. The data also contains `CDL` (208 items) which the label map
does not handle.

---

### 2.23 `/schedule-changes/*` — T3 (6 endpoints)

**Common enrichment join:**

| # | From → To | Condition | Tier |
|---|---|---|---|
| E1 | `summary_of_changes` → `items` → `medicines` | batch `i.pbs_code = ANY($codes) AND i.schedule_id = $2` | A |

⛔ **`[B5]`** For `/delistings` the item is absent from that schedule by definition → `drug_name`
always null.

**Classification (`_classify_change`) — Tier C throughout:**

| `section` contains | `change_type` | Emitted | Severity | Status |
|---|---|---|---|---|
| `pricing-event`, `dispensing-rule` | any | `PRICE_CHANGE` | MEDIUM | ✅ substring match works |
| `items` *(exact `==`)* | INSERT / DELETE / UPDATE | `NEW_LISTING` / `DELISTING` / `FORMULARY_CHANGE` | MEDIUM / **HIGH** / MEDIUM | ⛔ **never fires `[B4]`** |
| `restriction`, `prescribing-text`, `criteria`, `indication` | any | `RESTRICTION_CHANGE` | MEDIUM | ✅ |
| `fee` / `copayment` | any | `FEE_CHANGE` / `COPAYMENT_CHANGE` | LOW / HIGH | ✅ |
| `prescriber`, `atc`, `amt` | any | `OTHER_MODIFICATION` | LOW | ✅ |

**Per-endpoint additions:**

| Endpoint | Extra joins | Tier | Findings |
|---|---|---|---|
| `/new-listings` | `item_atc_relationships` for primary ATC; sibling `COUNT(DISTINCT pbs_code)` on same `atc_code` | A joins / **C** flag | ⛔ `section = 'items'` `[B4]`; ⚠️ dict comprehension returns **lowest**-priority ATC `[B6]`; N+1 |
| `/delistings` | `item_atc_relationships` → `LEFT(atc_code,5)` (ATC L4) → `items` → `medicines`, `LIMIT 5` | A joins / **C** alternatives | ⛔ `section = 'items'` `[B4]`; ⛔ ATC lookup in wrong schedule → alternatives **always `[]`** `[B5]`; N+1. `LEFT(atc_code,5)` = ATC L4 ✅ correct |
| `/price-changes` | `items` current + `items` previous via `schedules WHERE month < $1 AND ingest_status='complete'` | A | Lexicographic `YYYY-MM` comparison ✅. `price_delta`, `price_delta_pct` → **B** reversible ✅. Uses `government_price` `[C2]`, currently null `[D3]` |
| `/restriction-changes` | `restrictions` → `items` on `i.id = r.item_id AND i.schedule_id = $2` | A | ✅ correct |

---

### 2.24 `/market/*` — T4 (10 endpoints)

#### `/market/atc-summary`

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `atc_codes` → `atc_codes` **recursive descend** | `c.atc_parent_code = s.atc_code AND c.schedule_id = $2` | A |
| 2 | `subtree` → `item_atc_relationships` | `iar.atc_code = st.atc_code AND iar.schedule_id = $2` | A |
| 2b | *(optional)* | `AND iar.is_primary = TRUE` | ⛔ **column does not exist `[B1]` → HTTP 500** |
| 3 | `iar` → `items` | `pbs_code + schedule_id` | A |
| 4 | `items` → `item_pricing` **LEFT** | `pbs_code + schedule_id` | A |
| 5 | `items` → `item_organisation_relationships` **LEFT** | `pbs_code + schedule_id` | A |
| 6 | children breakdown | `atc_parent_code = $1`, per-child sub-query | A / N+1 |

⚠️ **`[C1]` Joins 4 and 5 create a cartesian product.** All `COUNT(*) FILTER (…)` metrics
(`f1_count`, `f2_count`, `authority_required_count`, `streamlined_count`, `sixty_day_items`,
`biosimilar_items`) count fan-out rows, not items — **measured 3.12× inflation**. `mean_dpmq` and
`median_dpmq` are sponsor-weighted. `unique_prescribing_rules` and `unique_brands` use `DISTINCT` and
are correct — so the response is internally contradictory.
⛔ `benefit_type = 'A'/'S'` never matches `[B3]` → authority counts always 0.

#### `/market/price-reduction-events`

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `item_pricing_events` → `schedules` | `s.id = pe.schedule_id`, month range | A |
| 2 | `pe` → `items` **LEFT** | `pbs_code + schedule_id` | A |
| 3 | `items` → `medicines` **LEFT** | `m.id = i.medicine_id` | A |
| 4 | *(optional EXISTS)* `item_atc_relationships` / `item_organisation_relationships` | ATC prefix / org filter | A |

**Filter (Tier C):** `event_type ILIKE '%reduction%' OR new_price < previous_price`, `previous_price > 0`.
⚠️ `min_pct_change` is applied **in Python after `LIMIT 500`** — with `sort=pct_change_desc` this
silently drops qualifying events. `meta.total` reports the post-filter count, not the true total.

#### `/market/manufacturer-landscape`

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `item_organisation_relationships` → `items` | `pbs_code + schedule_id` | A |
| 2 | `items` → `item_pricing` **LEFT** | `pbs_code + schedule_id` | A |
| 3 | `ior` → `organisations` **LEFT** | `organisation_id + schedule_id` | A |
| 4 | *(optional EXISTS ×3)* | ATC prefix, program, formulary | A |

`GROUP BY ior.organisation_id, o.name`; `HAVING COUNT(DISTINCT ip.li_item_id) >= $min`.
⚠️ `[C1]` fan-out inflates `f1_count`, `f2_count`, `biosimilar_count`, `authority_count`.
⚠️ `HAVING` on `COUNT(DISTINCT ip.li_item_id)` **excludes every manufacturer with no pricing rows** —
with `item_pricing` empty, this endpoint returns `[]` regardless of `min_item_count`.

#### `/market/schedule-comparison`

| # | From → To | Condition | Tier |
|---|---|---|---|
| 1 | `items`(target) NOT EXISTS `items`(base) | `pbs_code`, differing `schedule_id` | A |
| 2 | `items`(base) NOT EXISTS `items`(target) | `pbs_code`, differing `schedule_id` | A |
| 3 | `items` b ⋈ `items` t | `t.pbs_code = b.pbs_code`, `b.government_price != t.government_price` | A |
| 4 | `copayments` × 2 | `schedule_id` | A |

⛔ **`[B2]`** the ATC filter injected into query 3 references alias `i`, which does not exist there
(`b`/`t` only) → HTTP 500 whenever `atc_prefix` is supplied.
🏷️ "New listing" = absent from base — cannot distinguish a first listing from a re-listing. Tier C
semantics, unlabelled.

#### `/market/formulary-landscape`

`items` → `item_pricing` **LEFT** on `pbs_code + schedule_id`; `GROUP BY COALESCE(formulary,'None')`;
plus `copayments` on `schedule_id`.
`item_count` uses `COUNT(DISTINCT i.pbs_code)` ✅. `mean_dpmq` is pricing-row-weighted `[C1]`;
min/max unaffected. Percentage share computed in Python — **B** ✅.

#### `/market/biosimilar-landscape`

`items` → `medicines`; `items` → `item_pricing` **LEFT**; `GROUP BY m.ingredient, m.atc_code`;
`HAVING biosimilar_count > 0`.
⚠️ `[F7]` "originator" = `biosimilar = FALSE`, which includes generics.
`items.originator_brand_indicator` is ingested and unused.
⚠️ `[C3]` groups on the non-versioned `medicines.atc_code`.
Returns `[]` today — `biosimilar = TRUE` for 0 of 6,883 items.

#### `/market/authority-landscape`

Three independent aggregates:

| Query | Join | Tier | Finding |
|---|---|---|---|
| Benefit type | `items` only, `GROUP BY benefit_type` | A | ✅ correct, but labels fall through `[B3]` |
| Authority method | `restrictions` → `items` on `i.id = r.item_id`, `WHERE authority_method IS NOT NULL` | A | ⛔ **always empty** — `authority_method` 100% null `[F3]` |
| Prescriber type | `item_prescribers` → `items` on `pbs_code + schedule_id` | A | ✅ correct |

#### `/market/safety-net-burden`

`copayments` on `schedule_id`; `items` direct with optional ATC EXISTS.
**Derived (DB-side):** `CEIL(safety_net_general / general_charge)` — **C**, zero-guarded ✅.
⚠️ `[C7]` `min`/`max`/`mean` computed in Python over the `LIMIT 500` ascending slice but described as
"across all items".
⚠️ Uses `items.general_charge` `[C2]`, 100% null `[D3]` → returns `[]`.

#### `/market/listings-pipeline`

`summary_of_changes` → `items` **LEFT** on `pbs_code + schedule_id` → `medicines` **LEFT**.
**Filter is pure text matching on `change_type` / `description` — Tier C.**
🏷️ Disclosed via `meta.note` ✅. Weakness: the additions filter excludes `%delist%` on `change_type`
only, not on `description`. Returns `[]` — `summary_of_changes` is empty `[D2]`.

#### `/market/price-pressure-index`

| Signal | Weight | Join | Tier |
|---|---|---|---|
| F2 brand-premium prevalence | 50% | `items` → `item_pricing` **LEFT**, `WHERE formulary='F2'` | **C** |
| Recent reductions | 30% | `item_pricing_events` → `schedules`, month ≥ target − 3 | **C** |
| DPMQ : government-price ratio | 20% | `AVG(ip.commonwealth_price / NULLIF(i.government_price,0))` | **C** |

⚠️ `[C8]` three defects: `total_f2` is fan-out inflated then used as the denominator for a
`DISTINCT`-based numerator; the reductions window has **no upper bound** (counts future schedules);
weights are undocumented.
⚠️ `[C2]` signal 3 divides the two competing price sources by each other.

---

### 2.25 Reference and T1 endpoints

| Endpoint | Joins | Tier | Finding |
|---|---|---|---|
| `GET /restrictions` (list) | `restrictions` → `items` on `i.id = r.item_id`, filter only | A | ✅ full coverage; `?streamlined_code=` filters on restriction code `[F1]` |
| `GET /restrictions/{code}` | as above + T2: `restriction_prescribing_text_relationships` → `prescribing_texts` on `prescribing_text_id + schedule_id` | A | ⚠️ `[C4]` `fetchrow` no `ORDER BY` on a code shared by up to 2,124 items |
| `GET /organisations/search` | `organisations` → `ior` **LEFT** on `organisation_id + schedule_id`, `GROUP BY` | A | ✅ `COUNT(ior.pbs_code)` correct — `ior` is unique per (item, org) |
| `GET /organisations/{id}/portfolio` | `ior` → `items` on `pbs_code + schedule_id` → `medicines` | A | ✅ |
| `GET /atc-codes/{code}/hierarchy` | `atc_codes` recursive **upward** on `p.atc_code = c.atc_parent_code`, `schedule_id` scoped | A | ✅ |
| `GET /atc-codes/{code}/items` | recursive **downward** → `item_atc_relationships` → `items` → `medicines` | A | ✅ uses `SELECT DISTINCT` — **correctly avoids the fan-out that `[C1]` describes** |
| `GET /prescribing-texts` | → `item_prescribing_text_relationships` *or* `restriction_prescribing_text_relationships` on `prescribing_text_id + schedule_id` | A | ✅ |
| `GET /copayments`, `/copayments/current` | `copayments` → `schedules` on `s.id = c.schedule_id`, `ingest_status='complete'` | A | ✅ |
| `GET /summary-of-changes` | `summary_of_changes` → `schedules` on `sc.id = s.schedule_id` | A | ✅ |
| `GET /changes` | `changes` → `schedules` on `s.id = c.schedule_id` | A | ✅ (distinct from `summary_of_changes`; 87,983 rows) |
| `GET /extemporaneous/preparations` | `extemporaneous_preparations` → `extemporaneous_prep_sfp_relationships` **LEFT** on `ex_prep_pbs_code + schedule_id`, `ARRAY_AGG … FILTER` | A | ✅ |
| `GET /programs/{code}/fee-structure` | `program_dispensing_rules`, `markup_bands`, `fees` on `program_code + schedule_id` | A | ✅ (`fees` empty) |

---

## Part 3 — Ingest-layer derivations

Field-level transformations applied in `ingest/normaliser.py` before any join runs. These are
invisible to a query-level review but determine what the joins return.

| Target field | Derivation | Tier | Verified | Finding |
|---|---|---|---|---|
| `restrictions.streamlined_code` | `= res_code` (alias) | **C** | 22,769 / 22,769 identical | ⚠️ `[F1]` fabricated |
| `restrictions.authority_required` | `yn(complex_authority_rqrd_ind) OR authority_required` | **C** | 2,950 true of 22,769 | ⚠️ `[F2]` derived from wrong flag |
| `restrictions.continuation_only` | `"continu" in treatment_phase.lower()` | **C** | — | 🏷️ substring heuristic, undisclosed |
| `restrictions.restriction_text` | `li_html_text OR restriction_text` | **C** | 10,141 contain `<` | ⚠️ `[F5]` HTML in a text field |
| `restrictions.indication` | passthrough, "legacy compat" | — | 0 of 22,769 set | ⚠️ `[F4]` always null, documented as populated |
| `restrictions.authority_method` | passthrough | A | 0 of 22,769 set | ⚠️ `[F3]` never populated |
| `items.benefit_type` | `BENEFIT_TYPE_MAP[raw]` → long form | B | 4 long-form values | ⛔ `[B3]` API matches single letters |
| `items.sixty_day_eligible` | PBS flag OR legacy field | A/B | 728 true | ✅ |
| `items.biosimilar` | `item_overview.biosimilar OR raw.biosimilar` | A/B | 0 true | Endpoint returns `[]` |
| `medicines.atc_code` | destructive global upsert | — | no `schedule_id` | ⚠️ `[C3]` rewrites history |
| `summary_of_changes.section` | `changed_endpoint OR section` | A | *(0 rows)* | ⛔ `[B4]` leading `/` breaks `== 'items'` |
| `summary_of_changes.pbs_code` | `pbs_code OR table_keys.pbs_code OR table_keys.li_item_id.split('_')[0]` | **C** | *(0 rows)* | 🏷️ String-splitting a composite key; undisclosed |
| `item_atc_relationships.atc_priority_pct` | passthrough | A | 6,756 / 6,756 set | ✅ |

**Idempotency ✅** — tables lacking unique constraints (`markup_bands`, `item_pricing_events`,
`summary_of_changes`, `changes`, `restrictions`) are loaded `DELETE … WHERE schedule_id` first,
inside a transaction. Re-ingest will not duplicate.

---

## Part 4 — Review checklist for an independent assessor

Ordered by expected yield.

**Field mapping — highest priority**
1. Does PBS supply an authority-method field, and under what name? `restrictions.authority_method` is
   100% null. → `[F3]`
2. Is `authority_required` separately available, or must it be inferred? Current inference from
   `complex_authority_rqrd_ind` under-reports. → `[F2]`
3. What is the real PBS streamlined authority code field? → `[F1]`
4. Should `benefit_type` be stored as PBS single letters or expanded? → `[B3]`
5. Confirm the exact values of `summary_of_changes.changed_endpoint`. → `[B4]`

**PBS domain semantics**
6. Confirm F1 = single-brand, F2 = multiple-brand/price-disclosure. Is the codebase inverted? → `[F6]`
7. What is `CDL` as a formulary value (208 items)?
8. Is `LEFT(atc_code, 5)` the correct ATC Level-4 extraction for all PBS ATC strings?
9. Is drug-level or brand-level correct for `/drugs/{code}/amt`? PBS keys AMT on `li_item_id`. → `[C9]`
10. Which price is canonical for patient out-of-pocket: `items.government_price` or
    `item_pricing.commonwealth_price`? → `[C2]`

**Statistical and structural**
11. Reproduce the fan-out: run the `[C1]` verification query in `AUDIT_FINDINGS.md` §Appendix.
12. Should `medicines` be schedule-versioned, given the historical-accuracy claim? → `[C3]`
13. Are the 50/30/20 price-pressure weights defensible, or should the index ship raw signals only?
14. Is same-ingredient-ignoring-strength an acceptable substitution basis at T3? → `[H3]`

**Coverage**
15. `item_restriction_relationships` and `container_organisation_relationships` are ingested but never
    read. Should they be the authoritative source, or dropped?
16. `items.originator_brand_indicator` / `innovator_indicator` are ingested and unused while
    biosimilar-landscape infers "originator" from `NOT biosimilar`. → `[F7]`

---

*Every join in this catalogue was read from source and, where data existed, verified against the
development database. Claims about empty tables and null columns reflect the state of schedule
`2026-03` (6,883 items, `ingest_status='failed'`) on 2026-07-28.*

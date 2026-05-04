# PBS T3 / T4 Audit Approach

This document scopes the audit strategy for T3 (Scale) and T4 (Enterprise) endpoints before the audit runs.
It is intended to be fleshed out collaboratively — open questions are marked **[DECISION NEEDED]**.

---

## Pre-requisites (must be resolved before audit)

### P1 — AMT join key (schema migration required)

The `item_amt_relationships` table was built with `pbs_code` as the key
(`UNIQUE(pbs_code, amt_id, schedule_id)`). PBS ITEM_AMT_T uses `li_item_id` as the natural key
(brand-level relationship to AMT concepts).

Impact: `GET /drugs/{pbs_code}/amt` and `GET /drugs/{pbs_code}/full-profile` (via `_gather_drug_data`)
return AMT concepts aggregated at the wrong granularity.

Required work:
- Migration: add `li_item_id TEXT` to `item_amt_relationships`, update UNIQUE constraint
- Ingest pipeline: populate `li_item_id` from PBS ITEM_AMT_T source
- Query: change `WHERE iar.pbs_code = $1` → `WHERE iar.li_item_id = $1` in `get_drug_amt` and `_gather_drug_data`

**[DECISION NEEDED]** Should `GET /drugs/{pbs_code}/amt` remain a drug-level endpoint
(aggregating all li_item_ids under the pbs_code) or become a brand-level endpoint
(`GET /drugs/{li_item_id}/amt`)? The T2 spec implies drug-level, but PBS models it brand-level.

---

### P2 — Price field source-of-truth decision

Two different price fields appear across the codebase, used inconsistently:

| Field | Source | Used in |
|---|---|---|
| `items.government_price` / `items.general_charge` | Snapshot on the item row | `price-history`, `safety-net`, `60-day-pair`, `market_safety_net_burden` |
| `item_pricing.commonwealth_price` | Live pricing table per dispensing rule | `patient-cost`, `price`, `dispensing-context` |

These can differ. `items.government_price` is a single value per item per schedule.
`item_pricing.commonwealth_price` varies per dispensing rule and brand.

**[DECISION NEEDED]** Which is canonical for patient-facing cost calculations?
The T2 spec (§1.2) defines pricing against `item_pricing`. If `safety-net` and `price-history`
should use the same source, they need to be updated. If the divergence is intentional
(snapshot vs live), document it clearly.

---

## T3 Endpoints — Scale Tier

**Code location:** `api/routers/drugs.py` (§3.1–3.10), `api/routers/schedule_changes.py` (§4.1–4.5)

### 3.1 `GET /drugs/search` (`drugs.py:35`)

Known concerns:
- ILIKE search against ingredient/brand/pbs_code = Tier C (ranking/matching is opinionated)
- ATC prefix filter uses `EXISTS` subquery with schedule scoping ✓
- No cross-schedule leakage visible
- No LIMIT on results per page ✓ (paginated)

Audit tasks:
- Confirm `i.schedule_id = $1` is the first condition in all query branches
- Tag ILIKE matching and `ORDER BY m.ingredient, i.pbs_code` ordering as Tier C
- Confirm pagination offset arithmetic is correct

---

### 3.2 `GET /drugs/{pbs_code}/full-profile` (`drugs.py:665`)

Known concerns:
- Uses `_gather_drug_data` helper which inherits AMT Tier A violation (P1 above)
- Brand pricing uses same `MAX()` aggregation as T2 — Tier C, same issue as C1/C2 from T2 audit
- `has_split_atc` is derived from `len(atc_rels) > 1` ✓ (correct in this endpoint)

Audit tasks:
- Block on P1 resolution before auditing AMT section
- Confirm all Tier A joins from T2 (`/drugs/{pbs_code}`) are present as a superset
- Confirm no LIMIT on restrictions, prescribers, ATC rows
- Tag brand `MAX()` aggregation as Tier C

---

### 3.3 `GET /drugs/{pbs_code}/restriction-full` (`drugs.py:857`)

Known concerns: None identified from code review. Looks clean.

Audit tasks:
- Confirm `restriction → restriction_prescribing_text_relationships → prescribing_texts` joins are schedule-scoped ✓
- Confirm no LIMIT drops fragments (query has no LIMIT) ✓
- Confirm ordering by `restriction_code` then `prescribing_text_id` is PBS-sequence-aligned
- Tag text reconstruction (ordering + concatenation) as Tier B

---

### 3.4 `GET /drugs/{pbs_code}/authority-workflow` (`drugs.py:938`)

Known concerns:
- Checklist generation (`checklist.append(...)`) is pure Tier C heuristic interpretation
- All restrictions are returned (no LIMIT) — good, unlike the prescribers endpoint

Audit tasks:
- Confirm restriction and prescriber joins are schedule-scoped ✓
- Tag checklist steps as Tier C
- Confirm the response endpoint documentation states workflow steps are an internal interpretation

---

### 3.5 `GET /drugs/{pbs_code}/substitution` (`drugs.py:1047`)

Known concerns:
- Documented in response: `"note": "Brand substitution group IDs are not yet available..."`
- Same-ingredient join matches on `m.ingredient` — this is a text match, not a PBS-defined substitution group

Audit tasks:
- Tag `m.ingredient =` join as Tier B (PBS ingredient is a defined field, join is valid)
- Tag presentation as "substitution" as Tier C ✓ (already has note)
- Confirm `i.pbs_code != $3` filter correctly excludes the source item

---

### 3.6 `GET /drugs/{pbs_code}/price-history` (`drugs.py:1119`)

Known concerns:
- Uses `items.government_price`, `items.general_charge`, `items.concessional_charge` — **not** `item_pricing.commonwealth_price`
- This is a different price source from T2 pricing endpoints (see P2)
- Trend direction label ("up"/"down"/"stable") is Tier C

**[DECISION NEEDED]** See P2. Should history be based on `government_price` (item snapshot) or `commonwealth_price` (item_pricing)? They are different fields with different semantics.

Audit tasks:
- Confirm `JOIN schedules s ON s.id = i.schedule_id` uses `s.ingest_status = 'complete'` filter ✓
- Tag trend delta and direction as Tier C
- Confirm schedule ordering is DESC (newest first) ✓

---

### 3.7 `GET /drugs/{pbs_code}/pricing-events` (`drugs.py:1204`)

Known concerns: None significant.

Audit tasks:
- Confirm `item_pricing_events` is a direct PBS-sourced table (Tier A) — no derivation in SELECT
- `price_change = new_price - previous_price` — tag as Tier B ✓
- Confirm no direction/severity labels applied (there are none — clean)

---

### 3.8 `GET /drugs/{pbs_code}/safety-net` (`drugs.py:1261`)

Known concerns:
- Uses `items.general_charge` and `items.concessional_charge` directly, not from `item_pricing`
- Division-by-zero guard: `if sn_general and actual_general` ✓
- Formula `min(general_charge, copay_general)` vs T2 patient-cost formula `min(commonwealth_price, max_general_patient_charge)` — different inputs for same concept

**[DECISION NEEDED]** `safety-net` and `patient-cost` use different price inputs. Is this intentional (simplified vs detailed)? Both are T3/T2 respectively, so they could diverge by design — but the divergence should be documented.

Audit tasks:
- Tag scripts-to-safety-net as Tier C (estimate)
- Confirm guard against zero charges ✓
- Flag formula divergence from patient-cost for decision

---

### 3.9 `GET /drugs/{pbs_code}/60-day-pair` (`drugs.py:1342`)

Known concerns: None significant. Cleanest T3 endpoint.

Audit tasks:
- Confirm `i.sixty_day_eligible` comes from PBS source field (Tier A)
- Tag "pair" presentation and same-ingredient grouping as Tier C ✓ (has note)
- Confirm `i.pbs_code != $3` correctly excludes source item

---

### 3.10 `GET /drugs/{pbs_code}/formulary-status` (`drugs.py:1412`)

Known concerns:
- LIMIT 5 on pricing events is an undocumented Tier C selection (`drugs.py:1447-1455`)
- Formulary label descriptions are incorrect:
  - `"F1": "Formulary 1 — highest cost-effectiveness"` → should be "brand-substitutable"
  - `"F2": "Formulary 2 — medium cost-effectiveness"` → should be "not brand-substitutable"
  - `"F3": "Formulary 3 — lowest cost-effectiveness / price disclosure items"` → F3 is price-disclosure, cost-effectiveness framing is not PBS-defined

**[DECISION NEEDED]** Are the formulary label descriptions intentionally simplified for consumer-facing use, or should they match PBS definitions?

Audit tasks:
- Tag `LIMIT 5` on pricing events as Tier C and add response note
- Fix or confirm formulary label accuracy
- Tag status labels as Tier C

---

## T3 Schedule Changes Endpoints

**Code location:** `api/routers/schedule_changes.py`

### 4.1–4.5 General approach

The `_classify_change` function (`schedule_changes.py:24`) is a heuristic mapping of
PBS raw change types (INSERT/UPDATE/DELETE) + section names to semantic labels
(NEW_LISTING, DELISTING, PRICE_CHANGE, etc.). This is entirely Tier C.

Before auditing these endpoints, the following is needed:

**[DECISION NEEDED]** Golden test cases. Spec §4.2 requires at least one hand-checked
schedule where first-in-class and therapeutic-alternative results are manually verified.
Choose a past schedule (e.g. 2025-11) and manually verify a sample of:
- A genuinely new first-in-class listing (new ATC L5 code)
- A delisting with a therapeutic alternative in the same ATC L4
- A restriction change with meaningful clinical impact

Without golden tests, the classification audit cannot conclude pass/fail.

Specific endpoint concerns:
- `_classify_change` must never label a DELETE on a non-item section as DELISTING
- First-in-class logic (ATC L5 uniqueness) needs a definition: unique to this schedule, or never-before-listed?
- Therapeutic alternatives via ATC L4 siblings — does the endpoint verify the alternative is actually PBS-listed and active?

---

## T4 Endpoints — Enterprise Tier

**Code location:** `api/routers/market.py`

### Pre-audit: Materialized tables not used

Migration `008_t4_materialized_tables.sql` creates `market_atc_summary` and
`market_manufacturer_landscape` tables. However, no market endpoint actually reads from these
tables — all endpoints query live base tables.

**[DECISION NEEDED]** Are these materialized tables intended to be populated and used (for
query performance), or were they abandoned in favour of live queries? If populated, the
ingest pipeline needs a refresh step. If abandoned, the migration should be documented as
a placeholder for a future optimisation pass.

---

### 4.1 `GET /market/atc-summary` (`market.py:31`)

Known concerns — **SCHEMA BUG:**
The `primary_atc_only` query path references `iar.is_primary = TRUE`:

```python
subtree_join += " AND iar.is_primary = TRUE"
```

The `item_atc_relationships` schema (`migrations/004_real_schema.sql`) has no `is_primary`
column. This query will throw a PostgreSQL column-not-found error when `primary_atc_only=True`.

**This must be fixed before the endpoint goes live.**

Fix options:
- Add `is_primary BOOLEAN` to `item_atc_relationships` and populate during ingest
- Replace the filter with `iar.atc_priority_pct = 100` (single-ATC items) or
  `iar.atc_priority_pct = MAX(atc_priority_pct)` subquery

**[DECISION NEEDED]** What is the correct semantic for "primary ATC only"?

Audit tasks (after bug fix):
- Tag ATC subtree recursive CTE as Tier A
- Tag all aggregate metrics (mean_dpmq, biosimilar_count, etc.) as Tier C
- Confirm `primary_atc_only` filter logic after schema fix

---

### 4.2 `GET /market/price-reduction-events` (`market.py:157`)

Known concerns:
- `min_pct_change` filter is applied in Python after DB query (not in SQL WHERE) — for the capped
  500 rows, Python post-filtering may silently discard rows that would otherwise be within the
  cap if filtered in SQL
- ATC filter uses a correlated subquery with schedule re-join:
  `JOIN schedules s ON s.id = iar.schedule_id WHERE s.month = (SELECT month FROM schedules WHERE id = pe.schedule_id)`
  This is an expensive double-lookup that could be simplified

**[DECISION NEEDED]** Should `min_pct_change` be applied in SQL (before the LIMIT 500) or
in Python (after)? Current behaviour is post-filter which means the cap may apply before
useful results are excluded.

---

### 4.3 `GET /market/manufacturer-landscape` (`market.py:279`)

Known concerns: None significant. Clean implementation.

Audit tasks:
- Tag all aggregate metrics as Tier C
- Confirm schedule scoping on all joins ✓
- Confirm LIMIT 200 is documented

---

### 4.4 `GET /market/schedule-comparison` (`market.py:386`)

Known concerns:
- `atc_filter_base` uses a placeholder index `${len(atc_params) + 2}` which could be off-by-one
  if the base and target IDs are numbered differently — verify parameter indexing
- Items added/removed queries use `NOT EXISTS` subqueries — efficient if indexed on `pbs_code`

Audit tasks:
- Verify parameter numbering for `atc_params` across added/removed/price-change queries
- Tag new/removed listing detection as Tier A/B (PBS fact: item existed or not)
- Tag `pct_change` direction as Tier B
- Confirm cross-schedule join uses schedule_id correctly on both sides ✓

---

### 4.5 `GET /market/formulary-landscape` (`market.py:549`)

Known concerns: None significant.

Audit tasks:
- Tag `COALESCE(formulary, 'None')` grouping as Tier B
- Tag percentage share as Tier B (deterministic derivation from PBS counts)
- Confirm ATC filter subquery is schedule-scoped ✓

---

### 4.6 `GET /market/biosimilar-landscape` (`market.py:643`)

Known concerns:
- `price_delta_pct_vs_originator` uses `MIN(commonwealth_price)` for both biosimilar and originator — Tier C (representative worst-case comparison, not authoritative)
- Self-join in the `WHERE` clause: `EXISTS (SELECT 1 FROM items i2 ... WHERE m2.ingredient = m.ingredient AND i2.biosimilar = TRUE)` — performance concern at scale

Audit tasks:
- Tag `price_delta_pct_vs_originator` as Tier C
- Confirm biosimilar flag comes from PBS source field ✓

---

### 4.7 `GET /market/authority-landscape` (`market.py:737`)

Known concerns: None significant.

Audit tasks:
- Tag all COUNT distributions as Tier A (direct PBS field aggregations)
- Confirm restrictions join via items FK is schedule-scoped ✓

---

### 4.8 `GET /market/safety-net-burden` (`market.py:841`)

Known concerns:
- Uses `items.general_charge` from the items table — same P2 divergence as T3 safety-net
- `CEIL($n::numeric / i.general_charge)` is applied in SQL — division-by-zero guarded by `i.general_charge > 0` ✓
- `scripts_to_safety_net` is Tier C (same classification as T3 safety-net)

Audit tasks:
- Confirm schedule scoping ✓
- Tag scripts calculation as Tier C
- Note P2 divergence for resolution

---

### 4.9 `GET /market/listings-pipeline` (`market.py:940`)

Known concerns:
- Change detection uses `ILIKE '%delist%'` and `ILIKE '%list%'` text matching — highly Tier C
- `summary_of_changes` data quality warning already in response `meta.note` ✓

Audit tasks:
- Tag text-matching classification as Tier C ✓
- Confirm response note clearly states data quality dependency

---

### 4.10 `GET /market/price-pressure-index` (`market.py:1039`)

Known concerns:
- Composite index weights (50/30/20) are entirely opinionated
- Index is a 0–100 composite with interpretation thresholds (≥60 High, 30–59 Moderate, <30 Low) — Tier C
- Meta note already explains the composition ✓

**[DECISION NEEDED]** Is the weight distribution (50/30/20) documented in the business plan
or product spec, or was it chosen during implementation? If the latter, it should be clearly
labelled as a v1 heuristic that will evolve.

Audit tasks:
- Tag all three component signals as Tier C
- Tag composite_index and interpretation thresholds as Tier C ✓
- Confirm meta note describes the formula ✓

---

## Summary of open decisions before T3/T4 audit can conclude

| # | Decision | Blocking |
|---|---|---|
| P1 | AMT endpoint level: drug-level vs brand-level | T3.2 full-profile, T3 AMT |
| P2 | Canonical price source: items vs item_pricing | T3.6, T3.8, T4.8 |
| 4.1 | `is_primary` fix strategy for market/atc-summary | T4 deploy |
| 4.2 | `min_pct_change` filter: SQL vs Python | T4.2 correctness |
| 4.10 | Weight distribution documentation | T4.10 labelling |
| 3.6 | Price history source confirmation | T3.6 Tier B vs A classification |
| 3.8 | Safety-net formula divergence from patient-cost | T3.8 consistency |
| 3.10 | Formulary label accuracy | T3.10 correctness |
| 4.1 | Materialized tables: populate or document as placeholder | T4 performance |

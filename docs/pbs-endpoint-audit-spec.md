# PBS Endpoint Audit Specification (for AI Agent)

This document tells an AI agent how to audit each PBSdata.io endpoint for safe joins and risk tiers.

The agent should treat **PBS tables and relationships** as the source of truth (Tier A), and classify any additional logic as Tier B or C.[cite:1]

---

## 0. Global Rules

### 0.1 Tier definitions

- **Tier A – Mechanical PBS join**  
  Relationships that PBS documents explicitly in the v3 Data Model / Data Dictionary (e.g. items → ATC, items → programs, items → restrictions, items → pricing).[cite:1]  
  The agent must:
  - Verify keys and joins exactly match PBS documentation.
  - Treat violations as **errors**, not warnings.

- **Tier B – PBS-aligned derivation**  
  Calculations or aggregations built **only** from PBS fields, where the result can be explained or reversed back to PBS data (e.g. price breakdown that recomposes to the PBS DPMQ).[cite:2]  
  The agent must:
  - Check the derivation is reversible or clearly documented.
  - Flag inconsistencies as **high-risk defects**.

- **Tier C – Opinionated intelligence / heuristic**  
  Logic that goes beyond PBS definitions (e.g. choosing a "representative" price with LIMIT 1, first-in-class detection, substitution heuristics, composite indices).
  The agent must:
  - Ensure Tier C fields are never described as PBS-official.  
  - Ensure Tier C logic is clearly labelled and, where possible, optional (feature-flagged or clearly documented as heuristic).[cite:2]

### 0.2 Global checks for every endpoint

For **every endpoint**, the agent should perform these checks:

1. **Schedule scoping**  
   - Check that every join between PBS tables uses matching `schedule_id` (or `schedule_code`) on both sides.  
   - If any join uses only `pbs_code` (without schedule) across schedule-scoped tables, mark this as a **Tier A violation**.[cite:1]

2. **Key usage**  
   For the following relationships, check keys match PBS docs:
   - Items ↔ ATC: `pbs_code + schedule_id`.
   - Items ↔ AMT: `li_item_id + schedule_id` (from ITEM_AMT_T primary key).[cite:1]
   - Items ↔ Programs: `program_code + schedule_id`.
   - Items ↔ Organisations: `pbs_code + schedule_id`.
   - Items ↔ Restrictions:  
     - PBS keys: `pbs_code + restriction_code + schedule_id` via ITEM_RESTRICTION_RLTD_T (Tier A relationship).  
     - Local FK: `items.id` ↔ `restrictions.item_id` (implementation detail derived from PBS keys).
   - Items ↔ Prescribers: `pbs_code + schedule_id`.
   - Items/Brands ↔ Pricing: `pbs_code + schedule_id` (and, internally, `li_item_id + dispensing_rule_mnem`).
   - Copayments / Fees / Markup / Pricing events are joined as documented in PBS v3.[cite:1]

3. **Coverage checks**  
   - When an endpoint claims to list **all** related rows (e.g. all restrictions for an item, all ATC codes for an item), verify there is **no LIMIT** or grouping that silently drops rows.  
   - Treat unexpected LIMIT/GROUP BY as **Tier C heuristics** and require explicit documentation.

4. **Tier tagging**  
   For each endpoint, the agent must output:
   - A list of joins with their **Tier A/B/C** classification.
   - A list of derived fields with their **Tier B/C** classification.

5. **Path semantics**  
   - `{pbs_code}` paths represent PBS item code.  
   - `{li_item_id}` paths represent brand/TPP-level identifiers; these are used where PBS semantics are explicitly brand-based (e.g. dispensing/markup rules).[cite:2]

---

## 1. Items Endpoints

### 1.1 `GET /items/{pbs_code}`

**Purpose:** Canonical view of a PBS item for a given schedule.

**Expected joins:**

- `items → medicines` on `medicines.id = items.medicine_id` (**Tier A join**).
- `items → restrictions` on `restrictions.item_id = items.id`  
  - Relationship is Tier A (PBS says items have restrictions via `pbs_code + res_code + schedule_id`).  
  - The `item_id` FK is a local implementation detail derived from that mapping.
- `items → item_organisation_relationships` on `pbs_code + schedule_id` (**Tier A join**).
- `item_organisation_relationships → organisations` on `organisation_id + schedule_id` (**Tier A join**).
- `items → programs` on `program_code + schedule_id` (**Tier A join**).
- `items → item_atc_relationships` on `pbs_code + schedule_id` (**Tier A join**).
- `item_atc_relationships → atc_codes` on `atc_code + schedule_id` (**Tier A join**).
- `items → item_prescribers` on `pbs_code + schedule_id` (**Tier A join**).

**Derived fields / logic:**

- Any short restriction "summary" fields (e.g. brief indication text, single authority flag) built from the full restrictions table = **Tier B derivation**.

**Agent tasks:**

1. Confirm all joins use `schedule_id` and correct keys.  
   - If not, flag a **Tier A error**.
2. Confirm all restrictions for the item are accessible somewhere (this endpoint or a related one).  
   - If this endpoint only shows a summary, it must be clearly derived (Tier B) and the full restrictions must exist elsewhere.
3. Tag restriction summary fields as Tier B and ensure they are not described as "PBS official text".

---

### 1.2 `GET /items/{pbs_code}/price`

**Purpose:** Show item pricing components and copayment context.

**Expected joins:**

- `items → medicines` on `medicines.id = items.medicine_id` (**Tier A join**).
- `items → item_pricing` on `pbs_code + schedule_id` (**Tier A join**).
- `item_pricing → copayments` on `schedule_id` (**Tier A join**).

**Derived fields / logic:**

- `you_pay_gen = MIN(commonwealth_price, max_general_patient_charge)` (**Tier B derivation**).
- `government_pays = commonwealth_price - you_pay_gen` (**Tier B derivation**).

**Agent tasks:**

1. Check the join keys match PBS keys and include `schedule_id` (Tier A).  
2. For a sample of items, recompute `you_pay_gen` and `government_pays` and verify:  
   - `you_pay_gen + government_pays == commonwealth_price`.  
   If not, flag as a **Tier B failure**.
3. Ensure derived fields are labelled as computed from PBS (Tier B), not raw PBS fields.

---

### 1.3 `GET /items/{pbs_code}/patient-cost`

**Purpose:** Patient-facing view: what patients pay and scripts to safety net.

**Expected joins:**

- `items → medicines` on `medicines.id = items.medicine_id` (**Tier A join**).
- `items → item_pricing` on `pbs_code + schedule_id`, with `ORDER BY commonwealth_price DESC LIMIT 1` (**Tier A join with Tier C heuristic selection**).
- `item_pricing → copayments` on `schedule_id` (**Tier A join**).

**Derived fields / logic:**

- `you_pay_gen = MIN(commonwealth_price, max_general_patient_charge)` (**Tier B**).
- `scripts_to_safety_net_general` (**Tier C heuristic**):  
  - If `you_pay_gen <= 0`: value MUST be `NULL` or omitted (avoid divide-by-zero).  
  - Else: `ROUND(safety_net_general / you_pay_gen)`.

**Agent tasks:**

1. Tag the joins themselves as Tier A.  
2. Tag the **selection of a single pricing row** via `ORDER BY ... LIMIT 1` as **Tier C** (representative, not PBS-official).  
3. Tag `scripts_to_safety_net_general` as **Tier C** and check documentation describes it as an estimate and that it guards against `you_pay_gen <= 0`.  
4. Verify that the endpoint does not claim these values are the PBS official patient charge or official safety-net script count.

---

### 1.4 `GET /items/{pbs_code}/prescribing-texts`

**Purpose:** Show prescribing texts that are linked directly to items (item→prescribing_texts), separate from restriction graphs.

**Expected joins:**

- `prescribing_texts → item_prescribing_text_relationships` on `prescribing_text_id + schedule_id` (**Tier A join**).  
- Filters on `pbs_code + schedule_id` (**Tier A filter**).

**Derived fields / logic:**

- None beyond simple selection and ordering. All should be Tier A.

**Agent tasks:**

1. Confirm there is no LIMIT that would drop texts; all related prescribing texts must be returned.  
2. Confirm ordering is either by the PBS-defined sequence column or by ID if no sequence exists.  
3. Tag the entire endpoint as **Tier A**.

---

### 1.5 `GET /items/{pbs_code}/dispensing-rules`

**Purpose:** Item-specific dispensing rules.

**Expected joins:**

- `program_dispensing_rules → item_dispensing_rules` on `rule_code + schedule_id` (**Tier A join**).  
- Filter `item_dispensing_rules` by `pbs_code + schedule_id` (**Tier A filter**).

**Derived fields / logic:**

- None beyond selection and filtering.

**Agent tasks:**

1. Confirm all dispensing rules that exist for this `pbs_code + schedule_id` in the DB appear in the response (coverage).  
2. If any aggregation or LIMIT is used, tag that as **Tier C** and require documentation.

---

### 1.6 `GET /items/{li_item_id}/dispensing-context`

**Purpose:** Provide markup and fee context for pharmacy dispensing.

**Path semantics:**  
This endpoint is intentionally keyed by `{li_item_id}` (brand/TPP level), not `{pbs_code}`. Dispensing rules and markup bands attach to specific Trade Product Packs (li_item_id) in the PBS model.[cite:1]

**Expected joins:**

- `item_pricing → items` on `pbs_code + schedule_id` (**Tier A join**).  
- `item_pricing → markup_bands` on `schedule_id + program_code + dispensing_rule_mnem` (**Tier A join**).

**Derived fields / logic:**

- Any transformation of markup bands into a "context object" (e.g. ordered tiers) is **Tier B** (PBS-aligned derivation).

**Agent tasks:**

1. Confirm mapping to `markup_bands` uses the correct PBS keys.  
2. Confirm all markup band rows for the relevant program and rule are returned.  
3. Tag transformation into higher-level structures as **Tier B**.

---

## 2. Drugs Endpoints — T2

### 2.1 `GET /drugs/{pbs_code}`

**Purpose:** Pre-joined drug overview (identity, program, sponsor, ATC, prescribers, brand summary).

**Expected joins:**

- Same Tier A joins as `GET /items/{pbs_code}`:
  - `items → medicines` (Tier A join).
  - `items → programs` (Tier A join).
  - `items → item_organisation_relationships → organisations` (Tier A joins).
  - `items → item_atc_relationships → atc_codes` (Tier A joins).
  - `items → item_prescribers` (Tier A join).
- `items → item_pricing` with `GROUP BY li_item_id` to create brand summary (**Tier A join, Tier C aggregation**).

**Derived fields / logic:**

- Brand-level aggregates using `MAX` over dispensing-rule-level prices (e.g. `MAX(commonwealth_price)`) MUST be treated as **Tier C heuristics** (representative worst-case values).

**Agent tasks:**

1. Confirm all Tier A joins are schedule-scoped and key-correct.  
2. Tag brand aggregation as **Tier C** and ensure it is documented as a summary, not a canonical PBS brand price.  
3. Ensure this endpoint is documented as a pre-joined view built on Tier A joins, with clear labels for Tier C summaries.

---

### 2.2 `GET /drugs/{pbs_code}/brands`

**Purpose:** Brand-level pricing summary per PBS code.

**Expected joins:**

- `items → item_pricing` on `pbs_code + schedule_id` (**Tier A join**).  
- `GROUP BY li_item_id` with aggregations across dispensing rules.

**Derived fields / logic:**

- Using `MAX(commonwealth_price)` etc. per brand = **Tier C heuristic** (worst-case).  
- A richer summary (e.g. min/median/max) could be **Tier B** if explicitly labelled as summary statistics.

**Agent tasks:**

1. Confirm join and grouping use `schedule_id`.  
2. Tag brand price metrics as Tier C if only a single MAX is returned.  
3. Verify documentation describes these as **summaries**, not PBS canonical brand prices.

---

### 2.3 `GET /drugs/{pbs_code}/prescribers`

**Purpose:** List prescriber types and a headline authority indication.

**Expected joins:**

- `items → medicines` (Tier A join).
- `items → item_prescribers` (Tier A join).
- `items → restrictions` with `LIMIT 1` to get a single authority summary (**Tier C heuristic** over restrictions).

**Derived fields / logic:**

- Summarised authority method based on a single restriction = **Tier C**.

**Agent tasks:**

1. Confirm prescriber join uses `pbs_code + schedule_id` (Tier A).  
2. Tag the `LIMIT 1` restriction join as Tier C and ensure the endpoint does **not** claim it is the full restriction set.  
3. If the endpoint is meant to be authoritative for authority method, require returning **all** restrictions or a clearly documented primary-selection rule.

---

### 2.4 `GET /drugs/{pbs_code}/atc`

**Purpose:** ATC codes and full hierarchy for an item.

**Expected joins:**

- `items → item_atc_relationships` on `pbs_code + schedule_id` (Tier A join).  
- `item_atc_relationships → atc_codes` on `atc_code + schedule_id` (Tier A join).  
- Recursive CTE over `atc_codes` to build hierarchy (Tier A joins, Tier B derivation).

**Derived fields / logic:**

- Flags such as `has_split_atc` or `is_primary` based on `atc_priority_pct` = **Tier B derivation**.

**Agent tasks:**

1. Confirm all ATC relationships for the item are included (no LIMIT).  
2. Confirm hierarchy logic matches PBS structure.  
3. Tag derived flags (primary, split) as Tier B and ensure they are not described as PBS fields.

---

### 2.5 `GET /drugs/{pbs_code}/amt`

**Purpose:** AMT concepts associated with the item.

**Expected joins:**

- `items → item_amt_relationships` on `li_item_id + schedule_id` (Tier A join based on ITEM_AMT_T).[cite:1]  
- `item_amt_relationships → amt_items` on `amt_id + schedule_id` (Tier A join).

**Derived fields / logic:**

- Grouping concepts by `concept_type` in the response = **Tier B** (presentational).

**Agent tasks:**

1. Confirm the join uses `li_item_id + schedule_id`, not `pbs_code`.  
2. Confirm all AMT rows are returned; no LIMIT 1.  
3. Tag grouping as Tier B and ensure AMT IDs and terms remain traceable to PBS.

---

### 2.6 `GET /drugs/{pbs_code}/restrictions`

**Purpose:** List restrictions (codes, phase, authority method) for an item.

**Expected joins:**

- `items → medicines` (Tier A join).  
- `items → restrictions` filtered by `pbs_code + schedule_id` (Tier A relationship via internal FK).

**Derived fields / logic:**

- Ordering and summarisation are Tier B.

**Agent tasks:**

1. Confirm **all** restriction records for the item are present (coverage).  
2. Tag this endpoint as Tier A for joins, Tier B for any summarised fields.

---

## 3. Drugs Endpoints — T3 (Intelligence)

For all T3 endpoints, the agent uses this pattern:

- Joins that mirror PBS relations = **Tier A**.  
- Calculations that map directly to PBS thresholds/flags = **Tier B**.  
- Heuristics (LIMIT 1, representative values, composite scores, estimates) = **Tier C**.

### 3.1 `GET /drugs/search`

**Agent tasks:**

- Tag joins to items/medicines/ATC as Tier A.  
- Tag search matching and ranking rules as Tier C.  
- Confirm no joins cross schedules incorrectly.

---

### 3.2 `GET /drugs/{pbs_code}/full-profile`

**Agent tasks:**

- Confirm it is a superset of Tier A joins from `GET /drugs/{pbs_code}`.  
- Tag all core joins as Tier A, aggregations as Tier B.  
- Use this endpoint as a regression target against PBS item-overview/FMGP outputs.

---

### 3.3 `GET /drugs/{pbs_code}/restriction-full`

**Agent tasks:**

- Tag joins restrictions → restriction_prescribing_text_relationships → prescribing_texts as Tier A.  
- Tag reconstruction of full text (ordering, concatenation) as Tier B.  
- Check no fragments are dropped.

---

### 3.4 `GET /drugs/{pbs_code}/authority-workflow`

**Agent tasks:**

- Tag restriction and prescriber joins as Tier A.  
- Tag workflow steps derived from flags as Tier C.  
- Ensure documentation clearly states workflow is an internal interpretation.

---

### 3.5 `GET /drugs/{pbs_code}/substitution`

**Agent tasks:**

- Tag same-ingredient matching joins as Tier A.  
- Tag presentation as "substitution" as Tier C.  
- Ensure a clear caveat is present.

---

### 3.6 `GET /drugs/{pbs_code}/price-history`

**Agent tasks:**

- Tag joins from `items` to `schedules` and historical price snapshots as Tier A.  
- Tag trend computations (delta, direction up/down/stable) as Tier C summarisation.  
- Ensure schedule filtering uses completed ingests only.

---

### 3.7 `GET /drugs/{pbs_code}/pricing-events`

**Agent tasks:**

- Tag direct access to `item_pricing_events` as Tier A.  
- Tag `price_change = new_price - previous_price` as Tier B derivation.  
- Ensure reduction vs increase labels are Tier C and clearly documented.

---

### 3.8 `GET /drugs/{pbs_code}/safety-net`

**Agent tasks:**

- Tag joins to items and copayments as Tier A.  
- Tag patient charge and scripts-to-threshold calculations as Tier B/C (depending on whether they follow PBS thresholds exactly or simplify household rules).  
- Ensure division-by-zero is prevented when charges are zero.

---

### 3.9 `GET /drugs/{pbs_code}/60-day-pair`

**Agent tasks:**

- Tag joins using ingredient + `sixty_day_eligible` flag as Tier A/B (provided flag comes from PBS).  
- Tag presentation as "pair" or recommendation as Tier C.

---

### 3.10 `GET /drugs/{pbs_code}/formulary-status`

**Agent tasks:**

- Tag formulary fields (F1/F2/F3) and joins to recent pricing events as Tier A.  
- Tag any status labels (e.g. "high pressure") as Tier C.

---

## 4. Schedule Changes Endpoints — T3

### 4.1 `GET /schedule-changes` and `GET /schedule-changes/{schedule_code}`

**Agent tasks:**

- Tag joins from `summary_of_changes → items → medicines` as Tier A.  
- Tag classification logic (`_classify_change`) as Tier C.  
- Ensure at least one hand-checked schedule is used to validate classifications.

---

### 4.2 `GET /schedule-changes/{schedule_code}/new-listings`

**Agent tasks:**

- Tag detection of INSERT on `items` section via `summary_of_changes` as Tier A/B.  
- Tag first-in-class logic (using ATC L5 uniqueness) as Tier C.  
- Require golden test cases where first-in-class results are manually verified.

---

### 4.3 `GET /schedule-changes/{schedule_code}/delistings`

**Agent tasks:**

- Tag detection of DELETE on `items` section as Tier A/B.  
- Tag therapeutic alternative logic (ATC L4 siblings) as Tier C.  
- Ensure alternatives are clearly labelled as heuristic.

---

### 4.4 `GET /schedule-changes/{schedule_code}/price-changes`

**Agent tasks:**

- Tag joins to current and previous schedules and prices as Tier A.  
- Tag `price_delta` and `price_delta_pct` as Tier B derivations.  
- Tag any severity labels as Tier C.

---

### 4.5 `GET /schedule-changes/{schedule_code}/restriction-changes`

**Agent tasks:**

- Tag detection of changes in restriction-related sections via `summary_of_changes` as Tier A/B.  
- Tag any grouping/summary of impact as Tier C.

---

## 5. Market Endpoints — T4

**Agent pattern for all `GET /market/...` endpoints:**

- Tag joins (ATC tree ↔ items ↔ pricing ↔ organisations, pricing events ↔ schedules, summary_of_changes ↔ items) as Tier A.  
- Tag all aggregate metrics and composite scores as Tier C unless they directly restate PBS thresholds/flags (Tier B).

The agent should ensure that:

- Tier C metrics are always described as analytics built **on** PBS data, not PBS-defined metrics.  
- Underlying Tier A/B raw metrics are available so users can ignore Tier C scores if they wish.

---

## 6. Restrictions Endpoints — T1/T2

### 6.1 `GET /restrictions`

**Agent tasks:**

- Confirm join to items is used only as a filter (Tier A relationship via internal FK).  
- Confirm full coverage of the restrictions table per schedule (no hidden filters).

---

### 6.2 `GET /restrictions/{restriction_code}`

**Agent tasks:**

- Tag restriction → item join as Tier A relationship (via local FK based on PBS keys).  
- Tag restriction → restriction_prescribing_text_relationships → prescribing_texts as Tier A (relationships) and Tier B (ordered text reconstruction).  
- Confirm the endpoint exposes enough fields to fully represent PBS restriction semantics (codes, authority_method, phase, continuation flags) and that any additional summaries are clearly Tier B.

---

**Output expectation:**  
For each endpoint, the AI agent should produce a machine-readable summary of:

- List of joins with their Tier A/B/C status.  
- List of derived fields with their Tier B/C status.  
- Any detected Tier A violations (incorrect keys, missing schedule scoping).  
- Any Tier B reversibility failures.  
- Any Tier C logic not clearly documented as heuristic.

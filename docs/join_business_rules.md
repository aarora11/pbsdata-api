# PBSdata.io — Join Business Rules Reference

This document describes every cross-table join implemented in the API, the business rule each join represents, and the tier that gates it. Use it to validate joins against the PBS data dictionary and to drive test-case design.

All joins are schedule-scoped: every cross-table relationship is constrained to the same `schedule_id`, ensuring data consistency within a monthly PBS snapshot.

---

## Table of Contents

1. [Items Endpoints (T1/T2)](#1-items-endpoints)
2. [Drugs Endpoints — T2 Growth](#2-drugs-endpoints-t2-growth)
3. [Drugs Endpoints — T3 Scale Intelligence](#3-drugs-endpoints-t3-scale-intelligence)
4. [Schedule Changes Endpoints (T3 Scale)](#4-schedule-changes-endpoints-t3-scale)
5. [Market Endpoints (T4 Enterprise)](#5-market-endpoints-t4-enterprise)
6. [Restrictions — Tiered Enrichment (T1/T2)](#6-restrictions-tiered-enrichment)

---

## 1. Items Endpoints

### 1.1 `GET /items/{pbs_code}` — Base (T1) and Enriched (T2+)

**Business context:** The PBS item is the atomic unit of the schedule — one row per prescribing rule per brand per program. The `items` table stores administrative and pricing fields, but the active ingredient (INN) lives in `medicines`. Restrictions live in a child table keyed on `item_id`. Base tier returns this raw joined set; T2 enriches with manufacturer, program, ATC, and prescriber data.

**Tier:** T1 (base) / T2 Growth (enriched)

**Base join — always applied:**

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `medicines` | `medicines.id = items.medicine_id` | Resolve active ingredient (INN) and medicine-level ATC code |
| `items` | `restrictions` | `restrictions.item_id = items.id` | Embed restriction summary (indication, prescriber type, authority flag) |

**T2+ enrichment joins (only when `is_tier_or_above(growth)`):**

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `item_organisation_relationships` | `ior.pbs_code = items.pbs_code AND ior.schedule_id = items.schedule_id` | Link item to its sponsor/manufacturer |
| `item_organisation_relationships` | `organisations` | `o.organisation_id = ior.organisation_id AND o.schedule_id = ior.schedule_id` | Resolve organisation name, state, ABN |
| `items` | `programs` | `programs.program_code = items.program_code AND programs.schedule_id = items.schedule_id` | Resolve program title (e.g. General Schedule, Closing the Gap) |
| `items` | `item_atc_relationships` | `iar.pbs_code = items.pbs_code AND iar.schedule_id = items.schedule_id` | Link to ATC classification(s); `ORDER BY atc_priority_pct DESC LIMIT 1` returns primary only |
| `item_atc_relationships` | `atc_codes` | `a.atc_code = iar.atc_code AND a.schedule_id = iar.schedule_id` | Resolve ATC description and level |
| `items` | `item_prescribers` | `ip.pbs_code = items.pbs_code AND ip.schedule_id = items.schedule_id` | List authorised prescriber types for this item |

**History access rule:** T1 callers are limited by `history_months_limit` from the API key record. Requesting a schedule older than the allowed window returns HTTP 403.

---

### 1.2 `GET /items/{pbs_code}/price` — Pricing Detail (T2)

**Business context:** The PBS sets a government (commonwealth) price (DPMQ) per brand-dispensing rule combination. Patient out-of-pocket cost is the lower of DPMQ or the schedule copayment maximum. Fee components (dispensing, dangerous drug, container) are stored per pricing row.

**Tier:** T2 Growth

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `medicines` | `medicines.id = items.medicine_id` | Resolve ingredient for display |
| `items` | `item_pricing` | `ip.pbs_code = items.pbs_code AND ip.schedule_id = items.schedule_id` | One row per `(li_item_id, dispensing_rule_mnem)` — all fee and price components |
| `item_pricing` | `copayments` | `copayments.schedule_id = items.schedule_id` | Schedule-level general and concessional copayment caps for patient outcome calculation |

**Derived computation:** `patient_outcome.general_patient_charge = MIN(commonwealth_price, max_general_patient_charge)`. `government_pays = commonwealth_price - general_patient_charge`.

---

### 1.3 `GET /items/{pbs_code}/patient-cost` — Patient Cost Breakdown (T2)

**Business context:** Simplified patient-facing view — what you actually pay at the pharmacy, how much the government pays, and how many scripts until your safety net kicks in.

**Tier:** T2 Growth

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `medicines` | `medicines.id = items.medicine_id` | Ingredient name for display |
| `items` | `item_pricing` | `ip.pbs_code = items.pbs_code AND ip.schedule_id = items.schedule_id ORDER BY commonwealth_price DESC LIMIT 1` | Take the highest-priced pricing row as the representative price |
| `item_pricing` | `copayments` | `copayments.schedule_id = schedule_id` | General, concessional, safety net thresholds, increased discount limit |

**Derived computation:** `scripts_to_safety_net_general = ROUND(safety_net_general / you_pay_gen)`. `you_pay_gen = MIN(commonwealth_price, max_general_patient_charge)`.

---

### 1.4 `GET /items/{pbs_code}/prescribing-texts` — Prescribing Texts (T1)

**Business context:** Items are linked directly to prescribing text components (separate from the restriction→prescribing-text chain). These describe clinical criteria in structured text.

**Tier:** T1 (all tiers)

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `prescribing_texts` | `item_prescribing_text_relationships` | `rel.prescribing_text_id = pt.prescribing_text_id AND rel.schedule_id = pt.schedule_id` | Join via relationship table |
| Filter | `pbs_code = $1 AND schedule_id = $2` | | Scope to the requested item and schedule |

---

### 1.5 `GET /items/{pbs_code}/dispensing-rules` — Dispensing Rules (T1)

**Business context:** PBS dispensing rules define the quantity, unit, and repeat structure for each prescribing context. Rules are shared across items within a program; the `item_dispensing_rules` table links them.

**Tier:** T1 (all tiers)

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `program_dispensing_rules` | `item_dispensing_rules` | `idr.rule_code = pdr.rule_code AND idr.schedule_id = pdr.schedule_id` | Link shared dispensing rules to individual items |
| Filter | `idr.pbs_code = $1 AND idr.schedule_id = $2` | | Scope to requested item |

---

### 1.6 `GET /items/{li_item_id}/dispensing-context` — Dispensing Context (T3)

**Business context:** Pharmacy-level dispensing requires the full fee schedule including markup band tiers that determine the pharmacy markup on the government price. Markup bands vary by program and dispensing rule mnemonic.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `item_pricing` | `items` | `i.pbs_code = ip.pbs_code AND i.schedule_id = ip.schedule_id` | Retrieve program code and dangerous drug flag from item |
| `item_pricing` | `markup_bands` | `mb.schedule_id = ip.schedule_id AND mb.program_code = i.program_code AND mb.dispensing_rule_mnem = ip.dispensing_rule_mnem` | Attach the markup band schedule applicable to this brand+rule combination |

**Note:** One context row is returned per `dispensing_rule_mnem`. Markup bands are ordered by `limit_amount ASC NULLS LAST` to represent the tiered pricing structure.

---

## 2. Drugs Endpoints — T2 Growth

### 2.1 `GET /drugs/{pbs_code}` — Drug Detail (T2)

**Business context:** A single comprehensive view of a drug's identity, dispensing, pricing, classification, and restrictions. Designed to replace 5+ separate API calls for systems building formulary or drug reference tools. The `include_brands=true` parameter embeds the brand/pricing list inline.

**Tier:** T2 Growth

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `medicines` | `medicines.id = items.medicine_id` | Active ingredient (INN), medicine-level ATC code |
| `items` | `programs` | `programs.program_code = items.program_code AND programs.schedule_id = $2` | Program title |
| `items` | `item_organisation_relationships` | `ior.pbs_code = items.pbs_code AND ior.schedule_id = $2 LIMIT 1` | Primary manufacturer/sponsor |
| `item_organisation_relationships` | `organisations` | `o.organisation_id = ior.organisation_id AND o.schedule_id = ior.schedule_id` | Org name, state, ABN |
| `items` | `item_atc_relationships` | `iar.pbs_code = items.pbs_code AND iar.schedule_id = $2 ORDER BY atc_priority_pct DESC LIMIT 1` | Primary ATC classification only |
| `item_atc_relationships` | `atc_codes` | `a.atc_code = iar.atc_code AND a.schedule_id = iar.schedule_id` | ATC description and level |
| `items` | `item_prescribers` | `ip.pbs_code = items.pbs_code AND ip.schedule_id = $2` | All authorised prescriber codes and types |
| `items` | `item_pricing` | `ip.pbs_code = items.pbs_code AND ip.schedule_id = $2 GROUP BY li_item_id` | Brand count and per-brand pricing summary (when `include_brands=true`) |

---

### 2.2 `GET /drugs/{pbs_code}/brands` — Brands and Pricing (T2)

**Business context:** Multiple brands (identified by `li_item_id`) can exist for a single PBS code when multiple manufacturers have listed the same item. Pricing can differ per brand, and formulary status (F1/F2) determines brand substitution eligibility.

**Tier:** T2 Growth

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `item_pricing` | `ip.pbs_code = items.pbs_code AND ip.schedule_id = $2 GROUP BY li_item_id` | Aggregate per brand: `MAX(commonwealth_price)`, `MAX(max_general_patient_charge)`, `MAX(brand_premium)`, `MAX(fee_dispensing)` |

**Note:** `item_pricing` has one row per `(li_item_id, dispensing_rule_mnem)`. The GROUP BY collapses to brand level; MAX is used to surface the highest applicable value across dispensing rules.

---

### 2.3 `GET /drugs/{pbs_code}/prescribers` — Prescriber Rules (T2)

**Business context:** Not all prescribers can prescribe all PBS items. The PBS restricts certain items to specific prescriber types (e.g. GP only, specialist only). Authority method (Streamlined/Telephone/Written/Online) is held on the restriction record.

**Tier:** T2 Growth

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `medicines` | `medicines.id = items.medicine_id` | Benefit type context |
| `items` | `item_prescribers` | `ip.pbs_code = items.pbs_code AND ip.schedule_id = $2 ORDER BY prescriber_code` | Full list of authorised prescriber types |
| `items` | `restrictions` | `r.item_id = items.id LIMIT 1` | Authority required flag, authority method, written authority flag |

---

### 2.4 `GET /drugs/{pbs_code}/atc` — ATC Classifications (T2)

**Business context:** Some PBS items have split ATC assignment — multiple WHO ATC Level-5 codes representing different clinical uses, each with a priority percentage. The full hierarchy from L1 (anatomical) to L5 (chemical substance) is reconstructed via a recursive CTE.

**Tier:** T2 Growth

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `item_atc_relationships` | `iar.pbs_code = items.pbs_code AND iar.schedule_id = $2 ORDER BY atc_priority_pct DESC` | All ATC codes for this item, ranked by priority |
| `item_atc_relationships` | `atc_codes` | `a.atc_code = iar.atc_code AND a.schedule_id = iar.schedule_id` | ATC description and level for each assigned code |
| `atc_codes` | `atc_codes` (recursive) | `WITH RECURSIVE chain: p.atc_code = c.atc_parent_code WHERE p.schedule_id = $2` | Walk the ATC tree upward from L5 to L1 to build the full hierarchy breadcrumb |

**Split ATC rule:** `has_split_atc = TRUE` when more than one `item_atc_relationship` row exists for this `pbs_code`. The first result (highest `atc_priority_pct`) is marked `is_primary = TRUE`.

---

### 2.5 `GET /drugs/{pbs_code}/amt` — AMT Concepts (T2)

**Business context:** Australian Medicines Terminology (AMT) provides a standardised clinical vocabulary used in electronic prescribing (eRx), clinical decision support, and dispensing software. PBS items are linked to AMT concept types: CTPP (Containered Trade Product Pack), TPP, TP (Trade Product), MPP (Medicinal Product Pack), MP (Medicinal Product).

**Tier:** T2 Growth

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `item_amt_relationships` | `iar.pbs_code = items.pbs_code AND iar.schedule_id = $2` | Links PBS item to its AMT concepts |
| `item_amt_relationships` | `amt_items` | `a.amt_id = iar.amt_id AND a.schedule_id = iar.schedule_id ORDER BY concept_type, amt_id` | Resolve preferred term, concept type, parent AMT ID, ATC code |

**Grouping rule:** Response groups concepts by `concept_type` key in `concepts_by_type`.

---

### 2.6 `GET /drugs/{pbs_code}/restrictions` — Restrictions Index (T2)

**Business context:** Restricted (R) and Authority Required (A/S) PBS items carry clinical criteria the prescriber must satisfy. Each restriction has a unique `restriction_code`, a treatment phase, continuation-only flag, and authority method. This endpoint lists them without full text.

**Tier:** T2 Growth

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `medicines` | `medicines.id = items.medicine_id` | Ingredient for display |
| `items` | `restrictions` | `r.item_id = items.id WHERE items.pbs_code = $1 AND items.schedule_id = $2 ORDER BY restriction_code` | All restriction records for the item |

---

## 3. Drugs Endpoints — T3 Scale Intelligence

### 3.1 `GET /drugs/search` — Full-Text Drug Search (T3)

**Business context:** Allows lookups by ingredient name, brand name, or PBS code. The PBS government API has no search endpoint. Optional filters for program, benefit type, and ATC prefix enable focused searches (e.g. all diabetes drugs in General Schedule with authority requirement).

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `medicines` | `medicines.id = items.medicine_id` | Enable ILIKE search on `ingredient` |
| (optional) `items` | `item_atc_relationships` | `EXISTS (SELECT 1 FROM item_atc_relationships iar WHERE iar.pbs_code = i.pbs_code AND iar.schedule_id = i.schedule_id AND iar.atc_code LIKE $n)` | ATC prefix filter via subquery existence check |

**Search rule:** ILIKE match across `medicines.ingredient`, `items.brand_name`, and `items.pbs_code`. All three fields are OR'd together.

---

### 3.2 `GET /drugs/{pbs_code}/full-profile` — Complete Drug Profile (T3)

**Business context:** A single response containing everything needed to render a full drug monograph: identity, dispensing, program, manufacturer, all ATC classifications, all restrictions (summary), all prescribers, all brands with pricing, and all AMT concepts. Eliminates N+1 calls for downstream systems building drug databases or formularies.

**Tier:** T3 Scale

All joins from §2.1 (`/drugs/{pbs_code}`) plus:

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `restrictions` | `r.item_id = items.id ORDER BY restriction_code` | Full restriction list (all records, not just LIMIT 1) |
| `items` | `item_atc_relationships` → `atc_codes` | As §2.4 but without recursive hierarchy | All ATC codes with descriptions |
| `items` | `item_pricing` | `GROUP BY li_item_id` | All brands with pricing |
| `items` | `item_amt_relationships` → `amt_items` | As §2.5 | All AMT concepts |

---

### 3.3 `GET /drugs/{pbs_code}/restriction-full` — Full Restriction Texts (T3)

**Business context:** Restriction records carry structured clinical criteria but the human-readable prescribing text is stored in a separate `prescribing_texts` table linked via `restriction_prescribing_text_relationships`. This endpoint surfaces the full text chain including HTML (`li_html_text`), indication, and all prescribing components per restriction.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `restrictions` | `r.item_id = items.id WHERE items.pbs_code = $1 AND items.schedule_id = $2` | All restriction records, optionally filtered by `restriction_code` |
| `restrictions` | `restriction_prescribing_text_relationships` | `rel.restriction_code = r.restriction_code AND rel.schedule_id = $2` | Bridge table linking restriction to its prescribing text components |
| `restriction_prescribing_text_relationships` | `prescribing_texts` | `pt.prescribing_text_id = rel.prescribing_text_id AND pt.schedule_id = rel.schedule_id ORDER BY prescribing_text_id` | Resolve text type, prescribing text content, and complex authority flag |

---

### 3.4 `GET /drugs/{pbs_code}/authority-workflow` — Authority Workflow (T3)

**Business context:** Authority prescribing requires specific actions before a script is valid. The workflow is determined by the authority method code: S (Streamlined = self-assess, record code on script), T (Telephone = call Medicare), W (Written = submit form), O (Online). Continuation-only restrictions apply only when renewing treatment, not initiating. This endpoint generates a structured checklist from restriction fields.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `restrictions` | `r.item_id = items.id WHERE items.pbs_code = $1 AND items.schedule_id = $2 ORDER BY restriction_code` | Full restriction records including authority flags, clinical criteria, HTML text |
| `items` | `item_prescribers` | `ip.pbs_code = items.pbs_code AND ip.schedule_id = $2` | Authorised prescriber types shown alongside workflow |

**Derived logic:** Checklist items are generated in application code based on `authority_method`, `written_authority_required`, `complex_authority_required`, and `continuation_only` flags. No DB-side derivation.

---

### 3.5 `GET /drugs/{pbs_code}/substitution` — Substitution Options (T3)

**Business context:** When a drug is unavailable or a patient requests an alternative, prescribers and pharmacists need to identify same-ingredient PBS items. The PBS distinguishes F1 (brand-substitutable) from F2 (not substitutable), but brand-substitution group IDs are not yet in the schema. This endpoint returns same-ingredient matches as a proxy.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` (source) | `medicines` | `medicines.id = items.medicine_id` | Resolve `ingredient` for the source item |
| `medicines` | `items` (candidates) | `m.ingredient = source_ingredient AND i.schedule_id = $2 AND i.pbs_code != $3` | All other items with the same INN in the current schedule |

**Caveat documented in response:** `"Substitutes shown are same-ingredient matches only"` — formal F1 substitution groups are not yet available.

---

### 3.6 `GET /drugs/{pbs_code}/price-history` — Price History (T3)

**Business context:** PBS prices are set monthly. Tracking government price (DPMQ), patient charges, and brand premium across historical schedules enables trend analysis, price disclosure tracking, and regulatory reporting. The trend summary computes the price direction and delta over the requested window.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `schedules` | `s.id = items.schedule_id WHERE items.pbs_code = $1 AND s.ingest_status = 'complete' ORDER BY s.month DESC LIMIT $2` | Time-series of price snapshots across completed schedule ingests |

**Trend computation:** `delta = newest_price - oldest_price`. `direction = 'up' | 'down' | 'stable'`. Applied in application code after DB fetch.

---

### 3.7 `GET /drugs/{pbs_code}/pricing-events` — Pricing Events (T3)

**Business context:** The PBS records discrete pricing change events (price reductions, adjustments) in `item_pricing_events`. These are distinct from the monthly schedule snapshot — they represent the mechanism of change, not the resulting price.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `item_pricing_events` | (no join) | `WHERE pbs_code = $1 AND schedule_id = $2 ORDER BY effective_date DESC` | Direct table access; `price_change = new_price - previous_price` derived in application code |

---

### 3.8 `GET /drugs/{pbs_code}/safety-net` — Safety Net Calculation (T3)

**Business context:** The PBS Safety Net is a threshold beyond which patient copayments are waived or reduced. General patients pay a standard copayment per script; once they accumulate enough scripts to exceed the safety net threshold, further costs are covered. Concessional patients have a lower threshold and near-zero post-threshold cost.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | (no join) | `WHERE pbs_code = $1 AND schedule_id = $2` | Item pricing fields: `general_charge`, `concessional_charge`, `government_price`, `brand_premium` |
| (schedule) | `copayments` | `WHERE schedule_id = $2` | Schedule-level thresholds: `general`, `concessional`, `safety_net_general`, `safety_net_concessional`, `safety_net_card_issue` |

**Derived computations:**
- `actual_general = MIN(general_charge, copay_general)` — patient never pays more than the copayment cap
- `scripts_general = ROUND(safety_net_general / actual_general)` — estimate only; does not account for multiple family members

---

### 3.9 `GET /drugs/{pbs_code}/60-day-pair` — 60-Day Eligibility (T3)

**Business context:** The 60-day dispensing policy (introduced 2023) allows eligible PBS items to be dispensed with 2 months supply on a single prescription, reducing dispensing frequency for patients on stable chronic therapy. Not all items are eligible — `sixty_day_eligible` is set on the item record.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` (source) | `medicines` | `medicines.id = items.medicine_id` | Resolve `ingredient` for the source item |
| `medicines` | `items` (paired) | `m.ingredient = source_ingredient AND i.schedule_id = $2 AND i.sixty_day_eligible = TRUE AND i.pbs_code != $3` | Other items with same INN that are also 60-day eligible |

---

### 3.10 `GET /drugs/{pbs_code}/formulary-status` — Formulary Status (T3)

**Business context:** PBS formulary classification (F1/F2/F3) determines brand substitution rights and price disclosure obligations. F1 items are brand-substitutable (lowest-cost alternative must be dispensed unless patient pays the difference). F2 items are not substitutable. F3 covers price disclosure items. Recent pricing events provide context on whether price pressure has been applied.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `medicines` | `medicines.id = items.medicine_id` | Ingredient for display |
| `items` | `item_pricing_events` | `WHERE pbs_code = $1 AND schedule_id = $2 ORDER BY effective_date DESC LIMIT 5` | Up to 5 most recent pricing events for price context |

---

## 4. Schedule Changes Endpoints — T3 Scale

### 4.1 `GET /schedule-changes` and `GET /schedule-changes/{schedule_code}` — Change Summary (T3)

**Business context:** Every monthly PBS schedule update is recorded in `summary_of_changes`. Raw PBS change records contain only INSERT/UPDATE/DELETE and a section reference. PBSdata.io enriches this into structured change types (NEW_LISTING, DELISTING, PRICE_CHANGE, RESTRICTION_CHANGE, etc.) and severity levels (HIGH/MEDIUM/LOW) using the `_classify_change` business logic function, then resolves ingredient names.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `summary_of_changes` | `items` → `medicines` | Batch: `WHERE i.pbs_code = ANY($pbs_codes) AND i.schedule_id = $2` | Resolve ingredient names for all changed items in one batch query |

**Classification logic (`_classify_change`):**

| section contains | change_type | → classified as | severity |
|-----------------|-------------|-----------------|----------|
| `pricing-event` or `dispensing-rule` | any | PRICE_CHANGE | MEDIUM |
| `items` | INSERT | NEW_LISTING | MEDIUM |
| `items` | DELETE | DELISTING | HIGH |
| `items` | UPDATE | FORMULARY_CHANGE | MEDIUM |
| `restriction`, `prescribing-text`, `criteria`, `indication` | any | RESTRICTION_CHANGE | MEDIUM |
| `fee` | any | FEE_CHANGE | LOW |
| `copayment` | any | COPAYMENT_CHANGE | HIGH |
| `prescriber`, `atc`, `amt` | any | OTHER_MODIFICATION | LOW |

---

### 4.2 `GET /schedule-changes/{schedule_code}/new-listings` — New Listings (T3)

**Business context:** New PBS listings (INSERT on items section) represent market entry events. A first-in-class flag is computed to identify novel therapies — items where no other PBS item shares the same ATC Level-5 code in the current schedule.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `summary_of_changes` | (base) | `WHERE change_type = 'INSERT' AND section = 'items'` | New listings only |
| PBS codes batch | `items` → `medicines` | Batch ingredient lookup | Resolve ingredient names |
| `items` | `item_atc_relationships` | `WHERE pbs_code = ANY($codes) AND schedule_id = $2 ORDER BY atc_priority_pct DESC` | Primary ATC code for each new listing |
| `item_atc_relationships` | `item_atc_relationships` (sibling check) | `COUNT(DISTINCT pbs_code) WHERE atc_code = $atc AND schedule_id = $2 AND pbs_code != $current` | Count other items sharing the same ATC L5 |

**First-in-class rule:** `is_first_in_atc_class = TRUE` when sibling count == 0 (no other PBS item has the same primary ATC Level-5 code in the schedule).

---

### 4.3 `GET /schedule-changes/{schedule_code}/delistings` — Delistings (T3)

**Business context:** When a drug is delisted, patients and prescribers need to switch to alternatives. Therapeutic alternatives are identified as other active PBS items sharing the same ATC Level-4 code (the pharmacological subgroup), providing clinically plausible substitutes.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `summary_of_changes` | (base) | `WHERE change_type = 'DELETE' AND section = 'items'` | Delistings only |
| PBS codes batch | `items` → `medicines` | Batch ingredient lookup | Resolve ingredient names |
| `items` | `item_atc_relationships` | `WHERE pbs_code = ANY($codes) AND schedule_id = $2` | Get ATC L4 prefix (`LEFT(atc_code, 5)`) for each delisted item |
| `item_atc_relationships` | `items` → `medicines` | `WHERE LEFT(atc_code, 5) = $atc_l4 AND schedule_id = $2 AND pbs_code != $delisted_code LIMIT 5` | Up to 5 active alternatives sharing the same ATC L4 class |

**ATC L4 rule:** `LEFT(atc_code, 5)` extracts the 4th-level code (pharmacological subgroup) from the 7-character ATC string.

---

### 4.4 `GET /schedule-changes/{schedule_code}/price-changes` — Price Changes (T3)

**Business context:** Price changes are signalled via `summary_of_changes` on `pricing-event` or `dispensing-rule` sections. The actual price delta is computed by comparing the current schedule's `government_price` against the previous completed schedule.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `summary_of_changes` | (filter) | `section ILIKE '%pricing-event%' OR section ILIKE '%dispensing-rule%'` | Price-related changes only |
| PBS codes batch | `items` → `medicines` | Batch ingredient lookup | Resolve ingredient names |
| `items` (current) | (price lookup) | `WHERE pbs_code = ANY($codes) AND schedule_id = $current` | Current government price |
| `schedules` | (prev schedule) | `WHERE month < $current_month AND ingest_status = 'complete' ORDER BY month DESC LIMIT 1` | Find the immediately preceding schedule |
| `items` (previous) | (price lookup) | `WHERE pbs_code = ANY($codes) AND schedule_id = $prev_id` | Previous government price |

**Delta computation:** `price_delta = current_price - prev_price`. `price_delta_pct = (current_price - prev_price) / prev_price * 100`. Both `NULL` when either price is unavailable.

---

### 4.5 `GET /schedule-changes/{schedule_code}/restriction-changes` — Restriction Changes (T3)

**Business context:** Restriction changes affect prescribing criteria and authority requirements. This endpoint surfaces items where restrictions, prescribing texts, criteria, or indications changed, and includes the item's current active restriction codes so callers can immediately look up the updated text.

**Tier:** T3 Scale

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `summary_of_changes` | (filter) | `section ILIKE '%restriction%' OR '%prescribing-text%' OR '%criteria%' OR '%indication%'` | Restriction-related changes only |
| PBS codes batch | `items` → `medicines` | Batch ingredient lookup | Resolve ingredient names |
| `restrictions` | `items` | `r.item_id = items.id AND items.schedule_id = $2 WHERE items.pbs_code = ANY($codes)` | Fetch current restriction codes (and authority method) for affected items |

---

## 5. Market Endpoints — T4 Enterprise

### 5.1 `GET /market/atc-summary` — ATC Class Market Summary (T4)

**Business context:** Market analysts and payers need portfolio-level statistics for a therapeutic class. This endpoint traverses the entire ATC subtree beneath a given code and aggregates all PBS items within it — item counts, brand counts, benefit type split, formulary split, biosimilar count, 60-day eligibility, DPMQ statistics, and manufacturer count.

**Tier:** T4 Enterprise

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `atc_codes` | `atc_codes` (recursive) | `WITH RECURSIVE subtree: c.atc_parent_code = s.atc_code WHERE c.schedule_id = $2` | Walk the entire ATC tree downward from the target node |
| `subtree` | `item_atc_relationships` | `iar.atc_code = st.atc_code AND iar.schedule_id = $2` | Link all items in the subtree; optionally `AND iar.is_primary = TRUE` when `primary_atc_only=true` |
| `item_atc_relationships` | `items` | `i.pbs_code = iar.pbs_code AND i.schedule_id = $2` | Item records for aggregation |
| `items` | `item_pricing` (LEFT) | `ip.pbs_code = i.pbs_code AND ip.schedule_id = $2` | DPMQ statistics (min/max/mean/median via `PERCENTILE_CONT(0.5)`) |
| `items` | `item_organisation_relationships` (LEFT) | `ior.pbs_code = i.pbs_code AND ior.schedule_id = $2` | Manufacturer count (DISTINCT organisation_id) |
| `atc_codes` (children) | `item_atc_relationships` → `item_pricing` | Per-child breakdown sub-query | Item count and mean DPMQ for each immediate child ATC node |

**Filter rules:** `include_inactive=false` (default) adds `AND i.benefit_type IS NOT NULL`. `primary_atc_only=true` adds `AND iar.is_primary = TRUE`.

---

### 5.2 `GET /market/price-reduction-events` — Price Reduction Events (T4)

**Business context:** Price disclosure requires F2 brand manufacturers to reduce prices over time to align with the weighted average price of dispensed brands. Tracking reductions across a date range reveals which ATC classes or manufacturers are under the most price pressure.

**Tier:** T4 Enterprise

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `item_pricing_events` | `schedules` | `s.id = pe.schedule_id WHERE s.month >= $from AND s.month <= $to` | Scope events to the requested date range |
| `item_pricing_events` | `items` (LEFT) | `i.pbs_code = pe.pbs_code AND i.schedule_id = pe.schedule_id` | Brand name for display |
| `items` | `medicines` (LEFT) | `m.id = i.medicine_id` | Ingredient name for display |
| (optional) | `item_atc_relationships` (EXISTS) | `iar.pbs_code = pe.pbs_code AND iar.atc_code LIKE $prefix` | ATC prefix filter via subquery |
| (optional) | `item_organisation_relationships` (EXISTS) | `ior.pbs_code = pe.pbs_code AND ior.organisation_id = $org_id` | Organisation filter via subquery |

**Reduction filter:** `event_type ILIKE '%reduction%' OR new_price < previous_price` AND `previous_price > 0`. Post-query `min_pct_change` filter applied in application code.

---

### 5.3 `GET /market/manufacturer-landscape` — Manufacturer Portfolio (T4)

**Business context:** Enables competitive intelligence — which manufacturers have the largest PBS portfolios, what formulary/biosimilar mix they carry, and how their price ranges compare. Useful for market entry analysis and tender preparation.

**Tier:** T4 Enterprise

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `item_organisation_relationships` | `items` | `i.pbs_code = ior.pbs_code AND i.schedule_id = $1` | All items per manufacturer |
| `items` | `item_pricing` (LEFT) | `ip.pbs_code = i.pbs_code AND ip.schedule_id = $1` | DPMQ range and brand count |
| `item_organisation_relationships` | `organisations` (LEFT) | `o.organisation_id = ior.organisation_id AND o.schedule_id = $1` | Organisation name |
| (optional) | `item_atc_relationships` (EXISTS) | ATC prefix filter | Scope to therapeutic class |
| (optional) | `items` (EXISTS) | Program code filter | Scope to program |
| (optional) | `items` (EXISTS) | Formulary filter | Scope to F1/F2 |

**Aggregation:** `GROUP BY ior.organisation_id, o.name`. `HAVING COUNT(DISTINCT ip.li_item_id) >= min_item_count`.

---

### 5.4 `GET /market/schedule-comparison` — Schedule Comparison (T4)

**Business context:** Comparing two schedule snapshots reveals the net change in PBS listings and prices across a period — critical for payer reporting, market surveillance, and tracking the impact of government pricing decisions.

**Tier:** T4 Enterprise

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` (target) | `items` (base — NOT EXISTS) | `i2.pbs_code = i.pbs_code AND i2.schedule_id = $base_id` | New listings: items in target not in base |
| `items` (base) | `items` (target — NOT EXISTS) | `i2.pbs_code = i.pbs_code AND i2.schedule_id = $target_id` | Removals: items in base not in target |
| `items` (base) | `items` (target — JOIN) | `t.pbs_code = b.pbs_code WHERE b.schedule_id = $base AND t.schedule_id = $target AND b.government_price != t.government_price` | Price changes: items present in both with differing government price |
| `copayments` (base) | (direct) | `WHERE schedule_id = $base_id` | Base schedule copayment amounts |
| `copayments` (target) | (direct) | `WHERE schedule_id = $target_id` | Target schedule copayment amounts |

**Optional ATC filter:** Applied via `EXISTS (SELECT 1 FROM item_atc_relationships ... AND atc_code LIKE $prefix)` on both base and target item queries.

---

### 5.5 `GET /market/formulary-landscape` — Formulary Distribution (T4)

**Business context:** Understanding the F1/F2/F3 distribution of the PBS (or a therapeutic class) informs pharmacists about substitution opportunity and price disclosure exposure.

**Tier:** T4 Enterprise

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `item_pricing` (LEFT) | `ip.pbs_code = i.pbs_code AND ip.schedule_id = $1` | DPMQ statistics per formulary group |
| `copayments` | (direct) | `WHERE schedule_id = $1 LIMIT 1` | Copayment context (general/concessional) included for reference |

**Grouping:** `GROUP BY COALESCE(items.formulary, 'None')`. Percentage share computed in application code.

---

### 5.6 `GET /market/biosimilar-landscape` — Biosimilar vs Originator (T4)

**Business context:** Biosimilar uptake is a major PBS cost-reduction lever. This endpoint compares biosimilar and originator pricing side-by-side per ingredient, supporting health economists tracking biosimilar competition and pricing gaps.

**Tier:** T4 Enterprise

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `items` | `medicines` | `medicines.id = items.medicine_id` | Group by ingredient |
| `items` | `item_pricing` (LEFT) | `ip.pbs_code = i.pbs_code AND ip.schedule_id = $1` | DPMQ per item for aggregation |
| Filter | `WHERE biosimilar = TRUE OR EXISTS (items with same ingredient where biosimilar = TRUE)` | | Include all items for an ingredient if any biosimilar exists for that ingredient |

**Aggregation:** `GROUP BY m.ingredient, m.atc_code`. Separate `COUNT/AVG/MIN FILTER (WHERE biosimilar = TRUE/FALSE)`. `price_delta_pct = (biosimilar_min - originator_min) / originator_min * 100`.

---

### 5.7 `GET /market/authority-landscape` — Authority Prescribing Landscape (T4)

**Business context:** Provides a PBS-wide (or class-level) view of prescribing complexity — how many items require authority, which authority methods dominate, and which prescriber types are involved. Informs HTA submissions and prescribing policy analysis.

**Tier:** T4 Enterprise

Three separate aggregate queries, each with the same optional ATC/program filters:

| Query | Tables | Business rule |
|-------|--------|---------------|
| Benefit type distribution | `items` | `GROUP BY benefit_type` — count distinct pbs_codes per U/R/A/S |
| Authority method distribution | `restrictions` JOIN `items` | `WHERE authority_method IS NOT NULL GROUP BY authority_method` — restriction count per S/T/W/O |
| Prescriber type distribution | `item_prescribers` JOIN `items` | `GROUP BY prescriber_type` — items per prescriber type |

---

### 5.8 `GET /market/safety-net-burden` — Safety Net Burden (T4)

**Business context:** Higher-cost items reach the safety net threshold faster (fewer scripts). This endpoint ranks all PBS items by scripts-to-safety-net, enabling analysis of which items drive the fastest safety net access and the aggregate burden on the safety net fund.

**Tier:** T4 Enterprise

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `copayments` | (direct) | `WHERE schedule_id = $1 LIMIT 1` | Safety net threshold and copayment amounts |
| `items` | (direct) | `WHERE general_charge IS NOT NULL AND general_charge > 0` | Items with a known patient charge |

**Derived computation (DB-side):** `scripts_to_safety_net = CEIL(safety_net_general / general_charge)`. Sorted `ASC NULLS LAST` so lowest-scripts items (highest cost relative to threshold) appear first.

---

### 5.9 `GET /market/listings-pipeline` — Listings Pipeline (T4)

**Business context:** Identifies upcoming delistings and new additions based on PBS change description text. Useful for tracking market-entry signals and at-risk products.

**Tier:** T4 Enterprise

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `summary_of_changes` | `items` (LEFT) | `i.pbs_code = sc.pbs_code AND i.schedule_id = $1` | Brand name from current schedule |
| `items` | `medicines` (LEFT) | `m.id = i.medicine_id` | Ingredient name |

**Text filter rules:**
- Delistings: `change_type ILIKE '%delist%' OR description ILIKE '%delist%' OR change_type ILIKE '%removal%' OR description ILIKE '%removal%'`
- Additions: `(change_type ILIKE '%list%' OR change_type ILIKE '%addition%' OR description ILIKE '%new listing%') AND change_type NOT ILIKE '%delist%'`

---

### 5.10 `GET /market/price-pressure-index` — Price Pressure Index (T4)

**Business context:** A composite index quantifying how much downward price pressure exists across F2 items (or a therapeutic class). Used for portfolio risk assessment and price disclosure forecasting.

**Tier:** T4 Enterprise

Three sub-queries combined into a composite score:

| Signal | Weight | Tables | Business rule |
|--------|--------|--------|---------------|
| F2 brand premium prevalence | 50% | `items` LEFT JOIN `item_pricing` | `% of F2 items with brand_premium > 0` |
| Recent price reductions | 30% | `item_pricing_events` JOIN `schedules` | Items with `new_price < previous_price` in last 3 schedule months, as % of F2 count |
| DPMQ-to-government-price ratio | 20% | `items` LEFT JOIN `item_pricing` | `AVG(commonwealth_price / government_price)`; ratio ≤ 1 = pressure |

**Composite formula:** `index = (premium_pressure × 0.5) + (reduction_signal × 0.3) + (ratio_pressure × 0.2)`. Range 0–100. ≥60 = High, 30–59 = Moderate, <30 = Low.

---

## 6. Restrictions — Tiered Enrichment

### 6.1 `GET /restrictions` (list) — T1

**Business context:** Raw restriction list — used for browsing or syncing the full restrictions dataset. No joins beyond the table itself.

**Tier:** T1 (all tiers)

| From | To | Condition |
|------|----|-----------|
| `restrictions` | `items` | `r.item_id = items.id` — WHERE filter only, no additional columns selected |

---

### 6.2 `GET /restrictions/{restriction_code}` — Single Restriction (T1 base / T2 enriched)

**Business context:** A restriction defines the clinical criteria for a PBS benefit. At T1, the raw record is returned. At T2+, the prescribing text chain is joined in — the complete human-readable criteria text broken into typed components (indication, clinical criteria, authority statement, etc.).

**Tier:** T1 base / T2 Growth enriched

| From | To | Condition | Business rule |
|------|----|-----------|---------------|
| `restrictions` | `items` | `r.item_id = items.id WHERE r.restriction_code = $1` | Resolve item context |
| **T2+ only:** `restrictions` | `restriction_prescribing_text_relationships` | `rel.restriction_code = r.restriction_code AND rel.schedule_id = $2` | Bridge table |
| `restriction_prescribing_text_relationships` | `prescribing_texts` | `pt.prescribing_text_id = rel.prescribing_text_id AND pt.schedule_id = rel.schedule_id ORDER BY prescribing_text_id` | Typed prescribing text components |

---

*Document generated from source code inspection of `/api/routers/`. All joins reflect the implemented SQL as of the current codebase.*

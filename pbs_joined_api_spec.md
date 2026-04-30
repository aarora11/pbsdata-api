# PBS Joined API — Endpoint Specification
> **Document Type:** Internal Engineering & Product Specification  
> **Purpose:** Defines every joined endpoint exposed by your API — each one materialises one or more join paths from the PBS API Bible V1. Consumers call these instead of constructing their own joins against raw PBS endpoints.  
> **Based On:** PBS API Bible V1 (Sections 10, 11, 12) · Consumer Offerings Guide  
> **Versioned Against:** PBS API Data Dictionary v3.6.5  
> **Tier Structure:** 5 tiers — Base (existing), Core (T1), Clinical (T2), Intelligence (T3), Market (T4)  

---

## How to Read This Document

Each endpoint entry contains:

| Field | Content |
|---|---|
| **Route** | The HTTP path including path parameters |
| **Tier** | Which subscription tier this endpoint belongs to |
| **Join Sources** | Which raw PBS API endpoints are combined |
| **Primary Join Keys** | The exact fields used to stitch records together |
| **Resolution Logic** | Step-by-step join execution order |
| **Query Parameters** | All accepted filters, defaults and types |
| **Response Contract** | Every field returned, typed and annotated |
| **Caching Guidance** | TTL and cache key strategy |
| **Known Data Issues** | PBS source bugs that affect this endpoint |

---

## Tier Definitions

### The Five-Tier Model

```
BASE  →  CORE (T1)  →  CLINICAL (T2)  →  INTELLIGENCE (T3)  →  MARKET (T4)
 ↑              ↑               ↑                  ↑                    ↑
Already      First joins.    Multi-join         Full chain           Cross-item
built.       1–2 tables.    drug views.        resolution.          aggregation.
Raw PBS      Enriched ref   Pricing +          History +            Portfolio +
mimic.       data only.     restrictions.      change feeds.        signals.
```

Each tier is **additive** — subscribing to T3 (Intelligence) includes T2 and T1 access.  
Customers may also **pick individual endpoints** from any tier as add-ons to their base subscription.

---

### Base — Raw PBS API Mimic *(Already Built)*

Your existing API. A faithful representation of the government's PBS API V3 — same endpoints, same field names, same structure. No joins performed. This is the entry-level offering and the foundation everything else builds on.

| What's included | Raw PBS Endpoints |
|---|---|
| Schedule snapshots | `/schedules` |
| Drug items | `/items`, `/item-overview` |
| Pricing | `/copayments`, `/fees`, `/markup-bands`, `/item-dispensing-rule-relationships`, `/item-pricing-events` |
| Classification | `/atc-codes`, `/item-atc-relationships`, `/amt-items` |
| Restrictions | `/restrictions`, `/restriction-prescribing-text-relationships`, `/prescribing-texts`, `/indications`, `/criteria`, `/criteria-parameter-relationships`, `/parameters`, `/item-restriction-relationship`, `/item-prescribing-text-relationships` |
| Prescribers | `/prescribers` |
| Programs & Rules | `/programs`, `/program-dispensing-rules`, `/dispensing-rules` |
| Organisations | `/organisations`, `/item-organisation-relationships` |
| Extemporaneous | `/extemporaneous-ingredients`, `/extemporaneous-preparations`, `/extemporaneous-prep-sfp-relationships`, `/extemporaneous-tariffs`, `/standard-formula-preparations`, `/containers`, `/container-organisation-relationships` |
| Changes | `/summary-of-changes` |

> **Key principle:** If an endpoint is a raw passthrough of a single PBS table — no join, no enrichment, no cross-table resolution — it belongs in Base, not in a paid tier. Customers who only need raw PBS data pay for Base.

---

### T1 — Core *(First Join Tier)*

Endpoints that perform at least one join or enrichment but remain lightweight (1–2 table joins). Reference data made navigable. No raw passthroughs — anything that's a simple PBS endpoint copy lives in Base.

**What makes something Core:**
- Requires joining two or more PBS tables
- Resolves a recursive or hierarchical relationship (e.g. ATC parent chain)
- Resolves a foreign key into a meaningful label (e.g. program_code → program_title + dispensing rules)
- Produces a convenience view that doesn't exist as a single PBS table

---

### T2 — Clinical

Multi-join drug-centric views. Resolves a prescribing rule or item through 3–6 PBS tables to produce a coherent drug identity, pricing or restriction record. Primary target: pharmacy software, prescribing tools, clinical apps.

---

### T3 — Intelligence

Full join chain resolution. Cross-schedule history, complete restriction decomposition, change feed enrichment, extemporaneous compounding, portfolio views. Primary target: hospital pharmacy systems, health economists, formulary managers.

---

### T4 — Market

Cross-item aggregation and computed intelligence signals. Requires aggregating across many items, programmes or schedules. Primary target: pharmaceutical companies, market access teams, policy analysts.

---

## Endpoint Index

### Base — Raw PBS API Mimic *(existing)*
> Full list of raw endpoints: see [Base Tier Definitions](#base--raw-pbs-api-mimic-already-built) above.  
> These endpoints are not documented further in this spec — they mirror the PBS API V3 schema exactly.

---

### T1 — Core
| # | Route | Join Type | Description |
|---|---|---|---|
| 1.1 | `GET /v1/programs` | 3-table | Programs with all dispensing rules resolved and labelled |
| 1.2 | `GET /v1/programs/{program_code}` | 3-table | Single program with full dispensing rule detail |
| 1.3 | `GET /v1/atc-codes/{atc_code}/hierarchy` | Recursive self-join | ATC code with full ancestry and direct children |
| 1.4 | `GET /v1/atc-codes/{atc_code}/children` | Self-join | Direct children of an ATC node, one level down |
| 1.5 | `GET /v1/atc-codes/by-level/{level}` | Filter | All ATC codes at a given hierarchy level (1–5) |
| 1.6 | `GET /v1/dispensing-rules/by-program/{program_code}` | 2-table | Dispensing rules scoped to a program |
| 1.7 | `GET /v1/organisations/search` | Filter + count | Manufacturer search with PBS item count enrichment |
| 1.8 | `GET /v1/copayments/current` | 2-table | Current schedule co-payments with schedule context resolved |

### T2 — Clinical
| # | Route | Description |
|---|---|---|
| 2.1 | `GET /v1/drugs/{pbs_code}` | Drug identity with program, manufacturer, ATC, prescribers |
| 2.2 | `GET /v1/drugs/{pbs_code}/brands` | All brands for a prescribing rule |
| 2.3 | `GET /v1/drugs/{pbs_code}/prescribers` | Authorised prescribers with authority context |
| 2.4 | `GET /v1/drugs/{pbs_code}/atc` | ATC classifications with full hierarchy |
| 2.5 | `GET /v1/drugs/{pbs_code}/amt` | Full AMT concept hierarchy |
| 2.6 | `GET /v1/items/{li_item_id}` | Single brand+pack item with all identity joins |
| 2.7 | `GET /v1/items/{li_item_id}/price` | Full pricing chain for one item |
| 2.8 | `GET /v1/items/{li_item_id}/patient-cost` | Resolved patient out-of-pocket cost |
| 2.9 | `GET /v1/drugs/{pbs_code}/restrictions` | All restrictions for a drug (index) |
| 2.10 | `GET /v1/restrictions/{res_code}` | Single restriction with full prescribing text chain |

### T3 — Intelligence
| # | Route | Description |
|---|---|---|
| 3.1 | `GET /v1/drugs/{pbs_code}/full-profile` | Complete drug record — all joins in one response |
| 3.2 | `GET /v1/drugs/{pbs_code}/restriction-full` | Complete restriction chain with structured criteria |
| 3.3 | `GET /v1/drugs/{pbs_code}/authority-workflow` | Authority prescribing workflow view |
| 3.4 | `GET /v1/drugs/{pbs_code}/substitution` | Brand + therapeutic substitution landscape |
| 3.5 | `GET /v1/drugs/{pbs_code}/price-history` | DPMQ and AEMP across all 13 schedules |
| 3.6 | `GET /v1/drugs/{pbs_code}/pricing-events` | All statutory price reduction events |
| 3.7 | `GET /v1/drugs/{pbs_code}/safety-net` | Safety Net accumulation and threshold data |
| 3.8 | `GET /v1/drugs/{pbs_code}/60-day-pair` | Base + 60-day item pair with cost comparison |
| 3.9 | `GET /v1/drugs/{pbs_code}/formulary-status` | Formulary classification with disclosure trajectory |
| 3.10 | `GET /v1/items/{li_item_id}/dispensing-context` | Full dispensing rule + fee + markup resolution |
| 3.11 | `GET /v1/organisations/{organisation_id}/portfolio` | All PBS items for a manufacturer |
| 3.12 | `GET /v1/atc-codes/{atc_code}/items` | All drugs in a therapeutic class |
| 3.13 | `GET /v1/programs/{program_code}/fee-structure` | Complete fee + markup structure for a program |
| 3.14 | `GET /v1/extemporaneous/{pbs_code}` | Full extemporaneous preparation record |
| 3.15 | `GET /v1/schedule-changes/{schedule_code}` | Enriched change feed for a schedule |
| 3.16 | `GET /v1/schedule-changes/{schedule_code}/new-listings` | New listings this schedule, enriched |
| 3.17 | `GET /v1/schedule-changes/{schedule_code}/delistings` | Delistings and supply-only this schedule |
| 3.18 | `GET /v1/schedule-changes/{schedule_code}/price-changes` | Price changes this schedule |
| 3.19 | `GET /v1/schedule-changes/{schedule_code}/restriction-changes` | Restriction changes this schedule |
| 3.20 | `GET /v1/drugs/search` | Drug search across name, ATC, brand |

### T4 — Market (Aggregation)
| # | Route | Description |
|---|---|---|
| 4.1 | `GET /v1/market/atc-summary` | Aggregate statistics across an ATC class |
| 4.2 | `GET /v1/market/price-reduction-events` | All price events across schedules |
| 4.3 | `GET /v1/market/manufacturer-landscape` | Competitive manufacturer analysis |
| 4.4 | `GET /v1/market/schedule-comparison` | Aggregate diff between two schedules |
| 4.5 | `GET /v1/market/formulary-landscape` | Formulary classification across a drug scope |
| 4.6 | `GET /v1/market/biosimilar-landscape` | Biosimilar vs originator across ATC scope |
| 4.7 | `GET /v1/market/authority-landscape` | Authority type distribution across a drug scope |
| 4.8 | `GET /v1/market/safety-net-burden` | Safety Net exposure across an ATC scope |
| 4.9 | `GET /v1/market/listings-pipeline` | Supply-only → delisting forward pipeline |
| 4.10 | `GET /v1/market/price-pressure-index` | Price pressure signals across F2 drugs |

---

## Global Conventions

### Standard Query Parameters (all endpoints)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `schedule_code` | string | latest published | Override schedule snapshot |
| `limit` | integer | 50 | Max records per page (max 500) |
| `offset` | integer | 0 | Pagination offset |
| `format` | string | `json` | `json` or `csv` where supported |

### Standard Response Envelope

```json
{
  "data": {},
  "meta": {
    "schedule_code": "string",
    "schedule_effective_date": "YYYY-MM-DD",
    "generated_at": "ISO8601",
    "endpoint": "string",
    "tier": "T1|T2|T3|T4",
    "join_sources": ["list of raw PBS endpoints consumed"],
    "pagination": {
      "total": 0,
      "limit": 50,
      "offset": 0,
      "has_more": false
    }
  },
  "warnings": []
}
```

### Warning Codes

| Code | Trigger |
|---|---|
| `ITEM_ADVANCE_NOTICE` | `advanced_notice_date` is set on returned item |
| `ITEM_SUPPLY_ONLY` | `supply_only_indicator = Y` |
| `ITEM_DELISTING_IMMINENT` | `non_effective_date` within 90 days |
| `S19A_EXPIRY_IMMINENT` | `section_19a_expiry_date` within 90 days |
| `NO_AMT_MAPPING` | `amt_code` is null, fallback to `pbs_preferred_term` |
| `DISPENSE_FEE_BUG` | `dispense_fee_type_code = NF` for extemporaneous item (known PBS bug) |
| `PRELIMINARY_DATA` | Schedule is within last 3 months (data subject to revision) |
| `ATC_SPLIT` | Item has `atc_priority_pct < 100` — multiple ATC classes apply |

### Caching Strategy

| Data Type | TTL | Cache Key |
|---|---|---|
| Schedule metadata | 24 hours | `schedule_code` |
| Item-level data | Until next schedule (1st of month) | `li_item_id + schedule_code` |
| Pricing data | Until next schedule | `li_item_id + schedule_code + context` |
| Restriction text | Until next schedule | `res_code + schedule_code` |
| Aggregations | 4 hours | `atc_code + schedule_code + filters_hash` |
| Change feed | Until next schedule | `schedule_code + change_type` |

---

---

# TIER 1 — CORE ENDPOINTS

---

## 1.1 `GET /v1/schedules`

**Tier:** T1  
**Description:** Returns all available schedule snapshots in the Data Mart. Foundational endpoint — `schedule_code` from here is used as input to all other temporal queries.

**Join Sources:** `/schedules`  
**Join Keys:** None — direct passthrough  

**Query Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `status` | string | Filter: `Published`, `Embargo`, `all` (default: `Published`) |
| `limit` | integer | Default 13 (max available) |

**Response:**
```json
{
  "data": {
    "schedules": [
      {
        "schedule_code": "string",
        "effective_date": "YYYY-MM-DD",
        "effective_month": "NOVEMBER",
        "effective_year": 2025,
        "publication_status": "Published | Embargo | Embargo (Superseded)",
        "revision_number": 1,
        "is_latest": true,
        "is_embargo": false
      }
    ]
  }
}
```

---

## 1.2 `GET /v1/schedules/latest`

**Tier:** T1  
**Description:** Returns the single latest published schedule. Used as the default `schedule_code` by all other endpoints when no schedule is specified.

**Response:** Single schedule object (same shape as 1.1) with `is_latest: true`.

---

## 1.3 `GET /v1/programs`

**Tier:** T1  
**Description:** All PBS programs with their titles. Enriched with dispensing rule associations.

**Join Sources:** `/programs`, `/program-dispensing-rules`, `/dispensing-rules`  
**Join Keys:** `program_code` → `/program-dispensing-rules` → `dispensing_rule_mnem` → `/dispensing-rules`

**Response:**
```json
{
  "data": {
    "programs": [
      {
        "program_code": "string",
        "program_title": "string",
        "dispensing_rules": [
          {
            "dispensing_rule_mnem": "string",
            "dispensing_rule_reference": "string",
            "dispensing_rule_title": "string",
            "is_default": true,
            "community_pharmacy_dispensing": true
          }
        ]
      }
    ]
  }
}
```

---

## 1.4 `GET /v1/dispensing-rules`

**Tier:** T1  
**Description:** All dispensing rule definitions.

**Join Sources:** `/dispensing-rules`  
**Direct passthrough with no additional joins.**

---

## 1.5 `GET /v1/atc-codes`

**Tier:** T1  
**Description:** Full ATC hierarchy flat list. Use `atc_level` and `atc_parent_code` to reconstruct tree.

**Join Sources:** `/atc-codes`

**Query Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `level` | integer | Filter to ATC level 1–5 |
| `parent_code` | string | Return only direct children of this code |

---

## 1.6 `GET /v1/atc-codes/{atc_code}/hierarchy`

**Tier:** T1  
**Description:** Returns a single ATC code with its full ancestry (parents to Level 1) and all direct children. Used for building therapeutic class navigation trees.

**Join Sources:** `/atc-codes` (recursive self-join)

**Primary Join Key:** `atc_parent_code` (self-referencing)

**Resolution Logic:**
```
STEP 1: /atc-codes WHERE atc_code = {input} → get the target node
STEP 2: Walk UP: while atc_parent_code IS NOT NULL
          → fetch parent, add to ancestors[]
          → repeat until atc_level = 1
STEP 3: Walk DOWN: /atc-codes WHERE atc_parent_code = {atc_code}
          → direct children only (not recursive)
STEP 4: Assemble: ancestors (ordered Level1 → target) + target + children
```

**Response:**
```json
{
  "data": {
    "atc_code": "C09A",
    "atc_description": "ACE inhibitors, plain",
    "atc_level": 4,
    "ancestors": [
      { "atc_code": "C", "atc_description": "Cardiovascular system", "atc_level": 1 },
      { "atc_code": "C09", "atc_description": "Agents acting on the renin-angiotensin system", "atc_level": 3 },
      { "atc_code": "C09A", "atc_description": "ACE inhibitors, plain", "atc_level": 4 }
    ],
    "breadcrumb": "C → C09 → C09A",
    "children": [
      { "atc_code": "C09AA", "atc_description": "ACE inhibitors", "atc_level": 5 }
    ],
    "has_children": true,
    "is_leaf": false
  }
}
```

---

## 1.7 `GET /v1/organisations/{organisation_id}`

**Tier:** T1  
**Description:** Single manufacturer/responsible person record.

**Join Sources:** `/organisations`

---

## 1.8 `GET /v1/copayments/{schedule_code}`

**Tier:** T1  
**Description:** All co-payment thresholds and Safety Net amounts for a given schedule. Core financial reference data.

**Join Sources:** `/copayments`

**Response:**
```json
{
  "data": {
    "schedule_code": "string",
    "effective_date": "YYYY-MM-DD",
    "patient_charges": {
      "general_copayment": 31.60,
      "concessional_copayment": 7.70
    },
    "safety_net": {
      "general_threshold": 1579.40,
      "concessional_threshold": 348.90,
      "card_issue_fee": 0.00,
      "ctg_contribution": 7.70
    },
    "discount_limits": {
      "increased_discount_limit": 31.60,
      "general_discount_community": 0.00,
      "general_discount_hospital": 0.00,
      "concessional_discount_community": 0.00,
      "concessional_discount_hospital": 0.00
    }
  }
}
```

---

---

# TIER 2 — CLINICAL ENDPOINTS

---

## 2.1 `GET /v1/drugs/{pbs_code}`

**Tier:** T2  
**Description:** Core drug identity record. Resolves a prescribing rule to its drug name, program, manufacturer, primary ATC classification, restriction level and key policy flags. This is the "what is this drug?" endpoint.

**Join Sources:**
```
/items               → Core identity, flags, dates
/programs            → program_title
/organisations       → manufacturer name and state
/item-atc-relationships → primary ATC (highest atc_priority_pct)
/atc-codes           → ATC description and Level 1 parent
/item-restriction-relationship → benefit_type_code
/prescribers         → prescriber types (count only at this tier)
```

**Primary Join Keys:**
```
pbs_code → /items
program_code → /programs
organisation_id → /organisations
pbs_code → /item-atc-relationships ORDER BY atc_priority_pct DESC LIMIT 1
atc_code → /atc-codes (target + Level 1 ancestor)
pbs_code → /item-restriction-relationship LIMIT 1 (benefit_type_code)
pbs_code → /prescribers (count)
```

**Resolution Logic:**
```
STEP 1: /items WHERE pbs_code = {input} AND schedule_code = {sc}
         → Returns N rows (N brands). Use first row for shared drug-level fields.
         → brand_count = COUNT(DISTINCT li_item_id)

STEP 2: /programs WHERE program_code = {program_code} → program_title

STEP 3: /organisations WHERE organisation_id = {organisation_id}
         → name, state (first brand's manufacturer; note if brands have different manufacturers)

STEP 4: /item-atc-relationships WHERE pbs_code = {pbs_code}
         ORDER BY atc_priority_pct DESC LIMIT 1
         → primary atc_code and atc_priority_pct

STEP 5: /atc-codes WHERE atc_code = {primary_atc_code}
         Walk atc_parent_code until atc_level = 1 → Level 1 body system

STEP 6: /item-restriction-relationship WHERE pbs_code = {pbs_code} LIMIT 1
         → benefit_type_code (U/R/A/S)

STEP 7: /prescribers WHERE pbs_code = {pbs_code}
         → COUNT and list of prescriber_code values
```

**Query Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `schedule_code` | string | Default: latest published |
| `include_brands` | boolean | Default false — if true, embed brand list (use 2.2 for full brand data) |

**Response:**
```json
{
  "data": {
    "pbs_code": "string",
    "schedule_code": "string",
    "schedule_effective_date": "YYYY-MM-DD",
    "drug": {
      "drug_name": "string",
      "li_drug_name": "string",
      "li_form": "string",
      "schedule_form": "string",
      "unit_of_measure": "string",
      "manner_of_administration": "string | null"
    },
    "dispensing": {
      "maximum_prescribable_pack": 1,
      "maximum_quantity_units": 30,
      "number_of_repeats": 5,
      "pack_not_to_be_broken": false
    },
    "program": {
      "program_code": "GE",
      "program_title": "General"
    },
    "manufacturer": {
      "organisation_id": "string",
      "name": "string",
      "state": "string | null",
      "abn": "string | null"
    },
    "classification": {
      "primary_atc_code": "string",
      "primary_atc_description": "string",
      "primary_atc_priority_pct": 100,
      "atc_level1_code": "string",
      "atc_level1_description": "string",
      "has_split_atc": false
    },
    "restriction": {
      "benefit_type_code": "U | R | A | S",
      "benefit_type_label": "string"
    },
    "prescribers": {
      "count": 1,
      "codes": ["M"],
      "types": ["Medical Practitioners"]
    },
    "pricing_summary": {
      "formulary": "F1 | F2 | CDL | null",
      "formulary_label": "string",
      "brand_count": 1,
      "has_brand_premium": false,
      "has_therapeutic_group_premium": false,
      "therapeutic_exemption_applies": false
    },
    "status": {
      "is_active": true,
      "is_supply_only": false,
      "first_listed_date": "YYYY-MM-DD",
      "non_effective_date": null,
      "advanced_notice_date": null,
      "section_19a_expiry_date": null
    },
    "policy_flags": {
      "is_60_day_base": false,
      "is_60_day_item": false,
      "is_biosimilar_uptake": false,
      "is_indigenous_pharmacy": false,
      "is_section100_only": false,
      "is_infusible": false,
      "is_extemporaneous": false,
      "is_continued_dispensing": false
    }
  }
}
```

---

## 2.2 `GET /v1/drugs/{pbs_code}/brands`

**Tier:** T2  
**Description:** All brands (li_item_ids) for a given prescribing rule. Each brand returned with its pack, manufacturer, and pricing summary. Use this to show all available brands for a drug.

**Join Sources:**
```
/items WHERE pbs_code = {input}  → All li_item_ids
/organisations                   → Manufacturer per brand
/item-dispensing-rule-relationships (COM context) → DPMQ per brand
/copayments                      → Co-payment for patient charge computation
```

**Resolution Logic:**
```
STEP 1: /items WHERE pbs_code = {pbs_code} AND schedule_code = {sc}
         → All li_item_id rows

STEP 2: For EACH li_item_id:
         /item-dispensing-rule-relationships WHERE li_item_id = {id}
           AND dispensing_rule context resolves to COM (community)
         → cmnwlth_dsp_price_max_qty, brand_premium, max_general_patient_charge

STEP 3: /organisations for each unique organisation_id → manufacturer name

STEP 4: /copayments WHERE schedule_code = {sc}
         → general, concessional for patient charge resolution

STEP 5: Identify reference_brand = brand with lowest cmnwlth_dsp_price_max_qty
         Compute brand_premium_vs_reference for each brand
```

**Response:**
```json
{
  "data": {
    "pbs_code": "string",
    "drug_name": "string",
    "brand_count": 3,
    "reference_dpmq": 18.45,
    "brands": [
      {
        "li_item_id": "string",
        "brand_name": "string",
        "manufacturer_name": "string",
        "manufacturer_state": "string | null",
        "pack_size": 30,
        "pack_content": 30,
        "pricing_quantity": 30,
        "formulary": "F2",
        "is_originator_brand": false,
        "is_biosimilar_uptake": false,
        "therapeutic_exemption_applies": false,
        "pricing": {
          "dispensed_price_max_qty": 18.45,
          "brand_premium": 0.00,
          "brand_premium_vs_reference": 0.00,
          "general_patient_charge": 18.45,
          "concessional_patient_charge": 7.70,
          "max_general_patient_charge": 18.45
        },
        "status": {
          "is_active": true,
          "is_supply_only": false,
          "supply_only_date": null,
          "non_effective_date": null
        },
        "is_reference_price_brand": true
      }
    ]
  }
}
```

---

## 2.3 `GET /v1/drugs/{pbs_code}/prescribers`

**Tier:** T2  
**Description:** All authorised prescriber types for a drug, enriched with the authority context for each prescriber type.

**Join Sources:**
```
/prescribers                     → All prescriber_code rows
/item-restriction-relationship   → benefit_type_code
/restrictions                    → written_authority_required, assessment_type_code
/items                           → legal_unar_ind, legal_car_ind
```

**Resolution Logic:**
```
STEP 1: /prescribers WHERE pbs_code = {pbs_code} → all prescriber rows

STEP 2: /item-restriction-relationship WHERE pbs_code = {pbs_code}
         → benefit_type_code

STEP 3: /restrictions WHERE res_code IN {res_codes}
         → written_authority_required, assessment_type_code (first R/A/S restriction)

STEP 4: /items WHERE pbs_code = {pbs_code} LIMIT 1
         → legal_unar_ind, legal_car_ind, section100_only_indicator
```

**Response:**
```json
{
  "data": {
    "pbs_code": "string",
    "drug_name": "string",
    "benefit_type_code": "A",
    "benefit_type_label": "Authority Required",
    "requires_authority": true,
    "written_authority_required": false,
    "assessment_type": "IMMEDIATE",
    "authorised_prescribers": [
      {
        "prescriber_code": "M",
        "prescriber_type": "Medical Practitioners",
        "authority_applies_to_this_prescriber": true
      },
      {
        "prescriber_code": "N",
        "prescriber_type": "Nurse Practitioners",
        "authority_applies_to_this_prescriber": true
      }
    ],
    "hsd_flags": {
      "is_unar": false,
      "is_car": false
    },
    "special_programs": {
      "is_section100_only": false,
      "is_prescriber_bag_only": false
    }
  }
}
```

---

## 2.4 `GET /v1/drugs/{pbs_code}/atc`

**Tier:** T2  
**Description:** All ATC classifications for a drug with full hierarchy for each classification. Handles split-ATC drugs (where `atc_priority_pct < 100`).

**Join Sources:**
```
/item-atc-relationships WHERE pbs_code = {input}
/atc-codes WHERE atc_code IN {returned codes}
/atc-codes (recursive) → full hierarchy per code
```

**Resolution Logic:**
```
STEP 1: /item-atc-relationships WHERE pbs_code = {pbs_code}
         ORDER BY atc_priority_pct DESC
         → All ATC codes and percentages for this drug

STEP 2: For EACH atc_code:
         /atc-codes WHERE atc_code = {atc_code}
         Walk atc_parent_code upward until atc_level = 1
         → Full hierarchy for each classification

STEP 3: Flag primary = highest atc_priority_pct
         Flag has_split = COUNT(atc_codes) > 1
```

**Response:**
```json
{
  "data": {
    "pbs_code": "string",
    "drug_name": "string",
    "has_split_atc": false,
    "classifications": [
      {
        "atc_code": "C09AA05",
        "atc_description": "Ramipril",
        "atc_level": 5,
        "priority_pct": 100,
        "is_primary": true,
        "hierarchy": [
          { "level": 1, "atc_code": "C", "description": "Cardiovascular system" },
          { "level": 2, "atc_code": "C09", "description": "Agents acting on the renin-angiotensin system" },
          { "level": 3, "atc_code": "C09A", "description": "ACE inhibitors, plain" },
          { "level": 4, "atc_code": "C09AA", "description": "ACE inhibitors" },
          { "level": 5, "atc_code": "C09AA05", "description": "Ramipril" }
        ],
        "breadcrumb": "C → C09 → C09A → C09AA → C09AA05"
      }
    ]
  }
}
```

---

## 2.5 `GET /v1/drugs/{pbs_code}/amt`

**Tier:** T2  
**Description:** Full AMT concept hierarchy for all brands of a prescribing rule. Returns MP, MPP, MPUU, TPP, TPUU concepts per brand with AMT codes and preferred terms.

**Join Sources:**
```
/items WHERE pbs_code = {input} → All li_item_ids
/amt-items WHERE li_item_id IN {li_item_ids}
```

**Resolution Logic:**
```
STEP 1: /items WHERE pbs_code = {pbs_code}
         → All li_item_ids for this prescribing rule

STEP 2: /amt-items WHERE li_item_id IN {li_item_ids}
         GROUP BY li_item_id, concept_type_code
         → All concept types per brand

STEP 3: For EACH li_item_id:
         Assemble concept map: MP, MPP, MPUU, TPP, TPUU
         Identify primary_clinical_code = MPP amt_code (fallback to TPP)
         Compute is_fully_mapped = all expected concept types have amt_code

STEP 4: Check exempt_ind at MPUU level → is_exempt_from_statutory_reductions
```

**Response:**
```json
{
  "data": {
    "pbs_code": "string",
    "drug_name": "string",
    "brands": [
      {
        "li_item_id": "string",
        "brand_name": "string",
        "primary_clinical_code": "string | null",
        "primary_clinical_term": "string | null",
        "is_fully_amt_mapped": true,
        "is_exempt_from_statutory_reductions": false,
        "concepts": {
          "MP": { "pbs_concept_id": "string", "amt_code": "string | null", "preferred_term": "string | null", "non_amt_code": "string | null", "is_mapped": true },
          "MPP": { "pbs_concept_id": "string", "amt_code": "string | null", "preferred_term": "string | null", "non_amt_code": "string | null", "is_mapped": true },
          "MPUU": { "pbs_concept_id": "string", "amt_code": "string | null", "preferred_term": "string | null", "non_amt_code": "string | null", "is_mapped": true },
          "TPP": { "pbs_concept_id": "string", "amt_code": "string | null", "preferred_term": "string | null", "non_amt_code": "string | null", "is_mapped": true },
          "TPUU": { "pbs_concept_id": "string", "amt_code": "string | null", "preferred_term": "string | null", "non_amt_code": "string | null", "is_mapped": true }
        },
        "fhir": {
          "system": "http://snomed.info/sct",
          "code": "string | null",
          "display": "string | null"
        }
      }
    ]
  }
}
```

---

## 2.6 `GET /v1/items/{li_item_id}`

**Tier:** T2 *(tier-aware — same route as Base `GET /v1/items/{pbs_code}`)*

> **Design decision:** This route is shared between Base and T2+ subscribers. Both tiers use the same path `/v1/items/{id}`. The path parameter is accepted as either a `pbs_code` (prescribing rule) or `li_item_id` (brand+pack) — the server resolves which it is.
>
> - **Base subscribers** receive the raw PBS passthrough response: fields sourced directly from the `/items` endpoint with no joins applied.
> - **T2+ subscribers** receive the enriched response described below: all drug-level identity joins resolved, manufacturer, ATC, program and pricing fields included.
>
> The response `meta.tier` field indicates which version was served. API documentation and SDK clients must clearly communicate this behaviour so Base subscribers are not surprised by the reduced field set.

**Description:** Single brand+pack item with all drug-level identity joins resolved. The item-level equivalent of 2.1.

**Join Sources:** Same as 2.1 but keyed on `li_item_id`, returning single-brand data only.

**Additional fields over 2.1 (T2+ only):**
```json
{
  "li_item_id": "string",
  "brand_name": "string",
  "pack_size": 30,
  "pack_content": 30,
  "pricing_quantity": 30,
  "vial_content": null,
  "claimed_price": 18.45,
  "determined_price": 18.45,
  "proportional_price": null,
  "brand_substitution_group_id": "string | null",
  "therapeutic_group_id": "string | null"
}
```

---

## 2.7 `GET /v1/items/{li_item_id}/price`

**Tier:** T2  
**Description:** Full pricing chain for one item across all applicable dispensing contexts. Resolves the entire markup and fee chain to produce labelled DPMQ components.

**Join Sources:**
```
/items                              → determined_price, claimed_price, formulary, therapeutic_exemption_indicator
/item-dispensing-rule-relationships → All price and fee fields (all contexts)
/dispensing-rules                   → Rule title and community_pharmacy_indicator per context
/fees WHERE program_code            → Schedule fee amounts for labelling
/markup-bands WHERE program_code    → Markup band structure for band identification
/copayments                         → Co-payment for patient charge computation
```

**Resolution Logic:**
```
STEP 1: /items WHERE li_item_id = {input}
         → determined_price, claimed_price, program_code, formulary,
           pack_size, pricing_quantity, therapeutic_exemption_indicator

STEP 2: /item-dispensing-rule-relationships WHERE li_item_id = {li_item_id}
         → May return 1-3 rows (COM, PTE, PUB contexts)
         → GROUP by dispensing_rule_reference

STEP 3: For EACH dispensing_rule_reference:
         /dispensing-rules WHERE dispensing_rule_reference = {ref}
         → dispensing_rule_title, community_pharmacy_indicator
         → Assign context_label based on community_pharmacy_indicator and program

STEP 4: /markup-bands WHERE program_code = {program_code}
                         AND dispensing_rule_mnem = {mnem}
         ORDER BY limit ASC
         → For each context, identify which band applies:
           applied_band = band where price_to_pharmacist >= band.limit
                         AND (next_band.limit > price OR next_band IS NULL)
           calculated_markup = band.fixed + (band.variable/100 × (price - band.offset))

STEP 5: /fees WHERE program_code = {program_code} AND schedule_code = {sc}
         → Retrieve fee schedule for label enrichment

STEP 6: /copayments WHERE schedule_code = {sc}
         → general, concessional → compute patient charges

STEP 7: For EACH context:
         Apply therapeutic_exemption_indicator → zero out premiums if Y
         Compute:
           commonwealth_subsidy_general = DPMQ - MIN(general_copayment, max_general_patient_charge)
           patient_pays_less_than_copayment = DPMQ < general_copayment
```

**Response:**
```json
{
  "data": {
    "li_item_id": "string",
    "pbs_code": "string",
    "drug_name": "string",
    "brand_name": "string",
    "ex_manufacturer": {
      "determined_price": 18.45,
      "claimed_price": 18.45,
      "brand_premium_implied": 0.00,
      "formulary": "F2",
      "pack_size": 30,
      "pricing_quantity": 30
    },
    "pricing_contexts": [
      {
        "context_code": "COM",
        "context_label": "Community Pharmacy",
        "dispensing_rule_reference": "string",
        "dispensing_rule_title": "string",
        "dispense_fee_type": "RP",
        "dispense_fee_type_label": "Ready Prepared",
        "is_dangerous_drug": false,
        "markup_chain": {
          "wholesale": {
            "band_code": "W",
            "band_label": "Wholesale Markup",
            "price_to_pharmacist_input": 18.45,
            "band_limit": 0.00,
            "variable_pct": 7.52,
            "offset": 0.00,
            "fixed": 0.00,
            "calculated_markup": 1.39
          },
          "pharmacy": {
            "band_code": "C",
            "band_label": "Standard 3-Tier Ready Prepared",
            "price_to_pharmacist_input": 19.84,
            "band_limit": 0.00,
            "variable_pct": 0.00,
            "offset": 0.00,
            "fixed": 0.00,
            "calculated_markup": 0.00
          }
        },
        "price_to_pharmacist": {
          "commonwealth_price_to_pharmacist": 19.84,
          "manufacturer_price_to_pharmacist": 18.45
        },
        "fees": {
          "dispensing_fee": 8.15,
          "dangerous_drug_fee": null,
          "safety_net_recording_fee": 0.39,
          "container_fee_injectable": null,
          "container_fee_other": null,
          "extra_allowable_fee": null,
          "chemotherapy_fees": null,
          "acss_fees": null,
          "total_fees": 8.54
        },
        "premiums": {
          "brand_premium": 0.00,
          "therapeutic_group_premium": 0.00,
          "special_patient_contribution": 0.00,
          "total_patient_premium": 0.00,
          "therapeutic_exemption_applies": false
        },
        "final_prices": {
          "dispensed_price_max_qty": 31.60,
          "manufacturer_dispensed_price_max_qty": 29.83,
          "max_general_patient_charge": 31.60,
          "max_record_value_safety_net": 31.60,
          "therapeutic_group_manufacturer_dpmq": null
        },
        "patient_outcome": {
          "general_patient_charge": 31.60,
          "concessional_patient_charge": 7.70,
          "commonwealth_subsidy_general": 0.00,
          "commonwealth_subsidy_concessional": 23.90,
          "patient_pays_less_than_copayment": false
        }
      }
    ],
    "co_payment_reference": {
      "general": 31.60,
      "concessional": 7.70
    }
  }
}
```

---

## 2.8 `GET /v1/items/{li_item_id}/patient-cost`

**Tier:** T2  
**Description:** Patient-oriented simplification of the price endpoint. Answers "what will I pay?" in plain terms. Designed for patient-facing integrations.

**Join Sources:** Subset of 2.7 joins — COM context only.

**Resolution Logic:** Identical to 2.7 but only resolves COM context and surfaces patient-facing computed fields.

**Response:**
```json
{
  "data": {
    "li_item_id": "string",
    "drug_name": "string",
    "brand_name": "string",
    "dispensed_price": 31.60,
    "general_patient": {
      "copayment": 31.60,
      "you_pay": 31.60,
      "brand_premium": 0.00,
      "total_out_of_pocket": 31.60,
      "government_pays": 0.00,
      "counts_toward_safety_net": 31.60,
      "safety_net_threshold": 1579.40,
      "estimated_scripts_to_safety_net": 50
    },
    "concessional_patient": {
      "copayment": 7.70,
      "you_pay": 7.70,
      "brand_premium": 0.00,
      "total_out_of_pocket": 7.70,
      "government_pays": 23.90,
      "counts_toward_safety_net": 7.70,
      "safety_net_threshold": 348.90,
      "estimated_scripts_to_safety_net": 46
    },
    "discount_zone": {
      "eligible_for_pharmacist_discount": false,
      "increased_discount_limit": 31.60
    },
    "premiums_explanation": {
      "any_premium_applies": false,
      "brand_premium_reason": null,
      "therapeutic_group_reason": null,
      "exemption_applies": false
    }
  }
}
```

---

## 2.9 `GET /v1/drugs/{pbs_code}/restrictions`

**Tier:** T2  
**Description:** Index of all restrictions, notes and cautions for a drug. Returns summary-level restriction data — use 2.10 or 3.2 for full text.

**Join Sources:**
```
/item-restriction-relationship WHERE pbs_code = {input}
/restrictions WHERE res_code IN {returned res_codes}
```

**Resolution Logic:**
```
STEP 1: /item-restriction-relationship WHERE pbs_code = {pbs_code}
         → All res_codes, benefit_type_code, restriction_indicator

STEP 2: For EACH res_code:
         /restrictions WHERE res_code = {res_code}
         → authority_method, treatment_of_code, written_authority_required,
           assessment_type_code, first_listing_date, note_indicator, caution_indicator
```

**Response:**
```json
{
  "data": {
    "pbs_code": "string",
    "drug_name": "string",
    "benefit_type_code": "A",
    "benefit_type_label": "Authority Required",
    "restriction_count": 3,
    "note_count": 1,
    "caution_count": 0,
    "restrictions": [
      {
        "res_code": "string",
        "restriction_number": "string",
        "treatment_of_code": "string | null",
        "is_streamlined": false,
        "authority_method": "A",
        "written_authority_required": false,
        "assessment_type": "IMMEDIATE | FULL | null",
        "first_listing_date": "YYYY-MM-DD",
        "has_variation_rule": false,
        "type": "RESTRICTION | NOTE | CAUTION"
      }
    ]
  }
}
```

---

## 2.10 `GET /v1/restrictions/{res_code}`

**Tier:** T2**  
**Description:** Single restriction record with full prescribing text — one level below the full structured restriction (see 3.2 for fully decomposed criteria/parameters).

**Join Sources:**
```
/restrictions WHERE res_code = {input}
/restriction-prescribing-text-relationships WHERE res_code = {input}
/prescribing-texts WHERE prescribing_txt_id IN {returned ids}
```

**Resolution Logic:**
```
STEP 1: /restrictions WHERE res_code = {res_code}
         → Full restriction header record

STEP 2: /restriction-prescribing-text-relationships WHERE res_code = {res_code}
         ORDER BY pt_position ASC
         → Ordered prescribing_text_id list

STEP 3: For EACH prescribing_text_id:
         /prescribing-texts WHERE prescribing_txt_id = {id}
         → prescribing_type, prescribing_txt, assessment_type_code,
           apply_to_increase_mq_flag, apply_to_increase_nr_flag
```

**Response:**
```json
{
  "data": {
    "res_code": "string",
    "restriction_number": "string",
    "treatment_of_code": "string | null",
    "authority_method": "A",
    "written_authority_required": false,
    "assessment_type": "IMMEDIATE",
    "criteria_relationship": "ALL | null",
    "first_listing_date": "YYYY-MM-DD",
    "has_variation_rule": false,
    "full_text_legal": "string",
    "full_text_schedule": "string",
    "prescribing_components": [
      {
        "position": 1,
        "prescribing_txt_id": "string",
        "type": "INDICATION | CRITERIA | PRESCRIBING_INSTRUCTIONS | ADMINISTRATIVE_ADVICE | CAUTION | FORWORD | LEGACY_SCHEDULE_TEXT | LEGACY_LI_TEXT",
        "text": "string",
        "applies_to_increase_max_qty": false,
        "applies_to_increase_repeats": false,
        "is_legal_element": true,
        "assessment_type": "IMMEDIATE | FULL | null"
      }
    ]
  }
}
```

> ** **Note for tiering:** The basic restriction index (2.9) and single restriction text (2.10) are T2. The full structured decomposition into indications, criteria and parameters is T3 (see 3.2). This split allows prescribing software that only needs to display restriction text to remain on T2, while clinical decision support that needs to process structured criteria programmatically requires T3.

---

---

# TIER 3 — INTELLIGENCE ENDPOINTS

---

## 3.1 `GET /v1/drugs/{pbs_code}/full-profile`

**Tier:** T3  
**Description:** The complete drug record — all joins materialised in one response. Equivalent to calling 2.1 + 2.2 + 2.3 + 2.4 + 2.5 + 2.9. Use this when building a drug detail page or a comprehensive drug reference card. Not recommended for bulk operations — use individual endpoints for batch workloads.

**Join Sources:** All sources from 2.1–2.5, 2.9, plus:
```
/item-dispensing-rule-relationships (COM context summary)
/copayments → patient charge summary
```

**Resolution Logic:** Sequential execution of 2.1 → 2.2 → 2.3 → 2.4 → 2.5 → 2.9 join chains. Response assembles all into one object. See individual endpoints for step-by-step logic.

**Response shape:** Composite of all T2 drug endpoints embedded under keyed sections:
```json
{
  "data": {
    "pbs_code": "string",
    "schedule_code": "string",
    "drug": { "...from 2.1 drug fields..." },
    "dispensing": { "...from 2.1 dispensing fields..." },
    "program": { "...from 2.1 program fields..." },
    "manufacturer": { "...from 2.1 manufacturer fields..." },
    "classification": { "...from 2.4 all ATC classifications..." },
    "amt": { "...from 2.5 all concepts, all brands..." },
    "prescribers": { "...from 2.3 authorised prescribers..." },
    "brands": [ "...from 2.2 all brands with pricing..." ],
    "restrictions": { "...from 2.9 restriction index..." },
    "status": { "...from 2.1 status fields..." },
    "policy_flags": { "...from 2.1 policy flags..." },
    "patient_cost_summary": {
      "general_charge": 31.60,
      "concessional_charge": 7.70,
      "dpmq": 31.60,
      "any_premium_applies": false
    }
  }
}
```

**Caching:** Cache aggressively per `pbs_code + schedule_code` — this is expensive to compute.

---

## 3.2 `GET /v1/drugs/{pbs_code}/restriction-full`

**Tier:** T3  
**Description:** Complete restriction chain with fully structured criteria decomposition. Every restriction for the drug, each decomposed to indication + criteria + parameters. The programmatically processable form.

**Join Sources:**
```
/item-restriction-relationship
/restrictions
/restriction-prescribing-text-relationships
/prescribing-texts
/indications WHERE indication_prescribing_txt_id IN {indication type IDs}
/criteria WHERE criteria_prescribing_txt_id IN {criteria type IDs}
/criteria-parameter-relationships WHERE criteria_prescribing_txt_id IN {criteria IDs}
/parameters WHERE parameter_prescribing_txt_id IN {parameter IDs}
/item-prescribing-text-relationships (direct notes/cautions)
```

**Resolution Logic:**
```
STEP 1: /item-restriction-relationship WHERE pbs_code = {pbs_code}
         SPLIT: restriction_indicator Y → restrictions; N → notes/cautions

STEP 2: For EACH res_code (restrictions):
         /restrictions → full header
         /restriction-prescribing-text-relationships ORDER BY pt_position
         → ordered prescribing_text_ids

STEP 3: For EACH prescribing_text_id:
         /prescribing-texts → type and text

         IF type = INDICATION:
           /indications WHERE indication_prescribing_txt_id = {id}
           → condition, episodicity, severity

         IF type = CRITERIA:
           /criteria WHERE criteria_prescribing_txt_id = {id}
           → criteria_type, parameter_relationship
           /criteria-parameter-relationships WHERE criteria_prescribing_txt_id = {id}
            ORDER BY pt_position
           → For EACH parameter_prescribing_txt_id:
             /parameters WHERE parameter_prescribing_txt_id = {id}
             → assessment_type, parameter_type, prescribing_txt

STEP 4: /item-prescribing-text-relationships WHERE pbs_code = {pbs_code}
         → Additional direct Notes and Cautions not via res_code
```

**Response:**
```json
{
  "data": {
    "pbs_code": "string",
    "drug_name": "string",
    "benefit_type_code": "A",
    "restrictions": [
      {
        "res_code": "string",
        "restriction_number": "string",
        "treatment_of_code": "string | null",
        "is_streamlined_authority": false,
        "authority_method": "A",
        "written_authority_required": false,
        "assessment_type": "IMMEDIATE",
        "criteria_relationship": "ALL",
        "has_variation_rule": false,
        "first_listing_date": "YYYY-MM-DD",
        "components": [
          {
            "position": 1,
            "type": "INDICATION",
            "text": "string",
            "indication": {
              "condition": "severe rheumatoid arthritis",
              "episodicity": "chronic",
              "severity": "severe"
            }
          },
          {
            "position": 2,
            "type": "CRITERIA",
            "text": "string",
            "criteria": {
              "criteria_type": "CLINICAL",
              "parameter_relationship": "ANY",
              "parameters": [
                {
                  "position": 1,
                  "parameter_type": "CLINICAL_TREATMENT",
                  "parameter_type_label": "Clinical Treatment",
                  "assessment_type": "IMMEDIATE",
                  "text": "Patient has failed to respond to methotrexate"
                },
                {
                  "position": 2,
                  "parameter_type": "CLINICAL_TREATMENT",
                  "parameter_type_label": "Clinical Treatment",
                  "assessment_type": "IMMEDIATE",
                  "text": "Patient has a contraindication to methotrexate"
                }
              ]
            }
          },
          {
            "position": 3,
            "type": "PRESCRIBING_INSTRUCTIONS",
            "text": "string",
            "applies_to_increase_max_qty": false,
            "applies_to_increase_repeats": false
          }
        ]
      }
    ],
    "notes": [
      { "res_code": "string | null", "text": "string" }
    ],
    "cautions": [
      { "res_code": "string | null", "text": "string" }
    ]
  }
}
```

---

## 3.3 `GET /v1/drugs/{pbs_code}/authority-workflow`

**Tier:** T3  
**Description:** Authority prescribing workflow view. Structures restriction data specifically for the prescribing decision moment — streamlined codes, checklist generation, assessment type labelling. Designed for clinical decision support integration.

**Join Sources:** Same as 3.2, plus `/prescribers` and `/items` for limits.

**Additional computed field — `checklist`:**
```
For each restriction:
  For each CRITERIA component:
    For each PARAMETER:
      checklist_item = {
        item: parameter.text,
        type: derive from parameter_type,
        assessment: parameter.assessment_type,
        is_mandatory: criteria_relationship = ALL
      }
  Add administrative items:
    - If written_authority_required: "Submit via HPOS or post — cannot be approved by phone"
    - If assessment_type = FULL: "Written clinical evidence required for Services Australia"
```

**Response adds over 3.2:**
```json
{
  "authority_summary": {
    "streamlined_code": "string | null",
    "streamlined_instructions": "Write this code on the prescription and self-assess against criteria",
    "requires_services_australia_call": false,
    "requires_written_submission": false,
    "assessment_label": "Streamlined — prescriber self-assesses"
  },
  "prescribing_limits": {
    "maximum_prescribable_pack": 1,
    "number_of_repeats": 5,
    "variation_rule_applies": false
  },
  "checklists": [
    {
      "res_code": "string",
      "treatment_of_code": "string",
      "checklist_items": [
        {
          "item": "Patient has severe rheumatoid arthritis",
          "type": "PATIENT_ELIGIBILITY",
          "assessment": "IMMEDIATE",
          "is_mandatory": true
        }
      ]
    }
  ]
}
```

---

## 3.4 `GET /v1/drugs/{pbs_code}/substitution`

**Tier:** T3  
**Description:** Brand and therapeutic substitution landscape. Returns all brands in the same substitution group AND all therapeutically equivalent drugs (therapeutic group). Includes pricing comparison and premium exposure.

**Join Sources:**
```
/items WHERE pbs_code = {input}              → brand_substitution_group_id, therapeutic_group_id
/items WHERE brand_substitution_group_id =  → All brands in substitution group
/items WHERE therapeutic_group_id =         → All drugs in therapeutic group (may be different pbs_codes)
/item-dispensing-rule-relationships          → DPMQ and premiums per brand
/organisations                               → Manufacturer per brand
/copayments                                  → Co-payment reference
```

**Resolution Logic:**
```
STEP 1: /items WHERE pbs_code = {pbs_code}
         → brand_substitution_group_id, therapeutic_group_id, therapeutic_exemption_indicator

STEP 2: IF brand_substitution_group_id IS NOT NULL:
           /items WHERE brand_substitution_group_id = {group_id} AND schedule_code = {sc}
           → All pharmacist-substitutable brands (may span multiple pbs_codes)

STEP 3: IF therapeutic_group_id IS NOT NULL:
           /items WHERE therapeutic_group_id = {group_id} AND schedule_code = {sc}
           → All therapeutically interchangeable drugs (different active ingredients)

STEP 4: For ALL collected li_item_ids:
           /item-dispensing-rule-relationships COM context → DPMQ, premiums
           /organisations → manufacturer name

STEP 5: Compute:
           reference_dpmq = MIN(DPMQ) across substitution group
           brand_premium_vs_reference = brand DPMQ - reference_dpmq
           is_reference_brand = DPMQ = reference_dpmq
```

See Consumer Offerings Guide Offering 07 for full response shape.

---

## 3.5 `GET /v1/drugs/{pbs_code}/price-history`

**Tier:** T3  
**Description:** DPMQ and AEMP (determined_price) for a drug across all available schedule periods. Enables price trend analysis.

**Join Sources:**
```
/schedules → All 13 schedule_codes ordered by effective_date
/items WHERE pbs_code AND schedule_code IN {all codes} → determined_price, claimed_price per period
/item-dispensing-rule-relationships WHERE pbs_code COM context → DPMQ per period
```

**Resolution Logic:**
```
STEP 1: /schedules → Get all schedule_codes ordered by effective_date ASC

STEP 2: For EACH schedule_code:
         /items WHERE pbs_code = {pbs_code} AND schedule_code = {sc}
         → determined_price, claimed_price, formulary
         → NULL if item not listed in this schedule (new listing or post-delisting gap)

STEP 3: For EACH schedule_code where item exists:
         /item-dispensing-rule-relationships WHERE pbs_code = {pbs_code}
           AND schedule_code = {sc} AND context = COM
         → cmnwlth_dsp_price_max_qty

STEP 4: Compute for EACH period:
         determined_price_change = current.determined_price - previous.determined_price
         determined_price_change_pct = change / previous × 100
         was_listed = item exists in this schedule

STEP 5: /item-pricing-events WHERE pbs_code = {pbs_code}
         → Mark periods that had a formal pricing event
```

**Response:**
```json
{
  "data": {
    "pbs_code": "string",
    "drug_name": "string",
    "formulary": "F2",
    "price_history": [
      {
        "schedule_code": "string",
        "effective_date": "YYYY-MM-DD",
        "was_listed": true,
        "determined_price": 28.40,
        "claimed_price": 28.40,
        "community_dpmq": 31.60,
        "determined_price_change": -3.15,
        "determined_price_change_pct": -9.99,
        "had_pricing_event": true,
        "pricing_event_type": "string | null",
        "pricing_event_pct": -9.99
      }
    ],
    "summary": {
      "earliest_date": "YYYY-MM-DD",
      "latest_date": "YYYY-MM-DD",
      "price_at_earliest": 31.55,
      "price_at_latest": 28.40,
      "cumulative_reduction": -3.15,
      "cumulative_reduction_pct": -9.99,
      "total_reduction_events": 1,
      "periods_listed": 13,
      "periods_not_listed": 0
    }
  }
}
```

---

## 3.6 `GET /v1/drugs/{pbs_code}/pricing-events`

**Tier:** T3  
**Description:** All formal statutory price reduction events for a drug across all available schedules.

**Join Sources:**
```
/items WHERE pbs_code → All li_item_ids
/item-pricing-events WHERE li_item_id IN {ids}
/schedules → effective_date for each schedule_code
```

**Response:**
```json
{
  "data": {
    "pbs_code": "string",
    "drug_name": "string",
    "formulary": "F2",
    "subject_to_statutory_reductions": true,
    "events": [
      {
        "li_item_id": "string",
        "brand_name": "string",
        "schedule_code": "string",
        "effective_date": "YYYY-MM-DD",
        "event_type_code": "string",
        "event_type_label": "string",
        "percentage_applied": -9.99
      }
    ],
    "total_events": 1,
    "last_event_date": "YYYY-MM-DD",
    "last_event_percentage": -9.99
  }
}
```

---

## 3.7 `GET /v1/drugs/{pbs_code}/safety-net`

**Tier:** T3  
**Description:** Safety Net accumulation data for a drug — what counts, what doesn't, how many scripts to threshold.

**Join Sources:**
```
/item-dispensing-rule-relationships → max_record_val_for_safety_net, fee_safety_net_recording
/copayments → general_threshold, concessional_threshold
/items → safety_net_resup_rule_cnt_ind, safety_net_resupply_rule_days
/fees → safety_net_recording_fee_rp, safety_net_recording_fee_ep
```

See Consumer Offerings Guide Offering 12 for full response shape.

---

## 3.8 `GET /v1/drugs/{pbs_code}/60-day-pair`

**Tier:** T3  
**Description:** Returns the base and 60-day quantity paired items with cost comparison for both.

**Join Sources:**
```
/items WHERE policy_applied_imdq60_base_flag = Y OR policy_applied_imdq60_flag = Y
  AND drug name + program match to identify the pair
/item-dispensing-rule-relationships → DPMQ for both
/fees → acss_imdq60_payment, acss_payment
/copayments → patient charge computation
```

See Consumer Offerings Guide Offering 17 for full response shape.

---

## 3.9 `GET /v1/drugs/{pbs_code}/formulary-status`

**Tier:** T3  
**Description:** Formulary classification with full price disclosure trajectory. Answers "is this drug subject to price disclosure, and what has the trajectory been?"

**Join Sources:** Combines 3.5 (price history) + 2.2 (brands) filtered to formulary/originator/generic context.

See Consumer Offerings Guide Offering 08 for full response shape.

---

## 3.10 `GET /v1/items/{li_item_id}/dispensing-context`

**Tier:** T3  
**Description:** Complete dispensing context for one item — full fee schedule, markup bands, dispensing rule, all resolved and labelled. The implementation reference for a dispensing system.

**Join Sources:**
```
/item-dispensing-rule-relationships → all contexts and all fields
/dispensing-rules → titles
/fees WHERE program_code → full fee schedule
/markup-bands WHERE program_code → full markup band structure
/programs → program title
/copayments → patient charge reference
```

**Resolution Logic:** Full implementation of the pricing chain resolution from `item-dispensing-rule-relationships` documented in Consumer Offerings Guide Offering 02. All three contexts (COM, PTE, PUB) returned where applicable.

Response shape: Full version of 2.7 with all markup band tiers included in the response, not just the applied band.

---

## 3.11 `GET /v1/organisations/{organisation_id}/portfolio`

**Tier:** T3  
**Description:** All PBS-listed items for a given manufacturer with pricing and status summary.

**Join Sources:**
```
/organisations WHERE organisation_id = {input}
/items WHERE organisation_id = {organisation_id}
/item-dispensing-rule-relationships COM context → DPMQ per item
/item-atc-relationships → Primary ATC per item
/atc-codes → ATC description
/item-restriction-relationship → benefit_type_code per item
```

**Resolution Logic:**
```
STEP 1: /organisations WHERE organisation_id = {input} → org details

STEP 2: /items WHERE organisation_id = {organisation_id} AND schedule_code = {sc}
         → All li_item_ids + pbs_codes

STEP 3: For EACH li_item_id:
         /item-dispensing-rule-relationships COM context → DPMQ, premiums

STEP 4: For EACH pbs_code (deduplicated):
         /item-atc-relationships ORDER BY atc_priority_pct DESC LIMIT 1
         /atc-codes → primary ATC description
         /item-restriction-relationship LIMIT 1 → benefit_type_code

STEP 5: Aggregate:
         active_count = COUNT WHERE is_active = true
         supply_only_count = COUNT WHERE supply_only_indicator = Y
         f1_count, f2_count by formulary
         atc_classes = DISTINCT Level 1 atc_codes
```

See Consumer Offerings Guide Offering 14 for full response shape.

---

## 3.12 `GET /v1/atc-codes/{atc_code}/items`

**Tier:** T3  
**Description:** All PBS items within an ATC therapeutic class. Resolves the full child subtree and returns all drugs grouped by prescribing rule.

**Join Sources:**
```
/atc-codes (recursive) → all child codes in subtree
/item-atc-relationships WHERE atc_code IN {subtree}
/items WHERE pbs_code IN {returned pbs_codes}
/item-dispensing-rule-relationships COM context → DPMQ per item
/item-restriction-relationship → benefit_type_code per pbs_code
/organisations → manufacturer per item
```

**Resolution Logic:**
```
STEP 1: /atc-codes WHERE atc_code = {input} → target node
         Recursive descent: collect ALL atc_codes where atc_parent_code IN {collected}
         until no more children found

STEP 2: /item-atc-relationships WHERE atc_code IN {full_subtree}
         AND schedule_code = {sc}
         → All pbs_codes mapped to this class

STEP 3: DEDUPLICATE by pbs_code (drug may appear in multiple child ATC codes)
         For split items: include where atc_priority_pct >= 50 (primary ATC)
         Flag split items with has_split_atc = true

STEP 4: For EACH pbs_code:
         /items → all li_item_ids (brands)
         /item-dispensing-rule-relationships COM → DPMQ, premiums per brand
         /item-restriction-relationship → benefit_type_code
         /organisations → manufacturer

STEP 5: Compute per pbs_code:
         lowest_brand_dpmq = MIN(DPMQ)
         highest_brand_dpmq = MAX(DPMQ)
         brand_count = COUNT(active li_item_ids)
```

See Consumer Offerings Guide Offering 06 for full response shape.

---

## 3.13 `GET /v1/programs/{program_code}/fee-structure`

**Tier:** T3  
**Description:** Complete fee and markup structure for a program, with labelled tiers.

**Join Sources:**
```
/programs → program_title
/program-dispensing-rules WHERE program_code
/dispensing-rules → rule details per mnemonic
/fees WHERE program_code AND schedule_code
/markup-bands WHERE program_code AND schedule_code
/copayments → co-payment reference
```

See Consumer Offerings Guide Offering 20 for full response shape.

---

## 3.14 `GET /v1/extemporaneous/{pbs_code}`

**Tier:** T3  
**Description:** Complete extemporaneous preparation record — ingredients, tariff, standard formula, containers and wholesalers.

**Join Sources:**
```
/extemporaneous-preparations WHERE pbs_code
/extemporaneous-ingredients WHERE pbs_code
/extemporaneous-tariffs WHERE pbs_code
/extemporaneous-prep-sfp-relationships WHERE ex_prep_pbs_code = {pbs_code}
/standard-formula-preparations WHERE sfp_pbs_code IN {linked codes}
/containers (applicable to this preparation type)
/container-organisation-relationships → wholesalers per container
/organisations → wholesaler details
/fees WHERE program_code = EP → extemporaneous fee schedule
```

See Consumer Offerings Guide Offering 16 for full response shape.

---

## 3.15 `GET /v1/schedule-changes/{schedule_code}`

**Tier:** T3  
**Description:** Enriched change feed for a schedule. Converts raw SQL diff records into typed, labelled, severity-classified change events with drug name enrichment.

**Join Sources:**
```
/summary-of-changes WHERE schedule_code = {input}
/schedules → current and previous schedule effective dates
/items current schedule → drug_name enrichment for changed items
```

**Resolution Logic:**
```
STEP 1: /schedules → identify current and previous effective dates

STEP 2: /summary-of-changes WHERE schedule_code = {schedule_code}
         → All change records

STEP 3: For EACH change record:
         Parse table_keys to extract li_item_id or pbs_code or res_code
         /items WHERE li_item_id or pbs_code AND schedule_code = current
         → drug_name, brand_name enrichment

STEP 4: Classify change_type:
         new_ind = Y AND changed_table = ITEM_T → NEW_LISTING
         deleted_ind = Y AND changed_table = ITEM_T → DELISTING
         modified_ind = Y AND changed_table = ITEM_DISPENSING_RULE_RLTD_T
           AND cmnwlth_dsp_price_max_qty changed → PRICE_CHANGE
         modified_ind = Y AND changed_table IN (RESTRICTION_TEXT_T, ITEM_RESTRICTION_RLTD_T) → RESTRICTION_CHANGE
         modified_ind = Y AND changed_table = FEE_T → FEE_CHANGE
         modified_ind = Y AND changed_table = COPAYMENT_T → COPAYMENT_CHANGE
         modified_ind = Y AND changed_table = ITEM_T AND formulary changed → FORMULARY_CHANGE
         All others → OTHER_MODIFICATION

STEP 5: Assign severity:
         HIGH: DELISTING, COPAYMENT_CHANGE, price reduction > 10%
         MEDIUM: NEW_LISTING, PRICE_CHANGE, RESTRICTION_CHANGE, FORMULARY_CHANGE
         LOW: OTHER_MODIFICATION, FEE_CHANGE, administrative updates

STEP 6: For PRICE_CHANGE: parse change_detail and previous_detail JSON
         → Extract cmnwlth_dsp_price_max_qty from both
         → Compute delta and percentage
```

See Consumer Offerings Guide Offering 09 for full response shape.

---

## 3.16–3.19 Schedule Change Filters

**Tier:** T3

These four endpoints are pre-filtered views of 3.15 — same join logic, same enrichment, but filtered at the database layer before enrichment to reduce payload:

| Endpoint | Filter Applied |
|---|---|
| `GET /v1/schedule-changes/{schedule_code}/new-listings` | `new_ind = Y AND changed_table = ITEM_T` |
| `GET /v1/schedule-changes/{schedule_code}/delistings` | `deleted_ind = Y AND changed_table = ITEM_T` OR supply_only changes |
| `GET /v1/schedule-changes/{schedule_code}/price-changes` | `modified_ind = Y AND changed_table = ITEM_DISPENSING_RULE_RLTD_T` with price delta |
| `GET /v1/schedule-changes/{schedule_code}/restriction-changes` | `modified_ind = Y AND changed_table IN (RESTRICTION_TEXT_T, ITEM_RESTRICTION_RLTD_T, RSTRCTN_PRSCRBNG_TXT_RLTD_T)` |

Each adds domain-specific enrichment:
- **New listings:** Adds `is_first_in_atc_class` flag (no other items share same ATC Level 5)
- **Delistings:** Adds `therapeutic_alternatives` (other active items in same ATC Level 4)
- **Price changes:** Adds `dpmq_delta`, `dpmq_delta_pct`, `previous_dpmq`, `current_dpmq`
- **Restriction changes:** Adds `previous_restriction_number`, `current_restriction_number`, `treatment_of_code_changed` flag

---

## 3.20 `GET /v1/drugs/search`

**Tier:** T3  
**Description:** Full-text search across drug names, brand names, ATC descriptions and manufacturer names. Returns matching drugs with enough context to identify and select.

**Join Sources:**
```
/items → drug_name, brand_name (search targets)
/atc-codes → atc_description (search target)
/organisations → name (search target)
/item-atc-relationships → primary ATC
/item-restriction-relationship → benefit_type_code
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `q` | string | Yes | Search query (min 3 chars) |
| `search_fields` | string | No | Comma-separated: `drug_name,brand_name,atc,manufacturer` (default: all) |
| `benefit_type` | string | No | Filter: `U,R,A,S` |
| `program_code` | string | No | Filter by program |
| `atc_prefix` | string | No | Filter to ATC class (e.g. `C09`) |
| `is_active` | boolean | No | Default true |
| `limit` | integer | No | Default 20, max 100 |

**Search Resolution:**
```
Priority ranking (descending):
  1. Exact match on drug_name or brand_name
  2. Starts-with match on drug_name or brand_name
  3. Contains match on drug_name or brand_name
  4. Contains match on atc_description
  5. Contains match on manufacturer name

Return TOP limit results across all brands, deduplicated by pbs_code
```

**Response:**
```json
{
  "data": {
    "query": "ramipril",
    "result_count": 12,
    "results": [
      {
        "pbs_code": "string",
        "drug_name": "string",
        "brand_name": "string",
        "li_item_id": "string",
        "program_code": "string",
        "primary_atc_code": "string",
        "primary_atc_description": "string",
        "benefit_type_code": "U",
        "benefit_type_label": "Unrestricted",
        "formulary": "F2",
        "community_dpmq": 18.45,
        "is_active": true,
        "match_field": "drug_name",
        "match_type": "starts_with"
      }
    ]
  }
}
```

---

---

# TIER 4 — MARKET / AGGREGATION ENDPOINTS

> **Note:** These endpoints compute across item sets and require aggregation logic. Implement with materialised view caching (4-hour TTL recommended). All accept `schedule_code` for point-in-time analysis and `compare_schedule_code` for cross-schedule comparison where noted.

---

## 4.1 `GET /v1/market/atc-summary`

**Tier:** T4  
**Description:** Aggregate statistics across all drugs in an ATC class. The market-level view of a therapeutic category.

**Join Sources:**
```
/atc-codes (recursive subtree resolution)
/item-atc-relationships WHERE atc_code IN {subtree}
/items → formulary, status flags, benefit type
/item-dispensing-rule-relationships COM → DPMQ per item
/item-restriction-relationship → benefit_type per pbs_code
/copayments → co-payment reference for patient charge computation
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `atc_code` | string | Yes | ATC code at any level (1–5) |
| `schedule_code` | string | No | Default: latest |
| `include_inactive` | boolean | No | Default false |
| `primary_atc_only` | boolean | No | Default true — exclude split-ATC secondary assignments |

**Response:**
```json
{
  "data": {
    "atc_code": "C09",
    "atc_description": "Agents acting on the renin-angiotensin system",
    "atc_level": 3,
    "includes_children_to_level": 5,
    "schedule_effective_date": "YYYY-MM-DD",
    "item_counts": {
      "unique_prescribing_rules": 48,
      "unique_brands": 127,
      "active_prescribing_rules": 45,
      "supply_only": 2,
      "advance_notice": 1
    },
    "restriction_distribution": {
      "unrestricted": 12,
      "restricted": 18,
      "authority_required": 14,
      "streamlined": 4,
      "pct_requiring_authority": 37.5
    },
    "formulary_distribution": {
      "f1": 8,
      "f2": 36,
      "cdl": 2,
      "not_applicable": 2,
      "pct_f2": 75.0
    },
    "pricing": {
      "min_dpmq": 5.80,
      "max_dpmq": 2847.20,
      "median_dpmq": 31.60,
      "mean_dpmq": 187.45,
      "total_brand_premium_exposure": 145.60,
      "items_with_brand_premium": 8,
      "items_with_therapeutic_group_premium": 3,
      "general_patient_charges": {
        "min": 5.80,
        "max": 31.60,
        "median": 31.60
      }
    },
    "policy_flags": {
      "biosimilar_uptake_items": 4,
      "sixty_day_items": 12,
      "infusible_items": 2,
      "indigenous_pharmacy_items": 0
    },
    "manufacturer_count": 18,
    "program_distribution": {
      "GE": 42,
      "S90": 3,
      "EP": 1,
      "other": 2
    },
    "child_atc_breakdown": [
      {
        "atc_code": "C09A",
        "atc_description": "ACE inhibitors, plain",
        "item_count": 22,
        "brand_count": 58,
        "median_dpmq": 18.45
      }
    ]
  }
}
```

---

## 4.2 `GET /v1/market/price-reduction-events`

**Tier:** T4  
**Description:** All formal price reduction events across a date range or ATC scope. Portfolio-level view of the price disclosure cycle.

**Join Sources:**
```
/schedules → schedule_code to effective_date mapping
/item-pricing-events WHERE schedule_code IN {range}
/items → drug_name, brand_name, formulary, organisation_id
/atc-codes, /item-atc-relationships → ATC context per item
/organisations → manufacturer name
```

**Query Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `from_schedule` | string | Earliest schedule_code to include |
| `to_schedule` | string | Latest schedule_code to include (default: latest) |
| `atc_prefix` | string | Limit to ATC class |
| `organisation_id` | string | Limit to manufacturer |
| `min_pct_change` | decimal | Minimum percentage change (absolute) |
| `sort` | string | `percentage_applied`, `effective_date` (default: `effective_date DESC`) |

**Response:**
```json
{
  "data": {
    "period": {
      "from_date": "YYYY-MM-DD",
      "to_date": "YYYY-MM-DD",
      "schedules_included": 13
    },
    "summary": {
      "total_events": 84,
      "average_reduction_pct": -8.5,
      "largest_reduction_pct": -25.0,
      "items_affected": 61,
      "manufacturers_affected": 14
    },
    "events": [
      {
        "li_item_id": "string",
        "pbs_code": "string",
        "drug_name": "string",
        "brand_name": "string",
        "manufacturer_name": "string",
        "primary_atc_code": "string",
        "primary_atc_description": "string",
        "formulary": "F2",
        "schedule_code": "string",
        "effective_date": "YYYY-MM-DD",
        "event_type_code": "string",
        "percentage_applied": -9.99
      }
    ]
  }
}
```

---

## 4.3 `GET /v1/market/manufacturer-landscape`

**Tier:** T4  
**Description:** Competitive manufacturer analysis across a PBS scope. Market share, portfolio composition, and pricing behaviour by manufacturer.

**Join Sources:**
```
/organisations (all)
/items GROUP BY organisation_id
/item-atc-relationships → ATC scope per manufacturer
/item-dispensing-rule-relationships → pricing aggregates per manufacturer
```

**Query Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `atc_prefix` | string | Limit to ATC therapeutic class |
| `program_code` | string | Limit to program |
| `formulary` | string | F1, F2, CDL |
| `min_item_count` | integer | Only return manufacturers with at least N active items |

**Response:**
```json
{
  "data": {
    "scope": "C09 — Renin-angiotensin system",
    "total_manufacturers": 18,
    "manufacturers": [
      {
        "organisation_id": "string",
        "name": "string",
        "state": "string",
        "portfolio": {
          "active_pbs_codes": 12,
          "active_brands": 18,
          "supply_only": 1,
          "f1_count": 2,
          "f2_count": 10,
          "atc_classes": ["C09A", "C09B"],
          "programs": ["GE"]
        },
        "pricing": {
          "avg_dpmq": 28.45,
          "min_dpmq": 8.20,
          "max_dpmq": 185.40,
          "items_with_brand_premium": 2,
          "avg_brand_premium": 4.20
        },
        "market_position": {
          "is_originator_brands": false,
          "biosimilar_items": 0,
          "pct_of_scope_brands": 14.2
        }
      }
    ]
  }
}
```

---

## 4.4 `GET /v1/market/schedule-comparison`

**Tier:** T4  
**Description:** Aggregate diff between two schedules. Surfaces the macro-level changes: net listings, net delistings, co-payment changes, and average DPMQ movement.

**Join Sources:**
```
/schedules → both schedule metadata
/summary-of-changes WHERE schedule_code = {target}
/items (both schedules) → for counting and pricing
/copayments (both schedules) → co-payment delta
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `base_schedule` | string | Yes | Schedule to compare from |
| `target_schedule` | string | No | Default: latest. Schedule to compare to |
| `atc_prefix` | string | No | Scope to ATC class |

**Response:**
```json
{
  "data": {
    "base_schedule": { "schedule_code": "string", "effective_date": "YYYY-MM-DD" },
    "target_schedule": { "schedule_code": "string", "effective_date": "YYYY-MM-DD" },
    "listings": {
      "new_listings": 12,
      "delistings": 5,
      "supply_only_new": 3,
      "net_change": 7
    },
    "co_payment": {
      "general_base": 31.60,
      "general_target": 31.60,
      "general_changed": false,
      "concessional_base": 7.70,
      "concessional_target": 7.70,
      "concessional_changed": false
    },
    "pricing": {
      "items_with_price_change": 38,
      "avg_dpmq_change": -1.85,
      "avg_dpmq_change_pct": -2.1,
      "largest_reduction": { "drug_name": "string", "dpmq_change_pct": -22.5 },
      "largest_increase": { "drug_name": "string", "dpmq_change_pct": 3.2 }
    },
    "restrictions": {
      "restrictions_changed": 14,
      "new_restrictions_added": 3,
      "restrictions_removed": 1
    },
    "by_atc_level1": [
      {
        "atc_code": "C",
        "atc_description": "Cardiovascular system",
        "new_listings": 3,
        "delistings": 1,
        "price_changes": 12
      }
    ]
  }
}
```

---

## 4.5 `GET /v1/market/formulary-landscape`

**Tier:** T4  
**Description:** Formulary classification distribution across a drug scope. Designed for policy analysis and payer/formulary management.

See earlier strategic discussion for field definitions. Includes: F1/F2/CDL distribution, average brand premium exposure by ATC Level 2, Safety Net burden (MRVSN aggregates), and items currently in price disclosure cycle.

---

## 4.6 `GET /v1/market/biosimilar-landscape`

**Tier:** T4  
**Description:** Originator vs biosimilar analysis across an ATC scope. Designed for pharmaceutical market access and biosimilar uptake policy analysis.

**Key aggregated fields:**
```json
{
  "atc_scope": "L04",
  "molecules": [
    {
      "drug_name": "string",
      "atc_code": "string",
      "originator_dpmq": 4820.00,
      "lowest_biosimilar_dpmq": 2180.00,
      "max_saving_per_script": 2640.00,
      "biosimilar_count": 4,
      "uptake_policy_active": true,
      "all_in_substitution_group": true
    }
  ],
  "summary": {
    "total_molecules_with_biosimilar": 8,
    "total_biosimilar_brands": 22,
    "avg_saving_per_script": 1840.00,
    "molecules_with_uptake_policy": 6
  }
}
```

---

## 4.7 `GET /v1/market/authority-landscape`

**Tier:** T4  
**Description:** Authority type distribution across a drug scope. Designed for health policy and prescribing burden analysis.

**Key aggregated fields:**
```json
{
  "scope": "All active drugs | ATC class",
  "distribution": {
    "unrestricted": { "count": 1240, "pct": 28.4 },
    "restricted": { "count": 890, "pct": 20.4 },
    "authority_required": { "count": 1650, "pct": 37.8 },
    "streamlined": { "count": 582, "pct": 13.4 }
  },
  "authority_subtypes": {
    "written_authority_required": 284,
    "full_assessment_required": 96,
    "car_hsd": 42,
    "unar_hsd": 18
  },
  "by_atc_level1": [...]
}
```

---

## 4.8 `GET /v1/market/safety-net-burden`

**Tier:** T4  
**Description:** Safety Net exposure analysis across a drug scope. Identifies which therapeutic classes create the highest Safety Net accumulation burden for patients.

**Key aggregated fields:**
```json
{
  "atc_scope": "string",
  "by_atc_level2": [
    {
      "atc_code": "C09",
      "atc_description": "string",
      "avg_mrvsn": 31.60,
      "max_mrvsn": 31.60,
      "pct_at_full_copayment": 95.2,
      "avg_scripts_to_general_safety_net": 50,
      "avg_scripts_to_concessional_safety_net": 46
    }
  ]
}
```

---

## 4.9 `GET /v1/market/listings-pipeline`

**Tier:** T4  
**Description:** Forward-looking pipeline of items transitioning to Supply Only or delisting within a specified horizon.

**Join Sources:**
```
/items WHERE advanced_notice_date IS NOT NULL
         OR supply_only_indicator = Y
         OR non_effective_date IS NOT NULL
/item-atc-relationships → ATC per item
/atc-codes → therapeutic alternatives lookup
```

**Query Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `horizon_days` | integer | Items with changes within N days (default: 180) |
| `atc_prefix` | string | Limit to ATC class |
| `status` | string | `supply_only`, `advance_notice`, `both` (default: both) |

**Response:**
```json
{
  "data": {
    "horizon_days": 180,
    "pipeline": [
      {
        "li_item_id": "string",
        "pbs_code": "string",
        "drug_name": "string",
        "brand_name": "string",
        "atc_code": "string",
        "atc_description": "string",
        "status": "SUPPLY_ONLY | ADVANCE_NOTICE",
        "supply_only_date": "YYYY-MM-DD | null",
        "projected_delisting_date": "YYYY-MM-DD | null",
        "days_until_delisting": 84,
        "urgency": "IMMINENT (<30d) | NEAR (<90d) | UPCOMING (<180d)",
        "therapeutic_alternatives": [
          { "pbs_code": "string", "drug_name": "string", "atc_code": "string", "dpmq": 18.45 }
        ]
      }
    ],
    "summary": {
      "total_in_pipeline": 18,
      "imminent_count": 3,
      "near_count": 8,
      "upcoming_count": 7,
      "atc_classes_affected": ["C09", "N06A"]
    }
  }
}
```

---

## 4.10 `GET /v1/market/price-pressure-index`

**Tier:** T4  
**Description:** Computed price pressure signals across all F2 drugs. Identifies which drugs are most likely approaching a statutory price reduction event based on time-since-last-reduction, brand count, and formulary dynamics.

**Join Sources:**
```
/items WHERE formulary = F2 AND is_active = true
/item-pricing-events (all schedules) → time since last event
/items (across schedules) → brand count trend
/item-atc-relationships → ATC context
```

**Computed Score — Price Pressure Index (PPI):**
```
PPI (0–10) = weighted combination of:
  - months_since_last_reduction (higher = more pressure)       weight: 0.35
  - competing_brand_count (more brands = more pressure)        weight: 0.25
  - avg_brand_premium_above_reference (signals pricing gap)    weight: 0.20
  - total_cumulative_reduction (deeper cuts = more pressure)   weight: 0.10
  - originator_still_listed (adds pressure)                    weight: 0.10

Normalised to 0–10 scale. Score ≥ 7 = HIGH pressure signal.
```

**Response:**
```json
{
  "data": {
    "schedule_effective_date": "YYYY-MM-DD",
    "high_pressure_count": 28,
    "items": [
      {
        "pbs_code": "string",
        "drug_name": "string",
        "primary_atc_code": "string",
        "primary_atc_description": "string",
        "formulary": "F2",
        "price_pressure_index": 8.4,
        "pressure_label": "HIGH",
        "signals": {
          "months_since_last_reduction": 18,
          "competing_brand_count": 6,
          "avg_brand_premium_above_reference": 2.40,
          "cumulative_reduction_pct": -35.0,
          "originator_still_listed": true,
          "last_reduction_date": "YYYY-MM-DD"
        },
        "current_dpmq": 18.45,
        "originator_dpmq": 22.80
      }
    ]
  }
}
```

---

---

# Implementation Notes

## Join Execution Order — General Principles

1. **Always resolve `schedule_code` first.** If not provided, call `/schedules` to get latest published `schedule_code`. Pass this to all subsequent calls.

2. **`pbs_code` is the prescribing rule key; `li_item_id` is the brand+pack key.** Know which you have before starting the chain. Most joins go `pbs_code → many li_item_ids` or `li_item_id → one pbs_code`.

3. **ATC hierarchy is recursive.** Walk `atc_parent_code` upward until NULL. Cache the full hierarchy on first load — it changes infrequently and is identical across all queries for a given schedule.

4. **Co-payment data is schedule-scoped but single-row.** One row per `schedule_code`. Fetch once per request chain and reuse across all patient charge computations.

5. **Markup band resolution requires ordered comparison.** Always ORDER BY `limit ASC`. The applied band is where `price_to_pharmacist >= band.limit` AND `price_to_pharmacist < next_band.limit`. Null `next_band.limit` means highest tier.

6. **Therapeutic exemption must be checked before exposing premiums.** If `/items` returns `therapeutic_exemption_indicator = Y`, override `brand_premium`, `therapeutic_group_premium` and `special_patient_contribution` to `0.00` in your response, regardless of raw values.

7. **Restriction join chain order matters.** Always: `item-restriction-relationship` → `restrictions` → `restriction-prescribing-text-relationships` → `prescribing-texts` → (type-conditional) `indications` / `criteria` → `criteria-parameter-relationships` → `parameters`. Never skip levels.

8. **Summary of changes `table_keys` field requires parsing.** It is not consistently typed across PBS API versions. Implement a robust key extractor that handles both JSON-encoded and raw string formats. Test against `ITEM_T`, `COPAYMENT_T` and `RESTRICTION_TEXT_T` changed_table values.

## Known PBS Source Bugs to Handle in Joins

| Bug | Affected Endpoint | Your Mitigation |
|---|---|---|
| `dispense_fee_type_code = NF` for EP items | 2.7, 3.10 | If `extemporaneous_indicator = Y` on parent item AND `dispense_fee_type_code = NF`, override to `EP` in response and add `DISPENSE_FEE_BUG` warning |
| `preferred_term` null when `amt_code` null | 2.5, 3.1 | Fall back to `pbs_preferred_term`. Add `NO_AMT_MAPPING` warning |
| `schedule_html_text` formatting | 2.10, 3.2 | Expose as raw string. Document as "not rendered HTML" in consumer docs |
| `weighted_avg_disclosed_price` deprecated | 3.5, 3.9 | Exclude from responses — do not surface deprecated fields |

## Caching Architecture Recommendation

```
L1: In-process (per API instance)
    → schedules list: 24h TTL
    → ATC full hierarchy: 30d TTL (changes only on new schedule)
    → co-payment values: until next schedule

L2: Distributed cache (Redis / Memcached)
    → Item-level joins: keyed by li_item_id + schedule_code, TTL until next schedule
    → Drug-level joins: keyed by pbs_code + schedule_code, TTL until next schedule
    → Restriction chains: keyed by res_code + schedule_code, TTL until next schedule
    → Aggregation results: keyed by atc_code + schedule_code + filter_hash, 4h TTL

L3: Materialised views (database layer)
    → T4 aggregation endpoints: pre-compute on schedule_code change event
    → Price history: pre-compute across all 13 schedule periods on schedule refresh
    → Manufacturer portfolio: pre-compute per organisation_id per schedule
```

---

*End of PBS Joined API — Endpoint Specification*  
*Built against: PBS API Bible V1 (Dictionary v3.6.5) · Consumer Offerings Guide V1*  
*Tier assignments and join logic to be validated against live PBS API endpoints before production.*

# PBSdata.io — Access Tiers

PBSdata.io uses a five-tier access model. Higher tiers unlock joined, enriched, and aggregated responses that would otherwise require significant PBS data engineering work to reproduce.

---

## Tier Overview

| Tier | Internal Name | Monthly Requests | Per-Minute Burst | History | Webhooks | Target Use Case |
|------|--------------|-----------------|-----------------|---------|----------|-----------------|
| **Base** | `free` | 1,000 | 30/min | 12 months | No | Evaluation / hobby projects |
| **Core** | `starter` | 10,000 | 60/min | Full | Yes | Small apps, internal tools |
| **Clinical** | `growth` | 150,000 | 200/min | Full | Yes | Patient-facing apps, clinical decision support |
| **Intelligence** | `scale` | 1,000,000 | 600/min | Full | Yes | Analytics platforms, formulary tools |
| **Market** | `enterprise` | Unlimited | Unlimited | Full | Yes | Market intelligence, pharma strategy |

---

## What Each Tier Unlocks

### Base — Free

Raw PBS passthrough data. Responses match the underlying PBS API structure with no joins or enrichment.

**Available endpoints:**
- `POST /v1/auth/keys` — provision a free key
- `GET /v1/auth/keys/me` — check your key status
- `GET /v1/health` — health check
- `GET /v1/items/{pbs_code}` — raw item record (no manufacturer, ATC, or prescriber joins)
- `GET /v1/medicines` / `/{medicine_id}`
- `GET /v1/restrictions` / `/{restriction_code}` — raw restriction (no prescribing text join)
- `GET /v1/schedules` — list of schedule months
- `GET /v1/changes` — raw change log
- `GET /v1/programs` — flat program list (no dispensing rules embedded)
- `GET /v1/fees` / `/{fee_code}`
- `GET /v1/prescribing-texts` / `/{id}`
- `GET /v1/indications` / `/{id}`
- `GET /v1/atc-codes` / `/{atc_code}` — ATC record with linked items
- `GET /v1/dispensing-rules` / `/{rule_code}`
- `GET /v1/organisations` / `/{id}`
- `GET /v1/copayments` — copayment thresholds for a schedule
- `GET /v1/containers` / `/{code}`
- `GET /v1/amt` / `/{amt_id}`
- `GET /v1/criteria` / `/{id}`
- `GET /v1/parameters` / `/{id}`
- `GET /v1/prescribers`
- `GET /v1/markup-bands`
- `GET /v1/item-pricing-events`
- `GET /v1/summary-of-changes`
- `GET /v1/standard-formula-preparations` / `/{pbs_code}`

---

### Core — T1 (`starter`)

Adds structured, joined sub-resources. Designed for apps that need clean, pre-joined data without building their own joins. Includes webhook support.

**Adds over Base:**
- `GET /v1/schedules/latest` — latest complete schedule with metadata
- `GET /v1/copayments/current` — structured patient charge object with safety net fields
- `GET /v1/atc-codes/by-level/{level}` — ATC codes filtered by hierarchy level (1–5)
- `GET /v1/atc-codes/{atc_code}/hierarchy` — full ancestor chain + children via recursive CTE
- `GET /v1/atc-codes/{atc_code}/children` — direct children only
- `GET /v1/dispensing-rules/by-program/{program_code}` — rules scoped to a program
- `GET /v1/organisations/search` — name/state search with item counts
- `GET /v1/programs` — now includes embedded dispensing rules per program
- `POST /v1/webhooks` / `GET /v1/webhooks` / `DELETE /v1/webhooks/{id}` — webhook management

---

### Clinical — T2 (`growth`)

Pre-joined clinical drug profiles. This tier is the primary target for clinical decision support, EMR integrations, and patient-facing applications.

**Adds over Core:**
- `GET /v1/items/{pbs_code}` — now returns enriched envelope with manufacturer, ATC, program title, and prescribers joined in
- `GET /v1/items/{pbs_code}/price` — full pricing context per dispensing rule with fee breakdown
- `GET /v1/items/{pbs_code}/patient-cost` — patient-facing cost with safety net estimates
- `GET /v1/restrictions/{restriction_code}` — now includes prescribing text components joined in
- `GET /v1/drugs/{pbs_code}` — composite drug identity: ingredient, program, manufacturer, ATC, restriction summary, pricing
- `GET /v1/drugs/{pbs_code}/brands` — all brands/li_item_ids for a PBS code with pricing
- `GET /v1/drugs/{pbs_code}/prescribers` — authorised prescriber types with authority context
- `GET /v1/drugs/{pbs_code}/atc` — all ATC classifications with full hierarchy breadcrumbs
- `GET /v1/drugs/{pbs_code}/amt` — AMT concept map grouped by concept type
- `GET /v1/drugs/{pbs_code}/restrictions` — full restriction index for a drug

> **Note on tier-aware enrichment:** `/items/{pbs_code}` and `/restrictions/{restriction_code}` serve different response shapes depending on tier. Base subscribers receive the raw record; T2+ receive an enriched `{"data": {...}, "meta": {...}}` envelope. The `meta.tier` field always indicates which version was served.

---

### Intelligence — T3 (`scale`)

Analytical and workflow endpoints. Designed for formulary management tools, dispensing decision engines, and PBS analytics platforms.

**Adds over Clinical:**

**Drug intelligence:**
- `GET /v1/drugs/search` — full-text search by ingredient, brand name, or PBS code with filters
- `GET /v1/drugs/{pbs_code}/full-profile` — single response with all T2 drug data combined
- `GET /v1/drugs/{pbs_code}/restriction-full` — each restriction with its prescribing text components decomposed
- `GET /v1/drugs/{pbs_code}/authority-workflow` — per-restriction authority checklists (streamlined, telephone, written, complex)
- `GET /v1/drugs/{pbs_code}/substitution` — same-ingredient substitutes
- `GET /v1/drugs/{pbs_code}/price-history` — price snapshots across up to 36 schedule months
- `GET /v1/drugs/{pbs_code}/pricing-events` — discrete pricing events (additions, reductions, deletions)
- `GET /v1/drugs/{pbs_code}/safety-net` — patient safety net position with estimated scripts-to-threshold
- `GET /v1/drugs/{pbs_code}/60-day-pair` — 60-day dispensing eligibility and same-ingredient pairs
- `GET /v1/drugs/{pbs_code}/formulary-status` — formulary classification (F1/F2/F3) with recent pricing events

**Item-level:**
- `GET /v1/items/{li_item_id}/dispensing-context` — full markup band resolution + fee breakdown per dispensing rule

**Organisation:**
- `GET /v1/organisations/{id}/portfolio` — manufacturer's full PBS portfolio with pricing, filterable by benefit type

**Classification:**
- `GET /v1/atc-codes/{atc_code}/items` — all drugs in an ATC class, with optional recursive descendant expansion

**Program:**
- `GET /v1/programs/{program_code}/fee-structure` — dispensing rules, markup bands, and schedule fees for a program

**Extemporaneous:**
- `GET /v1/extemporaneous/{pbs_code}` — combined ingredient, tariff, and preparation data
- `GET /v1/extemporaneous/ingredients` — paginated ingredient price list
- `GET /v1/extemporaneous/tariffs` — paginated tariff list with recommended prices
- `GET /v1/extemporaneous/preparations` — paginated preparation list with linked SFP codes

**Schedule changes:**
- `GET /v1/schedule-changes` — full change log with filters for type, PBS code, section
- `GET /v1/schedule-changes/additions` — new listings only
- `GET /v1/schedule-changes/deletions` — removed listings only
- `GET /v1/schedule-changes/price-changes` — price movements only
- `GET /v1/schedule-changes/restriction-changes` — restriction amendments only
- `GET /v1/schedule-changes/{schedule_code}` — complete change summary for a specific schedule month

---

### Market — T4 (`enterprise`)

Aggregated market intelligence across the full PBS scope. Designed for pharmaceutical market research, competitive analysis, and pricing strategy teams.

**Adds over Intelligence:**
- `GET /v1/market/atc-summary` — aggregate statistics (item counts, authority rates, pricing distribution, manufacturer count) across an ATC class and its recursive descendants
- `GET /v1/market/price-reduction-events` — all price reduction events across a schedule range with ATC/organisation filters
- `GET /v1/market/manufacturer-landscape` — competitive manufacturer analysis: portfolio size, formulary mix, pricing stats per sponsor
- `GET /v1/market/schedule-comparison` — aggregate diff between two schedule months: additions, removals, price changes, copayment diff
- `GET /v1/market/formulary-landscape` — formulary classification distribution (F1/F2/None) across a drug scope with pricing context
- `GET /v1/market/biosimilar-landscape` — biosimilar vs originator analysis per ingredient: pricing delta, count comparison
- `GET /v1/market/authority-landscape` — benefit type and authority method distribution across a drug scope
- `GET /v1/market/safety-net-burden` — estimated scripts-to-safety-net threshold per item across an ATC scope
- `GET /v1/market/listings-pipeline` — upcoming delistings and additions from summary of changes
- `GET /v1/market/price-pressure-index` — composite price pressure signal for F2 drugs (brand premium prevalence, recent reductions, DPMQ ratio)

Contact [hello@pbsdata.io](mailto:hello@pbsdata.io) for enterprise access.

---

## Rate Limits

All responses include rate limit headers:

```
X-RateLimit-Limit: 10000
X-RateLimit-Remaining: 9847
X-RateLimit-Reset: 1748822400
X-RateLimit-Burst-Limit: 60
X-RateLimit-Burst-Remaining: 59
```

Exceeding the per-minute burst limit returns `429 Too Many Requests` with `code: BURST_LIMIT_EXCEEDED` and a `Retry-After` header (seconds until the window resets). Exceeding the monthly quota returns `429` with `code: RATE_LIMIT_EXCEEDED`.

---

## Tier Enforcement

Tier checks happen at the dependency layer before any database query runs:

- **Route-level gating** — endpoints decorated with `require_tier("growth")` return `403 TIER_INSUFFICIENT` immediately if the caller's tier is below the minimum.
- **In-handler branching** — some endpoints (e.g. `/items/{pbs_code}`, `/restrictions/{restriction_code}`) serve progressively richer responses based on tier without a hard block.

A `403` response always includes upgrade information:

```json
{
  "code": "TIER_INSUFFICIENT",
  "message": "This endpoint requires T2 (Clinical) or above.",
  "required_tier": "T2",
  "your_tier": "Base",
  "upgrade_url": "https://pbsdata.io/pricing"
}
```

---

## History Access

The `?schedule=YYYY-MM` parameter is available on most endpoints. How far back you can query depends on your tier:

| Tier | `history_months_limit` |
|------|----------------------|
| Base | 12 months |
| Core | Full (unlimited) |
| Clinical | Full (unlimited) |
| Intelligence | Full (unlimited) |
| Market | Full (unlimited) |

Requests for schedules outside your history window return `403 HISTORY_LIMIT_EXCEEDED`.

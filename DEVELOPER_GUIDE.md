# PBSdata.io — Developer Guide

The PBSdata.io API provides clean, authenticated REST access to the Australian PBS (Pharmaceutical Benefits Scheme) drug schedule. It ingests the monthly PBS release, normalises and diffs it, and exposes it as a typed JSON API with change tracking, webhooks, and full historical access.

Interactive API docs (Swagger UI) are available at `https://api.pbsdata.io/docs`.

---

## Contents

- [Getting started](#getting-started)
- [Authentication](#authentication)
- [Tiers and limits](#tiers-and-limits)
- [Rate limiting](#rate-limiting)
- [Pagination](#pagination)
- [Schedule versioning](#schedule-versioning)
- [Errors](#errors)
- [Endpoint reference](#endpoint-reference)
  - [Auth](#auth)
  - [Schedules](#schedules)
  - [Medicines](#medicines)
  - [Items](#items)
  - [Restrictions](#restrictions)
  - [Changes](#changes)
  - [Summary of changes](#summary-of-changes)
  - [Fees](#fees)
  - [Copayments](#copayments)
  - [Organisations](#organisations)
  - [Programs](#programs)
  - [ATC codes](#atc-codes)
  - [AMT](#amt)
  - [Indications](#indications)
  - [Prescribing texts](#prescribing-texts)
  - [Dispensing rules](#dispensing-rules)
  - [Webhooks](#webhooks)
- [Webhook signing](#webhook-signing)
- [Health check](#health-check)

---

## Getting started

### 1. Get a free API key

```bash
curl -X POST https://api.pbsdata.io/v1/auth/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "My App", "email": "you@example.com"}'
```

```json
{
  "key": "pbslive_abc123...",
  "key_prefix": "pbslive_abc",
  "tier": "free",
  "monthly_limit": 500,
  "history_months_limit": 1,
  "message": "Store this key securely — it will not be shown again."
}
```

The full key is shown once. Store it in an environment variable.

### 2. Make your first request

```bash
curl https://api.pbsdata.io/v1/schedules \
  -H "X-API-Key: pbslive_abc123..."
```

### 3. Search for a medicine

```bash
curl "https://api.pbsdata.io/v1/medicines?q=metformin" \
  -H "X-API-Key: pbslive_abc123..."
```

### 4. Get a specific PBS item

```bash
curl https://api.pbsdata.io/v1/items/02647H \
  -H "X-API-Key: pbslive_abc123..."
```

---

## Authentication

All requests require an `X-API-Key` header.

```
X-API-Key: pbslive_abc123...
```

Requests without a valid key return `401 Unauthorized`.

---

## Tiers and limits

| Tier | Monthly requests | History access | Webhooks | Price |
|---|---|---|---|---|
| `free` | 500 | Current month only | No | $0 |
| `starter` | 10,000 | 3 months | No | $49/mo |
| `growth` | 100,000 | 12 months | Yes | $199/mo |
| `scale` | 500,000 | Full history | Yes | $499/mo |
| `enterprise` | Unlimited | Full history | Yes | Custom |

To upgrade your key to a paid tier, contact support or use the billing portal. The `?schedule=` parameter respects your tier's history limit — requests for older schedules return `403 HISTORY_LIMIT_EXCEEDED`.

---

## Rate limiting

Every response includes rate limit headers:

| Header | Description |
|---|---|
| `X-RateLimit-Limit` | Requests allowed per minute |
| `X-RateLimit-Remaining` | Requests remaining in the current window |
| `X-RateLimit-Reset` | Unix timestamp when the window resets |

When the per-minute limit is exceeded the API returns `429 Too Many Requests`. Back off and retry after `X-RateLimit-Reset`.

Per-minute limits by tier: free 10, starter 120, growth 600, scale 600, enterprise custom.

---

## Pagination

Endpoints that return lists accept `page` and `limit` query parameters.

| Parameter | Default | Max |
|---|---|---|
| `page` | `1` | — |
| `limit` | `50` | `200` |

Paginated responses include a `meta` object:

```json
{
  "data": [...],
  "meta": {
    "total": 13813,
    "page": 1,
    "limit": 50
  }
}
```

---

## Schedule versioning

Most endpoints accept an optional `?schedule=YYYY-MM` query parameter. When omitted, the latest complete schedule is used. Pass a month to query any historical schedule your tier allows.

```bash
# Latest schedule (default)
GET /v1/items/02647H

# April 2026 schedule
GET /v1/items/02647H?schedule=2026-04
```

Available schedules and their metadata are listed at `GET /v1/schedules`.

---

## Errors

All errors use a consistent shape:

```json
{
  "detail": {
    "code": "NOT_FOUND",
    "message": "Item not found."
  }
}
```

| HTTP status | Code | Meaning |
|---|---|---|
| `400` | `BAD_REQUEST` | Malformed request |
| `401` | `UNAUTHORIZED` | Missing or invalid API key |
| `403` | `TIER_REQUIRED` | Feature not available on your tier |
| `403` | `HISTORY_LIMIT_EXCEEDED` | Requested schedule is outside your history limit |
| `404` | `NOT_FOUND` | Resource not found |
| `422` | `INVALID_URL` / `INVALID_EVENT` | Validation error |
| `429` | `RATE_LIMIT_EXCEEDED` | Too many requests |

---

## Endpoint reference

### Auth

#### `POST /v1/auth/keys`

Create a free-tier API key. Up to 3 active keys per email address. The full key is returned once — it cannot be retrieved again.

**Request body**

| Field | Type | Required |
|---|---|---|
| `name` | string | Yes |
| `email` | string (email) | Yes |

**Response `201`**

```json
{
  "key": "pbslive_abc123...",
  "key_prefix": "pbslive_abc",
  "tier": "free",
  "monthly_limit": 500,
  "history_months_limit": 1,
  "message": "Store this key securely — it will not be shown again."
}
```

---

#### `GET /v1/auth/keys/me`

Return metadata for the currently authenticated key. Useful for checking usage and limits.

**Response `200`**

```json
{
  "tier": "starter",
  "monthly_limit": 10000,
  "requests_this_month": 342,
  "history_months_limit": 3,
  "usage_reset_at": "2026-05-01T00:00:00",
  "is_active": true
}
```

---

#### `DELETE /v1/auth/keys/me`

Revoke the authenticated key immediately. Returns `204 No Content`.

---

### Schedules

#### `GET /v1/schedules`

List all ingested PBS schedule months, newest first.

**Response `200`**

```json
{
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "month": "2026-04",
      "released_at": "2026-04-01T00:00:00",
      "is_embargo": false,
      "item_count": 13813,
      "change_count": 47,
      "ingest_status": "complete"
    }
  ],
  "meta": {"total": 12}
}
```

---

### Medicines

A **medicine** is a unique active ingredient (e.g., metformin). Multiple PBS **items** (packs, strengths, forms) belong to each medicine.

#### `GET /v1/medicines`

List and search medicines. Fuzzy search (`?q=`) matches on ingredient name and brand name using trigram similarity.

**Query parameters**

| Parameter | Type | Description |
|---|---|---|
| `q` | string | Fuzzy search on ingredient or brand name |
| `sixty_day` | boolean | Filter to 60-day eligible items only |
| `schedule` | string | Schedule month `YYYY-MM`, defaults to latest |
| `page` | integer | Page number (default 1) |
| `limit` | integer | Results per page (default 50, max 200) |

**Response `200`**

```json
{
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "ingredient": "Metformin hydrochloride",
      "ingredient_lower": "metformin hydrochloride",
      "atc_code": "A10BA02",
      "therapeutic_group": "Alimentary tract and metabolism",
      "therapeutic_subgroup": "Blood glucose lowering drugs, excl. insulins"
    }
  ],
  "meta": {"total": 3, "page": 1, "limit": 50}
}
```

---

#### `GET /v1/medicines/{medicine_id}`

Get a single medicine by its UUID, including all PBS items from the requested schedule.

**Path parameters**

| Parameter | Description |
|---|---|
| `medicine_id` | UUID of the medicine |

**Query parameters**

| Parameter | Description |
|---|---|
| `schedule` | Schedule month `YYYY-MM`, defaults to latest |

**Response `200`**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "ingredient": "Metformin hydrochloride",
  "ingredient_lower": "metformin hydrochloride",
  "atc_code": "A10BA02",
  "therapeutic_group": "Alimentary tract and metabolism",
  "therapeutic_subgroup": "Blood glucose lowering drugs, excl. insulins",
  "items": [
    {
      "id": "660e8400-...",
      "pbs_code": "02647H",
      "brand_name": "Glucophage",
      "form": "Tablet",
      "strength": "500 mg",
      "pack_size": 100,
      "pack_unit": "tablet",
      "benefit_type": "R",
      "general_charge": 30.70,
      "concessional_charge": 7.70,
      "government_price": 14.59,
      "brand_premium": 16.11,
      "brand_premium_counts_to_safety_net": false,
      "sixty_day_eligible": true,
      "max_quantity": 1,
      "max_repeats": 5,
      "dangerous_drug": false
    }
  ]
}
```

---

### Items

An **item** is a specific PBS-listed product identified by a PBS code (e.g., `02647H`). Each item belongs to one medicine and one schedule.

#### `GET /v1/items/{pbs_code}`

Get full details for a PBS item including all restrictions.

**Path parameters**

| Parameter | Description |
|---|---|
| `pbs_code` | PBS item code (case-insensitive) |

**Query parameters**

| Parameter | Description |
|---|---|
| `schedule` | Schedule month `YYYY-MM`, defaults to latest |

**Response `200`**

```json
{
  "id": "660e8400-...",
  "pbs_code": "02647H",
  "brand_name": "Glucophage",
  "form": "Tablet",
  "strength": "500 mg",
  "pack_size": 100,
  "pack_unit": "tablet",
  "benefit_type": "R",
  "general_charge": 30.70,
  "concessional_charge": 7.70,
  "government_price": 14.59,
  "brand_premium": 16.11,
  "brand_premium_counts_to_safety_net": false,
  "sixty_day_eligible": true,
  "max_quantity": 1,
  "max_repeats": 5,
  "dangerous_drug": false,
  "formulary": null,
  "section": "85",
  "program_code": "GE",
  "artg_id": "12345",
  "sponsor": "Merck Serono Australia",
  "caution": null,
  "biosimilar": false,
  "ingredient": "Metformin hydrochloride",
  "ingredient_lower": "metformin hydrochloride",
  "atc_code": "A10BA02",
  "restrictions": [
    {
      "streamlined_code": "4500",
      "indication": "Type 2 diabetes mellitus",
      "restriction_text": "Patient must have type 2 diabetes mellitus.",
      "prescriber_type": "GP",
      "authority_required": false,
      "continuation_only": false
    }
  ]
}
```

---

#### `GET /v1/items/{pbs_code}/prescribing-texts`

Return all prescribing texts linked to a PBS item.

**Query parameters**: `schedule`

**Response `200`**

```json
{
  "data": [
    {
      "prescribing_text_id": "PT00123",
      "text_type": "Indication",
      "complex_authority_required": false,
      "prescribing_txt": "Patient must have a confirmed diagnosis of type 2 diabetes mellitus."
    }
  ],
  "meta": {"total": 1}
}
```

---

#### `GET /v1/items/{pbs_code}/dispensing-rules`

Return dispensing rules applicable to a PBS item.

**Query parameters**: `schedule`

**Response `200`**

```json
{
  "data": [
    {
      "program_code": "GE",
      "rule_code": "DR001",
      "dispensing_quantity": 100,
      "dispensing_unit": "tablet",
      "repeats_allowed": 5,
      "description": "Standard supply"
    }
  ],
  "meta": {"total": 1}
}
```

---

### Restrictions

Restrictions define the clinical criteria under which a PBS item may be prescribed. Authority-required items need the prescriber to satisfy these criteria before the PBS subsidy applies.

#### `GET /v1/restrictions`

List restrictions with optional filters.

**Query parameters**

| Parameter | Type | Description |
|---|---|---|
| `schedule` | string | Schedule month, defaults to latest |
| `pbs_code` | string | Filter to a specific item |
| `restriction_type` | string | e.g. `Restricted Benefit`, `Authority Required` |
| `authority_required` | boolean | Filter to authority-required items |
| `streamlined_code` | string | Filter by streamlined authority code |
| `page` | integer | |
| `limit` | integer | Max 200 |

**Response `200`**

```json
{
  "data": [
    {
      "id": "770e8400-...",
      "pbs_code": "02647H",
      "restriction_code": "2179",
      "streamlined_code": "4500",
      "restriction_type": "Restricted Benefit",
      "indication": "Type 2 diabetes mellitus",
      "restriction_text": "Patient must have type 2 diabetes mellitus.",
      "prescriber_type": "GP",
      "authority_required": false,
      "continuation_only": false,
      "clinical_criteria": null,
      "treatment_phase": null,
      "authority_method": null,
      "treatment_of_code": null,
      "written_authority_required": false,
      "complex_authority_required": false,
      "li_html_text": null
    }
  ],
  "meta": {"total": 45842, "page": 1, "limit": 50}
}
```

---

#### `GET /v1/restrictions/{restriction_code}`

Get a single restriction by its PBS restriction code.

**Query parameters**: `schedule`

---

### Changes

The `changes` endpoint surfaces **field-level diffs** computed by the ingest pipeline. Every time a PBS item changes between schedules, a change record captures what field changed, from what value to what value.

#### `GET /v1/changes`

**Query parameters**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `since` | string `YYYY-MM` | Yes | Return changes from schedules on or after this month |
| `until` | string `YYYY-MM` | No | Return changes up to and including this month |
| `change_type` | string | No | `ADDED`, `REMOVED`, `MODIFIED` |
| `page` | integer | No | |
| `limit` | integer | No | Max 200 |

**Response `200`**

```json
{
  "data": [
    {
      "id": "880e8400-...",
      "pbs_code": "02647H",
      "change_type": "MODIFIED",
      "field_name": "general_charge",
      "old_value": "29.50",
      "new_value": "30.70",
      "created_at": "2026-04-01T00:00:00",
      "month": "2026-04"
    }
  ],
  "meta": {"total": 47, "page": 1, "limit": 50}
}
```

---

### Summary of changes

The `summary-of-changes` endpoint surfaces the **official PBS change notices** published by the Department of Health each month. These are human-readable descriptions of what changed in each schedule release.

#### `GET /v1/summary-of-changes`

**Query parameters**

| Parameter | Type | Description |
|---|---|---|
| `schedule` | string | Filter to a specific schedule month |
| `since` | string | Changes from schedules on or after this month |
| `pbs_code` | string | Filter to a specific item |
| `change_type` | string | `NEW`, `DELETE`, `AMENDED`, `PRICE_CHANGE` |
| `page` | integer | |
| `limit` | integer | Max 200 |

**Response `200`**

```json
{
  "data": [
    {
      "pbs_code": "02647H",
      "change_type": "AMENDED",
      "effective_date": "2026-04-01",
      "description": "Restriction amended — additional clinical criteria added.",
      "section": "85",
      "schedule_month": "2026-04"
    }
  ],
  "meta": {"total": 23116, "page": 1, "limit": 50}
}
```

> **Changes vs Summary of Changes:** `GET /v1/changes` is computed by the ingest pipeline and tracks field-level diffs (e.g., price changed from $X to $Y). `GET /v1/summary-of-changes` is the official government changelog with human-readable descriptions and change types as the Department of Health publishes them.

---

### Fees

PBS dispensing fees for the current or any historical schedule.

#### `GET /v1/fees`

**Query parameters**

| Parameter | Description |
|---|---|
| `schedule` | Schedule month, defaults to latest |
| `fee_type` | Filter by fee type |

**Response `200`**

```json
{
  "data": [
    {
      "fee_code": "D1",
      "fee_type": "Dispensing",
      "description": "Standard dispensing fee",
      "amount": 8.29,
      "patient_contribution": null
    }
  ],
  "meta": {"total": 12}
}
```

---

#### `GET /v1/fees/{fee_code}`

Get a single fee by code. **Query parameters**: `schedule`.

---

### Copayments

Current PBS copayment thresholds including safety net values.

#### `GET /v1/copayments`

**Query parameters**: `schedule`

**Response `200`**

```json
{
  "month": "2026-04",
  "general": 30.70,
  "concessional": 7.70,
  "safety_net_general": 1748.20,
  "safety_net_concessional": 277.20,
  "safety_net_card_issue": 5,
  "increased_discount_limit": null,
  "safety_net_ctg_contribution": null
}
```

---

### Organisations

PBS-approved suppliers and organisations linked to specific items (e.g., Section 100 suppliers).

#### `GET /v1/organisations`

**Query parameters**

| Parameter | Description |
|---|---|
| `schedule` | Schedule month, defaults to latest |
| `state` | Filter by state code (e.g., `NSW`, `VIC`) |

**Response `200`**

```json
{
  "data": [
    {
      "organisation_id": 10023,
      "name": "Melbourne Specialty Pharmacy",
      "abn": "12345678901",
      "street_address": "1 Collins St",
      "city": "Melbourne",
      "state": "VIC",
      "postcode": "3000"
    }
  ],
  "meta": {"total": 413}
}
```

---

#### `GET /v1/organisations/{organisation_id}`

Returns the organisation plus a `linked_items` array of PBS codes associated with it.

**Query parameters**: `schedule`

---

### Programs

PBS programs define the supply and prescribing channel for items (e.g., General Schedule, Section 100, Highly Specialised Drugs).

#### `GET /v1/programs`

**Query parameters**: `schedule`

**Response `200`**

```json
{
  "data": [
    {"program_code": "GE", "program_title": "General Schedule"},
    {"program_code": "HSD", "program_title": "Highly Specialised Drugs"},
    {"program_code": "S100", "program_title": "Section 100 Supply"}
  ],
  "meta": {"total": 17}
}
```

---

#### `GET /v1/programs/{program_code}`

Returns the program plus its `dispensing_rules` array.

**Query parameters**: `schedule`

---

### ATC codes

The ATC (Anatomical Therapeutic Chemical) classification hierarchy. Codes range from level 1 (anatomical group) to level 5 (chemical substance).

#### `GET /v1/atc-codes`

**Query parameters**

| Parameter | Type | Description |
|---|---|---|
| `schedule` | string | Schedule month, defaults to latest |
| `level` | integer | Filter to a specific ATC level (1–5) |
| `parent_code` | string | Return direct children of this ATC code |

**Response `200`**

```json
{
  "data": [
    {
      "atc_code": "A10BA02",
      "atc_description": "Metformin",
      "atc_level": 5,
      "atc_parent_code": "A10BA"
    }
  ],
  "meta": {"total": 7891}
}
```

---

#### `GET /v1/atc-codes/{atc_code}`

Returns the ATC code entry plus `children` (direct child codes in the hierarchy) and `linked_items` (PBS codes mapped to this ATC code).

**Query parameters**: `schedule`

---

### AMT

The Australian Medicines Terminology (AMT) provides a standardised clinical vocabulary for medicines. AMT concepts are linked to PBS items.

#### `GET /v1/amt`

**Query parameters**

| Parameter | Description |
|---|---|
| `schedule` | Schedule month, defaults to latest |
| `atc_code` | Filter by ATC code |
| `concept_type` | AMT concept type (e.g., `CTPP`, `TPP`, `MPP`) |
| `page` | |
| `limit` | Max 200 |

**Response `200`**

```json
{
  "data": [
    {
      "amt_id": "9385011000036107",
      "concept_type": "CTPP",
      "preferred_term": "Metformin hydrochloride 500 mg tablet, 100",
      "atc_code": "A10BA02",
      "parent_amt_id": "9385001000036105"
    }
  ],
  "meta": {"total": 8234, "page": 1, "limit": 50}
}
```

---

#### `GET /v1/amt/{amt_id}`

Returns the AMT concept plus `linked_items` — PBS codes linked to this AMT concept and the relationship type.

**Query parameters**: `schedule`

---

### Indications

Clinical indications associated with PBS items, as published in the PBS schedule.

#### `GET /v1/indications`

**Query parameters**

| Parameter | Description |
|---|---|
| `schedule` | Schedule month, defaults to latest |
| `pbs_code` | Filter to a specific item |
| `page` | |
| `limit` | Max 200 |

**Response `200`**

```json
{
  "data": [
    {
      "indication_id": "IND00456",
      "pbs_code": "02647H",
      "indication_text": "Type 2 diabetes mellitus",
      "condition_description": "Non-insulin-dependent diabetes"
    }
  ],
  "meta": {"total": 1, "page": 1, "limit": 50}
}
```

---

#### `GET /v1/indications/{indication_id}`

**Query parameters**: `schedule`

---

### Prescribing texts

Prescribing texts are structured clinical criteria texts attached to restrictions and items. They describe the conditions under which authority prescribing applies.

#### `GET /v1/prescribing-texts`

**Query parameters**

| Parameter | Description |
|---|---|
| `schedule` | Schedule month, defaults to latest |
| `pbs_code` | Filter to texts linked to a specific PBS item |
| `restriction_code` | Filter to texts linked to a specific restriction |
| `page` | |
| `limit` | Max 200 |

**Response `200`**

```json
{
  "data": [
    {
      "prescribing_text_id": "PT00123",
      "text_type": "Indication",
      "complex_authority_required": false,
      "prescribing_txt": "Patient must have a confirmed diagnosis of type 2 diabetes mellitus."
    }
  ],
  "meta": {"total": 1, "page": 1, "limit": 50}
}
```

---

#### `GET /v1/prescribing-texts/{prescribing_text_id}`

**Query parameters**: `schedule`

---

### Dispensing rules

Program-level dispensing rules define maximum quantity, pack size, and repeat allowances.

#### `GET /v1/dispensing-rules`

**Query parameters**

| Parameter | Description |
|---|---|
| `schedule` | Schedule month, defaults to latest |
| `program_code` | Filter to a specific PBS program |

**Response `200`**

```json
{
  "data": [
    {
      "program_code": "GE",
      "rule_code": "DR001",
      "dispensing_quantity": 100,
      "dispensing_unit": "tablet",
      "repeats_allowed": 5,
      "description": "Standard supply"
    }
  ],
  "meta": {"total": 89}
}
```

---

#### `GET /v1/dispensing-rules/{rule_code}`

Returns the dispensing rule plus a `linked_items` array of PBS codes it applies to.

**Query parameters**: `schedule`

---

### Webhooks

Webhooks are available on **Growth and Enterprise** tiers. They deliver HTTP POST payloads to your endpoint when PBS schedule events occur.

#### Supported events

| Event | Description |
|---|---|
| `schedule.published` | A new PBS schedule has been ingested successfully |
| `schedule.failed` | Ingest failed for a scheduled month |
| `item.changed` | One or more fields changed for a specific PBS item |

#### `POST /v1/webhooks`

Register a new webhook endpoint.

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `url` | string | Yes | HTTPS URL to deliver payloads to |
| `events` | string[] | Yes | One or more event types from the list above |

**Response `201`**

```json
{
  "id": "990e8400-...",
  "endpoint_url": "https://your-app.example.com/webhooks/pbs",
  "event_types": ["schedule.published", "item.changed"],
  "signing_secret": "whsec_abc123...",
  "is_active": true,
  "failure_count": 0
}
```

The `signing_secret` is shown once. Use it to verify incoming payloads — see [Webhook signing](#webhook-signing).

---

#### `GET /v1/webhooks`

List all active webhooks for the authenticated key.

**Response `200`**

```json
{
  "data": [
    {
      "id": "990e8400-...",
      "endpoint_url": "https://your-app.example.com/webhooks/pbs",
      "event_types": ["schedule.published"],
      "is_active": true,
      "failure_count": 0,
      "last_triggered_at": "2026-04-01T03:15:22",
      "created_at": "2026-03-10T09:00:00"
    }
  ]
}
```

---

#### `DELETE /v1/webhooks/{webhook_id}`

Deactivate a webhook. Returns `204 No Content`.

---

## Webhook signing

Every webhook delivery includes an `X-PBSdata-Signature` header. Verify it to confirm the payload came from PBSdata.io.

The signature is an HMAC-SHA256 digest of the raw request body, keyed with your webhook's `signing_secret`.

**Python example**

```python
import hashlib
import hmac

def verify_webhook(raw_body: bytes, signature_header: str, signing_secret: str) -> bool:
    expected = hmac.new(
        signing_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

**Node.js example**

```js
const crypto = require('crypto');

function verifyWebhook(rawBody, signatureHeader, signingSecret) {
  const expected = crypto
    .createHmac('sha256', signingSecret)
    .update(rawBody)
    .digest('hex');
  return crypto.timingSafeEqual(
    Buffer.from(expected),
    Buffer.from(signatureHeader)
  );
}
```

Reject any request where the signature does not match.

**Delivery behaviour:** Failed deliveries (non-2xx response or timeout) are retried with exponential backoff. After 10 consecutive failures, the webhook is automatically deactivated. Check `failure_count` via `GET /v1/webhooks` to monitor delivery health.

---

## Health check

`GET /health` — no authentication required.

```json
{"status": "ok", "version": "1.0.0"}
```

---

## Common patterns

### Poll for new schedules

```python
import httpx

client = httpx.Client(headers={"X-API-Key": "pbslive_..."})

schedules = client.get("https://api.pbsdata.io/v1/schedules").json()
latest = schedules["data"][0]["month"]  # e.g. "2026-04"
```

### Check if a medicine is 60-day eligible

```python
results = client.get(
    "https://api.pbsdata.io/v1/medicines",
    params={"q": "metformin", "sixty_day": True}
).json()

for item in results["data"]:
    print(item["ingredient"])
```

### Get all changes for a specific item since January

```python
changes = client.get(
    "https://api.pbsdata.io/v1/changes",
    params={"since": "2026-01", "pbs_code": "02647H"}
).json()
```

### Watch for delistings this month

```python
delistings = client.get(
    "https://api.pbsdata.io/v1/summary-of-changes",
    params={"schedule": "2026-04", "change_type": "DELETE"}
).json()
```

### Browse all PBS medicines in a therapeutic class

```python
# Find the ATC code first
atc = client.get(
    "https://api.pbsdata.io/v1/atc-codes/A10BA"
).json()

# Then get children (specific chemical substances)
for child in atc["children"]:
    print(child["atc_code"], child["atc_description"])
```

---

## Data coverage (April 2026 schedule)

| Resource | Count |
|---|---|
| PBS items | 13,813 |
| Unique medicines | 1,227 |
| Restrictions | 45,842 |
| ATC codes | 7,891 |
| Change records (all history) | 23,116+ |
| PBS programs | 17 |
| Organisations | 413 |
| AMT concepts | — |

---

## Support

- Interactive docs: `https://api.pbsdata.io/docs`
- Email: support@pbsdata.io
- Status: `https://status.pbsdata.io`

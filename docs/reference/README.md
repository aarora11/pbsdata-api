# PBS source documents — the authority for ambiguous decisions

**Consult these before inventing an answer.** Several defects in this codebase came from guessing at
PBS semantics rather than reading the specification: field names that PBS does not publish
(`artg_id`, `sponsor`, `caution`, `biosimilar` on `/item-overview`), a join keyed on a column the
feed does not carry, and — most expensively — the `items` table keyed on `pbs_code` when the source
is keyed on `li_item_id`.

| File | What it settles |
|---|---|
| `PBS-API-V3-Data-Dictionary-v3.6.5.pdf` | Table definitions, **primary/composite keys**, and per-column definitions for every endpoint |
| `PBS-API-V3-Data-Dictionary-v3.6.5.txt` | Text extraction of the above — grep this first |
| `pbs-api-versions-and-changelog.json` | Every API version with its changelog; how new endpoints get announced |

## Where these came from

- Data Dictionary: <https://data.pbs.gov.au/download/api/files/2025-PBS-API-V3-Data-Dictionary-v3.6.5.pdf>
- Release notes: <https://data.pbs.gov.au/api/api-release-notes.html>
- Developer portal (Swagger, no auth required): <https://data-api-portal.health.gov.au/>
- Changelog JSON is simply `GET https://data-api.health.gov.au/pbs/api/v3/` — the API root returns
  its own version history. **Poll this to detect new endpoints**; that is how `/medication-charts`
  and `/item-chart-eligibilities` were found missing.

## Version caveat

The dictionary is **v3.6.5** (Nov 2025); the live API is **v3.7.8** (May 2026). No v3.7.8 dictionary
is published at the predictable URL. The v3.7.8 changelog only *adds* endpoints and columns, so the
keys below are very unlikely to have moved — but confirm before relying on anything marked new.

## The grain rule (the thing most worth knowing)

`/items` (ITEM_T) is defined by PBS as:

> "Captures the PBS item (drug) and its basic elements. **One row per unique medicinal product,
> container and brand per schedule.**"

with primary key **`li_item_id, schedule_code`**. And `li_item_id` is:

> "Unique identifier for each row on the /items endpoint generated using the **Item Code and
> PharmCIS internal IDs (PIG ID, MP, MPP, TPP)**."

So `li_item_id` is a 5-segment composite whose **first segment is the PBS item code** — which is why
`li_item_id.split("_")[0] == pbs_code` holds on 14,759/14,759 rows. It is a documented key, not a
heuristic.

**PBS deliberately uses two grains**, and every join must respect which one it is on:

| Grain | Endpoints keyed on it |
|---|---|
| **Brand** (`li_item_id`) | `/items`, `/amt-items`, `/item-dispensing-rule-relationships`, `/item-pricing-events` |
| **Listing** (`pbs_code`) | `/item-atc-relationships`, `/item-restriction-relationships`, `/item-prescribing-text-relationships`, `/item-organisation-relationships`, `/prescribers` |

Measured confirmation: within a `pbs_code`, brands disagree on `brand_name` (35.8%),
`organisation_id` (34.0%) and `determined_price` (2.0%), but **never** on `formulary`,
`benefit_type_code` or `maximum_quantity_units` (0%). The documentation and the data agree.

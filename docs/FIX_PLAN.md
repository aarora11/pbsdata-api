# PBSdata.io — Remediation Plan

**Date:** 2026-07-28
**Inputs:** `AUDIT_FINDINGS.md`, `JOIN_CATALOGUE_TECHNICAL.md`, `JOIN_CATALOGUE_PLAIN_ENGLISH.md`
**Superseded in part by:** `JOIN_CONFIDENCE.md` — the second audit pass (2026-07-29) reprioritised
the remaining work around join confidence. Follow its §5 uplift plan for sequencing from here.
**Status:** Phases 0–3, 5 (partial) and 6 implemented. Phase 4 blocked on external review.

> ## Execution status — 2026-07-28
>
> | Phase | Status |
> |---|---|
> | 0 — Data foundation | **Code complete.** Three fetches wired, loader transactional, quality gate added, `/health/data` added. **Re-ingest still required** to confirm. |
> | 1 — Blocking defects | **Complete.** `[B1]`–`[B6]` all fixed and pinned by tests. |
> | 2 — Correctness | **Complete** except `[C2]` and `[C9]`, which need domain answers. |
> | 3 — Guardrails | **Complete.** `tests/test_audit_regressions.py`, 47 tests. Mutation-tested. |
> | 4 — Domain-blocked | **Not started — awaiting external PBS review.** |
> | 5 — Structural | 5.2 (dead tables dropped) and 5.3 (cache) done. 5.1 needs your decision. |
> | 6 — Documentation | **Complete.** |
>
> Test count: **221 → 268**. The 47 new tests were mutation-tested by reverting fixes
> and confirming the corresponding test fails.
>
> **The one thing still needed before any of this is verifiable in production: re-run
> the ingest.** Every empty-table fix is at the source; the local database still holds
> pre-fix data.

---

## The headline: it is smaller than the audit made it look

The audit listed 32 findings. Investigating the ingest pipeline for this plan turned up something the
audit did not surface: **roughly a third of those findings are downstream of three unwired function
calls in `ingest/runner.py`.**

`ingest/pbs_client.py` implements these methods. `runner.py` never calls them:

| Client method (exists) | Feeds | Consequence of not calling it |
|---|---|---|
| `get_all_item_dispensing_rule_relationships` | `raw_item_pricing` | `item_pricing` **0 rows**; `items.government_price` **100% NULL**; `items.li_item_id` **100% NULL** |
| `get_all_item_amt` | `raw_item_amt` | `item_amt_relationships` **0 rows** — runner hardcodes `raw_item_amt = []` (line 212) |
| `get_all_item_overviews` | `raw_item_overviews` | `biosimilar`, `artg_id`, `sponsor`, `caution` never populated — runner hardcodes `raw_item_overviews = []` (line 211) |

The chain for the first one: `normaliser.py:126` builds `pricing_map` from `raw_item_pricing`;
`normaliser.py:200-206` derives `government_price` and `li_item_id` from that map. No input → empty
map → NULL everywhere → every pricing endpoint and every T4 price statistic returns nothing.

Note the near-miss: the runner *does* call `get_all_item_dispensing_rules` (line 189), a
**different method** with a confusingly similar name that feeds the `pbs_code`↔`rule_code` link table.
The pricing method sits unused beside it.

**So: do not start at the top of the findings list.** Fix the pipeline, re-ingest, re-measure. A
meaningful number of findings will change shape or disappear, and the rest become verifiable.

---

## Shape of the plan

| Phase | What | Blocked on | Size |
|---|---|---|---|
| **0** | Restore the data foundation | nothing — start now | 2–3 days |
| **1** | Blocking code defects | nothing — parallel with 0 | 1–2 days |
| **2** | Correctness defects | Phase 0 (to verify) | 3–4 days |
| **3** | Guardrails so this cannot recur | Phase 1 | 2–3 days |
| **4** | Domain-blocked fixes | external PBS review | 2–4 days after answers |
| **5** | Structural / product decisions | your call | 5–10 days |
| **6** | Disclosure and documentation | Phases 2–4 | 1–2 days |

**Send the domain questions out today** (see Phase 4). They have the longest lead time and nothing
else depends on them.

---

## Phase 0 — Restore the data foundation

*Nothing downstream is verifiable until this is done. Highest leverage work in the plan.*

### 0.1 Wire the three missing fetches — **S, ~2 hours**

In `ingest/runner.py`, add to the `asyncio.gather` block and the cache save/load lists:
- `fetch("/item-dispensing-rule-relationships", client.get_all_item_dispensing_rule_relationships(schedule_code))` → pass as `raw_item_pricing=`
- `fetch("/item-amt-relationships", client.get_all_item_amt(schedule_code))` → replaces `raw_item_amt = []`
- `fetch("/item-overview", client.get_all_item_overviews(schedule_code))` → replaces `raw_item_overviews = []`

Rename `get_all_item_dispensing_rules` / `get_all_item_dispensing_rule_relationships` to something
unmistakable while you are in there — this collision caused the bug.

**Resolves or re-scopes:** `[D2]` `[D3]`, most of `[C1]`'s blast radius, the data half of `[C9]`,
and the `biosimilar`-always-empty finding in `[F7]`.

### 0.2 Make `load_to_database` transactional — **S, ~2 hours**

There is no explicit transaction wrapper. This is how `2026-03` ended up holding 6,883 items while
flagged `failed` — a partial load left inconsistent state that every subsequent query silently
treats as truth.

Wrap the whole load in `async with conn.transaction():` so a schedule is either fully loaded or not
present at all.

### 0.3 Diagnose the genuinely-empty endpoints — **M, ~1 day**

`fees`, `prescribing_texts`, `indications`, `summary_of_changes`, `amt_items` **are** fetched by the
runner but landed 0 rows. The loader demonstrably ran to completion (later tables such as `criteria`
= 2,820 and `parameters` = 3,501 are populated), so these are not a load-ordering casualty.

For each, determine which is true:
- the PBS API returned 204/empty for this schedule (migration 007's comment notes
  `item_pricing_events` did exactly that for March 2026), or
- the subscription key lacks access to that endpoint, or
- normalisation silently dropped the rows.

Log per-endpoint fetch counts and post-normalisation counts side by side. The runner already logs
`ingest.fetched` with a count — capture that output rather than guessing.

**This is a diagnosis task, not a fix task.** Its output determines whether `[F3]` (null
`authority_method`) and `[F4]` (null `indication`) are field-mapping bugs or simply absent source data —
which changes the fix entirely.

### 0.4 Re-ingest and re-baseline — **S, ~half day**

Re-run `2026-03` to `complete`. Then re-run every verification query in `AUDIT_FINDINGS.md`
§Appendix and record the new numbers.

**Deliverable: an updated findings table.** Several items will close outright. `[C1]`'s measured
3.12× inflation will get worse once `item_pricing` is populated — expect it to be the dominant
correctness defect after this phase.

### 0.5 Fix the schedule-status trap — **S, ~2 hours**

Today every default-schedule query resolves `WHERE ingest_status='complete' ORDER BY month DESC LIMIT 1`
→ an empty `2026-04` → the API returns nothing. Add a startup or health-check assertion that the
latest `complete` schedule has a non-zero `item_count`, and alert (reuse `_alert_slack`) when it does not.

---

## Phase 1 — Blocking code defects

*All independent of Phase 0. Cheap. Can be done in parallel by a second person.*

| ID | Fix | Size |
|---|---|---|
| `[B1]` | `market.py:63` — remove `iar.is_primary` filter. Either add + populate the column at ingest (highest `atc_priority_pct` per `pbs_code`), or rewrite as a correlated subquery. **Recommend the column** — it also lets you fix `[B6]` and the `/items` primary-ATC selection consistently in one place. | S |
| `[B2]` | `market.py:486` — the injected ATC filter references alias `i` in a query aliased `b`/`t`. Build a separate filter string per query, or alias the price-change query's base table `i`. | S |
| `[B3]` | `benefit_type` enum mismatch. **Decision required — see below.** | S–M |
| `[B4]` | `summary_of_changes.section` — strip the leading `/` at ingest **and** relax both comparisons to `sec.strip('/') == 'items'`. Do both: the normalisation fixes new data, the comparison fixes data already loaded. | S |
| `[B5]` | Delisting enrichment — resolve drug names and ATC for delisted items against the **previous** completed schedule, not the current one. | M |
| `[B6]` | `schedule_changes.py:288` — dict comprehension keeps the last row. Use `DISTINCT ON (pbs_code)` in SQL, or guard with `if pc not in primary_atc`. | S |

### The `[B3]` decision — canonicalise on letters or long form

This one needs an explicit call because it touches ingest, API and docs.

**Recommendation: canonicalise on the PBS single letters (`U`/`R`/`A`/`S`)** and map to long labels
only at the response layer.

Rationale: the letters are what PBS emits and what customers migrating off the XML files already
have in their systems; the long form is our invention. It also means `benefit_labels` starts working
without touching six call sites.

Cost: a data migration on `items.benefit_type`, and `test_phase2_ingest.py:57-73` currently asserts
the long form and will need updating.

The alternative — keep long form, change the API's literals — is less code but locks in a
non-PBS vocabulary in a product whose entire pitch is faithful PBS data.

---

## Phase 2 — Correctness defects

*Verify against re-ingested data from Phase 0.*

### 2.1 `[C1]` Join fan-out — **M, ~1 day**

The mechanical part is simple: `COUNT(*) FILTER (…)` → `COUNT(DISTINCT i.pbs_code) FILTER (…)`
across `/market/atc-summary`, `/manufacturer-landscape`, `/price-pressure-index`.

The part that needs thought: price statistics. `mean_dpmq` and `median_dpmq` are currently weighted
by how many sponsors and pricing rows an item has. Correct approach is to aggregate pricing to item
level in a CTE first, then compute statistics over that.

`/atc-codes/{code}/items` already solves this correctly with `SELECT DISTINCT` — **use it as the
reference implementation.**

Depends on the Phase 4 answer to *"should an item count mean PBS codes or brands?"* — but you can
implement `COUNT(DISTINCT pbs_code)` now and add a `count_basis` field later.

### 2.2 `[C5]` Falsy-zero patient charge — **S, ~1 hour**

`items.py:244` and `items.py:350`: replace `max_charge or dpmq` with
`max_charge if max_charge is not None else dpmq`. A legitimate $0 patient charge currently reports
as full price. Grep for the same `X or Y` idiom on numeric fields elsewhere while you are there.

### 2.3 `[C6]` Inconsistent concessional charge — **S, ~2 hours**

Extract one shared patient-charge helper and call it from both `/items/{code}/price` and
`/patient-cost`. Add `government_pays` to the concessional branch of `/price`, which currently omits it.

### 2.4 `[C7]` Safety-net burden statistics — **S, ~2 hours**

Compute `min`/`max`/`mean` in a separate unlimited aggregate query, or amend the endpoint description
to say "across the 500 items closest to threshold". **Prefer the former** — the current wording is
a factual claim we do not meet.

### 2.5 `[C8]` Price pressure index — **M, ~half day**

Three separable fixes:
- add an upper bound to the reductions window (currently counts schedules *after* the target month,
  making historical values unreproducible)
- align the numerator and denominator counting bases (falls out of 2.1)
- publish the 50/30/20 weights in the response `meta`, or make them query parameters

The third signal (DPMQ ÷ government price) should be **removed or redefined** — it divides the two
competing price sources by each other. Hold until the `[C2]` decision lands.

### 2.6 `[C4]` Non-deterministic restriction lookup — **S, ~3 hours**

`/restrictions/{code}` returns one arbitrary row from up to 2,124. Return the restriction once with
an `applies_to_pbs_codes` array, or add a deterministic `ORDER BY` and rename the field to make clear
it is one example item.

**Recommend the array** — it is what a caller actually wants and removes the misleading `pbs_code`.

---

## Phase 3 — Guardrails

*The reason none of this was caught: **221 tests pass while the system is this broken.***

Concretely, on `[B3]`:
- `test_phase2_ingest.py:57` asserts the ingest produces `"unrestricted"` — passes.
- `test_comprehensive.py:581` asserts only that the key `"benefit_type_label"` **exists** — never
  checks its value — passes.
- Nothing tests the two layers against each other.

The suite validates each layer against its own assumptions. Fix that or this recurs.

### 3.1 Contract tests across the ingest/API seam — **M, ~1 day**

For every enum and code the API matches on literally (`benefit_type`, `formulary`, `authority_method`,
`section`, `concept_type`), assert the value the ingest produces is the value the API expects. One
parameterised test over a shared constant table.

### 3.2 Schema/query conformance test — **S, ~half day**

A test that every column referenced in router SQL exists. `[B1]` (`is_primary`) and `[B2]` (alias `i`)
would both have been caught at CI by running each endpoint's SQL through `EXPLAIN` against an empty
but correctly-shaped database — including the optional-filter branches, which is where both hid.

### 3.3 Post-ingest data quality assertions — **M, ~1 day**

After each ingest, assert and alert on: every expected table non-empty; null-rate per critical column
below a threshold (`government_price`, `authority_method`, `li_item_id` at 100% null should have
failed the ingest loudly); row counts within a tolerance band of the previous schedule.

This turns "silently serves nothing for three months" into a Slack alert. `_alert_slack` already exists.

### 3.4 Aggregate invariant tests — **S, ~half day**

Assert internal consistency on T4 responses: `f1_count + f2_count + other ≤ unique_prescribing_rules`.
The fan-out bug violates this and it is trivially checkable.

---

## Phase 4 — Blocked on external PBS domain review

**Send these now.** `JOIN_CATALOGUE_PLAIN_ENGLISH.md` §Part 6 is written to be forwarded as-is.

| Question | Unblocks | Impact if we guess wrong |
|---|---|---|
| Which PBS field carries **authority method**? | `[F3]` | Authority workflow is our flagship T3 demo and is currently generic for every drug |
| Is **authority_required** directly available, or must it be inferred? | `[F2]` | Currently derived from the *complex* authority flag — clinically material |
| What is the real **streamlined authority code** field? | `[F1]` | We ship a fabricated field and a filter on it |
| Do restrictions carry **indication** directly? | `[F4]` | `TIERS.md` promises it to Base tier; always null |
| Which price is canonical for **patient cost**? | `[C2]`, 2.5 | Two endpoints already disagree on the same drug |
| Are **F1/F2 inverted** in our docs? | `[F6]` | Published in `OFFERING.md`, `TIERS.md`, and a T3 response |
| What is formulary value **`CDL`** (208 items)? | `[F6]` | Renders as "Unknown formulary" |
| Is **same-ingredient substitution** safe without matching strength/form? | `[H3]` | Patient-safety exposure at a clinical-decision-support tier |
| Should **item count** mean PBS codes or brands? | 2.1 | Determines the fix for every T4 count |
| Is drug-level or brand-level correct for **AMT**? | `[C9]` | PBS keys on `li_item_id`; we key on `pbs_code` |

Once answers land, most of these are S-sized field-mapping changes. `[F6]` is a documentation sweep
across code, `OFFERING.md`, `TIERS.md` and `join_business_rules.md`.

---

## Phase 5 — Structural and product decisions

*These need your call before anyone writes code.*

### 5.1 `[C3]` Should `medicines` be schedule-versioned? — **L, ~1 week if yes**

Today ingredient-level ATC and therapeutic group are stored once globally and overwritten every
ingest, retroactively rewriting history. This contradicts `OFFERING.md`'s "every schedule stored
independently".

Three options:

| Option | Effort | Trade-off |
|---|---|---|
| Version `medicines` per schedule | L | Correct; touches ingest, schema, every `JOIN medicines` |
| Stop serving medicine-level ATC on historical queries; source ATC only from `item_atc_relationships` (already versioned) | M | Cheaper, no schema change, loses a convenience field |
| Accept and amend the marketing claim | S | Free, but it is a claim you are selling on |

**Recommend option 2 unless the domain review says ingredient ATC genuinely churns between
schedules** — that is one of the questions in Phase 4.

### 5.2 `[D4]` Kill or build the pre-computed market tables — **S to M**

`market_atc_summary` and `market_manufacturer_landscape` are created by migration 008, never written,
never read. `OFFERING.md` claims T4 metrics are "pre-computed at ingest time".

Either populate them at ingest and read from them, or drop the migration and correct the claim.
**Recommend dropping for now** — correctness first; optimise when there is load to justify it.

### 5.3 `[D5]` Replace the response cache — **M, ~1 day**

In-process dict, no TTL (the stored timestamp is read and discarded), unbounded growth, and
invalidation reaches only one of N gunicorn workers. `CACHE_TTL_*` env vars in `README.md` are dead config.

Low urgency — cache keys embed `schedule_id`, so a *new* schedule is unaffected. The real exposure is
**re-ingesting a corrected schedule**, where the ID is unchanged and other workers keep serving
superseded data. PBS does republish corrections.

Options: add TTL + size bound (S), or move to Redis (M). Redis is on the roadmap already.

### 5.4 Unused ingested data — **S, decision only**

`item_restriction_relationships` (the PBS-documented Tier A relationship) and
`container_organisation_relationships` are ingested and never read. `items.originator_brand_indicator`
and `innovator_indicator` are ingested and unused while `/market/biosimilar-landscape` infers
"originator" from `NOT biosimilar`.

Either use them or stop ingesting them. The originator one is a real correctness improvement `[F7]`.

---

## Phase 6 — Disclosure and documentation

*Cheap, and it is what the audit spec already requires. Do it once the underlying values are right —
disclosing a heuristic that is also wrong helps nobody.*

| ID | Fix |
|---|---|
| `[H1]` | `/items/{code}/patient-cost` has **no `meta` block at all**. Add tier, schedule, and explicit labelling of the representative-price selection and the safety-net estimate. Spec §1.3 requires it. |
| `[H2]` | `/drugs/{code}/authority-workflow` — add a note that the checklist is our interpretation, not a PBS-issued procedure. Spec §3.4 requires it. |
| `[H3]` | `/substitution` and `/60-day-pair` — state explicitly that strength, form and pack size are **not** matched. Strengthen pending the Phase 4 safety answer. |
| `[H4]` | `primary_manufacturer` — disclose that the selection is arbitrary when multiple sponsors exist, or make it deterministic. |
| — | Add the `brand_name_note` from `/drugs/{code}/brands` to `/drugs/{code}?include_brands=true` and `/full-profile`, which return the same repeated-name structure with no note. Remove the `brand_count … or 1` fabrication `[F8]`. |
| — | Supersede `docs/join_business_rules.md` — it documents joins that do not work. Either regenerate it from the new catalogues or delete it and point at them. |

---

## Suggested sequencing

**Week 1** — Phase 0 (0.1, 0.2, 0.5 immediately; 0.3 diagnosis in parallel) + Phase 1 in parallel.
Send Phase 4 questions on day 1. Re-baseline at end of week.

**Week 2** — Phase 2 against clean data. Start Phase 3 guardrails.

**Week 3** — Finish Phase 3. Apply Phase 4 answers as they arrive. Phase 5 decisions with you.

**Week 4** — Phase 6 disclosure sweep. Re-run the full audit and publish a closure report.

Roughly **3–4 weeks** for one engineer, or **2–3 weeks** with two working the parallel tracks.

---

## What I would not do

- **Do not fix findings downstream of empty data before Phase 0.** You would be tuning code paths
  against nulls and could not tell a fix from a no-op.
- **Do not fix `[C1]` fan-out before re-ingest.** The current 3.12× measurement understates it;
  fix it against realistic data so the tests you write are meaningful.
- **Do not touch `[F1]`–`[F4]` before the domain answers.** Guessing which PBS field carries
  authority method is how the current mapping went wrong.
- **Do not ship T3/T4 to a paying customer until Phases 0–2 are closed.** Those tiers are sold to
  hospital pharmacy, formulary managers and pharma market access. The current authority workflow,
  price statistics and change feeds would not survive their first review.

---

## Gate for reopening sales conversations

- [ ] One schedule ingests to `complete` with all expected tables non-empty
- [ ] Post-ingest data-quality alerts in place and passing
- [ ] `[B1]`–`[B6]` closed; no endpoint 500s on any documented parameter combination
- [ ] `[C1]` closed; T4 aggregate invariants pass
- [ ] Domain review returned; `[F1]`–`[F4]` and `[F6]` resolved or fields withdrawn
- [ ] Every Tier C heuristic labelled per audit spec §0.1
- [ ] Full audit re-run and closure report published

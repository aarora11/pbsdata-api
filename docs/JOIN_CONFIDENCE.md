# PBSdata.io — Join Confidence Assessment and Uplift Plan

**Date:** 2026-07-29
**Purpose:** State how much we actually trust each join, on what evidence, and what specifically
would raise the ones we don't trust.
**Companions:** `AUDIT_FINDINGS.md` (defects), `JOIN_CATALOGUE_TECHNICAL.md` (the joins themselves)

---

## The headline finding

**No join in this system has ever been validated against an independent source.**

Every check performed across both audits — key conformance, orphan rates, coverage, invariants,
regression tests — is a test of *internal* consistency. They prove the system agrees with itself.
None of them would catch a join that is coherently, consistently wrong: one where we join the right
tables on the right keys and produce an answer that simply does not match what the PBS Schedule says.

That is the ceiling. **Until a hand-verified golden set exists, no join can be rated above MEDIUM**,
regardless of how clean its internals look. Section 5 is the plan to break that ceiling.

The second-order finding: **five of the seven core relationships cannot currently be exercised at all**,
because the data behind them is absent or attached to the wrong schedule. Confidence in those is not
low because we found problems — it is low because *we have not been able to look*.

---

## 1. How confidence is scored

Seven evidence dimensions. Each is objectively checkable; none is a judgement call.

| # | Dimension | Question | How verified |
|---|---|---|---|
| **E1** | Key conformance | Does the join key match the PBS v3 data dictionary? | Source review |
| **E2** | Referential integrity | Do keys actually resolve, or are there orphans? | Measured — LEFT JOIN orphan count |
| **E3** | Coverage | What fraction of the parent entity has the relationship? | Measured — DISTINCT parent count |
| **E4** | Determinism | Same input, same output? | Source review — `LIMIT 1` without `ORDER BY` fails |
| **E5** | Reversibility | Can a derived value be recomputed and checked? | Source review + tests |
| **E6** | Test coverage | Is there a test using real-API-shaped fixtures? | Test review |
| **E7** | External validation | Has any output been checked against the PBS Schedule? | **Nothing passes this today** |

### Ratings

| Rating | Meaning |
|---|---|
| 🟩 **MEDIUM-HIGH** | E1–E6 all pass. The maximum achievable without E7. Safe to sell, worth re-checking after golden cases exist. |
| 🟨 **MEDIUM** | E1 passes and the join is exercised, but coverage is unproven, a disclosed heuristic is involved, or tests use synthetic shapes. |
| 🟧 **LOW** | The join key is derived heuristically, semantics are unconfirmed, or a known defect is open. |
| 🟥 **UNVERIFIABLE** | No data reaches this join. Correctness is unknown in both directions — we cannot say it works and cannot say it doesn't. |

---

## 2. Evidence base

All figures measured 2026-07-29 against schedule `2026-03` (6,883 items), the only schedule holding items.

### Referential integrity (E2)

| Join | Orphans | Rows | Result |
|---|---|---|---|
| `item_atc_relationships → atc_codes` | 0 | 6,756 | ✅ clean |
| `item_atc_relationships → items` | 0 | 6,756 | ✅ clean |
| `item_organisation_relationships → organisations` | 0 | 210 | ✅ clean |
| `item_organisation_relationships → items` | 0 | 210 | ✅ clean |
| `restrictions → items` | 0 | 22,769 | ✅ FK-enforced |
| `items → medicines` | 0 | 6,883 | ✅ FK-enforced |
| `item_prescribers → items` | **10,324** | 10,324 | ❌ **100% orphaned** — see N1 |
| `item_restriction_relationships → items` | — | **0** | ⚠️ table empty |

### Coverage (E3) — of 6,883 items

| Relationship | Items covered | Coverage |
|---|---|---|
| `items → medicines` | 6,883 | **100%** |
| `items → ATC` | 6,724 | **97.7%** |
| `items → restrictions` | 6,083 | **88.4%** |
| `items → organisations` | 104 | **1.5%** |
| `items → prescribers` | 0 | **0%** |
| `items → pricing` | 0 | **0%** |
| `items → dispensing rules` | 0 | **0%** |

The 1.5% sponsor coverage is the one that should worry you most among the *non-zero* rows: it is high
enough to look like it works and low enough that `primary_manufacturer` is null for 98.5% of items.

---

## 3. Confidence by join

### 🟩 MEDIUM-HIGH — 4 joins

| Join | Keys | Evidence |
|---|---|---|
| `items → medicines` | `medicines.id = items.medicine_id` | FK-enforced, 100% coverage, deterministic, tested |
| `items → item_atc_relationships` | `pbs_code + schedule_id` | 0 orphans, 97.7% coverage, conforms to PBS |
| `item_atc_relationships → atc_codes` | `atc_code + schedule_id` | 0 orphans, conforms |
| `restrictions → items` | `restrictions.item_id` (derived from PBS keys) | FK-enforced, 88.4% coverage, no hidden LIMIT |

*Caveat on all four:* the ATC hierarchy walk sitting on top of joins 2–3 has no cycle guard (N3), and
`medicines` is not schedule-versioned (`[C3]`), so historical ATC answers are not reproducible.

### 🟨 MEDIUM — 5 joins

| Join | Why not higher |
|---|---|
| `items → programs` (`program_code + schedule_id`) | Conforms and resolves, but `programs` has only 17 rows and no coverage check exists |
| `items → item_organisation_relationships → organisations` | Keys clean and 0 orphans, but **1.5% coverage** and the "primary sponsor" pick is an arbitrary `LIMIT 1` |
| `restrictions → restriction_prescribing_text_relationships → prescribing_texts` | Keys conform, no LIMIT, but `prescribing_texts` is empty so the chain has never run. Ordering is by ID, not a PBS sequence — unconfirmed |
| `program_dispensing_rules → item_dispensing_rules` | Keys conform; `item_dispensing_rules` empty for this schedule |
| `summary_of_changes → items → medicines` | Keys conform and the delisting fallback is now correct, but `pbs_code` is derived by string-splitting `li_item_id` (N4) and the table is empty |

### 🟧 LOW — 4 joins

| Join | Why |
|---|---|
| **`item_pricing → items`** (`pbs_code + schedule_id`) | Join key is a **string-split heuristic** (N4), nullable, and used by an INNER JOIN in `/dispensing-context`. Underpins every price in the product. Zero rows, so unverifiable |
| **`item_pricing_events → items`** | `pbs_code` is **100% NULL** (N2). The join cannot match. 1,796 rows present and unreachable |
| **`item_amt_relationships → amt_items`** | Keyed on `pbs_code`; PBS `ITEM_AMT_T` keys on `li_item_id` (`[C9]`). Known Tier A violation, plus `items.li_item_id` is 100% NULL so the corrected join has nothing to key on |
| **`item_pricing → markup_bands`** (`schedule + program_code + dispensing_rule_mnem`) | Keys conform, but `program_code` is taken from `pricing_rows[0]` and reused for every band lookup without checking the rows agree |

### 🟥 UNVERIFIABLE — 3 joins

| Join | Blocker |
|---|---|
| `items → item_prescribers` | 100% orphaned by the split-schedule state (N1). Feeds `/drugs/{code}/prescribers` and a third of `/market/authority-landscape` |
| `items → prescribing_texts` (via `item_prescribing_text_relationships`) | Table empty |
| `restrictions` authority fields → workflow output | `authority_method` 100% NULL, `authority_required` derived from the wrong flag (`[F2]`, `[F3]`). The join is fine; the columns are not |

### Derived-value confidence

| Output | Rating | Note |
|---|---|---|
| `patient_charge` / `government_pays` | 🟩 | Reversible, recomposition asserted by test |
| `price_delta`, `price_delta_pct` | 🟨 | Reversible but reads the contested price field (`[C2]`) |
| ATC hierarchy breadcrumb | 🟩 | Deterministic; cycle risk latent (N3) |
| `is_first_in_atc_class` | 🟧 | Same-schedule definition is arguably the wrong semantic |
| `therapeutic_alternatives` | 🟧 | ATC L4 heuristic; now disclosed, never validated |
| `scripts_to_safety_net` | 🟧 | Ignores family aggregation and multi-drug regimens; disclosed |
| `price_pressure_index` | 🟧 | Weights undocumented in policy terms; one signal still grain-inconsistent (N6) |
| `substitution` set | 🟥 | Ignores strength and form. **Patient-safety exposure.** Disclosed but not made safe |

### Distribution

| Rating | Joins | Share |
|---|---|---|
| 🟩 MEDIUM-HIGH | 4 | 25% |
| 🟨 MEDIUM | 5 | 31% |
| 🟧 LOW | 4 | 25% |
| 🟥 UNVERIFIABLE | 3 | 19% |

**Nothing is HIGH, because nothing has been externally validated.**

---

## 4. What the low ratings have in common

Three root causes account for every 🟧 and 🟥:

1. **Absent data (N1, `[D2]`).** Seven of eleven relationships have no rows to exercise. One re-ingest
   moves most of these out of UNVERIFIABLE — into MEDIUM at best, because being able to see a join is
   not the same as validating it.
2. **Heuristic key derivation (N4, N2).** `pbs_code` on the pricing and change feeds is reconstructed
   by string-splitting a composite identifier, or missing entirely. A Tier C heuristic underneath a
   Tier A join is the most dangerous shape in the codebase: the join *looks* mechanical and its
   correctness silently depends on a string format nobody has verified.
3. **Unconfirmed semantics (`[F1]`–`[F4]`, `[C2]`, `[C9]`, `[F6]`).** The joins execute; what the
   columns mean is unresolved. No amount of engineering closes these — they need the domain review.

---

## 5. The uplift plan

Ordered by confidence gained per unit of effort.

### Step 1 — Re-ingest one schedule cleanly · **prerequisite for everything**

Nothing below is measurable until this is done. Expected effect: the three 🟥 joins become assessable,
and `item_pricing`/`item_pricing_events` become testable.

Then immediately re-run the §2 evidence queries and update this document's tables. **Expect the
numbers to move; do not assume they improve.**

Acceptance: one schedule `complete`, quality gate passing, `/health/data` reporting `ok`.

### Step 2 — Golden test cases · **the only thing that breaks the MEDIUM ceiling** · ~3 days

Pick **20 PBS items** and hand-verify every joined field against the published PBS Schedule and
pbs.gov.au. Not generated — a human reads the Schedule and writes down the expected answer.

Selection matters more than volume. Cover:
- 2 unrestricted, 2 restricted, 2 authority-required, 2 streamlined
- 2 with split ATC · 2 with multiple sponsors · 2 with multiple brands
- 2 F1 and 2 F2 · 1 biosimilar · 1 with a brand premium
- 1 with a $0 patient charge · 1 60-day eligible · 1 extemporaneous

Store as `tests/fixtures/golden/*.json` — expected values keyed by endpoint. Add
`tests/test_golden_cases.py` asserting live output matches, tolerant on floats only.

This is the first evidence that would catch a coherently-wrong join, and it converts E7 from "nothing
passes" to a measurable gate. **It is the single highest-value item in this plan.**

Acceptance: 20 items × ~8 endpoints verified; every mismatch triaged as either our defect or a
corrected expectation.

### Step 3 — Reconcile aggregates against the raw PBS API · ~2 days

For a schedule, pull raw counts straight from the government API and assert our totals match:
`COUNT(items)`, `COUNT(DISTINCT pbs_code)` per program, per ATC L1, restriction count, pricing-row count.

This catches silent row loss — exactly the failure mode of a nullable INNER JOIN key (N4) — which
golden cases on individual items would miss.

Acceptance: a `scripts/reconcile.py` that reports per-table deltas, wired into the quality gate with
a tolerance.

### Step 4 — Eliminate the key-derivation heuristics · ~2 days · needs Step 1 data

Resolve N4 and N2. With real pricing data loaded:
- Verify the `li_item_id` → `pbs_code` split holds for **100%** of rows; if not, find the real field.
- Find whatever the pricing-events feed actually calls its item identifier.
- Make `item_pricing.pbs_code` NOT NULL once populated, so the failure is loud rather than silent.

This converts the pricing chain from 🟧 to 🟨 and removes the most dangerous shape in the codebase.

### Step 5 — Close the open defects · ~1 day

X1 (`changes` 6.37× duplication — add DELETE-first plus a dedupe migration), N3 (`UNION` + a
`statement_timeout`), N5 (atomic counter increment). Small, independent, no domain input needed.

### Step 6 — Coverage floors in the quality gate · ~half day

The gate currently checks emptiness and null rates. Add **relationship coverage**: alert when
`items → pricing`, `→ prescribers`, `→ organisations` or `→ ATC` fall below a floor calibrated from
Step 1's numbers. A drop from 97.7% ATC coverage to 40% is invisible today.

### Step 7 — Domain sign-off · external, longest lead time · **start now**

`[F1]`–`[F4]`, `[C2]`, `[C9]`, `[F6]`, plus the substitution-safety question. Nothing engineering does
raises those ratings. Questions are written up in `JOIN_CATALOGUE_PLAIN_ENGLISH.md` §Part 6 ready to
forward.

### Projected distribution after Steps 1–7

| Rating | Now | After |
|---|---|---|
| 🟩 HIGH (externally validated) | 0 | 6–8 |
| 🟨 MEDIUM | 5 | 5–7 |
| 🟧 LOW | 4 | 1–2 |
| 🟥 UNVERIFIABLE | 3 | 0 |

Steps 1, 5 and 6 are ours today. Step 2 is the one that actually changes the ceiling. Step 7 gates
roughly a third of the remaining uplift and has the longest lead time, so send it first.

---

## 6. What to tell a customer today

Honest positioning by tier, given the above:

| Tier | Position |
|---|---|
| **Base / T1** | Defensible. Core identity, ATC and restriction joins are the strongest in the system |
| **T2 Clinical** | Defensible **once pricing loads and Step 2 passes**. Do not sell patient-cost until then |
| **T3 Intelligence** | Not yet. Authority workflow rests on two columns that are null or wrongly derived; substitution ignores strength and form |
| **T4 Market** | Not yet. Aggregate arithmetic is now correct, but it aggregates data that is absent or split across schedules |

The gate for changing any of these is Step 2, not Step 1. Being able to see a join is not the same as
having checked it.

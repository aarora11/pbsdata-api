# Public PBS site — design spec

*Drafted 2026-09-28. Replaces `PBS_POCKET_SPEC.md` (April 2026), which predates the join
audit and relied on data we have since found to be wrong.*

A free public website for the Australian PBS schedule. Anyone can search for a medicine and
see what it costs, whether they can get it, and who can prescribe it, in plain language, on
a page that loads fast and reads well on a phone. Pharmacists can switch to a dense view
with every PBS detail. Every item carries a one-click link to the same item on pbs.gov.au,
so any user can check us.

---

## 1. Principles

1. **Only show facts something independent has checked.** A field reaches the site only if
   it is checked against pbs.gov.au or against PBS's own pre-joined `/item-overview` on
   every ingest (§3). Unvalidated derivations are left out, not labelled "beta".
2. **Anyone can check us in one click.** Every item links to its pbs.gov.au page (§6).
3. **Simple first, detail on request.** The default view answers four questions (§2). The
   pharmacist view adds everything else. Nothing is hidden for good, only folded away.
4. **Not advice.** We display the schedule. We never recommend a medicine, brand or
   action. Restriction criteria are shown verbatim, never paraphrased. Plain-language
   *labels* sit beside the official text; they never replace it.
5. **Fast and calm.** It must feel like the opposite of pbs.gov.au: one search box, no
   dropdowns, no item-code knowledge needed, readable on a phone.

---

## 2. Who it's for

| View | User | Questions it answers |
|---|---|---|
| **Simple** (default) | Patients, carers, anyone | Is it on the PBS? What will I pay? Can I get it (what approval does my doctor need)? What changed? |
| **Pharmacist** (toggle) | Pharmacists, prescribers, researchers, journalists | All of the above, plus item codes, DPMQ, quantities in units and packs, dispensing rules, restriction codes, notes and cautions, AMT, prescriber types, price history |

The toggle is a single switch in the header. The choice persists per browser
(`localStorage`, which must fail safely) and can be forced by URL (`?view=pharmacist`), so a
pharmacist can share a link that opens in their view.

---

## 3. What we show, and what checks it

| Checked by | Fields | Simple | Pharmacist |
|---|---|:-:|:-:|
| pbs.gov.au, 100 items, 99.8% (`docs/SITE_VERIFICATION_2026-08.md`) | Medicine name, brands, manufacturer, max quantity, repeats, **patient charge**, benefit type (unrestricted / restricted / streamlined / authority) | ✓ | ✓ |
| `/item-overview`, every ingest (`ingest/join_validation.py`) | Restriction text, drug class (ATC), prescriber types | ✓ | ✓ |
| | Prescribing texts, notes and cautions, dispensing rules, AMT, pricing events | | ✓ |
| Change feed, 0 false +/− on 2026-06 | Monthly changes: listings, delistings, price and restriction changes | ✓ | ✓ |
| Patient charge formula, recomposed on 99.57% of pricing rows | Brand premium, DPMQ | premium only | ✓ |

**Not shown (unvalidated or known wrong):** safety net estimates, substitution/equivalence, every `/market/*` aggregate, the
price-pressure index.

---

## 4. Pages

### Home — `/`
One search box with autocomplete on medicine and brand names (e.g. *atorvastatin*,
*Lipitor*). Below it: a "This month on the PBS" strip (counts of new listings, delistings,
price changes, linking to §Changes) and the schedule month the site reflects.

### Medicine — `/medicine/{slug}`
The core page. One page per medicine (a PBS schedule drug name; combination products are
their own medicine). Items are grouped by form and strength.

**Simple view**

```
Atorvastatin                                            Cholesterol-lowering
Schedule: October 2026

You pay      $25.00 general  ·  $7.70 concession  (per script)
Access       Unrestricted: any PBS prescriber, no approval needed
Prescribed by  Doctors · Nurse practitioners

Tablet 40 mg  ·  30 tablets  ·  up to 5 repeats           Check on PBS ↗
  Brands: Lipitor (+$1.20) · Atorvastatin Sandoz · …6 more
Tablet 80 mg  ·  30 tablets  ·  up to 5 repeats           Check on PBS ↗
  …

What changed recently
  Aug 2026 — price reduced on 40 mg
```

- "You pay" is a range if it varies across items or brands ("$25.00, or up to $26.20 for
  some brands"), with the premium named per brand.
- Access shows a plain label, then a "Read the official criteria" fold with the verbatim
  restriction text. Authority items state "Your doctor needs approval from Services
  Australia (or uses a streamlined code) to prescribe this."
- One row per item, never per brand. Brands are a line under the item.

**Pharmacist view** — the same page with each item expanded into a dense table:

| Field | Notes |
|---|---|
| Item code, program (S85 / S100 type) | |
| Max quantity | Units **and** packs; pbs.gov.au quotes packs |
| Repeats, DPMQ, patient charge, brand premium per brand | DPMQ from the community-pharmacy rule (`rp-s90-cp`) |
| Manufacturer per brand | |
| Restrictions | Code, streamlined authority code, full text |
| Notes and cautions | Tagged separately from restrictions |
| Dispensing rules, AMT codes, ATC code | |
| Price and listing history | Every held schedule |

### Item — `/item/{code}`
Redirects to the medicine page, anchored and highlighted at that item. People search by item
code; this makes those links work.

### Changes — `/changes/{month}` and `/changes`
One page per schedule month, grouped: new listings, delistings, price changes, restriction
changes. Plain one-line description per change, linking to the medicine page. The pharmacist
view adds item codes and before/after values.

### Browse — `/browse/{atc}`
The ATC tree, from body system down to medicine. Low build cost; the ATC join is checked on
every ingest.

### How we check this data — `/about/data`
States the checks in §3 in plain language, the verification result, the schedule month, and
the last ingest date. This is the reason to trust the site over a scraper, so it is a real
page, not a footer line.

### About and disclaimer — `/about`
Scope, attribution to the Australian Government PBS, and the not-advice statement.

---

## 5. Search

Runs entirely in the browser against an index built at deploy time (Pagefind or a prebuilt
JSON index; ~1,200 medicines and ~15,000 items is small). It matches medicine names, brand
names and item codes, and must tolerate misspellings. An exact item code (e.g. `10001J`)
goes straight to that item.

---

## 6. "Check on PBS" links

- **Target:** `https://www.pbs.gov.au/medicine/item/{pbs_code}`, one per item row. Verified
  2026-09-28 to render the brand, quantity, repeats, DPMQ, patient charge and restriction,
  the same facts we show.
- **Not usable (return 400):** pbs.gov.au search URLs and multi-code item URLs
  (`/medicine/item/A-B`). So the link sits on each item row, not on the medicine header.
- **No page exists** for Extemporaneous (EP) and Paraplegic/Quadriplegic (PQ) items (~169),
  which 404 on pbs.gov.au. Show "PBS doesn't publish a page for this item" instead of a
  link.
- **Same month or the check fails.** pbs.gov.au always shows the current schedule. If our
  build is a month behind, prices will disagree and the check will say we are wrong. The
  rebuild must run as soon as the new schedule is ingested (§7), and every page shows the
  schedule month next to the link. If the site's month is behind the current month, a
  banner says so.
- **Keep links working.** A build step checks a sample of links (e.g. 50 per build) and
  fails on unexpected 404s, so a URL change on pbs.gov.au is caught before users hit it.

---

## 7. Architecture

A static site, generated monthly. No production server, database or API.

```
Monthly ingest ─▶ Postgres ─▶ export script ─▶ JSON ─▶ static build ─▶ static host
 (existing)                   (new, Python)            (one HTML page   (Cloudflare Pages
                                                        per medicine)    / Netlify)
```

- **Export** reads only validated fields (§3) and writes one JSON file per medicine, plus the
  search index, the changes and the ATC tree. It refuses to run against a schedule whose
  ingest status isn't `complete`, so a failed quality gate never reaches the public.
- **Build**: Astro or similar. It's content-first, ships no JavaScript by default, and the
  mode toggle and search are small islands.
- **Deploy**: rebuilds after each successful ingest. It's manual at first and can be
  scheduled once the ingest is.
- The FastAPI service remains an internal tool. It's not deployed for this site.

---

## 8. Out of scope

Medical advice of any kind, drug interactions, dosing, side effects, "is this right for
me", non-PBS (private) prices, pharmacy stock, user accounts, email alerts (these need a
backend; they may come later).

---

## 9. Data work before building

| # | Work | Why |
|---|---|---|
| 1 | **Display `drug_name`, not `li_drug_name`** (store both) | ~15% of items show the legal name where pbs.gov.au shows the schedule name. The "Check on PBS" link would expose the mismatch immediately. |
| 2 | ~~Fix `sixty_day_eligible`~~ **Done 2026-09-29**: read from `policy_applied_imdq60_flag` / `_base_flag`, paired in `ingest/sixty_day.py`, verified 806/806 against pbs.gov.au (`scripts/verify_sixty_day.py`) | Shipped as the 60-day feature. |
| 3 | **Brand premium selects the community-pharmacy dispensing rule** instead of first-wins by feed order | Latent: correct today by luck of feed order for the 741 brands whose premium varies. |
| 4 | **Ingest the current schedule** and keep it current | We hold 2026-05, -06, -08. The "Check on PBS" link only agrees with us if we're on the live month. |
| 5 | **Backfill history** (~13 months available) | "What changed recently" and pharmacist price history need depth; ~30 min of ingest per month. |
| 6 | **Define the medicine grouping** (slug from `drug_name`; combinations separate) and check it on a sample against pbs.gov.au | Every page depends on it. |

---

## 10. Build order

1. Data work #1, #4, #6 (the minimum for correct pages)
2. Export script + medicine page (simple view) + search + "Check on PBS" links
3. Pharmacist view
4. Changes pages
5. How-we-check page, About, Browse
6. Data work #3, #5 (#2, the 60-day fix, is done)

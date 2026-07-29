# PBSdata.io — What We Join, and Why, in Plain English

**For:** an independent reviewer with PBS domain knowledge (pharmacist, PBS analyst, formulary
manager, health economist). **No software knowledge is assumed.**

**Date:** 2026-07-28
**Companion documents:** `JOIN_CATALOGUE_TECHNICAL.md` (the SQL-level version),
`AUDIT_FINDINGS.md` (the defect list)

---

## What we are asking you to check

The Australian Government publishes the PBS schedule every month as a set of about 30 separate
tables. Those tables are connected — an item points to a program, a program has dispensing rules,
an item has restrictions, restrictions have prescribing text, and so on. On its own, each table
is close to meaningless.

Our product's entire value is that **we connect those tables for the customer**. Every time we
connect two tables, we are making a claim: *"this row over here belongs with that row over there."*
If we get one of those claims wrong, the customer gets a confidently-presented wrong answer — a
wrong price, a wrong manufacturer, a wrong prescribing rule.

**We are not asking you to check our code. We are asking you to check our claims.**

Each section below states a connection in plain language, tells you how confident we are in it, and
lists the specific question we want you to answer.

---

## How to read the confidence ratings

| Rating | What it means |
|---|---|
| 🟢 **Stated by PBS** | The Government's own data dictionary says these two things are linked, and we link them exactly that way. If this is wrong, we have made an error. |
| 🟡 **Calculated by us** | We did arithmetic on PBS numbers. The inputs are all PBS; the output is ours. You should be able to check our maths and get the same answer. |
| 🔴 **Our judgement call** | PBS does not say this. We decided it. It might be a reasonable decision or a bad one — that is what we need you to tell us. **These must never be presented to a customer as PBS-official.** |

We also flag things we already know are broken:

> ⛔ **Known broken** — this does not work today and we know it.
> ⚠️ **Known wrong** — this returns a value, but the value is incorrect.
> 🏷️ **Not disclosed** — this is a judgement call we are not currently telling the customer about.

---

## Part 1 — The shape of the data

Before the connections, the building blocks. Six concepts carry almost everything.

**A schedule** is one month of PBS data. We store each month separately so a customer can ask
"what did this look like in March?"

**An item** is one PBS prescribing rule — a specific drug, form, strength and pack, listed under a
specific program. This is the atom of the whole system. Identified by a **PBS code**.

**A brand** is a specific manufacturer's product for that item. One PBS code can have several brands.
Identified by an **li_item_id**. This is a finer level of detail than the PBS code.

**A medicine** is the active ingredient, e.g. "metformin". Many items share one medicine.

**A restriction** is the clinical rule a prescriber must satisfy — the criteria, the authority
requirement, the treatment phase.

**A sponsor** (or organisation) is the company that lists the product.

> ### ⚠️ Two structural issues you should know about before reading further
>
> **1. Ingredient information is not stored per month.**
> Everything else in our system is stored separately for each schedule month. Ingredient-level
> information — including the ATC code we hold against an ingredient — is stored **once, globally**,
> and gets overwritten every time we load a new month.
>
> This means that if a customer asks "what was this ingredient's ATC code in January?", they get
> **today's** answer, not January's. We advertise that any endpoint can be queried against any
> historical month. For ingredient-level fields, that is not currently true.
>
> **Question for you:** does ingredient-level information (ATC code, therapeutic group) actually
> change between PBS schedules often enough to matter? If it does, we have a real historical-accuracy
> problem. If it essentially never changes, this is a low-priority cleanup.
>
> **2. Restrictions are stored as one copy per item, not once each.**
> A single restriction code can apply to thousands of items. We store a separate copy attached to
> each item. In current data, one restriction code is attached to **2,124 different items**.
>
> This mostly works, but our "look up a single restriction by its code" function returns **one
> arbitrary copy out of the 2,124** — and reports that copy's PBS code as though it were *the* item
> for that restriction. Two identical requests can return different items.
>
> **Question for you:** when someone looks up a restriction code, what do they expect to get back?
> Just the restriction text, or the restriction plus the full list of items it applies to?

---

## Part 2 — The connections we make

### 2.1 Item → its active ingredient
🟢 **Stated by PBS**

**The claim:** this PBS item contains this active ingredient.

**Why it matters:** it is how we answer "show me all the metformin products" and how ingredient
names appear on every response.

**Question for you:** none. This one is straightforward.

---

### 2.2 Item → its program
🟢 **Stated by PBS**

**The claim:** this item is listed under this PBS program (General Schedule, Closing the Gap,
Highly Specialised Drugs, etc.), and the program title we show is that program's official title.

**Question for you:** none, unless you think program titles change mid-schedule.

---

### 2.3 Item → its sponsor / manufacturer
🟢 **Stated by PBS** for the link · 🔴 **Our judgement call** for which one we show

**The claim:** this item is sponsored by this company.

**The judgement call:** an item can have **more than one** sponsor. In current data, 50 items do.
When that happens we show **one of them** and label it "primary manufacturer" — but we have no rule
for choosing. We take whichever the database returns first, which can genuinely differ between two
identical requests.

We do tell the customer "Primary sponsor only. Multiple organisations may be listed." We do **not**
tell them the choice is arbitrary.

**Questions for you:**
- Does the PBS actually designate a primary sponsor for multi-sponsor items? If so we should use it.
- If not, is showing an arbitrary one acceptable, or should we return all of them?
- Is "primary manufacturer" a misleading label for something we picked at random?

> Also worth knowing: only about **3% of items currently have any sponsor recorded at all**. For the
> other 97%, manufacturer comes back empty. That is a data-loading gap, not a logic problem.

---

### 2.4 Item → its ATC classification(s)
🟢 **Stated by PBS** for the link · 🟡 **Calculated by us** for "primary" · ⚠️ one bug

**The claim:** this item belongs to this WHO ATC class.

**The nuance:** some items have **split ATC** — more than one classification, each with a priority
percentage, reflecting different clinical uses. In current data, 32 items are split.

**What we do:** we sort by priority percentage and call the highest one "primary". That is a
reasonable reading of the PBS priority field.

⚠️ **Known wrong:** in the schedule-changes area of the product, a coding error means we return the
**lowest**-priority ATC code while labelling it "primary". This is a genuine bug, already logged.

**Questions for you:**
- Is "highest priority percentage = primary ATC" the correct interpretation of that PBS field?
- When two classifications have the *same* priority percentage, is there a tie-break rule we should
  be applying?

---

### 2.5 Item → its restrictions
🟢 **Stated by PBS**

**The claim:** to prescribe this item on the PBS, these clinical restrictions apply.

We return **all** restrictions for an item — nothing is truncated or sampled.

> ⚠️ **However, several fields inside each restriction are wrong or empty.** This is the most
> important section of this document. See Part 3.

---

### 2.6 Restriction → its prescribing text
🟢 **Stated by PBS**

**The claim:** this restriction's human-readable clinical criteria are made up of these text
components (indication, clinical criteria, authority statement, and so on).

We return all components, in ID order.

**Question for you:** does the PBS supply an explicit **ordering or sequence number** for these text
components? We currently order by ID, which we *assume* matches the intended reading order. If
clinical criteria are being displayed in the wrong order to a prescriber, that matters.

> This currently returns nothing, because the prescribing text tables have not been loaded.

---

### 2.7 Item → who is allowed to prescribe it
🟢 **Stated by PBS**

**The claim:** these prescriber types (Medical Practitioners, Nurse Practitioners, Midwives, Dental,
Optometrist) are authorised to prescribe this item.

We return the complete list. This one is clean and well-populated (10,324 records).

---

### 2.8 Item → its price
🟢 **Stated by PBS** for the link · 🔴 **Our judgement call** for which price we show

**The claim:** this item costs this much.

**The judgement call — and this is a significant one.** There are **two different price figures** in
the PBS data, and we use both, in different places, without a rule:

| Price figure | What it is | Where we use it |
|---|---|---|
| **Item-level price** | one figure per item per month | price history, safety net, market safety-net analysis, schedule comparison |
| **Brand-and-rule-level price** | varies by brand *and* by dispensing rule | item pricing detail, patient cost, dispensing context, all market price statistics |

These are not the same number. So today, our "patient cost" endpoint and our "safety net" endpoint
can give **different answers for the same drug**, because they read different columns.

There is a further judgement call inside the patient-cost calculation: when an item has several
brand-and-rule prices, we pick the **highest** one as "representative". We do not currently tell the
customer we did that.

**Questions for you — these are the most commercially important questions in this document:**
- For "what does this patient pay at the counter", which figure is correct?
- Is picking the highest price a sensible conservative default, or is it misleading? Should it be
  the lowest, or should we return a range?
- Is it ever acceptable for two endpoints in the same product to report different prices for the
  same drug?

> **Currently, all price fields are empty.** The brand-and-rule pricing table has zero rows, and the
> item-level price column is null for all 6,883 items. Nothing in the pricing story works today.

---

### 2.9 Item → what the patient actually pays
🟡 **Calculated by us**

**The claim:** the patient pays the lower of (the dispensed price) or (the copayment cap), and the
Government pays the rest.

**Our maths:**
- `patient pays = the smaller of [dispensed price, maximum patient charge]`
- `government pays = dispensed price − patient pays`

These should always add back up to the dispensed price. That is the test you can apply.

⚠️ **Known wrong:** there is a coding bug where a legitimate patient charge of **$0** — for example a
fully subsidised or CTG item — is treated as "no value supplied" and replaced with the **full
dispensed price**. So an item that should cost the patient nothing is reported at full price. This is
logged and is a straightforward fix.

⚠️ **Known wrong:** the concessional patient charge is calculated correctly in one endpoint (capped at
the dispensed price) and incorrectly in another (reported as the flat copayment regardless of whether
the drug costs less than that).

**Questions for you:**
- Is "the lower of dispensed price or copayment cap" the correct general rule?
- Are there categories of item where that rule does not hold — CTG, safety-net-reached, brand premium,
  special patient contribution?

---

### 2.10 How many scripts until the safety net
🔴 **Our judgement call**

**The claim:** at this per-script cost, a patient needs roughly this many scripts to reach the safety
net threshold.

**Our maths:** `safety net threshold ÷ what the patient pays per script`, rounded.

**What we ignore:** that thresholds accumulate across a **family**, not an individual; that a patient
takes several different medicines, not the same one repeatedly; that concessional and general status
can change mid-year; and that brand premiums may or may not count toward the threshold.

We label this "estimated". We think that is the right framing.

⚠️ In the market-wide version of this calculation, we report a "mean scripts to safety net **across
all items**" — but we actually compute it across only the 500 cheapest-to-threshold items. The
average is therefore biased and the description overstates it.

**Question for you:** is a single-drug scripts-to-threshold figure useful enough to publish at all,
given it ignores family aggregation and multi-drug regimens? Or does it invite misuse?

---

### 2.11 Substitution — "what else could I use instead?"
🔴 **Our judgement call** · 🏷️ **not fully disclosed**

**The claim:** these other PBS items contain the same active ingredient.

**What we do not do:** we do **not** check form, strength, or pack size.

So for a 500 mg immediate-release tablet, we will list a 1 g modified-release tablet as a
"substitute". Both contain the same ingredient. They are not interchangeable.

We tell the customer "same-ingredient matches only" and that formal brand-substitution groups are not
yet available. We do **not** warn that strengths and forms differ.

**Questions for you — please treat these as safety questions:**
- Is same-ingredient-only substitution safe to publish at all in a tier we sell into clinical
  decision support?
- What is the minimum additional matching we should require — same form? same strength? same
  route of administration?
- Should this endpoint carry a much stronger warning, or be withdrawn until we have real PBS
  brand-substitution group data?

The same blindness applies to our "60-day dispensing pair" endpoint.

---

### 2.12 Authority prescribing workflow
🔴 **Our judgement call** · ⛔ **currently has no data**

**The claim:** to prescribe this authority item, a prescriber must complete these steps.

**What we do:** we generate a checklist from the restriction's flags. If the authority method is
"Streamlined" we say "confirm criteria, record the code on the script". Otherwise we say "obtain
authority approval", plus extra steps for written or complex authority.

⛔ **The authority method field is empty for every one of our 22,769 restriction records.** So the
"streamlined" branch never runs, and **every** authority item receives the same generic
"obtain authority approval before prescribing" checklist regardless of what is actually required.

The same missing field means our market-level "authority method distribution" analysis returns an
empty result every time.

🏷️ We also do not tell the customer that this workflow is our interpretation rather than a PBS-issued
procedure.

**Questions for you:**
- Which PBS field carries the authority method (streamlined / telephone / written / online)? We are
  clearly not reading the right one.
- Are our checklist steps clinically and procedurally correct for each authority type?
- Should this be presented as guidance at all, or purely as structured data for the customer to
  interpret themselves?

---

### 2.13 Month-to-month change tracking
🟢 **Stated by PBS** for the source · 🔴 **Our judgement call** for the classification · ⛔ **partly broken**

**The claim:** these items were newly listed / delisted / repriced this month, and this change is
high, medium or low importance.

**The judgement call:** the PBS tells us only *"a row changed in this table, and it was an
insert/update/delete"*. We translate that into business language — NEW_LISTING, DELISTING,
PRICE_CHANGE — and assign a severity. Delistings we call HIGH; fee changes LOW.

⛔ **Known broken, and it is significant:**
- Our "new listings" and "delistings" endpoints **return nothing at all**, for any month, because of
  a text-matching error on the source table name.
- For the same reason, item listings and delistings are never classified as such. A **delisting —
  which we grade HIGH — is currently reported as "other modification, LOW importance".**
- For delisted items we also fail to look up the drug name, because we search for the item in the
  very month it was removed from. **Drug name is always blank on a delisting.**
- For the same reason, the "therapeutic alternatives for a delisted drug" feature — which we sell as
  a headline capability — **always returns an empty list**.

**Questions for you (for once these are fixed):**
- Is our severity grading sensible? Is a copayment change really as significant as a delisting?
- For a delisted drug, is "other items sharing the same ATC Level-4 subgroup" a clinically reasonable
  basis for suggesting alternatives, or is it too coarse?

---

### 2.14 "First in class" new listings
🔴 **Our judgement call**

**The claim:** this newly listed drug is the first of its kind on the PBS.

**Our test:** no other PBS item in the current month shares its ATC Level-5 code.

**Question for you:** is that a defensible definition? An arguably better test is *"nothing shared
this ATC code in the **previous** month"* — a genuinely novel therapy. Our current test compares
within the same month, which behaves differently. Which do you consider correct?

---

### 2.15 Market-level statistics
🟡 **Calculated by us** · ⚠️ **currently miscounted**

**The claim:** across this therapeutic class / this manufacturer's portfolio, here are the item
counts, the formulary split, and the price statistics.

⚠️ **Known wrong — this affects every market-level count.** When we connect items to their prices
and their sponsors, an item with 3 sponsors appears 3 times, and an item with 4 price rows appears
4 times. Our counting then counts the **appearances**, not the **items**.

We measured this on current data: **counts are inflated by 3.12×.**

The effect is visible in the output — an "F1 count" plus an "F2 count" can add up to more than the
"total items" figure printed directly above them, because the totals are counted correctly and the
breakdowns are not.

The same problem skews the average prices: a drug with several sponsors is weighted more heavily in
the average than a drug with one.

**Question for you:** when we report "number of items in this ATC class", should that count
**prescribing rules (PBS codes)** or **brands (li_item_ids)**? These are different numbers and
different customers may want different ones. Right now we report a third thing that is neither.

---

### 2.16 The price pressure index
🔴 **Our judgement call — entirely**

**The claim:** this therapeutic class is under high / moderate / low downward price pressure, scored
0–100.

**How we build it:** three signals, weighted.

| Signal | Weight |
|---|---|
| Share of F2 items carrying a brand premium | 50% |
| Recent price reductions in the last 3 months | 30% |
| Ratio of dispensed price to government price | 20% |

**Questions for you:**
- Are those the right three signals? Are we missing something obvious, like time since last
  price disclosure cycle?
- Where does 50/30/20 come from? It is not documented anywhere. Can you defend it, or should we
  publish the raw signals and let customers weight them?
- The third signal divides our two competing price figures (see §2.8) by each other. If those are
  meant to be the same quantity from different sources, that ratio carries no information. Does it
  make sense to you?

We currently also count price reductions from months **after** the month being analysed, which makes
historical index values unreproducible. That is a logged bug.

---

### 2.17 Biosimilar vs originator comparison
🔴 **Our judgement call**

**The claim:** for this ingredient, biosimilars cost this much and the originator costs that much.

**The judgement call:** we define "originator" as **"any item for this ingredient that is not flagged
as a biosimilar."** That bucket includes generics, which are not originators.

The PBS data contains a proper `originator brand indicator` field. **We load it and never use it.**

**Question for you:** confirm that the PBS originator-brand indicator means what we think, and that
we should be using it here instead of inferring.

---

## Part 3 — Fields we are getting wrong

These are not connection problems — they are individual values that are fabricated, empty, or derived
from the wrong source. **We consider this the highest-risk section of the audit** because each one is
presented to a customer as a PBS fact.

| Field | What we currently do | Evidence | What we need from you |
|---|---|---|---|
| **Streamlined authority code** | We set it equal to the restriction code. They are different PBS concepts. | Identical in **22,769 of 22,769** records | Which PBS field holds the real streamlined authority code — the short number a prescriber writes on a script? |
| **Authority required (yes/no)** | We derive it from the **complex** authority indicator. Complex authority is only a *subset* of authority. | Only 2,950 of 22,769 restrictions flagged, against 2,170 authority-required and 2,090 streamlined items | Is there a direct authority-required field? If not, what is the correct rule for inferring it? |
| **Authority method** | Empty for every record. | **0 of 22,769** populated | Which PBS field carries this? |
| **Indication** | Empty for every record, but our own documentation tells Base-tier customers they receive it. | **0 of 22,769** populated | Do PBS restrictions carry an indication directly, or does it only exist in the separate indications dataset? |
| **Restriction text** | Contains raw HTML markup. A customer displaying it as plain text will see tags. | **10,141 of 22,769** contain markup | Should we strip markup, or is a separate clean-text field available? |
| **Continuation only (yes/no)** | We look for the letters "continu" in the treatment phase text. | — | Is that a safe rule, or is there a proper flag? |
| **Benefit type** | Our data loader writes long words ("authority_required"); the rest of the system expects single letters ("A"). They never match. | 4 long-form values in the data | Which form do you want in customer-facing responses? |

---

## Part 4 — Where our published documentation is wrong

Two errors in material we already show customers:

**1. F1 and F2 are described backwards.**
Our documentation and code say *"F1 = brand-substitutable, F2 = not substitutable."* Our
understanding is that this is inverted — **F1 is the single-brand formulary; F2 holds
multiple-brand medicines subject to price disclosure**, and brand substitution applies to F2.

**Please confirm which is correct.** This appears in our public offering document, our tier
documentation, and in a T3 API response field, so getting it right matters.

**2. F1/F2/F3 are labelled as a cost-effectiveness ranking.**
One of our endpoints returns:
- "Formulary 1 — highest cost-effectiveness"
- "Formulary 2 — medium cost-effectiveness"
- "Formulary 3 — lowest cost-effectiveness"

We do not believe F1/F2 is a cost-effectiveness ranking at all, and we do not believe F3 is a current
PBS formulary. **Please tell us the correct plain-English description for each.**

Separately, our data contains a fourth formulary value — **`CDL`, on 208 items** — which we do not
recognise and currently display as "Unknown formulary". What is it?

---

## Part 5 — The state of the data itself

Independent of any logic question, you should know what is and is not loaded, so you do not waste
time reviewing outputs that are empty for unrelated reasons.

**The month with data is marked as a failed load.** We hold two months. March has 6,883 items but is
flagged `failed`. April is flagged `complete` but has **zero items**. Because the system always
serves "the most recent completed month" by default, **the API currently returns nothing unless you
explicitly ask for March.**

**These are completely empty:**

| Missing | What stops working |
|---|---|
| All brand-and-rule pricing | Every pricing, patient-cost and market price statistic |
| Item-level prices | Price history, safety net, schedule price comparison |
| Brand identifiers (li_item_id) | Brand-level anything |
| Month-to-month change records | All six change-tracking endpoints, listings pipeline |
| AMT clinical terminology | The AMT endpoint |
| Prescribing text | Full restriction text, restriction enrichment |
| Fees, indications | Those endpoints |

**Also empty:** the biosimilar flag is false for all 6,883 items, so the biosimilar comparison
returns nothing.

**What is well populated and worth reviewing now:** items (6,883), restrictions (22,769), ATC codes
and their item links (7,891 / 6,756), prescriber authorisations (10,324), programs (17),
criteria and parameters.

---

## Part 6 — What we would most like you to answer

If you only have time for a few, these are the ones where being wrong costs us the most.

1. **Which price is the patient price?** (§2.8) — two competing figures, no rule, two endpoints
   already disagreeing.
2. **Which PBS fields carry authority method and authority-required?** (Part 3) — our authority
   workflow, our clinical checklists, and a whole T4 analysis are all built on fields that are empty
   or wrongly derived.
3. **Are F1 and F2 backwards in our documentation?** (Part 4) — this is published and customer-facing.
4. **Is same-ingredient substitution safe to ship without matching strength and form?** (§2.11) —
   this is the one with genuine patient-safety exposure.
5. **When we say "number of items", should that mean prescribing rules or brands?** (§2.15) —
   determines how we fix every market-level count.
6. **Does ingredient-level ATC actually change between schedules?** (Part 1) — determines whether our
   historical-accuracy claim is broken or merely theoretically imperfect.

---

*This document describes the system as at 2026-07-28. Every figure quoted (record counts, percentages,
"0 of 22,769") was measured directly against the development database on that date, not estimated.*

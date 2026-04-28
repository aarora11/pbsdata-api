# PBS Pocket — Product Specification

## What This Is

A free, public web tool that makes Australian PBS schedule data legible to ordinary people.
Not an app that gives medical advice. Not a clinical tool. A transparency layer over
publicly-available government data — the same data that already exists on pbs.gov.au,
presented in plain English, with context that helps people understand their entitlements.

---

## Liability Design Principles (Non-Negotiable)

These principles are baked into every feature, not bolted on as disclaimers.

**What we are:**
- A display tool for publicly available Australian Government PBS data
- An information resource, equivalent to a well-organised government website
- A calculator using published government figures (copayments, thresholds)

**What we are not:**
- A medical advice service
- A prescribing tool
- A recommendation engine
- A clinical decision support tool

**Implementation rules:**

1. **We never recommend.** We never say "you should take X" or "switch to Y" or
   "ask for Z". We say "according to the PBS schedule, this medicine is available in
   the following forms." Every action step is directed to a GP or pharmacist.

2. **We display government data verbatim or summarised.** Restriction criteria,
   clinical indications, and prescribing conditions are shown as written by the
   Department of Health — we do not paraphrase clinical criteria.

3. **Every data point links to its source** on pbs.gov.au. We are a lens, not an oracle.

4. **Global disclaimer** on every page: *"This information comes directly from the
   Australian Government PBS Schedule and is provided for general information only.
   It is not medical advice. Always talk to your doctor or pharmacist before making
   any decisions about your medications."*

5. **Sensitive pages carry contextual warnings.** Restriction pages carry: *"These
   are the official PBS criteria. Whether you meet them is a clinical decision for
   your prescriber."* Delisting pages carry: *"If this affects you, speak to your
   pharmacist or GP before making any changes to your medication."*

6. **No personalised health analysis.** The safety net calculator uses numbers the
   user inputs. We calculate costs, not health outcomes.

7. **TGA and clinical scope are explicitly out of scope.** We never display
   information about side effects, drug interactions, dosing, or clinical
   appropriateness. A clear scope statement is shown in the About page.

---

## User Personas

**1. The Chronic Condition Patient (primary)**
Managing 2–5 ongoing prescriptions. Cost is a real concern. Doesn't know about the safety
net or 60-day scripts. Just wants to know: how much will this cost me and can I pay less?

**2. The Carer / Parent**
Managing medications for a child, elderly parent, or partner. Tracking multiple PBS items
across family members. Wants to know when a medicine changes, gets delisted, or a cheaper
form becomes available.

**3. The Recently Diagnosed Patient**
Just told they need a new long-term medication. Googling whether it's on the PBS, what
they'll pay, whether there are criteria they need to meet for the GP to prescribe it.

**4. The Patient Advocate / Support Group Member**
Tracking whether a specific drug has been listed or delisted. Monitoring PBS changes
month-on-month for conditions affecting their community (cancer, rare disease, MS, diabetes).

**5. The Journalist / Researcher**
Wants to understand what changed this month across the PBS — new listings, delistings,
price changes. Needs a readable, citable source.

**6. The Financially Stressed Patient**
Hit a bad month. Considering skipping a script because of cost. Needs to understand
their full entitlements quickly: concession eligibility, 60-day options, safety net
proximity.

---

## Features

---

### Feature 1: Medicine Search

**The core entry point for the entire app.**

User types any brand name or generic ingredient name. Results show all PBS items matching
that search, grouped by ingredient.

**Each result shows:**
- Brand name and generic (ingredient) name
- Strength and form (e.g., "Tablet 10 mg")
- Pack size
- **What you pay today:** $25.00 (general) / $7.70 (concession)
- **60-day eligible?** Yes / No — with a plain-English explanation if yes
- **Biosimilar?** Yes / No badge
- **PBS program** (General, Section 100, etc.)
- Link to the official PBS listing

**What it does NOT show:**
- Clinical indications or conditions
- Dosing instructions
- Side effects
- Any comparison framed as a "recommendation"

**UI guidance:**
A Google-style single search box. No forms, no dropdowns. Autocomplete on ingredient
and brand name. Mobile-first.

**Data source:** `items`, `medicines`, `copayments` tables.

---

### Feature 2: 60-Day Prescription Checker

**Highest impact feature. Could save Australians hundreds of millions in aggregate.**

724 PBS items are eligible for 60-day prescriptions — twice the supply for one copayment.
Many patients and even GPs don't know. The government website has a searchable table but
it is buried.

**The experience:**
1. Search a medicine
2. If 60-day eligible: a prominent banner — "This medicine is available on a 60-day prescription"
3. Plain-English explanation: "A 60-day prescription gives you twice the supply for the
   same $25 co-payment. That's a potential saving of up to $150 per year for this medicine."
4. **Not a recommendation.** Call to action: *"Ask your doctor or pharmacist if a 60-day
   prescription is right for you."* Not "you should get a 60-day script."
5. Savings calculator: how many scripts per year? Shows total annual saving at current
   vs 60-day frequency.

**Caveats shown:**
- "Your doctor decides whether to prescribe 30 or 60 days based on your clinical situation."
- "Not all eligible medicines are appropriate for all patients."

**Data source:** `items.sixty_day_eligible`, `copayments.general`, `copayments.concessional`.

---

### Feature 3: Safety Net Calculator

**For families managing ongoing prescription costs.**

The PBS safety net means that once you (or your family) spend a threshold amount in a
calendar year, all further PBS scripts are free (general) or further reduced (concession).
Most patients don't know this exists or don't track toward it.

**Thresholds (2026):**
- General: $1,748.20 → then free
- Concession: $277.20 → then $0 per script (Safety Net)

**The experience:**
1. User selects: General patient / Concession card holder
2. User enters: how many scripts per month (or per person in a family)
3. Calculator shows:
   - Total annual cost at current rate
   - Month when they'd hit the safety net threshold
   - Cost after hitting safety net (free / reduced)
   - Net annual saving from the safety net
4. Family mode: add multiple people, combine spending
5. Prominent note: *"This is a general estimate based on published PBS co-payment rates.
   Actual costs may vary. Record-keeping is done by your pharmacy — ask them for a
   Prescription Record Form."*

**Important scope boundary:** We calculate costs. We do not tell users how to claim
the safety net or determine their eligibility. We link to Services Australia for that.

**Data source:** `copayments` table (general, concessional, safety_net_general,
safety_net_concessional thresholds).

---

### Feature 4: Monthly PBS Changes Digest

**For carers, patient advocates, and anyone who needs to know what changed.**

The government publishes a summary of changes each month but it's structured for
clinicians, not patients. We re-present it in plain English.

**Change types surfaced:**
- **New listings** — medicines newly subsidised on the PBS
- **Delistings** — medicines removed from the PBS subsidy
- **Restriction changes** — conditions for prescribing a medicine changed
- **Price changes** — government price or co-payment changed for a specific item
- **New 60-day eligibility** — a medicine became available on 60-day scripts

**Per item shown:**
- What changed (in plain English — not clinical text)
- Effective date
- Link to official PBS change notice

**Delisting treatment (sensitive):**
Delistings carry a prominent notice: *"If you are currently taking this medicine,
do not stop or change your medication without speaking to your GP or pharmacist first.
There may be alternative medicines available on the PBS."* No alternative is named.
We direct to a healthcare provider.

**New listing treatment:**
*"This medicine has been added to the PBS. If you think this might be relevant to you,
speak to your doctor."* We do not say it treats any specific condition in a consumer
context — we show the clinical indication text from the PBS verbatim with a disclaimer
that it is official government text.

**Data source:** `summary_of_changes`, `changes` tables.

---

### Feature 5: Medicine Watchlist (Email Alerts)

**For anyone with a long-term prescription, carer, or patient advocate.**

Users submit medicines they want to monitor. No account needed — just an email address.
When our monthly ingest detects a change for a watched medicine, we send a plain-English
email summarising what changed.

**Alert types:**
- Delisted from PBS
- New form or strength added
- 60-day eligibility added or removed
- Restriction changed (criteria updated)
- Government price changed

**What the email says:**
Plain English description of the change, the effective date, a link to the PBS source,
and: *"If you have questions about how this affects your medication, speak to your
pharmacist or GP."* The email never says "you should" anything.

**Privacy:**
Email addresses are stored hashed. Not sold, not used for marketing. Deleted on
unsubscribe. GDPR/Privacy Act compliant.

**Data source:** `summary_of_changes`, `changes`, `items`. Triggered by post-ingest
webhook pipeline.

---

### Feature 6: Brand & Form Browser

**For patients trying to understand why their pharmacy gave them a different brand.**

Many patients are confused when they receive a different brand of the same medicine.
This feature shows all available PBS brands for a given ingredient, helping them
understand they are therapeutically equivalent (according to PBS listing status — not
a clinical claim from us).

**The experience:**
1. User searches an ingredient (e.g., "metformin")
2. Sees all PBS-listed brands, strengths, and forms
3. Each brand shows: brand name, manufacturer/sponsor, form, pack size
4. A banner states: *"Multiple brands of the same medicine are listed on the PBS.
   Your pharmacist may substitute brands. If you have questions about brand substitution,
   ask your pharmacist."*

**What it does NOT say:**
- That brands are interchangeable (that's a clinical claim)
- Which brand to prefer
- That any brand is cheaper or better

**Biosimilar note:**
Where a biosimilar flag exists, we show: *"This medicine has a biosimilar listed on
the PBS. Biosimilar medicines have been assessed by the TGA as comparable in quality,
safety and efficacy to the reference medicine. Ask your doctor or pharmacist if you
have questions."* — This is verbatim TGA/government language, not our clinical opinion.

**Data source:** `items`, `medicines`, `organisations` tables.

---

### Feature 7: ATC Class Explorer

**For patient advocates, researchers, and the curious.**

The ATC (Anatomical Therapeutic Chemical) classification system groups medicines by
therapeutic class. This lets users browse all PBS-listed medicines in a class —
e.g., "all PBS-listed diabetes medicines" or "all PBS-listed blood pressure medicines."

**Use cases:**
- Patient advocacy groups monitoring all listings in a disease area
- Journalists writing about a therapeutic class
- Patients wanting to understand the landscape of options their doctor might consider
  (not a recommendation — we explicitly frame it as "medicines in this class that are
  listed on the PBS")

**Important framing:**
*"This shows PBS-listed medicines in this drug class. It is not a list of treatment
options for any condition. Which medicines are appropriate for you is a decision for
your doctor."*

**Data source:** `atc_codes`, `item_atc_relationships`, `items`, `medicines`.

---

### Feature 8: Concession Entitlement Explainer

**For people who don't know they might be paying $7.70 instead of $25.**

A simple page explaining concession card eligibility in plain language, with a
calculator: "If you take X scripts per month, switching to concession pricing
would save you $Y per year."

**What it covers:**
- What cards qualify (Pension Concession Card, Health Care Card, Commonwealth
  Seniors Health Card, DVA cards)
- How much the co-payment drops ($25 → $7.70)
- How much lower the safety net threshold is ($1,748 → $277)
- How to find out if you're eligible — links to Services Australia, Centrelink

**What it does NOT do:**
- Tell users whether they are eligible (eligibility depends on income, benefits,
  circumstances — we are not Centrelink)
- Recommend applying for any benefit

**Data source:** `copayments` table.

---

### Feature 9: Delistings Tracker

**For patients, carers, and patient advocacy groups.**

A dedicated view of medicines recently delisted from the PBS, with enough context
for patients to understand the impact and know to speak to their GP.

This is a distinct feature from the monthly digest because:
- Delistings are high-stakes — patients may face sudden cost increases
- The Panadol Osteo delisting (2024–25) caught thousands of pensioners off-guard
- Patient advocacy groups actively monitor delistings for their conditions

**The experience:**
- Filterable by date range, ATC class, section
- Each delisting shows: medicine name, what section it was listed under,
  effective date of delisting
- Prominent contextual warning on every delisting: *"If you currently take this
  medicine, speak to your pharmacist or GP before making any changes. Do not stop
  taking a prescribed medication without medical advice."*
- Link to official PBS change notice

**Data source:** `summary_of_changes` (change_type = DELETE), `changes`.

---

### Feature 10: New Listings Monitor

**For patient advocacy groups, rare disease communities, and recently diagnosed patients.**

Mirror of the delistings tracker for new additions — medicines newly added to the PBS.
This is the "good news" feed: a treatment that was previously unaffordable may now be
subsidised.

**Key audiences:**
- Rare disease communities watching for PBAC decisions to take effect
- Oncology patient groups tracking new cancer drug listings
- Patients on private prescriptions who want to know if their medicine has been listed

**Treatment:**
New listing shows: ingredient, brand, indication section from PBS (verbatim, labelled
as official PBS text), effective date, program.

*"If you think a newly listed medicine may be relevant to you, speak to your doctor.
Only a doctor can assess whether a medicine is appropriate for your situation."*

**Data source:** `summary_of_changes` (change_type = NEW), `items`, `medicines`.

---

### Feature 11: PBS Program Explainer

**For patients confused about why their medicine is prescribed differently.**

Different PBS programs (General, Section 100, Highly Specialised Drugs, Palliative Care,
Botulinum Toxin, etc.) have different supply and prescribing arrangements. Many patients
don't understand why their medicine comes from a hospital rather than a pharmacy, or
why it requires a specialist.

**Simple explainer page per program:**
Plain-English description of what the program is, who it applies to, and what it means
for access. No clinical criteria. Link to official government page for detail.

**Data source:** `programs` table.

---

### Feature 12: Authority Prescription Explainer

**For patients who have been told their GP needs "authority" to prescribe their medicine.**

Many patients are confused or distressed when their doctor says they need to apply for
authority before prescribing. This feature explains the two types of authority in plain
English.

**Two types explained:**
- **Streamlined Authority:** GP records a code on the script. No phone call needed.
  Faster. Still requires the patient to meet clinical criteria — but the paperwork is minimal.
- **Written/Telephone Authority:** GP must contact Services Australia. Slower. For
  medicines with higher misuse risk or more complex criteria.

**Per-medicine view:**
When viewing a medicine, if it is authority-required, we show: Authority type
(streamlined/telephone/written), streamlined code if applicable, and a note:
*"Whether you meet the criteria for authority prescribing is a clinical decision
for your doctor. This page shows the type of authority required according to the
PBS schedule."*

**What we do NOT show:**
- The clinical criteria in detail on the consumer-facing view (to avoid patients
  self-diagnosing or pressuring GPs)
- We show a plain-English statement like "Authority required: your doctor must confirm
  you meet the clinical criteria before prescribing this at the PBS subsidy price"

**Data source:** `items.benefit_type`, `restrictions.authority_required`,
`restrictions.authority_method`, `restrictions.streamlined_code`.

---

## What Is Explicitly Out of Scope

The following will never be built into this product:

| Out of scope | Why |
|---|---|
| Drug interactions | Clinical domain — requires TGA-licensed databases, liability exposure |
| Dosing recommendations | Clinical domain — prescribing decision |
| Side effects / adverse events | Clinical domain — use NPS MedicineWise or CMI |
| "Is this medicine right for me?" | Medical advice |
| ARTG / TGA approval status | Separate regulatory dataset |
| Private (non-PBS) medication pricing | Not in PBS data |
| Pharmacy stock availability | Real-time operational data we don't have |
| Personalised medication plan | Medical advice |
| Drug-condition matching | Clinical advice |

---

## Legal & Liability Architecture

### Data provenance
All data displayed is sourced from the Australian Government PBS Schedule, published
by the Department of Health and Aged Care under an open licence. We do not create,
modify, or interpret clinical data.

### Disclaimer placement
- Global footer on every page
- Inline contextual warnings on sensitive features (delistings, authority info, changes)
- Dedicated About/Disclaimer page
- Email alert footer

### Standard disclaimer text
> *This website displays publicly available information from the Australian Government
> PBS Schedule. It is for general information purposes only and does not constitute
> medical, clinical, or pharmaceutical advice. It is not a substitute for professional
> advice from a doctor, pharmacist, or other qualified health professional. Always
> consult a qualified health professional before making decisions about your medications.
> We accept no liability for any loss or damage arising from reliance on the information
> displayed. Data sourced from [pbs.gov.au](https://www.pbs.gov.au).*

### What we never say
- "You should take..."
- "Switch to..."
- "This medicine treats..."
- "This is appropriate for your condition..."
- "Based on your symptoms..."
- "You qualify for..."

### Action language
Every action prompt follows the pattern:
- "Ask your doctor or pharmacist about..."
- "Speak to your GP if..."
- "Your pharmacist can advise you on..."
- "For clinical advice, consult a health professional"

---

## Data We Have (and What Each Feature Uses)

| Feature | Tables Used | Coverage |
|---|---|---|
| Medicine Search | `items`, `medicines`, `copayments` | 13,813 items, 1,227 medicines |
| 60-Day Checker | `items.sixty_day_eligible` | 724 eligible items |
| Safety Net Calculator | `copayments` | Thresholds: $1,748.20 / $277.20 |
| Monthly Changes | `summary_of_changes`, `changes` | 23,116 change records |
| Medicine Watchlist | `items`, `changes`, webhooks | Full coverage |
| Brand Browser | `items`, `medicines`, `organisations` | 413 multi-brand ingredients |
| ATC Explorer | `atc_codes`, `item_atc_relationships` | 7,891 ATC codes |
| Concession Explainer | `copayments` | Live rates from schedule |
| Delistings Tracker | `summary_of_changes` | Full change history |
| New Listings | `summary_of_changes`, `items` | Full change history |
| Program Explainer | `programs` | 17 programs |
| Authority Explainer | `items`, `restrictions` | 45,842 restrictions |

---

## Tech Stack Recommendation

**Backend:** FastAPI (already built) — the API is the data layer.

**Frontend:** Next.js with static generation for medicine pages (good for SEO —
people Google "[medicine name] PBS price"), server-side for the dynamic features.

**Hosting:** Vercel for frontend (free tier), existing Railway/Fly deployment for API.

**Search:** Postgres full-text search on `ingredient` and `brand_name` is sufficient
for v1. Upgrade to Typesense or Meilisearch if autocomplete latency becomes an issue.

**Email alerts:** Resend or Postmark — simple transactional email, low volume.

**Analytics:** Plausible (privacy-respecting, GDPR-friendly, no cookie banner needed).

---

## Build Priority

| Priority | Feature | Reason |
|---|---|---|
| P0 | Medicine Search | Core entry point, everything else hangs off it |
| P0 | 60-Day Checker | Highest impact, embedded in search results |
| P0 | Monthly Changes Digest | Highest SEO value, unique content |
| P1 | Safety Net Calculator | High emotional value for chronic patients |
| P1 | Delistings Tracker | Urgent need — patients find out at the pharmacy |
| P1 | New Listings Monitor | Patient advocacy demand |
| P2 | Medicine Watchlist / Alerts | Requires email infrastructure |
| P2 | Brand & Form Browser | Lower urgency but high utility |
| P2 | Concession Explainer | Static content, low build cost |
| P3 | ATC Explorer | Research / advocacy audience |
| P3 | Authority Explainer | Embedded in medicine detail page |
| P3 | Program Explainer | Static content |

---

## Success Metrics

We are a public good, not a revenue product. Metrics that matter:

- Unique monthly visitors
- "60-day eligible" pages viewed (proxy for information reaching patients)
- Email watchlist subscribers (proxy for ongoing engagement)
- Organic search traffic for medicine names (SEO reach)
- Qualitative: mentions by patient advocacy groups, pharmacy newsletters, media

What we do not optimise for: session time, return visits, conversion. We want people
to find what they need quickly and leave to talk to their pharmacist or GP.

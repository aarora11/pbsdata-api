# PBSdata.io — Business Plan

## The One-Line Pitch

We are the developer infrastructure layer for Australian PBS schedule data — a clean, reliable REST API sitting between the government's complex raw feed and the software teams who need it.

---

## The Problem

The Australian government publishes PBS schedule data monthly through a raw, paginated API
(v3.6.5 as of Dec 2025) with known data accuracy issues, documentation gaps, null-value
inconsistencies, and rate limiting. Until recently, vendors could fall back on flat XML/Text
file exports — but **those are discontinued from 1 May 2026.**

Every piece of software that touches PBS data in Australia now has to either:

1. Build and maintain a direct integration with the government API — handling pagination,
   rate limiting, data normalisation, monthly re-ingestion, schema changes, and change diffing
2. Pay MIMS Australia for an enterprise drug database contract (opaque pricing, annual commit,
   heavy sales process)
3. Use our API

The government API is free but raw. MIMS is comprehensive but enterprise-gated.
There is no clean, self-serve, developer-friendly middle layer. That is the gap.

---

## Market Context

### The Forcing Function — May 2026

PBS XML and PBS Text files are **discontinued 1 May 2026.** All software vendors currently
using these files must migrate. This is not optional and the deadline is imminent. It creates
an immediate, well-defined pool of buyers actively solving this problem right now.

### Who Needs PBS Data

| Segment | Description | Size Signal |
|---|---|---|
| **Pharmacy dispensing software** | Fred Dispense (2,500+ sites), Minfos, RxOne, Z Dispense | 6,000+ community pharmacies in AU |
| **GP / clinic software** | Best Practice, Medical Director, Zedmed — need PBS item lookups for e-prescribing | 30,000+ GPs |
| **Telehealth platforms** | Eucalyptus, Heliuscare, instantconsult — need drug subsidisation lookup | Fast-growing |
| **Clinical decision support** | MedAdvisor, Heidi Health (AI-powered pharmacy tools) | 95% of AU pharmacies via MedAdvisor |
| **Hospital pharmacy** | Pharmhos, MEDITECH, Oracle Cerner AU installs | 700+ hospitals |
| **Insurers / health funds** | Need formulary and price data for gap cover calculations | Medibank, BUPA, etc. |
| **Researchers / academia** | Need structured, versioned, historically queryable PBS data | AIHW, universities |
| **AI / LLM developers** | Need structured PBS data as RAG context for clinical copilots | Emerging category |

### The Incumbent (and Why We Win)

**MIMS Australia** is the dominant drug database provider. They offer PBS restrictions, pricing,
and clinical decision support modules via a proprietary API. But:

- Pricing is opaque — custom enterprise contracts, no self-serve
- Long sales cycles — not suited for startups and indie developers
- Focused on clinical/therapeutic content (drug interactions, CMI), not PBS operational data
- No webhook/change-notification layer
- No public documentation or free tier

We are not replacing MIMS. We focus on **PBS operational data** — the schedule itself:
items, restrictions, fees, changes, dispensing rules. The data the government publishes,
but served cleanly.

---

## What We Build (Product)

A REST API with:

- **Monthly auto-refresh** — ingested within hours of each PBS schedule release
- **Clean schema** — normalised, typed, consistent null handling
- **Change tracking** — diff between any two schedule months; know exactly what changed
  for a given PBS code
- **Webhooks** — push notifications when a new schedule publishes or a specific item changes
- **Historical access** — query any past schedule month (gated by tier)
- **Search** — fuzzy search on ingredient, brand name, ATC code
- **Fast responses** — cached, sub-100ms for common queries

### What We Are NOT Building

- Drug interaction checking (that's MIMS / Micromedex territory)
- Clinical decision support rules
- Patient data or prescribing records
- Anything requiring TGA or ARTG integration (separate dataset)

---

## Monetisation Model

### Tiered SaaS — Self-Serve First

| Tier | Price | Limits | Target |
|---|---|---|---|
| **Free** | $0/mo | 500 req/mo, current month only | Indie devs, prototypes, students |
| **Starter** | $49/mo | 10,000 req/mo, 3 months history | Small apps, solo developers |
| **Growth** | $199/mo | 100,000 req/mo, 12 months history, webhooks | Startups, telehealth apps |
| **Scale** | $499/mo | 500,000 req/mo, full history, priority support | Mid-size software vendors |
| **Enterprise** | Custom | Unlimited, SLA, dedicated ingest, embargo access | Fred IT, hospital software, health funds |

### Revenue Drivers

1. **Self-serve subscription** — primary volume driver; developers sign up, get a key, start
   building. No sales call required. Target: 200 paying customers in Year 1.

2. **Enterprise contracts** — pharmacy software vendors need guaranteed SLAs, embargo-schedule
   access (pre-release), and dedicated support. Single enterprise deal at $2,000–$5,000/mo
   matches 10–25 self-serve Growth customers.

3. **Webhook premium** — real-time schedule change notifications are a natural upsell.
   Time-sensitive use cases (formulary apps, price comparison tools) pay for push over poll.

4. **Data export / bulk access** — monthly CSV snapshots for analytics, research, and
   compliance teams. Sold as an add-on or a higher-tier feature.

5. **Embargo access** — PBS pre-release schedules are available a few days before public
   release. Software vendors need this to prep their systems. Gated at Enterprise tier.

### Revenue Projections (Conservative)

| | Year 1 | Year 2 | Year 3 |
|---|---|---|---|
| Free users | 500 | 1,500 | 3,000 |
| Paid self-serve (avg $150/mo) | 80 | 300 | 600 |
| Enterprise deals (avg $2,500/mo) | 3 | 10 | 20 |
| **MRR** | ~$19,500 | ~$70,000 | ~$140,000 |
| **ARR** | ~$234K | ~$840K | ~$1.68M |

These are conservative. A single deal with a company like Fred IT (serving 2,500 sites) or
MedAdvisor (95% of AU pharmacies) would materially move these numbers.

---

## Competitive Landscape

| | PBSdata.io | Government API | MIMS Australia | Build-it-yourself |
|---|---|---|---|---|
| Developer-friendly | ✅ | ❌ | ⚠️ | — |
| Self-serve / free tier | ✅ | ✅ (raw) | ❌ | — |
| Change tracking | ✅ | ❌ | ❌ | Expensive |
| Webhooks | ✅ | ❌ | ❌ | Expensive |
| Historical data | ✅ | ❌ | ❌ | Expensive |
| SLA / reliability | ✅ (paid) | ❌ | ✅ | Varies |
| Drug interactions | ❌ | ❌ | ✅ | — |
| Pricing | Transparent | Free (raw) | Opaque | CapEx |

---

## Go-to-Market Strategy

### Phase 1 — Developer Adoption (Months 1–6)

- Ship public documentation and a free tier on pbsdata.io
- Target the May 2026 XML discontinuation wave — find developers in the PBS developer
  mailing list, GitHub, and health tech Slack communities who are actively migrating
- Post in r/australia, health tech forums, and Dev.to about the XML discontinuation and
  how PBSdata.io solves it
- Reach out directly to open-source pharmacy tools and small telehealth startups

### Phase 2 — SMB Growth (Months 6–18)

- Convert free users to Starter/Growth via usage limits and webhook upsell
- Publish a public changelog (powered by our own `/v1/summary-of-changes` endpoint) as
  SEO content — developers Google "what changed in PBS April 2026"
- Partner with health tech accelerators (Stone & Chalk, BlueChilli health cohort)
- Speak at HealthTech AU, HISA conferences

### Phase 3 — Enterprise (Months 12+)

- Direct outreach to Fred IT, Zedmed, Best Practice, hospital pharmacy vendors
- Offer embargo access as the enterprise anchor feature (they need to prep before schedule release)
- SLA-backed uptime and dedicated ingest windows

---

## The AI Opportunity

LLM-based clinical decision support tools are emerging rapidly in Australian healthcare
(Heidi Health + MedAdvisor partnership in 2024 as an example). These tools need structured,
current PBS data as retrieval-augmented generation (RAG) context to answer questions like:

- "Is this medication subsidised for this indication?"
- "What are the authority prescribing criteria for this drug?"
- "What changed this month that affects my patient's current script?"

We are the natural data layer for these use cases. An AI developer building a clinical
copilot does not want to scrape pbs.gov.au — they want a clean JSON API with embeddings-ready
text fields. This is a premium tier play, potentially bundled with a dedicated semantic search
endpoint.

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Government builds a better API | Low | They've been improving since 2023; API v3 is much better but still raw. They are not in the developer-platform business. |
| MIMS adds a self-serve tier | Medium | Our moat is PBS-specific operational data and change tracking, not the broad clinical content MIMS owns. |
| Low willingness to pay (data is "free") | Medium | The raw data is free. The reliability, normalisation, caching, change-diffing, and webhooks are not. Same argument Stripe makes about payment rails. |
| PBS API schema changes break our ingest | High (routine) | Monthly ingest monitoring, automated schema drift detection, alerting on field count changes. |
| Small Australian market | Real | AU is the wedge. The model is replicable for PHARMAC (NZ), NHS BSA (UK), and other national drug schedule APIs globally. |

---

## What We Need to Ship to Be Viable

### Immediate (to open the door)

1. `Dockerfile` + production `docker-compose` — deployable anywhere
2. Wire `/internal/ingest` to actually run the pipeline (currently a stub)
3. Cron job for automatic monthly ingest on the 1st
4. Webhook delivery worker — fire HTTP POSTs after each ingest
5. `POST /auth/keys` — self-serve API key provisioning
6. Public documentation (Swagger/Redoc is already generated by FastAPI)
7. Hosted deployment (Railway or Fly.io to start, ~$50/mo)

### Shortly After (to charge money)

8. Stripe integration for subscription billing tied to API key tier
9. Usage dashboard — show developers their request count vs limit
10. Ingest failure alerting — email/Slack when a monthly run fails
11. Embargo access implementation for enterprise tier

### Total build time to revenue: 3–4 weeks of focused engineering

---

## Summary

The May 2026 PBS XML discontinuation is a once-in-a-decade forcing function that will push
hundreds of software vendors to rebuild their PBS data pipelines. The government API is
technically usable but operationally hostile for developers. MIMS is enterprise-only and
clinically-focused. There is a clear, unoccupied position for a developer-first, self-serve,
transparent-pricing PBS data API.

The core infrastructure is already built. The data pipeline works end-to-end. The API serves
real data. The remaining work is operational plumbing — deployment, billing, automation — not
product invention.

The moat is not the data (it's public). The moat is reliability, normalisation, change
tracking, and being the easiest path for a developer to get PBS data into their app on a
Tuesday afternoon without reading 40 pages of government documentation.

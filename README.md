# MGC Sales Copilot

A sales tool for **MGC Aurora Heights** that tackles the two problems in the brief:

1. **"Staff answer the same questions from PDFs, and get them wrong."** A document assistant answers
   in plain language from MGC's own brochure, price list and booking FAQ. It **always cites
   document and section**, calculates prices exactly, flags where the documents disagree, and
   refuses what they don't cover.
2. **"Nobody knows which lead to call first."** A lead-scoring model, **trained from scratch** on
   9,000 historical leads, ranks new leads and explains each score.

*Deployed the frontend and the backend too: frontend on vercel and backend on railway*

visit the following link to test the app, it'll run same on all the machines

**Live demo:** https://mgc-task-ai-engineer-frontend.vercel.app/

| Part | Where |
|---|---|
| 1 · AI development: document assistant | `app/knowledge/`, `app/agents/` |
| 2 · Database: schema + 3 queries | `schema.sql`, `queries.sql`, `sql/` |
| 3 · ML: lead scoring with a time split | `app/ml/`, `reports/ml_report.json` |
| 4 · Web: one page for both | `frontend/` (served by FastAPI) |

---

## Run it

Requires Python 3.10+. No API keys needed.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m app.ml.train          # trains the lead model (~30 s); also runs automatically on first start
python -m app                   # or: uvicorn app.main:app --port 8000
```

Open **http://localhost:8000**. Swagger UI: http://localhost:8000/docs.

Check the brief's six questions plus four adversarial ones, end to end:

```bash
python scripts/eval_brief.py
```

Optional settings live in `.env` (copy `.env.example`); see [Configuration](#configuration).

---

## How it works

### Part 1: the document assistant

```
question ─► Orchestrator ─► Pricing │ Policy │ Compliance │ General agent ─► Critic ─► answer + sources
                                                                              │ fails
                                                     retry once with feedback ┘ ─► still fails: withhold, escalate
```

**Knowledge is built from the documents, not typed in.** At startup the system:
- splits the three markdown documents into 22 citable sections
- **parses the price list into structured data**: base prices, premiums, discounts, payment plan,
  charges
- reads the brochure's unit and orientation rules
- **audits the documents against each other** for conflicting facts

If the documents change, the answers change. If parsing breaks, the app refuses to start rather
than serve stale prices.

**An orchestrator routes each question** with fast rules to one of four agents:

| Agent | Handles | How |
|---|---|---|
| **Pricing** | Prices, premiums, discounts, amounts due | Parses the question; validates against brochure rules (block, floor, Margalla orientation, penthouse floors); calculates in exact `Decimal` code; returns a line-by-line calculation with the source of each line |
| **Policy** | Booking, transfers, refunds, possession, amenities… | Retrieves the best sections (BM25), adds both sides of any known conflict, answers only from those sections |
| **Compliance** | Topics the FAQ forbids staff to answer (yield, mark-up rates) | Quotes the documented policy verbatim |
| **General** | Greetings, help | Instant |

**A critique layer reviews every answer before it is shown.** Rule checks: it cites a real
section; every number in it exists in the sources or the calculator output; every side of a
conflict is disclosed; a "not in documents" answer quotes no figures. If an answer fails, it's
retried once with the critic's feedback; if it still fails, it is **withheld** and the
salesperson is told to check with the marketing manager.

| Brief question | Result |
|---|---|
| Base price, 2-bed Block B | **PKR 22,425,000** (2-Bed Standard; notes the 2-Bed Corner at 26,855,000) |
| Margalla corner, floor 15, 2-bed B | 26,855,000 × (4% + 3% + 6%) premiums → **PKR 30,346,150** |
| Overseas, 50% upfront, 3-Bed Exec | 39,480,000 − (6% + 2%) of base → **PKR 36,321,600**; **PKR 18,160,800** due upfront. Assumptions stated; notes that the rest of the 50%-upfront schedule isn't in the documents. |
| Transfer fee | **Conflict, found automatically**: 2% (price list, Apr 2025) vs 2.5% (FAQ, May 2025). Both shown; don't quote until management confirms. |
| Rental yield | **Restricted**: the FAQ forbids projections; refer to the marketing manager |
| Anchor tenant | **Unconfirmed**: discussions ongoing, none confirmed |

**Where the arithmetic happens:** in code, never in a language model. The calculator works on the
parsed price book with exact decimals, and the critic verifies that the answer contains no number
the calculator didn't produce.

### Part 3: the lead-scoring model, trained from scratch

The model is our own, trained on MGC's lead history (`leads.csv`). No pre-trained model is involved.

**Data decisions** (from profiling every column first):
- **Dropped `token_amount_received_pkr`: target leakage.** It is > 0 for every one of the 634
  converted leads. Paying the token *is* converting.
- **Dropped the engagement columns** (calls, call seconds, WhatsApp replies, site visits, first
  response time, agent experience). They only exist after the team has worked the lead. The
  question is "which *new* lead do we call first", and a new lead has none of them.
- **Dropped identifiers** (`lead_id`, `crm_record_hash`; the hash shows no relationship with the
  outcome).
- **Deduplicated** 160 leads entered twice (`-B` copies), using the same rule as SQL Q2.
- **Normalised cities**: 21 spellings (`ISB`, `Rwp`, `khi`, `LAHORE`…) → 9.
- **`bedrooms`** is missing for 100% of plots and shops and 0% of everything else. It means
  "not applicable", so it's set to 0.
- **`area`** (5% missing) → "Unknown". **`budget`** (3% missing) → median plus a missing flag,
  then log-transformed.

**Features:** source, city, area, property type, budget, bedrooms, overseas, referred by a client,
financing approved: everything known the moment a lead arrives.

**Model:** one-hot encoding + scaling → **logistic regression**. It's a sound baseline for 9,000
rows, and it's explainable: every score comes with the factors that drove it. The same cleaning
code runs in training and in live scoring.

**Split by time, not randomly.** Trained on leads before **1 Apr 2025** (6,410), tested on
**1 Apr – 1 Oct 2025** (2,590). The model will always score *future* leads, so the test has to look
like that. A random split lets it learn from later leads and overstates performance: here it
reports PR-AUC 0.197 instead of the honest 0.172.

**Metric: average precision (PR-AUC)**, the right choice for a 7.7% conversion rate where only the
top of the list matters.

| | PR-AUC | Top 100 scored → converted |
|---|---|---|
| **Our model** | **0.172** | **28** |
| No skill (random) | 0.077 | ~8 |
| Rule of thumb: best source first | 0.139 | 24 |
| *Comparison only: + engagement columns* | *0.415* | *54* |

**For the sales team:** calling the top 100 scored new leads yields about **28 sales instead of
about 8**, a 3.6× improvement. Honestly put: much of that lift is "referrals and walk-ins first",
which a good manager knows. The model beats that rule and applies it consistently to every lead.
The engagement version looks far stronger, but it can't score a lead nobody has called, so it's
reported for contrast. It would make a good second model for re-ranking after first contact.

Each score returns: probability, lift vs average, percentile among recent leads, whether it would
make the top-100 list, a priority (*call first / call soon / normal*), and the top factors.

### Part 2: the database (PostgreSQL)

- **`schema.sql`**:
  - `lead_import`: raw text staging, so the CSV import never fails.
  - `leads`: typed columns, CHECK constraints, indexes.
  - Why one table, and what I'd split out (an `agents` table), is in the comments.
- **`sql/transform_import.sql`** casts staging → `leads`, then prints `9160 | 634 | 160` as a
  sanity check.
- **`queries.sql`**:
  - **Q1:** conversion by source (200+ leads).
  - **Q2:** duplicates. The key is the same `crm_record_hash`. Timestamps aren't a key: two
    different leads share one. The winner is the earliest `created_at`, then the original id
    (not `-B`), then the lowest id. One row per pair. Prevention: `UNIQUE (crm_record_hash)`
    plus upserts.
  - **Q3:** conversion by city, with spellings normalised in the query.
  - Q1 and Q3 reuse the dedupe rule.
- Verified end to end on PostgreSQL 17 and on Supabase.

| Q1 source | leads | conv % | | Q3 city | leads | conv % |
|---|---|---|---|---|---|---|
| Referral | 721 | 13.18 | | Peshawar | 633 | 9.16 |
| Walk-in | 603 | 10.28 | | Gujranwala | 288 | 9.03 |
| Facebook Ads | 2325 | 6.80 | | Islamabad | 2653 | 7.24 |
| Google Search | 1431 | 6.50 | | Lahore | 1353 | 6.80 |
| WhatsApp Campaign | 537 | 6.33 | | Karachi | 973 | 6.68 |
| Property Portal | 1784 | 6.00 | | Multan | 382 | 6.54 |
| Instagram | 983 | 5.60 | | Faisalabad | 505 | 6.34 |
| Billboard | 277 | 4.33 | | Rawalpindi | 1945 | 6.22 |
| Expo Stall | 339 | 2.95 | | Abbottabad | 268 | 5.60 |

Supabase setup: [ARCHITECTURE.md › Supabase setup](ARCHITECTURE.md#supabase-setup).

### Part 4: the web interface

One page (`frontend/`, plain HTML/CSS/JS, no build step):
- a KPI strip
- a chat-style assistant, where each answer shows a status chip, a "Verified" badge, the
  calculation, source citations and a "how this answer was produced" trace
- a lead-scoring panel with a gauge, priority, lift and the factors behind the score

It's responsive, with dark mode.

---

## Speed

| Path | Typical |
|---|---|
| Pricing, compliance, refusals, greetings | 1–5 ms |
| Document answers (quoted / written + reviewed) | < 10 ms / ~2–5 s |
| Lead score | ~5 ms |

Everything heavy (parsing, indexing, training) happens once at startup. Audit logging runs after
the response is sent.

---

## Deployment

The backend runs on **Railway** (`railway.json`, `python -m app`), and the frontend on **Vercel**
(`frontend/vercel.json` proxies `/api/*` to Railway). Step by step:
[documentation/DEPLOYMENT.md](documentation/DEPLOYMENT.md).

## Documentation

- [documentation/DEPLOYMENT.md](documentation/DEPLOYMENT.md): Railway + Vercel guide
- [ARCHITECTURE.md](ARCHITECTURE.md): code layout, design decisions, Supabase setup

---

## Known limits and what I'd do next

- **Retrieval is lexical (BM25).** That's fine for three short documents. Without a language
  model, synonyms the documents don't use (e.g. "gym" vs "Gymnasium") can return "not in the
  documents". Next: embeddings with hybrid ranking.
- **The pricing parser** covers the price list's vocabulary; unusual phrasings fall through to the
  Policy agent.
- **The conflict audit** compares facts phrased as "X: value" / "X is value". It catches the
  transfer-fee class of error, not every disagreement.
- **The 50%-upfront schedule** beyond the upfront amount isn't defined in the documents; the answer
  flags this.
- **Lead model:** untuned, no calibration check. The newest leads may not have had time to
  convert, so test labels at the very end may be incomplete. Next: measure conversion within a
  fixed window, try gradient boosting on the same split, and add a post-contact re-ranking model.
- **Tests:** `scripts/eval_brief.py` covers the brief end to end; there's no unit-test suite (out of
  scope per the brief).

---

## Configuration

All optional. Copy `.env.example` to `.env`.

| Variable | Purpose |
|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Audit log of every answer and score |
| `RATE_LIMIT_PER_MIN` | Per-visitor limit on the public demo (default 20) |
| `CORS_ORIGINS` | Only if the frontend calls the API cross-origin |
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `LLM_PROVIDERS` | Optional answer writing, see below |

### Not dependent on Gemini or OpenAI

**The system works fully without any language model or API key.** Pricing, the transfer-fee
conflict, the rental-yield refusal, the unconfirmed anchor tenant, the critic's rule checks and
the lead model are all deterministic code. `scripts/eval_brief.py` passes 10/10 with no keys set.

With no key, document answers quote the relevant sentences verbatim, with their source.
Optionally, a language model can **write** those answers in natural language from the retrieved
sections, and a second pass reviews them. Two providers are supported behind one function
(`app/llm/client.py`):

- **Google Gemini** (`gemini-2.5-flash`) and **OpenAI** (`gpt-4.1-mini`), tried in the order set
  by `LLM_PROVIDERS` (e.g. `gemini,openai`).
- If one errors, times out or is rate-limited, the next answers in the same request. If none is
  available, the system falls back to quoting.
- The model is never used for arithmetic or refusals, and never without the critic checking its
  output.

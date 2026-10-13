# Architecture

## Layout

```
app/
  __main__.py           `python -m app`: production entry point, reads $PORT
  main.py               FastAPI app: startup loading, /api/*, rate limit, serves frontend/
  config.py             settings from .env
  schemas.py            request bodies
  knowledge/            everything derived from docs/*.md at startup
    corpus.py           split markdown into citable sections (FAQ questions become sub-sections)
    retriever.py        BM25 over sections + query-coverage score
    pricebook.py        parse price tables, premiums, discounts, plan, charges, brochure rules
    conflicts.py        cross-document fact audit (finds the transfer-fee disagreement)
  agents/
    orchestrator.py     route -> agent -> critic -> retry / withhold
    pricing_agent.py    parse question -> validate against brochure rules -> calculator
    calculator.py       Decimal arithmetic only
    policy_agent.py     retrieval-grounded answers (LLM-written JSON, or quoted without a key)
    compliance_agent.py topics the documents forbid staff to answer (yield, mark-up)
    general_agent.py    greetings / help
    critic.py           rule checks + optional LLM review
    base.py             AgentResult, Status
  llm/
    client.py           generate_json(): tries providers in LLM_PROVIDERS order
    gemini.py           Gemini: JSON schema output, timeout, 429 cooldown
    openai_llm.py       OpenAI: Structured Outputs, timeout, 429 cooldown
  ml/
    features.py         cleaning + feature lists shared by training and scoring
    train.py            time split, metrics, writes models/ and reports/
    scorer.py           loads the model, scores one lead, explains the score
  db/supabase_log.py    optional background audit log (PostgREST insert)
frontend/                       index.html, styles.css, app.js, config.js, vercel.json (static, no build)
schema.sql, queries.sql, sql/   Part 2 + app log tables
scripts/                        load_leads.py (CSV -> Supabase), eval_brief.py
railway.json, .python-version   Railway deployment config
documentation/                  deployment guide (Railway + Vercel)
```

## Request flow (`POST /api/ask`)

1. **Route** (`orchestrator.route`), rules first:
   - greeting → `general`
   - restricted topic → `compliance`
   - unit type + money word → `pricing`
   - otherwise → `policy`

   Gemini is consulted only when the rules see exactly one signal (a money word but no unit type,
   or the reverse). That keeps most requests at zero LLM calls for routing.
2. **Agent** produces an `AgentResult`. It holds the answer, status, the sections it cites, the
   sections it was given (`context`), any calculator numbers, and conflicts.
3. **Critic** runs rule checks. If the answer was LLM-written, it also runs an LLM review.
4. **Retry / withhold:** if an LLM answer fails, the agent retries once with the critic's feedback.
   If it still fails, the user gets "check with the marketing manager" with the closest sources.
5. The response includes the route, every critic check and per-stage timings, so the UI can show
   *why* the answer is trustworthy.

## Why these choices

| Decision | Reason |
|---|---|
| Parse facts from the docs at startup | One source of truth. Change the price list and the answers change, with no prices hard-coded. Parsing fails loudly if the documents change shape. |
| Arithmetic in `Decimal`, never in the LLM | The brief's hint, and money. The LLM never sees a pricing question. |
| Regex parser for pricing, not the LLM | The vocabulary is tiny and closed: deterministic, ~1 ms, testable. |
| LLM cites section *ids*, mapped back in code | The model can't invent a document or section name. |
| Conflicts detected, not hard-coded | `conflicts.py` scans for the same fact with different values across documents, so a future conflict is caught too. |
| Compliance agent bypasses the LLM | For "must not answer" topics, the safest answer is the documented policy, verbatim. |
| Critic number check | It catches the most damaging failure (an invented figure) with zero latency. |
| Everything works without a key | The reviewer's machine may not have one. The hard cases (arithmetic, conflict, refusal, unconfirmed) all pass with the LLM off. |
| Two LLM providers behind one function | Free-tier Gemini allows 5 requests/min; OpenAI takes over in the same request. Every caller only knows `generate_json`, so adding a provider is one file. |
| Supabase logging in a background task | Adds no latency, and does nothing when it isn't configured. |

## Supabase setup

You need this only to run Part 2 in Supabase and/or to log answers. The app itself runs without it.

1. **Create a project** at https://supabase.com/dashboard → *New project*. Pick a region near
   Pakistan (e.g. Mumbai / Singapore) and save the database password.
2. **Create the tables:** *SQL Editor* → *New query* → paste `schema.sql` → **Run**.
3. **Load the CSV.** Pick one option:
   - **Dashboard:** *Table Editor* → `lead_import` → *Insert* → *Import data from CSV* →
     `leads.csv` → Import. Then *SQL Editor* → run `sql/transform_import.sql`. The last statement
     should print `9160 | 634 | 160`.
   - **Script:** put the connection string in `.env` as `DATABASE_URL` (*Connect* button at the
     top → *Session pooler* URI, with your password filled in), then run `python scripts/load_leads.py`.
4. **Queries:** paste `queries.sql` into the SQL Editor and run each query (select one and click
   *Run selected*).
5. **App logging (optional):** run `sql/app_tables.sql`. Then in *Project Settings → API* (or
   *API Keys*) copy the **Project URL** into `SUPABASE_URL`, and the **service_role / secret** key
   into `SUPABASE_SERVICE_ROLE_KEY` in `.env`. Restart the app. Every question and score now lands
   in `assistant_logs` / `lead_scores`.

`sql/app_tables.sql` enables Row Level Security on all four tables with no policies. The public anon
key can't read the leads; only the server-side service-role key (which bypasses RLS) can write logs.
Never put the service-role key in browser code.

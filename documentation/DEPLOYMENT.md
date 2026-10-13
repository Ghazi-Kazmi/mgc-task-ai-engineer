# Deployment: Railway (backend) + Vercel (frontend)

```
   reviewer's browser
          │
          ▼
   Vercel  (frontend/: static HTML/CSS/JS)
          │  /api/*  is proxied by frontend/vercel.json
          ▼
   Railway (FastAPI: python -m app)  ──►  Supabase (audit log)   ──► Gemini / OpenAI (optional)
```

Why this split:
- **Vercel** serves the static frontend from a global CDN for free.
- **Railway** runs the Python backend, which needs a long-running process: the model and
  documents load into memory at startup.
- **Vercel proxies `/api/*` to Railway**, so the browser only ever talks to one domain. That means
  no CORS setup, and the Railway URL never appears in the frontend code.

The Railway backend also serves the same frontend at its own URL, so that URL alone is a complete,
working demo. Vercel is the polished front door.

---

## 0. Before you start

1. Push the project to a **public GitHub repo** (the brief requires one anyway):
   ```bash
   git add .
   git commit -m "MGC Sales Copilot"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
   `.env` is in `.gitignore`, so your keys are **not** pushed. Check with `git status` before
   pushing: `.env` must not be listed.
2. Protect your paid key:
   - In the OpenAI dashboard → *Settings → Limits*, set a **monthly budget** (e.g. $5).
   - The app also rate-limits each visitor to 20 requests/minute (`RATE_LIMIT_PER_MIN`).

---

## 1. Backend on Railway

1. Go to https://railway.com → sign in with GitHub → **New Project** → **Deploy from GitHub repo**
   → pick your repo. (Grant Railway access to the repo if asked.)
2. Railway detects Python from `requirements.txt` and `.python-version` (3.12), and reads
   `railway.json`:
   - start command: `python -m app` (binds to Railway's `$PORT`)
   - health check: `/api/health`, with 300 s allowed for the first boot
   - restart on failure
3. Open the service → **Variables** → **Raw Editor**, and paste (with your real values):
   ```
   GEMINI_API_KEY=...
   GEMINI_MODEL=gemini-2.5-flash
   OPENAI_API_KEY=...
   OPENAI_PROJECT_ID=...
   OPENAI_MODEL=gpt-4.1-mini
   LLM_PROVIDERS=openai,gemini
   SUPABASE_URL=https://<project>.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=...
   RATE_LIMIT_PER_MIN=20
   ```
   `LLM_PROVIDERS=openai,gemini` puts the paid, un-throttled provider first for reviewers. Every
   key is optional: leave out the LLM keys and the app runs in quoted mode, leave out the
   Supabase ones and it simply doesn't log. Don't add `DATABASE_URL`; only the local loader
   script uses it.
4. **Settings → Networking → Generate Domain.** You get something like
   `mgc-sales-copilot-production.up.railway.app`. Railway routes it to the app's `$PORT`
   automatically.
5. Watch **Deployments → View logs**. The first boot trains the model (about 20–40 s), then shows:
   ```
   INFO mgc: Ready. LLM providers: openai, gemini | Supabase logging: on | conflicts found in docs: 1
   ```
6. Test it:
   - `https://<your-domain>.up.railway.app/api/health` → JSON with `"ok": true`
   - `https://<your-domain>.up.railway.app/` → the full app, already working

**Cost:** the $5 trial credit is usage-based (memory and CPU while running). This app idles at a
few hundred MB of RAM. Check **Usage** in the Railway dashboard after a day to see the burn rate,
and keep it running through the review window.

---

## 2. Frontend on Vercel

1. In `frontend/vercel.json`, replace the placeholder with your Railway domain:
   ```json
   { "source": "/api/:path*", "destination": "https://<your-domain>.up.railway.app/api/:path*" }
   ```
   Optionally set your repo link in `frontend/config.js` (`window.MGC_REPO_URL = "https://github.com/…"`),
   then commit and push.
2. Go to https://vercel.com → sign in with GitHub → **Add New… → Project** → import the same repo.
3. Configure:
   - **Root Directory:** `frontend`  ← important
   - **Framework Preset:** Other
   - **Build Command:** leave empty · **Output Directory:** leave empty (it's already static)
4. **Deploy.** You get `https://<project>.vercel.app`.
5. Test it:
   - `https://<project>.vercel.app/api/health` → the same JSON as Railway (proves the proxy works)
   - Open the page: the status pill says "API online" and the KPI tiles fill in.

Every `git push` redeploys both automatically.

### Alternative: call Railway directly (no proxy)
Set `window.MGC_API_BASE = "https://<your-domain>.up.railway.app"` in `frontend/config.js`, and on
Railway add the variable `CORS_ORIGINS=https://<project>.vercel.app`. The proxy approach above is
simpler and is the recommended one.

---

## 3. Final checklist

- [ ] `/api/health` works on both URLs
- [ ] Ask the six brief questions on the Vercel URL; each shows sources and a "Verified" badge
- [ ] Score a lead
- [ ] (If Supabase is set) new rows appear in `assistant_logs` / `lead_scores`
- [ ] Put the Vercel URL at the top of `README.md` ("Live demo")
- [ ] After the review period: rotate the OpenAI, Gemini and Supabase service-role keys

## Troubleshooting

| Symptom | Fix |
|---|---|
| Railway deploy fails the health check | Check the logs. The first boot trains the model; `healthcheckTimeout` is 300 s. A Python error means a missing variable or dependency. |
| Vercel page says "API unreachable" | The `vercel.json` destination is wrong or still the placeholder; open `<vercel-url>/api/health` to see the error |
| Answers are quoted, not written | No LLM key set on Railway, or every provider is rate-limited; check `/api/health` → `llm_providers` |
| HTTP 429 "Too many requests" | The per-visitor rate limit; wait a minute or raise `RATE_LIMIT_PER_MIN` |
| Vercel shows the repo README instead of the app | Root Directory isn't set to `frontend` |

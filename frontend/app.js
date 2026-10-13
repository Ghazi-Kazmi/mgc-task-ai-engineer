/* MGC Sales Copilot — vanilla JS front end (no build step, deploys anywhere static). */
"use strict";

const API = (window.MGC_API_BASE || "").replace(/\/$/, "");
const $ = (sel) => document.querySelector(sel);

const SUGGESTIONS = [
  "What's the base price of a 2-bed in Block B?",
  "What's the total for a Margalla-facing corner unit on floor 15, 2-bed Block B?",
  "An overseas Pakistani buyer pays 50% upfront on a 3-Bed Executive — what's the price after discounts, and what's due at booking?",
  "What's the transfer fee?",
  "What's the rental yield on a 1-bed?",
  "Who is the anchor tenant?",
];

const STATUS = {
  answered:            { label: "Answered",          cls: "ok" },
  conflict:            { label: "Sources disagree",  cls: "warn" },
  unconfirmed:         { label: "Not confirmed yet", cls: "info" },
  restricted:          { label: "Restricted topic",  cls: "warn" },
  not_in_documents:    { label: "Not in documents",  cls: "bad" },
  needs_clarification: { label: "Needs more detail", cls: "neutral" },
  out_of_scope:        { label: "General",           cls: "neutral" },
};
const AGENTS = {
  pricing: "Pricing engine", policy: "Document Q&A", compliance: "Compliance guard", general: "General",
};
const ICON = {
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  doc: '<svg viewBox="0 0 24 24"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/></svg>',
  shield: '<svg viewBox="0 0 24 24"><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/></svg>',
};

/* ---------- helpers ------------------------------------------------------ */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
const pct = (x, d = 1) => `${(x * 100).toFixed(d)}%`;
const docShort = (title) => title.replace(/^MGC (Aurora Heights|Developments)\s*[—-]\s*/, "");

async function api(path, body) {
  const res = await fetch(API + path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : undefined);
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) {
    const detail = data && (data.detail || data.error);
    throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
  }
  return data;
}

/* ---------- header status + KPIs ------------------------------------------ */
async function loadOverview() {
  try {
    const [health, opts] = await Promise.all([api("/api/health"), api("/api/lead-options")]);
    const aiOn = health.llm_providers && health.llm_providers.length > 0;
    $("#status").innerHTML = `
      <span class="pill"><span class="dot ${aiOn ? "on" : "off"}"></span>${aiOn ? "AI-written answers, reviewed" : "Document-quoted answers"}</span>
      <span class="pill"><span class="dot ${health.audit_log ? "on" : "off"}"></span>Audit log ${health.audit_log ? "on" : "off"}</span>`;

    const m = opts.model_report, t = m.model_intake_features, ns = m.no_skill;
    // "The documents disagree on the transfer fee: 2% per … vs 2.5% per …" -> "Transfer fee: 2% vs 2.5%"
    const raw = (health.conflicts || [])[0];
    const m2 = raw && raw.match(/disagree on the (.+?): (\S+) per .*? vs (\S+) per/);
    const conflict = m2 ? `${m2[1][0].toUpperCase()}${m2[1].slice(1)}: ${m2[2]} vs ${m2[3]}` : raw;
    $("#kpis").innerHTML = `
      <div class="kpi"><div class="kpi-label">Knowledge base</div>
        <div class="kpi-value">${health.documents} <small>documents</small></div>
        <div class="kpi-sub">${health.sections} citable sections · prices parsed, not typed</div></div>
      <div class="kpi"><div class="kpi-label">Conflicts detected</div>
        <div class="kpi-value">${(health.conflicts || []).length}</div>
        <div class="kpi-sub">${conflict ? esc(conflict) : "documents are consistent"}</div></div>
      <div class="kpi"><div class="kpi-label">Lead model · PR-AUC</div>
        <div class="kpi-value">${t.average_precision.toFixed(3)}</div>
        <div class="kpi-sub">${(t.average_precision / ns.average_precision).toFixed(1)}× the no-skill baseline (${ns.average_precision.toFixed(3)})</div></div>
      <div class="kpi"><div class="kpi-label">Top-100 call list</div>
        <div class="kpi-value">${t.top_100_converted} <small>conversions</small></div>
        <div class="kpi-sub">vs ~${Math.round(ns.top_100_converted_random_mean)} from 100 random leads</div></div>`;

    fillLeadForm(opts.options);
    renderModelCard(m);
  } catch (e) {
    $("#status").innerHTML = `<span class="pill"><span class="dot"></span>API unreachable</span>`;
    $("#kpis").innerHTML = `<div class="error" style="grid-column:1/-1">Can't reach the API (${esc(e.message)}). Is the backend running?</div>`;
  }
}

/* ---------- assistant ------------------------------------------------------ */
function renderSuggestions() {
  const box = $("#suggestions");
  SUGGESTIONS.forEach((q) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "suggestion"; b.title = q;
    b.textContent = q.length > 46 ? q.slice(0, 44) + "…" : q;
    b.onclick = () => ask(q);
    box.appendChild(b);
  });
}

function addUserMessage(text) {
  $("#empty")?.remove();
  const el = document.createElement("div");
  el.className = "msg-user"; el.textContent = text;
  $("#thread").appendChild(el);
}

function addTyping() {
  const el = document.createElement("div");
  el.className = "msg-bot";
  el.innerHTML = `<span class="typing"><span></span><span></span><span></span></span><span class="typing-label">Routing question…</span>`;
  $("#thread").appendChild(el);
  scrollThread();
  const steps = ["Routing question…", "Reading the documents…", "Checking every figure…", "Reviewing the answer…"];
  let i = 0;
  const timer = setInterval(() => {
    i = Math.min(i + 1, steps.length - 1);
    const lbl = el.querySelector(".typing-label");
    if (lbl) lbl.textContent = steps[i];
  }, 900);
  return { el, done: () => clearInterval(timer) };
}

function scrollThread() { const t = $("#thread"); t.scrollTop = t.scrollHeight; }

function renderAnswer(d) {
  const st = STATUS[d.status] || { label: d.status, cls: "neutral" };
  const c = d.critique;
  const verified = c.withheld
    ? `<span class="chip bad">${ICON.x}Withheld by reviewer</span>`
    : c.passed ? `<span class="chip ok">${ICON.shield}Verified</span>` : "";

  const notes = d.notes.length
    ? `<div class="notes">${d.notes.map((n) => `<div class="note">${md(n)}</div>`).join("")}</div>` : "";

  const sources = d.sources.length
    ? `<div class="section-label">Sources</div><div class="sources">${d.sources.map((s) =>
        `<span class="source" title="${esc(s.file)}">${ICON.doc}<span><span class="doc">${esc(docShort(s.document))} ›</span> ${esc(s.section)}</span></span>`).join("")}</div>`
    : "";

  const calc = d.calculation.length
    ? `<details class="fold" open><summary>Calculation</summary><ul class="calc">${d.calculation.map((line) => {
        const m = line.match(/^(.*?)\s*\[(.+)\]$/);
        return m ? `<li><span>${esc(m[1])}</span><span class="ref">${esc(m[2])}</span></li>` : `<li><span>${esc(line)}</span></li>`;
      }).join("")}</ul></details>`
    : "";

  const passed = c.checks.filter((k) => k.passed).length;
  const t = d.timings_ms;
  const trace = `
    <details class="fold"><summary>How this answer was produced</summary>
      <div class="pipeline">
        <div class="stage"><div class="stage-name">1 · Route</div><div class="stage-val">${esc(AGENTS[d.route.agent] || d.route.agent)}</div><div class="stage-ms">${esc(d.route.method)} · ${t.route} ms</div></div>
        <div class="stage"><div class="stage-name">2 · Answer</div><div class="stage-val">${d.used_llm ? "AI-written from sources" : "Deterministic"}</div><div class="stage-ms">${t.agent} ms</div></div>
        <div class="stage"><div class="stage-name">3 · Review</div><div class="stage-val">${passed}/${c.checks.length} checks${c.attempts > 1 ? " · retried" : ""}</div><div class="stage-ms">${t.critic} ms</div></div>
      </div>
      <ul class="checks">${c.checks.map((k) =>
        `<li><span class="${k.passed ? "pass" : "fail"}">${k.passed ? "✓" : "✗"}</span><span>${esc(k.name.replaceAll("_", " "))}${k.detail ? ` — ${esc(k.detail)}` : ""}</span></li>`).join("")}</ul>
      <div class="muted" style="margin-top:6px">Routing reason: ${esc(d.route.reason)}</div>
    </details>`;

  return `
    <div class="msg-top">
      <span class="chip ${st.cls}">${esc(st.label)}</span>${verified}
      <span class="chip gold">${esc(AGENTS[d.route.agent] || d.route.agent)}</span>
      <span class="ms">${t.total < 1000 ? `${Math.round(t.total)} ms` : `${(t.total / 1000).toFixed(1)} s`}</span>
    </div>
    <div class="answer">${md(d.answer)}</div>
    ${notes}${calc}${sources}${trace}`;
}

let busy = false;
async function ask(question) {
  question = (question || "").trim();
  if (!question || busy) return;
  busy = true; $("#askBtn").disabled = true;
  $("#q").value = ""; autosize();
  addUserMessage(question);
  const typing = addTyping();
  try {
    const d = await api("/api/ask", { question });
    typing.el.innerHTML = renderAnswer(d);
  } catch (e) {
    typing.el.innerHTML = `<div class="error">${esc(e.message)}</div>`;
  } finally {
    typing.done(); busy = false; $("#askBtn").disabled = false; scrollThread(); $("#q").focus();
  }
}

function autosize() { const q = $("#q"); q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 140) + "px"; }

/* ---------- lead scoring ---------------------------------------------------- */
function fillLeadForm(options) {
  const f = $("#leadForm");
  for (const [field, values] of Object.entries(options)) {
    const sel = f.elements[field];
    if (!sel) continue;
    sel.innerHTML = "";
    if (field === "area") sel.add(new Option("Not given", ""));
    values.filter((v) => v !== "Unknown").forEach((v) => sel.add(new Option(v, v)));
  }
  // A sensible starting lead so the demo is one click.
  f.source.value = "Referral"; f.property_type.value = "Apartment"; f.city.value = "Islamabad";
  f.budget_pkr_lac.value = 120; f.bedrooms.value = 2;
  syncBedrooms();
}

function syncBedrooms() {
  // Plots and shops have no bedrooms; the model treats that as 0, not "unknown".
  const f = $("#leadForm");
  const none = ["Plot", "Commercial Shop"].includes(f.property_type.value);
  f.bedrooms.disabled = none;
  if (none) f.bedrooms.value = "";
}

function gauge(prob, percentile, priority) {
  const r = 48, C = 2 * Math.PI * r;
  const color = priority === "call first" ? "var(--ok)" : priority === "call soon" ? "var(--gold)" : "var(--text-3)";
  return `
    <div class="gauge" title="Ring = rank among recent leads (${percentile}th percentile)">
      <svg viewBox="0 0 112 112">
        <circle class="track" cx="56" cy="56" r="${r}" stroke-width="10"/>
        <circle class="arc" cx="56" cy="56" r="${r}" stroke-width="10" stroke="${color}"
          stroke-dasharray="${C}" stroke-dashoffset="${C}" data-target="${C * (1 - percentile / 100)}"/>
      </svg>
      <div class="gauge-label"><div><div class="gauge-pct">${pct(prob)}</div><div class="gauge-cap">likely to convert</div></div></div>
    </div>`;
}

function renderScore(d) {
  const maxEffect = Math.max(...d.top_factors.map((f) => Math.abs(f.effect)), 0.01);
  const prioCls = d.priority === "call first" ? "ok" : d.priority === "call soon" ? "gold" : "neutral";
  $("#scoreResult").innerHTML = `
    <div class="score-top">
      ${gauge(d.probability, d.percentile_vs_recent_leads, d.priority)}
      <div class="score-facts">
        <div><span class="chip ${prioCls}" style="font-size:13px">${esc(d.priority.replace(/^./, (c) => c.toUpperCase()))}</span>
          ${d.in_top_100_of_recent_leads ? '<span class="chip ok">Top-100 list</span>' : ""}</div>
        <div class="big">${d.lift_vs_average}× the average lead</div>
        <div class="muted">Average conversion is ${pct(d.base_rate)}. Ranks above ${d.percentile_vs_recent_leads}% of recent leads.</div>
        <div class="bar"><i style="width:0" data-w="${d.percentile_vs_recent_leads}%"></i></div>
      </div>
    </div>
    ${d.top_factors.length ? `
      <div class="section-label">What drives this score</div>
      <ul class="factors">${d.top_factors.map((f) => {
        const w = (Math.abs(f.effect) / maxEffect) * 50;
        return `<li class="factor"><span>${esc(f.feature.replaceAll("_", " "))}</span>
          <span class="factor-bar" title="${f.direction} the score (${f.effect > 0 ? "+" : ""}${f.effect} log-odds)">
            <i class="${f.effect > 0 ? "up" : "down"}" style="width:${w}%"></i></span></li>`;
      }).join("")}</ul>` : ""}`;
  $("#scoreResult").hidden = false;
  requestAnimationFrame(() => {   // animate gauge + bar in
    const arc = $("#scoreResult .arc"); if (arc) arc.style.strokeDashoffset = arc.dataset.target;
    const bar = $("#scoreResult .bar > i"); if (bar) bar.style.width = bar.dataset.w;
  });
}

function renderModelCard(m) {
  const t = m.model_intake_features, ns = m.no_skill, h = m.source_rate_heuristic;
  $("#modelInfo").innerHTML = `
    <p style="margin-top:8px">Logistic regression trained from scratch on <b>${m.split.train.rows.toLocaleString()}</b> leads created before
    <b>${esc(m.split.cutoff)}</b>, tested on the <b>${m.split.test.rows.toLocaleString()}</b> newer leads after it, so the
    test mimics scoring next month's leads. Leakage (token paid) and post-contact activity (calls, visits) are excluded:
    a brand-new lead doesn't have them yet.</p>
    <div class="stats">
      <div class="stat"><b>${t.top_100_converted} / 100</b>top-scored leads converted</div>
      <div class="stat"><b>~${Math.round(ns.top_100_converted_random_mean)} / 100</b>random leads convert</div>
      <div class="stat"><b>${t.average_precision.toFixed(3)}</b>PR-AUC (no-skill ${ns.average_precision.toFixed(3)})</div>
      <div class="stat"><b>${h.top_100_converted} / 100</b>simple "best source first" rule</div>
    </div>`;
}

/* ---------- wiring ---------------------------------------------------------- */
$("#composer").addEventListener("submit", (e) => { e.preventDefault(); ask($("#q").value); });
$("#q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask($("#q").value); }
});
$("#q").addEventListener("input", autosize);
$("#leadForm").property_type.addEventListener("change", syncBedrooms);

$("#leadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, btn = $("#scoreBtn");
  const num = (v) => (v === "" ? null : Number(v));
  btn.disabled = true; btn.textContent = "Scoring…";
  try {
    const d = await api("/api/score", {
      source: f.source.value, city: f.city.value, area: f.area.value || null,
      property_type: f.property_type.value,
      budget_pkr_lac: num(f.budget_pkr_lac.value), bedrooms: f.bedrooms.disabled ? null : num(f.bedrooms.value),
      is_overseas: f.is_overseas.checked, referred_by_existing_client: f.referred_by_existing_client.checked,
      has_financing_approved: f.has_financing_approved.checked,
    });
    renderScore(d);
  } catch (err) {
    $("#scoreResult").hidden = false;
    $("#scoreResult").innerHTML = `<div class="error">${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "Score lead";
  }
});

if (window.MGC_REPO_URL) $("#repoLink").href = window.MGC_REPO_URL; else $("#repoLink").remove();

renderSuggestions();
loadOverview();

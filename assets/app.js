/* Campus Rumor Check — front end. Talks to server.py (/api/check). */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => {
  if (!iso) return "";
  const d = /^\d{4}-\d{2}-\d{2}/.test(String(iso)) ? new Date(iso + "T00:00:00") : new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
};
const fmtTime = (iso) => {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
};

const STATUS_LABEL = {
  confirmed_official: "Confirmed by an official source",
  reported_news: "Being reported by local news — no official statement matched",
  no_match: "No matching official statement or news coverage found",
  unclear: "Inconclusive",
  error: "Check error",
};

let CONFIG = null;

async function boot() {
  wireTabs();
  $("#check-form").addEventListener("submit", onCheck);
  try {
    CONFIG = await (await fetch("data/config.json", { cache: "no-store" })).json();
    $("#school").textContent = CONFIG.school || "campus";
    $("#tagline").innerHTML =
      `Paste a rumor you saw about <span>${esc(CONFIG.school || "campus")}</span>. ` +
      `It gets checked against official university channels and local news.`;
    renderSourcesList();
  } catch {
    /* config is optional for the UI to load */
  }
  renderIncidents();
  renderChecks();
  $("#inc-cat").addEventListener("change", renderIncidents);
  $("#inc-search").addEventListener("input", renderIncidents);
  pingServer();
}

function wireTabs() {
  $$(".tab").forEach((tab) =>
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
      $$(".view").forEach((v) => (v.hidden = v.id !== `view-${tab.dataset.view}`));
      if (tab.dataset.view === "checks") renderChecks();
    })
  );
}

async function pingServer() {
  const foot = $("#foot-status");
  try {
    const s = await (await fetch("data/sources.json", { cache: "no-store" })).json();
    const when = s.fetchedAt ? `source cache updated ${fmtTime(s.fetchedAt)}` : "source cache not yet built — run fetch_sources.py";
    foot.textContent = when + " · student-built, unaffiliated tool";
  } catch {
    foot.textContent = "Open this page through the app server (python server.py), not as a file.";
  }
}

/* ---------------- check ---------------- */
async function onCheck(e) {
  e.preventDefault();
  const text = $("#rumor").value.trim();
  const box = $("#check-result");
  if (!text) return;
  const btn = $("#check-btn");
  btn.disabled = true;
  box.innerHTML = `<div class="card">Checking…</div>`;
  try {
    const res = await fetch("/api/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, useAI: $("#use-ai").checked }),
    });
    if (!res.ok) throw new Error(`server ${res.status}`);
    renderResult(await res.json());
    renderChecks();
  } catch (err) {
    box.innerHTML =
      `<div class="card"><b>Could not reach the checker.</b><br>` +
      `Start it with <code>python server.py</code> in the <code>campus-rumor-check</code> folder, then reload.<br>` +
      `<span class="muted-note">(${esc(err.message)})</span></div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderResult(r) {
  const ai = r.ai && r.ai.status && r.ai.status !== "error" ? r.ai : null;
  const aiErr = r.ai && r.ai.status === "error" ? r.ai : null;
  const primary = ai ? ai.status : r.heuristic.status;

  let h = `<div class="card">`;
  h += `<span class="status ${esc(primary)}">${esc(STATUS_LABEL[primary] || primary)}</span>`;
  h += `<span class="pill">${esc(r.category)}${r.isSafety && r.category !== "safety" ? " · safety" : ""}</span>`;
  h += `<div class="claim-read">We read your rumor as: <b>${esc(r.claim)}</b></div>`;

  if (r.isSafety && CONFIG && CONFIG.emergencyPanel) {
    const p = CONFIG.emergencyPanel;
    h +=
      `<div class="emergency"><h3>${esc(p.heading)}</h3><ul>` +
      p.points.map((x) => `<li>${esc(x)}</li>`).join("") +
      `</ul><div class="elinks">` +
      p.links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join("") +
      `</div></div>`;
  }

  if (r.latestOfficialAlert && r.latestOfficialAlert.title) {
    const a = r.latestOfficialAlert;
    h +=
      `<div class="block"><h4>Most recent official item on file</h4>` +
      `<div class="src"><div class="s-title"><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a></div>` +
      `<div class="s-meta">${esc(fmtDate(a.date))}</div></div></div>`;
  }

  if (ai) {
    h += `<div class="block"><h4>AI web check</h4>`;
    h += `<div class="ai-summary">${esc(ai.summary || "")}</div>`;
    h += srcListHtml(ai.sources || []);
    h += `</div>`;
  } else if (aiErr) {
    h += `<div class="banner-warn">AI web check unavailable: ${esc(aiErr.summary || "")} — heuristic result shown.</div>`;
  } else if (r.ai === null && $("#use-ai").checked) {
    h += `<div class="banner-warn">AI web check is off on the server (no <code>anthropic</code> package or credentials). Showing cached-source matching only.</div>`;
  }

  const H = r.heuristic;
  if (H.officialMatches.length || H.newsMatches.length) {
    h += `<div class="block"><h4>Matches in the cached source feed</h4>`;
    h += srcListHtml([...H.officialMatches.map((x) => ({ ...x, type: "official" })),
                      ...H.newsMatches.map((x) => ({ ...x, type: "news" }))]);
    h += `</div>`;
  } else {
    h += `<div class="block"><h4>Cached source feed</h4><div class="muted-note">No items in the local cache matched this claim. That is not confirmation either way — check the sources directly below.</div></div>`;
  }

  h += `<div class="block"><h4>Check these verified sources yourself</h4><div class="searchlinks">`;
  h += (r.searches || []).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)} ↗</a>`).join("");
  h += `</div></div>`;

  h += `<div class="fresh">Checked ${esc(fmtTime(r.checkedAt))}. `;
  h += r.sourcesFetchedAt ? `Source cache from ${esc(fmtTime(r.sourcesFetchedAt))}.` : `Source cache not yet built.`;
  h += ` "No match" never means "safe" — official notices can lag events.</div>`;

  h += `</div>`;
  $("#check-result").innerHTML = h;
}

function srcListHtml(rows) {
  if (!rows.length) return `<div class="muted-note">No sources returned.</div>`;
  return rows
    .map(
      (s) =>
        `<div class="src"><div class="s-title">` +
        (s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>` : esc(s.title || "")) +
        `</div><div class="s-meta">${esc(s.type || "")}${s.source ? " · " + esc(s.source) : ""}${s.date ? " · " + esc(fmtDate(s.date)) : ""}</div></div>`
    )
    .join("");
}

/* ---------------- incidents ---------------- */
let INCIDENTS = null;
let CRIMELOG = null;

async function renderIncidents() {
  if (!INCIDENTS) {
    try {
      INCIDENTS = (await (await fetch("data/incidents.json", { cache: "no-store" })).json()).incidents || [];
    } catch {
      INCIDENTS = [];
    }
  }
  if (!CRIMELOG) {
    try {
      CRIMELOG = (await (await fetch("data/crimelog_raw.json", { cache: "no-store" })).json()).rows || [];
    } catch {
      CRIMELOG = [];
    }
    renderCrimelog();
  }
  const cat = $("#inc-cat").value;
  const q = $("#inc-search").value.trim().toLowerCase();
  const rows = INCIDENTS.filter((i) => (cat === "all" || i.category === cat))
    .filter((i) => !q || JSON.stringify(i).toLowerCase().includes(q))
    .sort((a, b) => String(b.date).localeCompare(String(a.date)));

  $("#incident-list").innerHTML =
    rows
      .map((i) => {
        const srcs = (i.sources || [])
          .map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>`)
          .join(" · ");
        return (
          `<li class="tl-item cat-${esc(i.category)}">` +
          `<div class="tl-date">${esc(fmtDate(i.date))}</div>` +
          `<div class="tl-title">${esc(i.title)}` +
          (i.verified === false ? ` <span class="pill tl-unverified">unverified detail</span>` : "") +
          `</div>` +
          `<div class="tl-sum">${esc(i.summary)}</div>` +
          (i.checkNote ? `<div class="tl-meta">Note: ${esc(i.checkNote)}</div>` : "") +
          `<div class="tl-meta">${esc(i.location || "")}${i.outcome ? " — " + esc(i.outcome) : ""}</div>` +
          (srcs ? `<div class="tl-meta">Sources: ${srcs}</div>` : "") +
          `</li>`
        );
      })
      .join("") || `<li class="muted-note">No incidents match.</li>`;
}

function renderCrimelog() {
  $("#crimelog-count").textContent = CRIMELOG.length ? `(${CRIMELOG.length})` : "(none yet)";
  $("#crimelog-list").innerHTML =
    CRIMELOG.slice(0, 100)
      .map(
        (r) =>
          `<div class="cl-row">#${esc(r.case)} · ${esc((r.dates || []).join(" / "))} · ${esc(r.disposition || "")}<br>${esc(r.raw || "")}</div>`
      )
      .join("") || `<div class="cl-row">Run <code>python scripts/scrape_crimelog.py</code> to populate this.</div>`;
}

/* ---------------- recent checks ---------------- */
async function renderChecks() {
  let checks = [];
  try {
    checks = (await (await fetch("data/checks.json", { cache: "no-store" })).json()).checks || [];
  } catch {
    /* ignore */
  }
  $("#checks-list").innerHTML =
    checks
      .map((c) => {
        const st = c.aiStatus || c.heuristicStatus;
        return (
          `<li class="chk-item"><span class="status ${esc(st)}" style="font-size:12px;padding:2px 8px">${esc(STATUS_LABEL[st] || st || "?")}</span>` +
          `<div class="c-claim">${esc(c.claim)}</div>` +
          (c.aiSummary ? `<div class="c-meta">${esc(c.aiSummary)}</div>` : "") +
          `<div class="c-meta">${esc(c.category)}${c.isSafety && c.category !== "safety" ? " · safety" : ""} · ${esc(fmtTime(c.checkedAt))}</div></li>`
        );
      })
      .join("") || `<li class="muted-note">No checks yet.</li>`;
}

/* ---------------- about ---------------- */
function renderSourcesList() {
  if (!CONFIG) return;
  $("#sources-list").innerHTML = (CONFIG.verifiedSources || [])
    .map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a> — <span class="muted-note">${esc(s.type)}</span></li>`)
    .join("");
}

boot();

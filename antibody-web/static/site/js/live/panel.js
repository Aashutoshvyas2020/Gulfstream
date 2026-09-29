/* Live chapter: runs real Flower scans and shows the agents, connectors and the
 * coordinator's alert. Renders into #livePanel. Talks to Flower only through ./api.js
 * and shares results with the 3D scene through ./state.js.
 * Owner: live scan UI. */

import { CODES, LIVE, statusOf } from "./state.js";
import { fetchMeta, startScan, streamRun, stopRun } from "./api.js";

const $ = (s, el = document) => el.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Reading overrides for the next scan (keys match agent/agent/building_data.py)
const INJECTIONS = [
  { label: "Leak spreads", o: { H2O: { night_flow_lpm_building_empty: 14, ceiling_moisture_sensor_F5: "wet on tower floors 5 and 6", pressure_drop_psi_last_6h: 9 } } },
  { label: "Breaker overheats", o: { ELEC: { breaker_temp_c: 96 } } },
  { label: "Crack widens", o: { STR: { crack_width_mm_now: 0.9, column_tilt_deg: 0.08 } } },
  { label: "Battery runaway", o: { PWR: { cell_temp_c_now: 55, hydrogen_ppm: 900 } } },
  { label: "Sprinkler valve closed", o: { FIRE: { control_valves_open: false, static_pressure_psi: 12 } } },
  { label: "Patch the BMS", heal: true, o: { CYBER: { default_admin_password: false, open_ports_to_internet: [], firmware_age_months: 1, failed_logins_24h: 0 } } },
];
const WATCH_PROMPT =
  "Check the building, then keep watching it: schedule a scan every 10 minutes for the next 24 hours (max 144 runs) and alert me on any new infection.";
const LABEL = { healthy: "Healthy", watch: "Watch", infected: "Infected", scanning: "Hunting", offline: "Offline", idle: "Idle" };
const TOOLS = ["web_search", "web_fetch", "start_automation", "slack", "notion", "github"];
const CONNECT_URL = "https://flower.ai/app"; // Settings > Connectors

const root = document.getElementById("livePanel");
root.innerHTML = `
  <div class="lp-top">
    <div class="lp-conn"><span class="dot" id="lpDot"></span><span id="lpConn">connecting to Flower…</span></div>
    <div class="lp-actions">
      <button class="btn primary" id="lpScan">Scan building</button>
      <button class="btn" id="lpWatch" title="The coordinator schedules recurring scans with Flower's start_automation connector">Watch 24/7</button>
      <button class="btn danger" id="lpStop" hidden>Stop</button>
      <button class="btn ghost" id="lpNew" title="Start a new run series: clears memory and trend">New session</button>
    </div>
  </div>
  <div class="lp-err" id="lpErr" role="alert"></div>
  <div class="lp-grid">
    <div class="lp-col">
      <div class="lp-stats">
        <div><b id="lpHealth">—</b><span>health</span></div>
        <div><b id="lpRep">0/${CODES.length}</b><span>reported</span></div>
        <div><b id="lpInf" class="c-inf">0</b><span>infected</span></div>
        <div><b id="lpTime">0s</b><span>scan time</span></div>
      </div>
      <p class="lp-trend" id="lpTrend">Each scan is a real Flower run: ${CODES.length} agents report, then the coordinator ranks, investigates and alerts.</p>
      <h4>Change a reading before the next scan</h4>
      <div class="chips" id="lpInject">${INJECTIONS.map((x, i) => `<button class="chip${x.heal ? " heal" : ""}" data-i="${i}" aria-pressed="false">${esc(x.label)}</button>`).join("")}</div>
      <h4>Account connectors</h4>
      <div class="chips" id="lpAccounts"><span class="chip-note">Checking your Flower connectors…</span></div>
      <h4>Flower connectors called</h4>
      <div class="tools" id="lpTools">${TOOLS.map(t => `<span class="tool" data-t="${t}">${t} <b>0</b></span>`).join("")}</div>
    </div>
    <div class="lp-col">
      <h4>The ${CODES.length} agents</h4>
      <div class="agents" id="lpAgents"></div>
    </div>
    <div class="lp-col lp-wide">
      <h4>Coordinator alert</h4>
      <div class="alert" id="lpAlert"></div>
      <h4>Sources the coordinator found</h4>
      <ol class="sources" id="lpSources"><li class="empty">Standards and references from web_search appear here.</li></ol>
      <form class="ask" id="lpAsk"><input id="lpAskIn" placeholder="Ask Antibody, e.g. what must be fixed tonight?" aria-label="Ask Antibody"><button class="btn" type="submit">Ask</button></form>
    </div>
  </div>`;

let META = null, runId = null, closeStream = null, t0 = 0, timer = null, alertBuf = "", renderPending = false, nextNew = true;
const reports = {}, toolCounts = {}, sources = new Map();

function showError(msg) { const e = $("#lpErr"); e.textContent = msg || ""; e.classList.toggle("show", !!msg); }

function drawAgents() {
  $("#lpAgents").innerHTML = CODES.map(c => `
    <div class="agent" data-code="${c}" data-s="idle">
      <span class="code">${c}</span><span class="name">${esc(META ? META.specialists[c].name : c)}</span><span class="state">Idle</span>
      <div class="bar"><i></i></div><p class="finding"></p><div class="more"></div>
    </div>`).join("");
  root.querySelectorAll(".agent").forEach(el => el.addEventListener("click", () => el.classList.toggle("open")));
}
function setAgent(code, status, r) {
  const el = $(`.agent[data-code="${code}"]`, root); if (!el) return;
  el.dataset.s = status; $(".state", el).textContent = LABEL[status];
  const bar = $(".bar i", el);
  if (!r) { bar.style.width = "0"; $(".finding", el).textContent = ""; $(".more", el).innerHTML = ""; return; }
  bar.style.width = Math.round(r.risk_score * 100) + "%";
  $(".finding", el).textContent = `${r.finding} · risk ${r.risk_score.toFixed(2)}`;
  const ev = typeof r.evidence === "string" ? r.evidence : JSON.stringify(r.evidence);
  $(".more", el).innerHTML = `<b>Evidence</b> ${esc(ev)}<br><b>Fix</b> ${esc(r.recommended_action)}`;
}

function recompute() {
  let total = 0, weighted = 0, infected = 0;
  CODES.forEach((c, i) => {
    const cons = META.specialists[c].consequence; total += cons;
    const r = reports[c]; LIVE.risk[i] = r ? r.risk_score : null;
    if (r) { weighted += r.risk_score * cons; if (statusOf(r) === "infected") infected++; }
  });
  LIVE.provisional = 100 * (1 - weighted / total);
  LIVE.reported = Object.keys(reports).length;
  $("#lpRep").textContent = `${LIVE.reported}/${CODES.length}`; $("#lpInf").textContent = infected;
  $("#lpHealth").textContent = LIVE.reported ? Math.round(LIVE.health ?? LIVE.provisional) : "—";
}

function busy(on, text) {
  $("#lpScan").disabled = on; $("#lpWatch").disabled = on; $("#lpStop").hidden = !on; LIVE.scanning = on;
  root.classList.toggle("is-scanning", on);
  if (text) $("#lpTrend").textContent = text;
  clearInterval(timer);
  if (on) { t0 = Date.now(); timer = setInterval(() => ($("#lpTime").textContent = Math.round((Date.now() - t0) / 1000) + "s"), 500); }
}
function renderAlert(done) {
  renderPending = false;
  let html = window.marked ? marked.parse(alertBuf) : `<pre>${esc(alertBuf)}</pre>`;
  if (window.DOMPurify) html = DOMPurify.sanitize(html);
  const el = $("#lpAlert"); el.innerHTML = html + (done ? "" : '<span class="caret"></span>'); el.scrollTop = el.scrollHeight;
}
// What each account connector is used for in this demo
const ACCOUNT_USE = { slack: "tenant reports", notion: "work orders", github: "BMS config repo" };
function renderAccounts(list) {
  const el = $("#lpAccounts");
  if (!list.length) { el.innerHTML = '<span class="chip-note">No account connectors on this SuperLink (local mode).</span>'; return; }
  el.innerHTML = list.map(c => c.connected
    ? `<button class="chip acct" data-ref="${esc(c.ref)}" aria-pressed="true" title="${esc(c.description)}">${esc(c.name)} · ${esc(ACCOUNT_USE[c.ref] || "connected")}</button>`
    : `<a class="chip acct off" href="${CONNECT_URL}" target="_blank" rel="noopener" title="Connect ${esc(c.name)} in Flower: Settings > Connectors">${esc(c.name)} · connect ↗</a>`).join("");
  el.querySelectorAll("button.chip").forEach(ch => ch.addEventListener("click", () => ch.setAttribute("aria-pressed", ch.getAttribute("aria-pressed") === "true" ? "false" : "true")));
}
function markOffered(refs) {
  root.querySelectorAll(".tool").forEach(t => t.classList.toggle("off", !refs.includes(t.dataset.t)));
}

function renderSources() {
  const el = $("#lpSources");
  if (!sources.size) { el.innerHTML = '<li class="empty">Standards and references from web_search appear here.</li>'; return; }
  el.innerHTML = [...sources.values()].map(s => {
    let host = ""; try { host = new URL(s.url).hostname.replace(/^www\./, ""); } catch (e) {}
    return `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a><span>${esc(host)}</span></li>`;
  }).join("");
}
function resetTools() {
  sources.clear(); renderSources();
  for (const k in toolCounts) delete toolCounts[k];
  root.querySelectorAll(".tool").forEach(t => { t.classList.remove("used", "err", "off"); $("b", t).textContent = "0"; });
}
function onTool(m) {
  const name = m.name || "";
  const group = ["slack", "notion", "github"].find(g => name.startsWith(g + "_")) || name;
  const t = $(`.tool[data-t="${group}"]`, root);
  if (m.status === "called") {
    toolCounts[group] = (toolCounts[group] || 0) + 1;
    if (t) { t.classList.add("used"); $("b", t).textContent = toolCounts[group]; }
    $("#lpTrend").textContent = `Coordinator → ${name}${m.detail ? ": " + m.detail : ""}`;
  } else if (m.status === "failed" && t) t.classList.add("err");
  for (const s of m.sources || []) if (s && s.url && !sources.has(s.url) && sources.size < 8) sources.set(s.url, s);
  if (m.sources && m.sources.length) renderSources();
}
function fail(msg) { busy(false); showError(msg); CODES.forEach(c => { if (!reports[c]) setAgent(c, "idle"); }); }

function onEvent(m) {
  if (m.kind === "antibody.agent.report") {
    const r = m.report; reports[r.subsystem] = r; setAgent(r.subsystem, statusOf(r), r); recompute();
  } else if (m.kind === "antibody.tool") {
    onTool(m);
  } else if (m.kind === "antibody.connectors") {
    markOffered(m.offered || []);
  } else if (m.kind === "antibody.scan.ranked") {
    LIVE.health = m.health; recompute();
    const box = $("#lpAgents"); m.ranked.forEach(r => box.appendChild($(`.agent[data-code="${r.subsystem}"]`, root)));
    const prev = m.previous_health;
    $("#lpTrend").innerHTML = prev != null
      ? `Health ${prev} → ${m.health} <b class="${m.health >= prev ? "up" : "down"}">${m.health >= prev ? "+" : ""}${m.health - prev}</b>, remembered in Flower run-series state`
      : `Health ${m.health}/100. Top threat: ${esc(m.ranked[0].subsystem)}. The coordinator is investigating…`;
  } else if (m.kind === "response.output_text.delta") {
    alertBuf += m.delta || "";
    if (!renderPending) { renderPending = true; requestAnimationFrame(() => renderAlert(false)); }
  } else if (m.kind === "done") {
    renderAlert(true); const secs = Math.round((Date.now() - t0) / 1000); busy(false); $("#lpTime").textContent = secs + "s";
  } else if (m.kind === "failed") {
    fail((m.raw && (m.raw.message || (m.raw.error && m.raw.error.message))) || "Run failed");
  }
}

async function scan(prompt) {
  const newSeries = nextNew; nextNew = false;
  if (closeStream) closeStream();
  showError(""); resetTools();
  for (const k in reports) delete reports[k];
  LIVE.active = true; LIVE.health = null;
  CODES.forEach(c => setAgent(c, "scanning")); recompute();
  alertBuf = ""; $("#lpAlert").innerHTML = "";
  const overrides = {};
  root.querySelectorAll('.chip[data-i][aria-pressed="true"]').forEach(ch => {
    for (const [k, v] of Object.entries(INJECTIONS[+ch.dataset.i].o)) overrides[k] = { ...(overrides[k] || {}), ...v };
  });
  const connectors = [...root.querySelectorAll('.chip.acct[aria-pressed="true"]')].map(c => c.dataset.ref);
  busy(true, "Starting a Flower run…");
  try { runId = (await startScan({ prompt, overrides, connectors, newSeries })).run_id; }
  catch (e) { return fail(e.message); }
  busy(true, `Flower run …${runId.slice(-6)}: ${CODES.length} agents hunting`);
  closeStream = streamRun(runId, onEvent);
}

root.querySelectorAll("#lpInject .chip").forEach(ch => ch.addEventListener("click", () => ch.setAttribute("aria-pressed", ch.getAttribute("aria-pressed") === "true" ? "false" : "true")));
$("#lpScan").onclick = () => scan("Check the building.");
$("#lpWatch").onclick = () => scan(WATCH_PROMPT);
$("#lpStop").onclick = async () => { if (runId) await stopRun(runId); if (closeStream) closeStream(); fail("Scan stopped"); };
$("#lpNew").onclick = () => { nextNew = true; $("#lpTrend").textContent = "New session: the next scan starts a fresh run series."; };
$("#lpAsk").onsubmit = e => { e.preventDefault(); const q = $("#lpAskIn").value.trim(); if (q) { scan(q); $("#lpAskIn").value = ""; } };

try {
  META = await fetchMeta();
  drawAgents();
  renderAccounts(META.connectors || []);
  if (META.error) { $("#lpDot").className = "dot bad"; $("#lpConn").textContent = `${META.superlink}: offline`; showError(META.error); }
  else { $("#lpDot").className = "dot on"; $("#lpConn").textContent = `Flower · ${META.superlink} · ${META.federation} · ${META.app}`; }
} catch (e) {
  drawAgents(); renderAccounts([]); $("#lpDot").className = "dot bad"; $("#lpConn").textContent = "Backend offline. Start antibody-web/server.py";
}

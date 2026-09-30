/* Governor view: Sentience Governor records across runs and agents (antibody-web/governor_stats.py).
 * Reads /api/governor every 10 seconds. Owner: governance lane. */

const $ = s => document.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const code = a => String(a).replace(/^antibody-/, "").toUpperCase();
const when = t => (t && t.length >= 15) ? `${t.slice(4, 6)}/${t.slice(6, 8)} ${t.slice(9, 11)}:${t.slice(11, 13)} UTC` : t;
const FLAG = { high_consequence: "high-consequence", outside_lane: "outside lane", scan_to_fix: "scan → fix", undeclared: "undeclared" };
const OPS = ["READ", "WRITE", "EXECUTE", "DELETE"];

function kpis(t) {
  const items = [
    ["Runs", t.runs], ["Agent sessions", t.sessions], ["Actions recorded", t.actions],
    ["High-consequence", t.high_consequence, "hc"], ["Outside lane", t.outside_lane, t.outside_lane ? "bad" : "ok"],
    ["Approvals recorded", t.approvals, "appr"],
  ];
  $("#gvKpis").innerHTML = items.map(([k, v, c]) => `<div class="gv-kpi ${c || ""}"><b>${v}</b><span>${k}</span></div>`).join("");
}

function learnings(list) {
  $("#gvLearn").innerHTML = list.map(l => `<li class="k-${esc(l.kind)}"><i></i>${esc(l.text)}</li>`).join("");
}

function running(live, runs, superlink) {
  const el = $("#gvLive");
  if (!live) { el.className = "gv-live off"; $("span", el).textContent = `${superlink}: no runs`; $("#gvRun").innerHTML = '<p class="gv-empty">No runs on this SuperLink yet.</p>'; return; }
  const on = !live.status.startsWith("finished");
  el.className = "gv-live " + (on ? "on" : "idle");
  $("span", el).textContent = on ? `run …${live.run.slice(-6)} ${live.status}` : `${superlink} · idle`;
  const recorded = runs.find(r => r.run === live.run);
  $("#gvRun").innerHTML = `
    <div class="gv-runline"><code>${esc(live.run)}</code><span class="st ${on ? "on" : ""}">${esc(live.status)}</span></div>
    <p>${recorded ? `${recorded.agents} agents recorded · ${recorded.actions} actions · ${recorded.flags.high_consequence || 0} high-consequence`
      : on ? "Agents are working; their records appear as they report." : "No Governor records for this run on this machine (hosted runs keep them on Flower's host)."}</p>`;
}

function approvals(list) {
  $("#gvAppr").innerHTML = list.length ? list.map(a => `
    <li><b>${esc(code(a.agent))}</b> <code>${esc(a.actions.join(", "))}</code><span>${esc(a.approval)} · ${esc(when(a.time))}</span></li>`).join("")
    : '<li class="gv-empty">None yet. Approve a held action in the live scan.</li>';
}

function heat(grid) {
  if (!grid.agents.length) { $("#gvHeat").innerHTML = '<p class="gv-empty">No records yet.</p>'; return; }
  const cols = grid.runs.length;
  $("#gvHeat").style.setProperty("--cols", cols);
  $("#gvHeat").innerHTML = grid.agents.map((a, i) => `
    <div class="hr"><b>${esc(code(a))}</b><div class="hc">${grid.cells[i].map((v, j) =>
      `<span class="c ${v == null ? "na" : v ? "v" + Math.min(v, 3) : "z"}" title="${esc(code(a))} · run …${esc(grid.runs[j].split("-")[0].slice(-6))}: ${v == null ? "not in run" : v + " flagged"}">${v || ""}</span>`).join("")}</div></div>`).join("");
}

function opsBar(ops) {
  const total = Object.values(ops).reduce((a, b) => a + b, 0) || 1;
  return `<div class="gv-ops">${OPS.filter(o => ops[o]).map(o => `<i class="o-${o}" style="width:${100 * ops[o] / total}%" title="${o} ${ops[o]}"></i>`).join("")}</div>`;
}

function agentsTable(list) {
  $("#gvAgents").innerHTML = `<thead><tr><th>Agent</th><th>Runs</th><th>Actions</th><th>Operation mix</th><th>Flags</th><th>Approvals</th><th>Most frequent actions</th></tr></thead>
    <tbody>${list.map(a => `<tr${a.no_profile ? ' class="warn"' : ""}>
      <td><b>${esc(code(a.agent))}</b></td><td>${a.runs}</td><td>${a.actions}</td><td>${opsBar(a.operations)}</td>
      <td>${Object.entries(a.flags).map(([f, n]) => `<span class="fl f-${f}">${esc(FLAG[f] || f)} ${n}</span>`).join("") || '<span class="gv-dim">none</span>'}</td>
      <td>${a.approvals || '<span class="gv-dim">0</span>'}</td>
      <td class="tools">${a.top_tools.map(t => `<code>${esc(t)}</code>`).join(" ")}</td></tr>`).join("")}</tbody>`;
}

function runsTable(list) {
  $("#gvRuns").innerHTML = `<thead><tr><th>Run</th><th>When</th><th>Agents</th><th>Actions</th><th>Flags</th><th>Approvals</th></tr></thead>
    <tbody>${list.map(r => `<tr><td><code>…${esc(r.run.slice(-8))}</code></td><td>${esc(when(r.time))}</td><td>${r.agents}</td><td>${r.actions}</td>
      <td>${Object.entries(r.flags).map(([f, n]) => `<span class="fl f-${f}">${esc(FLAG[f] || f)} ${n}</span>`).join("") || '<span class="gv-dim">none</span>'}</td>
      <td>${r.approvals || '<span class="gv-dim">0</span>'}</td></tr>`).join("")}</tbody>`;
}

async function load() {
  try {
    const d = await (await fetch("/api/governor", { cache: "no-store" })).json();
    $("#gvSrc").textContent = `Records: ${d.source} · SuperLink: ${d.superlink} · refreshed ${new Date().toLocaleTimeString()}`;
    kpis(d.totals); learnings(d.learnings); running(d.live, d.runs, d.superlink);
    approvals(d.approvals); heat(d.grid); agentsTable(d.agents); runsTable(d.runs);
  } catch (e) {
    $("#gvLive").className = "gv-live off"; $("#gvLive span").textContent = "backend offline";
  }
}
load();
setInterval(load, 10000);

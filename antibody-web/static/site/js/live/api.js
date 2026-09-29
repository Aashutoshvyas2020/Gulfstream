/* Client for the Antibody backend (antibody-web/server.py), which starts real Flower runs.
 * Owner: Flower integration.
 *
 * Run events relayed by the backend (the `kind` field):
 *   antibody.agent.report   { report: {subsystem, risk_score, confidence, finding, evidence,
 *                                      time_to_failure_days, recommended_action, failed} }
 *   antibody.tool           { name, status: "called" | "ok" | "failed", detail }
 *   antibody.scan.ranked    { health, previous_health, ranked: [report + rank, priority] }
 *   response.output_text.delta { delta }     coordinator alert text
 *   done | failed           end of the run ({ raw: { message } } on failure)
 */

export async function fetchMeta() {
  const res = await fetch("/api/meta");
  if (!res.ok) throw new Error(`meta failed (${res.status})`);
  return res.json(); // { building, snapshot, specialists, superlink, federation, error }
}

export async function startScan({ prompt, overrides = {}, connectors = [], newSeries = false }) {
  const res = await fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, overrides, connectors, new_series: newSeries }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Scan failed (${res.status})`);
  return data; // { run_id, series_id, prompt }
}

/** Subscribe to one run's events. Returns a function that closes the stream. */
export function streamRun(runId, onEvent) {
  const es = new EventSource(`/api/runs/${runId}/events`);
  es.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.kind === "done" || m.kind === "failed") es.close();
    onEvent(m);
  };
  return () => es.close();
}

export async function stopRun(runId) {
  await fetch(`/api/runs/${runId}/stop`, { method: "POST" }).catch(() => {});
}

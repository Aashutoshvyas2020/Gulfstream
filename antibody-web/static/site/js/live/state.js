/* Shared live-scan state: the contract between the Flower integration (api.js),
 * the live panel (panel.js) and the 3D scene (../scene.js).
 * Owner: Flower integration. Change the shape only together with the other two owners.
 *
 *   active      true once a live scan has started; the scene then shows live data
 *               in the Live chapter and while a scan runs
 *   scanning    a Flower run is in progress
 *   risk[i]     latest risk 0..1 for CODES[i], or null if that agent has not reported
 *   health      coordinator's health score (null until ranked); provisional is the
 *               running estimate while reports arrive
 *   reported    number of agents that have reported this scan
 */

// Same order as AREAS in ../areas.js and SPECIALISTS in agent/agent/specialists.py
export const CODES = ["PWR", "ELEC", "WIRE", "FIRE", "STR", "LIFT", "HVAC", "H2O", "ENV", "AIR", "CYBER", "GEN", "GAS", "EGRESS"];

export const LIVE = {
  active: false,
  scanning: false,
  risk: CODES.map(() => null),
  health: null,
  provisional: 100,
  reported: 0,
};

// Status bands used everywhere (panel, 3D, HUD)
export const statusOf = r =>
  !r ? "idle" : r.failed ? "offline" : r.risk_score >= 0.6 ? "infected" : r.risk_score >= 0.3 ? "watch" : "healthy";

/* The 11 problem areas as the site shows them, placed on the demo building: a model
 * of Stanford's Hoover Tower (library base, 12-floor tower, bell tower, dome) with two
 * levels below ground. Readings are simulated; the building is only the setting.
 * code/consequence match agent/agent/specialists.py; pos is the 3D anchor
 * (x: west→east, y: height, z: north→south; ground floor is y = 0).
 * demo is the risk the scripted story shows (taken from a real local scan).
 * Owner: story & 3D. */

export const FLOOR = 1.2; // scene units per floor

export const AREAS = [
  { code: "PWR",   name: "Battery bank",    where: "B1 · UPS room",           threat: "Thermal runaway",            consequence: 3,   pos: [4.5, -0.6, 2.4],   demo: 0.85 },
  { code: "ELEC",  name: "Switchgear",      where: "B1 · main board",         threat: "Hot breaker, arc flash",     consequence: 3,   pos: [-4.5, -0.6, 2.4],  demo: 0.55 },
  { code: "WIRE",  name: "Wall wiring",     where: "Library · west wall",     threat: "Hidden arcing",              consequence: 3,   pos: [-6.95, 1.8, 0.6], demo: 0.0 },
  { code: "FIRE",  name: "Sprinkler riser", where: "Tower · F5 stair",        threat: "Closed valve, low pressure", consequence: 3,   pos: [-1.8, 6.0, -2.55], demo: 0.4 },
  { code: "STR",   name: "Concrete column", where: "P1 · tower footing",      threat: "Growing crack",              consequence: 3,   pos: [1.6, -1.8, 1.6],   demo: 0.7 },
  { code: "LIFT",  name: "Elevator",        where: "Tower core",              threat: "Door and levelling faults",  consequence: 2,   pos: [0.0, 9.6, 0.0],    demo: 0.35 },
  { code: "HVAC",  name: "Rooftop chiller", where: "Library roof",            threat: "Efficiency loss, leaks",     consequence: 1.5, pos: [5.0, 3.05, -2.6],  demo: 0.35 },
  { code: "H2O",   name: "Pipe leak",       where: "Tower · F6 east riser",   threat: "Water at 2 a.m.",            consequence: 2,   pos: [2.55, 7.2, -1.0],  demo: 0.9 },
  { code: "ENV",   name: "Facade",          where: "Tower · F10 west face",   threat: "Rain getting in",            consequence: 1.5, pos: [-2.6, 12.0, 0.8],  demo: 0.4 },
  { code: "AIR",   name: "Air quality",     where: "Library reading room",    threat: "CO₂ and VOC build-up",       consequence: 1.5, pos: [-3.2, 1.0, 1.6],   demo: 0.2 },
  { code: "CYBER", name: "BMS controller",  where: "Ground floor · control room", threat: "Default password, open port", consequence: 2, pos: [3.6, 0.6, -2.6], demo: 0.95 },
];

export const statusFor = risk =>
  risk == null ? "idle" : risk >= 0.6 ? "infected" : risk >= 0.3 ? "watch" : "healthy";

/** Health out of 100: consequence-weighted risk, same formula as the coordinator. */
export function healthOf(risks) {
  let total = 0, weighted = 0;
  AREAS.forEach((a, i) => { total += a.consequence; if (risks[i] != null) weighted += risks[i] * a.consequence; });
  return 100 * (1 - weighted / total);
}

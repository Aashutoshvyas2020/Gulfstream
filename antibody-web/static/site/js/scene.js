/* The demo building, modelled on Stanford's Hoover Tower: sandstone library base, a
 * 12-floor tower with tall window slits, an open bell tower where the coordinator lives,
 * and a red-tile dome. Walls are semi-transparent so the infection points show through.
 * Also: Y-shaped antibody agents, the immune-shield dome (dims and reddens as health
 * drops) and a campus network for the network-immunity chapter.
 * Reads STORY (scroll) and LIVE (real Flower scans). Owner: story & 3D. */

import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js";
import { AREAS, FLOOR, statusFor, healthOf } from "./areas.js";
import { STORY } from "./story.js";
import { LIVE } from "./live/state.js";

const RM = matchMedia("(prefers-reduced-motion: reduce)").matches;
const lerp = (a, b, t) => a + (b - a) * t;
const clamp01 = x => Math.max(0, Math.min(1, x));
const ease = t => t * t * (3 - 2 * t);

const HEX = {
  bg: 0xf4efe6, sand: 0xd9c4a0, sandLite: 0xe8dcc5, sandEdge: 0x9a8262, sandDim: 0xbfae90, tile: 0xb4472f, tileDark: 0x8f3522,
  glassFloor: 0xe6d8bf, core: 0x8c1515,
  idle: 0xa39a8a, scan: 0x2f6fde, healthy: 0x12966a, watch: 0xc27c00, infected: 0xd6245e,
};
const STATUS_COLOR = {
  idle: new THREE.Color(HEX.idle), scanning: new THREE.Color(HEX.scan), healthy: new THREE.Color(HEX.healthy),
  watch: new THREE.Color(HEX.watch), infected: new THREE.Color(HEX.infected), offline: new THREE.Color(HEX.idle),
};

const canvas = document.getElementById("scene");
let renderer = null;
try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" }); }
catch (e) { document.documentElement.classList.add("no-webgl"); }

if (renderer) {
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.75));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(HEX.bg);
  scene.fog = new THREE.Fog(HEX.bg, 70, 190);
  const camera = new THREE.PerspectiveCamera(34, innerWidth / innerHeight, 0.1, 500);

  scene.add(new THREE.HemisphereLight(0xfff6e8, 0xd8ccb8, 1.15));
  const sun = new THREE.DirectionalLight(0xffffff, 1.1); sun.position.set(-18, 30, 22); scene.add(sun);

  /* ---------- helpers ---------- */
  const mat = (color, opacity = 1, extra = {}) => new THREE.MeshStandardMaterial({
    color, roughness: 0.85, metalness: 0, transparent: opacity < 1, opacity, depthWrite: opacity >= 1, ...extra });
  const edges = (geo, color, opacity, dashed = false) => {
    const m = dashed
      ? new THREE.LineDashedMaterial({ color, transparent: true, opacity, dashSize: 0.3, gapSize: 0.22 })
      : new THREE.LineBasicMaterial({ color, transparent: true, opacity });
    const line = new THREE.LineSegments(new THREE.EdgesGeometry(geo), m);
    if (dashed) line.computeLineDistances();
    return line;
  };
  const place = (obj, x, y, z) => { obj.position.set(x, y, z); return obj; };
  const shell = (w, h, d, x, y, z, color, opacity, edgeColor, edgeOpacity, group) => {
    const g = new THREE.BoxGeometry(w, h, d);
    group.add(place(new THREE.Mesh(g, mat(color, opacity)), x, y, z));
    group.add(place(edges(g, edgeColor, edgeOpacity), x, y, z));
  };

  /* ---------- the tower ---------- */
  const building = new THREE.Group(); scene.add(building);
  const BASE_W = 14, BASE_D = 10, BASE_H = 2 * FLOOR;       // library base, 2 floors
  const TW = 5, TOWER_H = 12 * FLOOR;                        // tower shaft, 12 floors
  const BELL_H = 2.4, BELL_Y = TOWER_H;                      // bell tower (open arcade)
  const DOME_Y = BELL_Y + BELL_H + 0.7;

  // below ground: B1 and P1 under the base, dashed
  const pit = new THREE.BoxGeometry(BASE_W, 2 * FLOOR, BASE_D);
  building.add(place(edges(pit, HEX.sandEdge, 0.55, true), 0, -FLOOR, 0));
  building.add(place(new THREE.Mesh(new THREE.BoxGeometry(BASE_W, 0.05, BASE_D), mat(HEX.glassFloor, 0.35)), 0, -FLOOR, 0));
  building.add(place(new THREE.Mesh(new THREE.BoxGeometry(BASE_W, 0.05, BASE_D), mat(HEX.glassFloor, 0.35)), 0, -2 * FLOOR, 0));

  // library base with parapet and an arched entrance on the south face
  shell(BASE_W, BASE_H, BASE_D, 0, BASE_H / 2, 0, HEX.sand, 0.32, HEX.sandEdge, 0.8, building);
  building.add(place(new THREE.Mesh(new THREE.BoxGeometry(BASE_W, 0.05, BASE_D), mat(HEX.glassFloor, 0.35)), 0, FLOOR, 0));
  shell(BASE_W + 0.3, 0.3, BASE_D + 0.3, 0, BASE_H + 0.15, 0, HEX.sandLite, 0.9, HEX.sandEdge, 0.6, building);
  for (const x of [-4.5, 0, 4.5]) {
    const arch = new THREE.Mesh(new THREE.TorusGeometry(0.75, 0.1, 8, 24, Math.PI), mat(HEX.sandEdge, 0.9));
    building.add(place(arch, x, 1.1, BASE_D / 2 + 0.02));
    const door = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 1.1), mat(0x7a6448, 0.35, { side: THREE.DoubleSide }));
    building.add(place(door, x, 0.55, BASE_D / 2 + 0.01));
  }
  // base windows
  for (let x = -6; x <= 6; x += 1.5) for (const y of [0.7, 1.9]) {
    if (y < 1.2 && [-4.5, 0, 4.5].some(ax => Math.abs(ax - x) < 1)) continue;
    const w = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.6), mat(0x8a7556, 0.35, { side: THREE.DoubleSide }));
    building.add(place(w, x, y, BASE_D / 2 + 0.015));
    const w2 = w.clone(); building.add(place(w2, x, y, -BASE_D / 2 - 0.015));
  }

  // tower shaft: walls, floors, corner piers, tall window slits
  shell(TW, TOWER_H, TW, 0, TOWER_H / 2, 0, HEX.sand, 0.3, HEX.sandEdge, 0.85, building);
  for (let f = 1; f < 12; f++) building.add(place(new THREE.Mesh(new THREE.BoxGeometry(TW - 0.1, 0.04, TW - 0.1), mat(HEX.glassFloor, 0.3)), 0, f * FLOOR, 0));
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
    shell(0.55, TOWER_H + 0.4, 0.55, sx * (TW / 2 - 0.1), (TOWER_H + 0.4) / 2, sz * (TW / 2 - 0.1), HEX.sandLite, 0.75, HEX.sandEdge, 0.5, building);
  }
  const slitMat = mat(0x8a7556, 0.26, { side: THREE.DoubleSide });
  for (const off of [-0.6, 0, 0.6]) {
    const slit = new THREE.PlaneGeometry(0.28, TOWER_H - 2.6);
    building.add(place(new THREE.Mesh(slit, slitMat), off, BASE_H + (TOWER_H - BASE_H) / 2 + 0.2, TW / 2 + 0.01));
    building.add(place(new THREE.Mesh(slit, slitMat), off, BASE_H + (TOWER_H - BASE_H) / 2 + 0.2, -TW / 2 - 0.01));
    const s2 = new THREE.Mesh(slit, slitMat); s2.rotation.y = Math.PI / 2;
    building.add(place(s2, TW / 2 + 0.01, BASE_H + (TOWER_H - BASE_H) / 2 + 0.2, off));
    const s3 = s2.clone(); building.add(place(s3, -TW / 2 - 0.01, BASE_H + (TOWER_H - BASE_H) / 2 + 0.2, off));
  }
  // cornice under the bell tower
  shell(TW + 0.5, 0.3, TW + 0.5, 0, TOWER_H + 0.15, 0, HEX.sandLite, 0.95, HEX.sandEdge, 0.6, building);

  // bell tower: open arcade on four piers with slender columns, the coordinator inside
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
    shell(0.6, BELL_H, 0.6, sx * (TW / 2 - 0.25), BELL_Y + 0.3 + BELL_H / 2, sz * (TW / 2 - 0.25), HEX.sandLite, 0.95, HEX.sandEdge, 0.5, building);
  }
  const colGeo = new THREE.CylinderGeometry(0.08, 0.08, BELL_H - 0.3, 10);
  for (const off of [-1.1, 0, 1.1]) for (const [x, z] of [[off, TW / 2 - 0.25], [off, -(TW / 2 - 0.25)], [TW / 2 - 0.25, off], [-(TW / 2 - 0.25), off]]) {
    building.add(place(new THREE.Mesh(colGeo, mat(HEX.sandLite, 0.95)), x, BELL_Y + 0.3 + BELL_H / 2 - 0.15, z));
  }
  shell(TW + 0.4, 0.35, TW + 0.4, 0, BELL_Y + 0.3 + BELL_H + 0.17, 0, HEX.sandLite, 0.95, HEX.sandEdge, 0.6, building);

  // drum, red-tile dome, lantern and finial
  const drum = new THREE.Mesh(new THREE.CylinderGeometry(2.1, 2.2, 0.7, 8), mat(HEX.sandLite, 0.95));
  building.add(place(drum, 0, DOME_Y - 0.35, 0));
  const domeGeo = new THREE.SphereGeometry(2.1, 32, 16, 0, Math.PI * 2, 0, Math.PI / 2);
  const dome = new THREE.Mesh(domeGeo, mat(HEX.tile, 1, { roughness: 0.7 })); dome.scale.set(1, 0.9, 1);
  building.add(place(dome, 0, DOME_Y, 0));
  const ribs = new THREE.Mesh(domeGeo, new THREE.MeshBasicMaterial({ color: HEX.tileDark, wireframe: true, transparent: true, opacity: 0.35 }));
  ribs.scale.set(1.005, 0.905, 1.005); building.add(place(ribs, 0, DOME_Y, 0));
  building.add(place(new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.48, 0.9, 8), mat(HEX.sandLite)), 0, DOME_Y + 2.2, 0));
  building.add(place(new THREE.Mesh(new THREE.ConeGeometry(0.5, 0.5, 8), mat(HEX.tile)), 0, DOME_Y + 2.9, 0));
  building.add(place(new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.7), mat(0x6b5a3f)), 0, DOME_Y + 3.45, 0));

  // services: elevator core, risers, chiller on the library roof
  const coreShaft = new THREE.BoxGeometry(1.2, TOWER_H + 2 * FLOOR, 1.2);
  building.add(place(edges(coreShaft, HEX.scan, 0.4), 0, (TOWER_H - 2 * FLOOR) / 2, 0));
  const riser = (x, z, y0, y1, color) => building.add(place(new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, y1 - y0), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.6 })), x, (y0 + y1) / 2, z));
  riser(2.2, -1.0, -2 * FLOOR, TOWER_H, 0x3a86d9);
  riser(-1.8, -2.2, -2 * FLOOR, TOWER_H, 0xc0506e);
  shell(2.4, 0.8, 1.6, 5.0, BASE_H + 0.3 + 0.4, -2.6, 0xcfc3ad, 0.95, HEX.sandEdge, 0.7, building);

  /* ---------- ground ---------- */
  // see-through lawn so the basement infections stay visible
  const lawn = new THREE.Mesh(new THREE.CircleGeometry(60, 64), new THREE.MeshBasicMaterial({ color: 0xece4d6, transparent: true, opacity: 0.55, depthWrite: false }));
  lawn.rotation.x = -Math.PI / 2; lawn.position.y = -0.01; scene.add(lawn);
  const grid = new THREE.GridHelper(120, 60, 0xd7ccb9, 0xe4dbcb); grid.position.y = 0.002; scene.add(grid);

  /* ---------- immune shield ---------- */
  const shieldMat = new THREE.MeshBasicMaterial({ color: HEX.healthy, wireframe: true, transparent: true, opacity: 0.2, depthWrite: false });
  const shield = new THREE.Mesh(new THREE.SphereGeometry(11.5, 36, 18, 0, Math.PI * 2, 0, Math.PI / 2), shieldMat);
  shield.scale.set(1, 1.95, 1); scene.add(shield);

  /* ---------- coordinator core in the bell tower ---------- */
  const brainPos = new THREE.Vector3(0, BELL_Y + 0.3 + BELL_H / 2, 0);
  const coordinator = new THREE.Group(); coordinator.position.copy(brainPos); scene.add(coordinator);
  const coordShell = new THREE.Mesh(new THREE.IcosahedronGeometry(0.75, 1), new THREE.MeshBasicMaterial({ color: HEX.core, wireframe: true, transparent: true, opacity: 0.85 }));
  const coordCore = new THREE.Mesh(new THREE.SphereGeometry(0.3, 20, 20), new THREE.MeshBasicMaterial({ color: 0xe0303a }));
  const coordGlow = new THREE.PointLight(0xe0303a, 3, 6);
  coordinator.add(coordShell, coordCore, coordGlow);

  /* ---------- infection points ---------- */
  const ringGeo = new THREE.RingGeometry(0.36, 0.44, 40);
  const spots = AREAS.map(a => {
    const g = new THREE.Group(); g.position.set(...a.pos); scene.add(g);
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.26, 20, 20), new THREE.MeshBasicMaterial({ color: HEX.idle, transparent: true }));
    const ring = new THREE.Mesh(ringGeo, new THREE.MeshBasicMaterial({ color: HEX.idle, transparent: true, side: THREE.DoubleSide, depthWrite: false }));
    const light = new THREE.PointLight(HEX.infected, 0, 4);
    g.add(dot, ring, light);
    return { g, dot, ring, light, color: new THREE.Color(HEX.idle), vis: 0 };
  });

  /* ---------- antibodies (one per area) ---------- */
  const abMat = new THREE.MeshBasicMaterial({ color: HEX.healthy });
  const stemGeo = new THREE.CylinderGeometry(0.045, 0.045, 0.42), armGeo = new THREE.CylinderGeometry(0.04, 0.04, 0.32);
  const antibodies = AREAS.map((a, i) => {
    const g = new THREE.Group();
    const stem = new THREE.Mesh(stemGeo, abMat); stem.position.y = -0.21;
    const l = new THREE.Mesh(armGeo, abMat); l.position.set(-0.1, 0.12, 0); l.rotation.z = 0.62;
    const r = new THREE.Mesh(armGeo, abMat); r.position.set(0.1, 0.12, 0); r.rotation.z = -0.62;
    g.add(stem, l, r); g.visible = false; scene.add(g);
    const end = new THREE.Vector3(...a.pos).add(new THREE.Vector3(0, 0.65, 0));
    const ctrl = brainPos.clone().lerp(end, 0.5).add(new THREE.Vector3((i % 2 ? 1 : -1) * 5, 3, (i % 3 - 1) * 3));
    return { g, end, ctrl, delay: i * 0.045 };
  });
  const bezier = (p0, p1, p2, t, out) => out.set(
    (1 - t) ** 2 * p0.x + 2 * (1 - t) * t * p1.x + t * t * p2.x,
    (1 - t) ** 2 * p0.y + 2 * (1 - t) * t * p1.y + t * t * p2.y,
    (1 - t) ** 2 * p0.z + 2 * (1 - t) * t * p1.z + t * t * p2.z);

  /* ---------- campus network (red-roof quad buildings) ---------- */
  const network = new THREE.Group(); scene.add(network);
  const nodes = [];
  const NODE_COUNT = 6;
  for (let i = 0; i < NODE_COUNT; i++) {
    const ang = (i / NODE_COUNT) * Math.PI * 2 + 0.5, R = 34;
    const w = 7 + (i % 3), d = 5, h = 2.6 + (i % 2) * 1.2;
    const n = new THREE.Group(); n.position.set(Math.cos(ang) * R, 0, Math.sin(ang) * R); n.rotation.y = -ang;
    const bodyMat = mat(HEX.sand, 0.9), edgeLine = edges(new THREE.BoxGeometry(w, h, d), HEX.sandEdge, 0.8);
    const body = place(new THREE.Mesh(new THREE.BoxGeometry(w, h, d), bodyMat), 0, h / 2, 0);
    const roofMat = mat(HEX.tile, 0.95);
    const roof = new THREE.Mesh(new THREE.ConeGeometry(Math.hypot(w, d) / 2 * 1.02, 1.6, 4, 1), roofMat);
    roof.rotation.y = Math.PI / 4; roof.scale.set(1, 1, d / w); place(roof, 0, h + 0.8, 0);
    n.add(body, place(edgeLine, 0, h / 2, 0), roof);
    const dome = new THREE.Mesh(new THREE.SphereGeometry(6.2, 22, 11, 0, Math.PI * 2, 0, Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: HEX.healthy, wireframe: true, transparent: true, opacity: 0, depthWrite: false }));
    n.add(dome);
    network.add(n);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0.05, 0), n.position.clone().setY(0.05)]),
      new THREE.LineDashedMaterial({ color: HEX.scan, transparent: true, opacity: 0, dashSize: 0.8, gapSize: 0.5 }));
    line.computeLineDistances(); network.add(line);
    const packet = new THREE.Mesh(new THREE.SphereGeometry(0.4, 12, 12), new THREE.MeshBasicMaterial({ color: HEX.healthy, transparent: true, opacity: 0 }));
    network.add(packet);
    nodes.push({ n, dome, line, packet, mats: [bodyMat, edgeLine.material, roofMat], phase: i / NODE_COUNT });
  }

  /* ---------- HTML labels ---------- */
  const labelsEl = document.getElementById("labels");
  const labels = AREAS.map(a => {
    const el = document.createElement("div"); el.className = "tag"; el.innerHTML = `<i></i>${a.code}<span>${a.name}</span>`;
    // Click a label: the live panel opens that agent (evidence, fix, Governor record)
    el.dataset.code = a.code; el.title = `${a.code}: evidence, fix and Governor record`;
    el.addEventListener("click", () => window.dispatchEvent(new CustomEvent("antibody:focus", { detail: a.code })));
    labelsEl.appendChild(el); return el;
  });

  /* ---------- camera poses per chapter (hero … flower) ---------- */
  const POSES = [
    { p: [40, 17, 50], t: [0, 10, 0] },     // hero: the whole tower
    { p: [29, 10, 34], t: [0, 6.5, 0] },    // threats
    { p: [-33, 22, 31], t: [0, 9, 0] },     // hunt: antibodies leave the bell tower
    { p: [25, 7, 27], t: [0.5, 5, 0] },     // respond
    { p: [0, 78, 88], t: [0, 0, 0] },       // network: the campus
    { p: [38, 15, 46], t: [0, 9.5, 0] },    // live: whole tower beside the panel
    { p: [52, 30, 60], t: [0, 9, 0] },      // flower
  ];
  const camPos = new THREE.Vector3(...POSES[0].p), camTarget = new THREE.Vector3(...POSES[0].t);
  const wantPos = new THREE.Vector3(), wantTarget = new THREE.Vector3(), tmpA = new THREE.Vector3(), tmpB = new THREE.Vector3();
  const UP = new THREE.Vector3(0, 1, 0);
  let mx = 0, my = 0;
  addEventListener("pointermove", e => { mx = e.clientX / innerWidth - 0.5; my = e.clientY / innerHeight - 0.5; });

  function resize() {
    const w = innerWidth, h = innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // wide screens: push the building right of the text column
    if (w > 900) camera.setViewOffset(w, h, -w * 0.17, 0, w, h); else camera.clearViewOffset();
    camera.updateProjectionMatrix();
  }
  addEventListener("resize", resize); resize();

  /* ---------- per-frame state ---------- */
  const risks = AREAS.map(() => null);
  const clock = new THREE.Clock();
  const proj = new THREE.Vector3();

  function areaState(i) {
    if (STORY.useLive) {
      const r = LIVE.risk[i];
      return { risk: r, status: r == null ? (LIVE.scanning ? "scanning" : "idle") : statusFor(r), visible: 1 };
    }
    if (STORY.chapter === "live" || STORY.chapter === "flower") return { risk: null, status: "idle", visible: 1 };
    const visible = clamp01(STORY.reveal * AREAS.length - i); // reveal one by one
    if (visible <= 0) return { risk: null, status: "idle", visible: 0 };
    const demo = AREAS[i].demo;
    // before the hunt anything found reads as a threat; once an antibody binds, healthy areas clear
    const bound = STORY.hunt * AREAS.length * 1.2 - i > 1;
    const status = demo < 0.3 ? (bound ? "healthy" : "watch") : statusFor(demo);
    return { risk: demo, status, visible };
  }

  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;

    /* camera: hold each chapter's pose while its card is read, travel as the next card arrives */
    const i0 = Math.min(POSES.length - 1, Math.floor(STORY.f)), i1 = Math.min(POSES.length - 1, i0 + 1);
    const k = ease(clamp01((STORY.f - i0 - 0.6) / 0.35));
    wantPos.set(...POSES[i0].p).lerp(tmpA.set(...POSES[i1].p), k);
    wantTarget.set(...POSES[i0].t).lerp(tmpB.set(...POSES[i1].t), k);
    wantPos.applyAxisAngle(UP, RM ? 0 : Math.sin(t * 0.08) * 0.12 + mx * 0.18);
    wantPos.y += RM ? 0 : -my * 2;
    camPos.lerp(wantPos, 1 - Math.pow(0.001, dt));
    camTarget.lerp(wantTarget, 1 - Math.pow(0.001, dt));
    camera.position.copy(camPos); camera.lookAt(camTarget);

    /* infection points */
    let anyVisible = false;
    AREAS.forEach((a, i) => {
      const s = spots[i], st = areaState(i);
      risks[i] = st.visible > 0.5 ? st.risk : null;
      if (st.visible > 0.5) anyVisible = true;
      s.vis = lerp(s.vis, st.visible, 1 - Math.pow(0.01, dt));
      s.color.lerp(STATUS_COLOR[st.status] || STATUS_COLOR.idle, 1 - Math.pow(0.02, dt));
      const focused = STORY.focus > 0 && st.status === "infected";
      const pulse = st.status === "infected" ? 1 + 0.35 * Math.abs(Math.sin(t * 3 + i)) : st.status === "scanning" ? 1 + 0.25 * Math.sin(t * 5 + i) : 1;
      const dim = STORY.focus > 0 && !focused ? 1 - 0.7 * STORY.focus : 1;
      s.g.visible = s.vis > 0.02;
      s.dot.material.color.copy(s.color); s.dot.material.opacity = s.vis * dim;
      s.dot.scale.setScalar(s.vis * pulse * (focused ? 1 + STORY.focus * 0.6 : 1));
      const phase = (t * 0.9 + i * 0.13) % 1;
      s.ring.material.color.copy(s.color); s.ring.material.opacity = s.vis * 0.85 * dim * (1 - phase);
      s.ring.scale.setScalar(1 + phase * (st.status === "infected" ? 2.2 : 1.2));
      s.ring.quaternion.copy(camera.quaternion);
      s.light.color.copy(s.color);
      s.light.intensity = st.status === "infected" ? s.vis * (3 + 2 * Math.sin(t * 4 + i)) * dim : 0;

      /* labels */
      const lab = labels[i];
      if (!(s.vis > 0.5 && STORY.network < 0.3 && STORY.f > 0.8)) { lab.style.opacity = 0; lab.style.pointerEvents = "none"; return; }
      proj.copy(s.g.position).project(camera);
      if (proj.z > 1) { lab.style.opacity = 0; lab.style.pointerEvents = "none"; return; }
      const x = (proj.x + 1) / 2 * innerWidth, y = (1 - proj.y) / 2 * innerHeight;
      lab.style.transform = `translate3d(${(x + 12).toFixed(1)}px,${(y - 11).toFixed(1)}px,0)`;
      lab.style.opacity = (dim * s.vis).toFixed(2);
      lab.style.pointerEvents = dim * s.vis > 0.3 ? "auto" : "none"; // only visible labels take clicks
      lab.dataset.s = st.status;
    });

    /* antibodies fly from the bell tower and bind */
    const huntT = STORY.useLive ? (LIVE.reported / AREAS.length) : STORY.f >= 4.9 ? 0 : STORY.hunt;
    antibodies.forEach((ab, i) => {
      const k2 = ease(clamp01((huntT - ab.delay) / 0.5));
      ab.g.visible = k2 > 0.001 && STORY.network < 0.5;
      if (!ab.g.visible) return;
      bezier(brainPos, ab.ctrl, ab.end, k2, ab.g.position);
      ab.g.position.y += k2 >= 1 ? Math.sin(t * 2 + i) * 0.06 : 0;
      ab.g.rotation.y = t * 1.2 + i;
      ab.g.scale.setScalar(0.6 + 0.6 * k2);
    });

    /* health, shield and coordinator */
    const anyRisk = risks.some(r => r != null);
    const health = STORY.useLive && LIVE.health != null ? LIVE.health : anyVisible && anyRisk ? healthOf(risks) : null;
    STORY.health = health;
    const h01 = health == null ? 1 : health / 100;
    shieldMat.color.set(HEX.healthy).lerp(STATUS_COLOR.infected, 1 - h01);
    shieldMat.opacity = (0.04 + 0.08 * h01) * (1 - STORY.network * 0.6);
    shield.rotation.y = t * 0.03;
    coordShell.rotation.set(t * 0.3, t * 0.45, 0);
    coordCore.scale.setScalar(1 + 0.12 * Math.sin(t * 2.4) + (LIVE.scanning ? 0.25 : 0));
    coordGlow.intensity = 2.5 + 1.5 * Math.sin(t * 2.4) + (LIVE.scanning ? 2 : 0);

    /* campus network */
    const nv = STORY.network;
    network.visible = nv > 0.01;
    nodes.forEach(nd => {
      nd.mats[0].opacity = 0.9 * nv; nd.mats[1].opacity = 0.8 * nv; nd.mats[2].opacity = 0.95 * nv;
      nd.line.material.opacity = 0.6 * nv;
      const travel = (t * 0.35 + nd.phase) % 1;
      nd.packet.position.lerpVectors(tmpA.set(0, 0.8, 0), tmpB.copy(nd.n.position).setY(0.8), travel);
      nd.packet.material.opacity = nv * (travel < 0.95 ? 1 : 0);
      nd.dome.material.opacity = nv * 0.3 * clamp01((nv - 0.3) / 0.4);
    });

    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

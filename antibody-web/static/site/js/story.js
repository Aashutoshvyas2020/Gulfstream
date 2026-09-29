/* Scroll story: turns scroll position into STORY state for the 3D scene, and keeps the
 * HUD and chapter widgets in sync. Owner: story & 3D.
 *
 * STORY (read by scene.js every frame):
 *   f          continuous chapter position: 2.4 = 40 % through chapter index 2
 *   chapter    id of the chapter under the viewport centre
 *   reveal     0..1 threats found (Threats chapter)
 *   hunt       0..1 antibodies deployed and bound (Hunt chapter)
 *   focus      0..1 top threats highlighted (Respond chapter)
 *   network    0..1 network view (Network chapter)
 *   useLive    the scene should show LIVE data instead of the script
 *   health     written back by scene.js: the health it is showing (null = unmonitored)
 */

import { AREAS, statusFor } from "./areas.js";
import { LIVE } from "./live/state.js";

export const CHAPTERS = ["hero", "threats", "hunt", "respond", "network", "live", "flower"];

export const STORY = { f: 0, chapter: "hero", reveal: 0, hunt: 0, focus: 0, network: 0, useLive: false, health: null };

const clamp01 = x => Math.max(0, Math.min(1, x));
const ramp = (a, b, x) => clamp01((x - a) / (b - a));

function update() {
  const mid = innerHeight / 2;
  let f = 0;
  CHAPTERS.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.top <= mid) f = i + clamp01((mid - r.top) / r.height);
  });
  STORY.f = f;
  STORY.chapter = CHAPTERS[Math.min(CHAPTERS.length - 1, Math.floor(f))];
  // a chapter's card is read while f is about i-0.2 … i+0.6 (see scene.js POSES)
  STORY.reveal = ramp(0.85, 1.4, f);
  STORY.hunt = ramp(1.85, 2.4, f);
  STORY.focus = ramp(2.9, 3.2, f) * (1 - ramp(3.6, 3.85, f));
  STORY.network = ramp(3.75, 4.05, f) * (1 - ramp(4.6, 4.85, f));
  fadeCards();
  STORY.useLive = LIVE.active && (STORY.chapter === "live" || LIVE.scanning);
}

/* ---------- chapter cards fade in as they arrive and out as they leave ---------- */
const cards = CHAPTERS.map(id => document.querySelector(`#${id} .card`)).filter(Boolean);
function fadeCards() {
  const vh = innerHeight;
  for (const card of cards) {
    const r = card.parentElement.getBoundingClientRect();
    const o = clamp01((vh * 0.9 - r.top) / (vh * 0.3)) * clamp01((r.bottom - vh * 0.55) / (vh * 0.3));
    card.style.opacity = o.toFixed(3);
    card.style.transform = `translateY(${((1 - o) * 18).toFixed(1)}px)`;
  }
}

/* ---------- HUD ---------- */
const hud = {
  el: document.getElementById("hud"),
  num: document.getElementById("hudNum"),
  state: document.getElementById("hudState"),
  src: document.getElementById("hudSrc"),
  ring: document.getElementById("hudRing"),
};
let lastHud = "";
function renderHud() {
  const h = STORY.health;
  const state = h == null ? "Unmonitored" : h < 55 ? "Critical" : h < 80 ? "Degraded" : "Healthy";
  const src = STORY.useLive ? (LIVE.scanning ? "Live · Flower run" : "Live · last scan")
    : STORY.chapter === "live" ? "Press Scan building" : "Scripted story";
  const sig = `${h == null ? "-" : Math.round(h)}|${state}|${src}`;
  if (sig === lastHud) return;
  lastHud = sig;
  hud.num.textContent = h == null ? "—" : Math.round(h);
  hud.state.textContent = state;
  hud.src.textContent = src;
  hud.el.dataset.state = state.toLowerCase();
  hud.el.classList.toggle("is-live", STORY.useLive);
  hud.ring.style.strokeDashoffset = String(176 * (1 - (h ?? 0) / 100));
}

/* ---------- Threats chapter list ---------- */
const threatItems = [...document.querySelectorAll("#threatList li")];
let lastReveal = "";
function renderThreats() {
  const shown = Math.round(STORY.reveal * AREAS.length), bound = STORY.hunt > 0.6;
  const sig = shown + (bound ? "b" : "");
  if (sig === lastReveal) return;
  lastReveal = sig;
  threatItems.forEach((li, i) => {
    const demo = AREAS[i].demo;
    li.dataset.s = i >= shown ? "idle" : demo < 0.3 && !bound ? "watch" : statusFor(demo);
  });
}

function frame() {
  update();
  renderHud();
  renderThreats();
  requestAnimationFrame(frame);
}

/* in-page links scroll smoothly and keep the URL clean */
document.querySelectorAll("[data-go]").forEach(a => a.addEventListener("click", e => {
  e.preventDefault();
  document.getElementById(a.dataset.go)?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}));

requestAnimationFrame(frame);

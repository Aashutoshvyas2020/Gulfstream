# Antibody site

The home page served at `/` by `../../server.py`: a scroll story over a three.js model of Stanford's Hoover Tower (the demo building; readings are simulated), plus a Live chapter that runs real Flower scans. Light sandstone and Stanford-cardinal palette. All code here is original to this project.

## Chapters

| # | Id | What the 3D shows |
| :-- | :-- | :-- |
| 0 | `hero` | The tower under its immune-shield dome; the coordinator core glows in the bell tower |
| 1 | `threats` | The 11 infection points appear one by one (scripted demo risks) |
| 2 | `hunt` | Y-shaped antibodies fly out of the bell tower and bind; low-risk areas clear to green |
| 3 | `respond` | The top threats pulse, everything else dims; Tier 0–3 ladder and a sample alert |
| 4 | `network` | Camera pulls back to a campus: six red-roof quad buildings, lessons travelling to each (illustrative) |
| 5 | `live` | Real Flower scans: points, antibodies and HUD follow the agent reports |
| 6 | `flower` | The Flower features used |

The shield dims and turns red as health drops. The HUD (top right) shows health and whether it comes from the scripted story or a live run.

## Files and lanes

| File | What it does | Lane |
| :-- | :-- | :-- |
| `index.html` | Markup and copy for every chapter | Story & 3D |
| `css/site.css` | Design tokens, layout, chapters, HUD, live panel styles | Story & 3D (live panel block: Live scan UI) |
| `js/areas.js` | The 11 areas: names, 3D positions, consequence weights, demo risks, `healthOf()` | Story & 3D |
| `js/story.js` | Scroll → `STORY` state (chapter, reveal, hunt, focus, network), HUD, card fades, threat list | Story & 3D |
| `js/scene.js` | three.js scene: the tower model, infection points, antibodies, shield, coordinator, campus network, labels, camera | Story & 3D |
| `js/live/panel.js` | Live chapter UI: scan, Watch 24/7, injections, connectors, agents, alert, Ask | Live scan UI |
| `js/live/api.js` | Backend client and the run-event contract | Flower integration |
| `js/live/state.js` | Shared `LIVE` state read by the scene and written by the panel | Flower integration |

`state.js` and the events documented in `api.js` are shared contracts: change them only with all three lanes.

## Tuning

- Camera per chapter: `POSES` in `scene.js`. Each pose holds while its card is read and travels as the next card arrives.
- Story timing: the `ramp(...)` ranges in `story.js`.
- Demo risks and 3D positions: `areas.js`.

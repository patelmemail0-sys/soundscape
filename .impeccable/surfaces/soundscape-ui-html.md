---
version: 1
slug: "soundscape-ui-html"
primary_target: "soundscape/ui.html"
related_targets: ["soundscape/remote.py"]
---

## Scope

Operate mode: the Soundscape dashboard, a single static page served by `soundscape ui` /
`soundscape start` and opened in the system browser on a Mac or on a phone over the local
network. Redesign, not refinement: the old look is anti-reference. Preserve every API call,
element id and class the JavaScript and the Playwright drivers rely on, the job polling,
the token header, the stale-version banner, the Quit flow and all copy that states a
product rule (full mix to every speaker, built-in microphone pinned, no volume limiting).

Audience: one person at home, evening, laptop on the table between the speakers or phone
in hand. Job: bring the speakers up, confirm every one is connected in music mode and in
sync, set volumes, calibrate once, leave the music playing. Proof on the page is live
state: connection, mode, sample rate, measured lag, applied trim. Constraints: no build
step, no external fonts or scripts, Safari and Chromium, light and dark, 390 px to 1280 px.

## Direction contract

THESIS: A control surface that proves alignment, not just connection. The page leads with
the live signal path and a time ruler of measured arrivals; it refuses the dashboard
default of a sidebar plus a grid of identical metric cards, and it refuses cards nested in
panels under tracked uppercase labels.

OWN-WORLD: A late-evening listening room. Dark scheme: warm graphite ground
(`oklch(19% 0.012 60)`) under a deeper tinted top bar; light scheme: neutral cool-white
ground with the same bar logic. One system sans on a fixed 1.2 rem scale, sentence case,
tabular numerals everywhere a number moves. One accent, cobalt (`oklch(62% 0.17 255)`),
for the primary action, selection, focus and the live path. Six speaker series hues used
only as data colour: the name dot, the slider, the path branch, the ruler mark. Green,
amber and red appear only on state. Flat surfaces, 1 px hairlines, 10 px radii, one soft
shadow on the toast only.

STORY: In one glance the visitor knows whether music can flow and whether the speakers
agree in time; they believe the chain adds nothing and hides nothing; they press one
button, nudge a slider, calibrate, and go back to the music.

FIRST VIEWPORT: 1280 px: top bar with the mark, the name, a status chip, and the primary
Bring up button at the right. Below it a status strip: engine, speakers connected, sync
spread, master volume. Then the signal path across the full width: Your apps, the
Soundscape mix, a fan of branches each carrying its trim chip into a speaker node, lit in
cobalt when audio can flow. Beneath: speaker cards in two columns on the left; Sync with
the arrival ruler at the top right, then Add a speaker, Settings, Doctor; Activity last,
full width. 390 px: one column, the path stacks vertically, actions wrap under the brand.

FORM: Operate shell with one signature move, the arrival ruler (position 1 of my list;
alternatives considered: console channel strips, an Audio MIDI Setup lookalike).
seed: none (unavailable)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Unresolved

* Whether to show a session history of sync spreads (only the last measurement exists in
  the API today).
* Media keys are Linux-only (`playerctl`); the row is hidden on macOS rather than
  reworked.

## Adaptations (recorded after the finish review)

* Lower grid: Add a speaker and Doctor sit in the left column under the speaker cards; Sync
  and Settings in the right column. Reason: with two speakers the contract's arrangement left
  about 640 px of empty ground under the cards at 1280 px; the finish reviewer offered this
  balance as an acceptable alternative.
* Two shades of the one cobalt: lines, focus, selection and the live chip use
  `oklch(62% 0.17 255)`; filled primary buttons and switches use `oklch(52% 0.19 258)` so
  white text on them keeps 4.5:1. Same hue, one accent.
* The header mark is the flat-fill rendition of `assets/soundscape.svg` (same geometry, the
  gradients dropped) so it stays crisp at 26 px.
* The sweep along the signal path is the page's one authored motion: links, trunk and
  branches fill once when the output flips from down to up (480 ms, staggered), never on
  page load, and not at all under reduced motion.

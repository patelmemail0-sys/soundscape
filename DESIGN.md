---
name: Soundscape
description: A late-evening control surface that proves the speakers agree in time.
colors:
  # dark scheme (:root) is canonical; -light siblings are the prefers-color-scheme: light overrides of the same custom property
  ground: "oklch(19% 0.012 60)"
  bar: "oklch(15% 0.014 60)"
  surface: "oklch(23% 0.012 60)"
  surface-raised: "oklch(28% 0.012 60)"
  hairline: "oklch(33% 0.012 60)"
  edge: "oklch(42% 0.012 60)"
  ink: "oklch(95% 0.008 80)"
  ink-muted: "oklch(73% 0.012 70)"
  cobalt: "oklch(62% 0.17 255)"
  cobalt-deep: "oklch(52% 0.19 258)"
  cobalt-deep-hover: "oklch(57% 0.19 258)"
  on-cobalt: "#fff"
  ok: "oklch(76% 0.16 150)"
  warn: "oklch(80% 0.14 75)"
  bad: "oklch(72% 0.18 25)"
  series-1: "oklch(74% 0.16 335)"
  series-2: "oklch(74% 0.12 195)"
  series-3: "oklch(78% 0.15 110)"
  series-4: "oklch(74% 0.14 300)"
  series-5: "oklch(76% 0.15 50)"
  series-6: "oklch(74% 0.15 0)"
  ground-light: "oklch(97.5% 0.004 250)"
  bar-light: "oklch(92.5% 0.012 250)"
  surface-light: "oklch(100% 0 0)"
  surface-raised-light: "oklch(95% 0.005 250)"
  hairline-light: "oklch(88% 0.008 250)"
  edge-light: "oklch(78% 0.01 250)"
  ink-light: "oklch(18% 0.01 250)"
  ink-muted-light: "oklch(45% 0.012 250)"
  cobalt-light: "oklch(50% 0.2 258)"
  cobalt-deep-light: "oklch(50% 0.2 258)"
  cobalt-deep-hover-light: "oklch(45% 0.2 258)"
  ok-light: "oklch(46% 0.15 150)"
  warn-light: "oklch(47% 0.13 70)"
  bad-light: "oklch(48% 0.19 25)"
  series-1-light: "oklch(54% 0.19 335)"
  series-2-light: "oklch(52% 0.12 195)"
  series-3-light: "oklch(55% 0.15 110)"
  series-4-light: "oklch(52% 0.17 300)"
  series-5-light: "oklch(58% 0.17 50)"
  series-6-light: "oklch(54% 0.18 0)"
typography:
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.2rem"
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "-0.01em"
  brand:
    fontFamily: "-apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.0667rem"
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "-0.01em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "-0.01em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tabular-nums"
  small:
    fontFamily: "-apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.8667rem"
    fontWeight: 400
    lineHeight: 1.45
  control:
    fontFamily: "-apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.8667rem"
    fontWeight: 500
    lineHeight: 1.3
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.8rem"
    fontWeight: 400
    lineHeight: 1.4
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.8rem"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  r-s: "7px"
  r: "10px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "8px"
  md: "10px"
  lg: "12px"
  xl: "14px"
  2xl: "16px"
  3xl: "18px"
  gutter: "24px"
  section: "30px"
  page-end: "72px"
components:
  button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.control}"
    rounded: "{rounded.r-s}"
    padding: "6px 12px"
  button-hover:
    backgroundColor: "{colors.surface-raised}"
  button-pressed:
    backgroundColor: "{colors.surface-raised}"
  button-primary:
    backgroundColor: "{colors.cobalt-deep}"
    textColor: "{colors.on-cobalt}"
    typography: "{typography.control}"
    rounded: "{rounded.r-s}"
    padding: "6px 12px"
  button-primary-hover:
    backgroundColor: "{colors.cobalt-deep-hover}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink-muted}"
    typography: "{typography.control}"
    rounded: "{rounded.r-s}"
    padding: "6px 12px"
  button-quiet-hover:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
  button-danger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.bad}"
  chip:
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "2px 9px"
  chip-live:
    textColor: "{colors.ink}"
  trim-chip:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "1px 8px"
  trim-chip-set:
    textColor: "{colors.ink}"
  switch:
    backgroundColor: "{colors.surface-raised}"
    rounded: "11px"
    width: "38px"
    height: "22px"
  switch-on:
    backgroundColor: "{colors.cobalt-deep}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.r-s}"
    padding: "6px 9px"
  stepper:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.r-s}"
  stepper-button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.control}"
    padding: "4px 9px"
  stepper-readout:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    typography: "{typography.small}"
    padding: "0 8px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.r}"
    padding: "12px 14px"
  path-node:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.r}"
    padding: "9px 12px"
  summary-strip:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.r}"
    padding: "12px 16px"
  top-bar:
    backgroundColor: "{colors.bar}"
    padding: "10px 24px"
  banner:
    typography: "{typography.small}"
    padding: "8px 24px"
  log:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.mono}"
    rounded: "{rounded.r}"
    padding: "10px 12px"
  toast:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.r}"
    padding: "11px 14px"
---

# Design System: Soundscape

## Overview

**Creative North Star: "The Late-Evening Listening Room"**

Soundscape's dashboard is a control surface for one person at home, laptop on the table between the speakers, lights low. The system is built for that scene first: a warm graphite ground under a deeper tinted bar, warm-white ink, and a single cobalt accent that marks what is alive, what is selected and what has focus. The light scheme keeps the same logic on a cool near-white ground. Everything else on the page is either a measurement or a control, and the colour vocabulary is sized to exactly that: six series hues that belong to speakers, three state colours that belong to conditions, nothing decorative.

Density is high and the tone is calm. One system sans on a 15 px root carries five sizes; headings are sentence case at weight 600 and never shout; every number sits in tabular figures so readouts and trims hold still while they change. Surfaces are flat and separated by 1 px hairlines and one tonal step; the only shadow on the page belongs to the toast. State is drawn as a border or ink colour change over a 200 ms ease, never as a glow, a fill or a lift.

The page refuses the dashboard default. There is no sidebar, no grid of identical metric cards, and no card nested in a panel under a tracked uppercase label. It leads with the live signal path (your apps, the Soundscape mix, a fan of branches into speaker nodes) and with the arrival ruler, the page's one signature move, where each speaker's measured arrival and its position after delay sit on a shared millisecond axis.

**Key Characteristics:**
- Dark first, two schemes: warm graphite with a deeper bar; cool white in light with the same tonal logic.
- One accent, cobalt, in two shades of one hue: `cobalt` for lines, focus, selection and the live chip; `cobalt-deep` for the filled primary action and switches.
- Six series hues used only as data colour; green, amber and red only on state, always beside a word.
- One system sans, five sizes on a 15 px root, sentence case everywhere, tabular numerals on the whole page.
- Flat surfaces, 1 px hairlines, 10 px surface radius, 7 px control radius, a single shadow on the toast.
- One authored motion: the signal path sweeps on (480 ms, staggered) only when the output comes up, never on load.

## Colors

A warm graphite room lit by one cobalt lamp; everything else with a hue is a speaker or a state. Values are the OKLCH strings declared on `:root`. The dark scheme is canonical; each `-light` sibling is the `@media (prefers-color-scheme: light)` override of the same custom property, so one token name maps to one CSS variable in both schemes (`ground` and `ground-light` are both `--bg`). `on-cobalt` has no light sibling because the build does not override it.

### Primary
- **Cobalt** (`--accent`, oklch(62% 0.17 255); light oklch(50% 0.2 258)): the one accent, used at hairline weight. The lit signal path, trunk and links; the live engine chip's border and an `on` path node's border; the focus ring; text selection at 35 %; the caret; links; the slider colour when no series hue is bound.
- **Deep cobalt** (`--accent-strong`, oklch(52% 0.19 258), hover oklch(57% 0.19 258); light oklch(50% 0.2 258), hover oklch(45% 0.2 258)): the filled primary button and the checked switch. The same hue stepped down so white text holds 4.5:1 in the dark scheme; in light both shades collapse to one value.
- **On cobalt** (`--on-accent`, #fff): text on a deep-cobalt fill and the checked switch's knob, in both schemes.

### Neutral
- **Warm graphite ground** (`--bg`, oklch(19% 0.012 60); light cool white oklch(97.5% 0.004 250)): the page, the stepper readout cell, the trim chip fill.
- **Deep graphite bar** (`--bar`, oklch(15% 0.014 60); light oklch(92.5% 0.012 250)): the sticky top bar only, one tonal step below the ground.
- **Raised surface** (`--surface`, oklch(23% 0.012 60); light white oklch(100% 0 0)): cards, path nodes, the status strip, the log, the toast, buttons, inputs and stepper cells, one step above the ground.
- **Lifted surface** (`--surface-2`, oklch(28% 0.012 60); light oklch(95% 0.005 250)): hover and pressed fills, the switch track at rest, skeleton bars.
- **Hairline** (`--line`, oklch(33% 0.012 60); light oklch(88% 0.008 250)): every 1 px separator; card, node, chip and panel borders; stepper dividers; the unlit signal line.
- **Control edge** (`--line-strong`, oklch(42% 0.012 60); light oklch(78% 0.01 250)): borders of buttons, inputs, the stepper group, the switch and the toast; the ruler's base line, zero line and ticks; the spinner track.
- **Ink** (`--fg`, oklch(95% 0.008 80); light oklch(18% 0.01 250)): headings, names, values, button labels, live chip text.
- **Muted ink** (`--fg-2`, oklch(73% 0.012 70); light oklch(45% 0.012 250)): secondary copy, fact keys, control labels, hints, chips, the log, quiet buttons at rest, the switch knob at rest, the hover border of a plain button.

### State
- **Signal green** (`--ok`, oklch(76% 0.16 150); light oklch(46% 0.15 150)): connected, output up, microphone pinned, a passing doctor check; the brand dot when the output is up.
- **Amber** (`--warn`, oklch(80% 0.14 75); light oklch(47% 0.13 70)): headset mode, muted, microphone not pinned right now; the amber border on a headset-mode path node; the stale-version banner, mixed 22 % into the ground.
- **Fault red** (`--bad`, oklch(72% 0.18 25); light oklch(48% 0.19 25)): not connected, not paired, a speaker set as system input, a failed check; the Remove and Quit labels; the brand dot when the output is down; the offline banner, mixed 22 % into the ground.

### Series
Six data hues assigned by speaker index (`--s1` to `--s6`) and bound to a card, branch or lane as `--c`. Dark: magenta oklch(74% 0.16 335), teal oklch(74% 0.12 195), lime oklch(78% 0.15 110), violet oklch(74% 0.14 300), orange oklch(76% 0.15 50), rose oklch(74% 0.15 0); light: the same hues at 52–58 % lightness. A speaker's hue appears in exactly five places: the dot by its name, its volume slider, its branch of the signal path, the border of its trim chip once a trim is set, and its span and marks on the arrival ruler.

### Named Rules
**The One Accent Rule.** Cobalt is the only hue that means Soundscape: primary action, selection, focus, caret and the live path. It comes in two shades of one hue and never a second hue; filled controls take `cobalt-deep`, everything drawn as a line takes `cobalt`.

**The Data Colour Rule.** The six series hues colour data and nothing else: a speaker's dot, slider, branch, set trim chip and ruler marks. They never colour a button, a heading, a card border or a surface.

**The State-Only Rule.** Green, amber and red appear only to state a condition, on text, a pip, a border or a doctor icon, and always next to the word for it (connected, headset mode, not connected). They are never decoration and never the only carrier.

## Typography

**Display Font:** none. The system has no display tier; the largest text is the 18 px section headline.
**Body Font:** the platform system sans (`-apple-system, BlinkMacSystemFont, system-ui, "Segoe UI", Roboto, sans-serif`), no external fonts.
**Label/Mono Font:** `ui-monospace, SFMono-Regular, Menlo, monospace`, for the activity log and inline `code`/`kbd` only.

**Character:** Quiet and exact. One face, no display tier, sentence case everywhere, weight doing the hierarchy work (600 for headings and names, 500 for controls, 400 for copy) with a −0.01em tuck on headings. The whole page sets `font-variant-numeric: tabular-nums`, so a trim stepping from 0.0 ms to 12.0 ms never shifts its neighbours.

### Hierarchy
Root size is 15 px (`html{font-size:15px}`); the ramp is 12 / 13 / 15 / 18 px with one off-ramp size, the 16 px brand name.
- **Headline** (600, 1.2rem = 18 px, 1.45, −0.01em, `text-wrap: balance`): section heads (Signal path, Speakers, Sync, Settings, Doctor, Activity), set on a baseline hairline with right-aligned meta text.
- **Brand** (600, 1.0667rem = 16 px, −0.01em): the product name in the bar; deliberately below the section headlines so the sections outrank the brand.
- **Title** (600, 1rem = 15 px, −0.01em): speaker names, found-device names and path node titles.
- **Body** (400, 1rem = 15 px, 1.45): descriptive copy; setting labels take 500 at this size.
- **Small** (400, 0.8667rem = 13 px, 1.45): the working size of the page: fact rows, lead paragraphs (max 68ch), control labels and readouts, node subtitles, hints, ruler lane names, doctor checks, banners, the toast body. Emphasis inside small is weight 600 (fact values, toast title).
- **Control** (500, 0.8667rem = 13 px, 1.3): every button label.
- **Label** (400, 0.8rem = 12 px, 1.4): chips, trim chips, status pips, the sink line under a name, ruler axis labels, lane values and the legend.
- **Mono** (400, 0.8rem = 12 px, 1.55): the activity log; inline code at 0.9em of its context.

### Named Rules
**The Tabular Rule.** Every number on the page is set in tabular figures and keeps its unit in the same run of text (12.0 ms, 48 kHz, 100%). Numbers that move never move their neighbours.

**The Sentence Case Rule.** Nothing is uppercased or letter-spaced. There are no eyebrows or kickers; a heading carries its own weight, and a label is a muted-ink word at 12 or 13 px beside its value.

## Layout

A single centered column of sections, 1240 px wide at most, with 24 px side gutters (16 px at 640 px and below) and 72 px of ground beneath the last section. Below 960 px the sections stack in one column; from 960 px `main` is a 7:5 grid (`minmax(0,7fr) minmax(0,5fr)`, 28 px column gap) where the status strip, the signal path and the activity log span both columns and two `.col` stacks hold Speakers, Add a speaker and Doctor on the left, Sync and Settings on the right. Sections are 30 px apart (26 px at 640 px and below).

The top bar is sticky (z-index 5) with a bottom hairline and no shadow; it wraps at 900 px and below and becomes static and wrapping at 640 px and below, where the actions drop to their own full-width row and each button flexes to share it. Banners for a stale version and for offline sit above the bar as full-width strips.

Each section opens with a `sec-head`: heading and meta on one baseline, 12 px apart, 9 px of padding over a 1 px hairline, 14 px above the content. Meta is small muted ink or a single button, pushed right. Rows inside a section (settings, doctor checks) carry 8–10 px of vertical padding and are divided by hairlines, never boxed.

Inner grids:
- Status strip: facts in one column, two columns from 600 px (5 px row gap, 24 px column gap); the master control is a four-column grid (`auto minmax(140px,220px) 52px auto`) that takes the full strip width at 640 px and below.
- Signal path: stacked at narrow widths (2 px vertical links 22 px tall, the fan indented 27 px); from 760 px a five-column grid `minmax(140px,1fr) 40px minmax(170px,1.25fr) 40px minmax(240px,2.3fr)` with horizontal 2 px links. Each branch is `auto 18px minmax(0,1fr)` (trim chip, link stub, node) at 52 px minimum height, 10 px apart.
- Speaker cards: `repeat(auto-fill, minmax(270px, 1fr))`, 14 px gap; found devices `minmax(250px, 1fr)`, 12 px gap.
- Card control rows: `52px minmax(0,1fr) 46px` (label, slider, readout); the delay row `52px 1fr`.
- Arrival ruler: lanes `168px minmax(0,1fr)` with 12 px gaps: a 168 px column holding the series dot, the name and the 12 px value beneath it, then the track; at 560 px and below one column, the track under the name.

Spacing rhythm as used: 6 / 8 / 10 / 12 / 14 / 16 / 18 / 24 / 30 / 72 px. Inline gaps are 6–10 px, control padding 6 × 12 px, card padding 12 × 14 px, section spacing 30 px. Explanatory copy runs to 68ch at most.

## Elevation & Depth

The system is flat and tonal. Depth is a ladder of four graphite steps, bar (15 %) below ground (19 %) below surface (23 %) below lifted surface (28 %), with 1 px hairlines doing the separating and control edges one step stronger. Nothing at rest casts a shadow: not cards, not path nodes, not the sticky bar, not menus. State is carried by a border colour change (a node turning cobalt when audio can flow, amber in headset mode; a chip border going cobalt when live; a trim chip taking its series hue when set) over the 200 ms ease. The single exception is the toast, which floats over the page and carries the one shadow.

### Shadow Vocabulary
- **Toast** (`box-shadow: 0 10px 28px -10px oklch(0% 0 0 / .6), 0 2px 6px oklch(0% 0 0 / .25)`; light `0 10px 28px -10px oklch(0% 0 0 / .28), 0 2px 6px oklch(0% 0 0 / .12)`): the fixed notification only. A soft ambient drop plus a tight contact shadow, held in `--shadow`.

### Named Rules
**The One Shadow Rule.** The toast is the only element that casts a shadow. Everything else is separated by a 1 px hairline or a tonal step.

**The Border Carries State Rule.** A change of state changes a border colour or an ink colour, never a shadow, a weight or a fill, so a lit node and a resting node are the same shape in the same place.

## Shapes

Two radii and a pill. Surfaces (cards, path nodes, the status strip, the log, the toast) use 10 px (`--r`); controls (buttons, inputs, selects, the stepper group, the brand mark) use 7 px (`--r-s`); chips and trim chips are full pills (999px), the switch a 38 × 22 px pill with a 16 px round knob that travels 16 px. Dots are circles: 9 px for the brand dot, 8 px for a speaker's series dot, 7 px for the status pip, 10 px for the measured mark and the legend swatch, 16 px with a 2 px ring for the after-delay mark.

Lines are 1 px for every hairline, separator and the ruler's base, zero and tick marks; 2 px only for signal lines (links, trunk and branch stubs), the after-delay ring, the spinner and the focus outline. Skeleton cards are the same card with a dashed hairline and no fill. Icons are inline SVG at 16 px, stroked at 2–2.2 px with round caps (check, warning triangle, failed circle, close); the select chevron is a 12 px stroked SVG. The brand mark is a 26 px flat-fill tile at 7 px radius. There are no coloured side borders, no clipped or angled corners, and the only gradient is the hard two-stop edge that sweeps a signal line from hairline to colour.

## Components

Every control is a flat, hairline-edged shape in one system sans at 13 px; what changes between states is colour, not geometry.

### Buttons
- **Shape:** control radius (7px), 6px 12px padding, 13 px / 500 label at 1.3 line height; inline icons 16 px.
- **Default:** raised surface fill with a control-edge border and ink text; hover lifts the fill to `surface-raised` and the border to muted ink; active mixes 70 % lifted surface into the ground; disabled drops to 45 % opacity; `aria-busy` shows a progress cursor; `aria-pressed="true"` (Mute / Unmute) takes the lifted fill with an ink border.
- **Primary:** deep cobalt fill and border with white text (Bring up / Rebuild, Calibrate with microphone, Add); hover steps to `cobalt-deep-hover`. One primary per region.
- **Quiet:** transparent fill and border, muted ink; hover fills with lifted surface and brightens to ink (the toast close).
- **Danger:** a plain or quiet button whose text is fault red; hover draws the border in red (Remove, Quit).
- **Focus:** 2 px cobalt outline offset 2 px, shared by every focusable element.
- **Motion:** background, border and colour over 200 ms `cubic-bezier(.2,.7,.2,1)`.

### Chips
- **Style:** pill, 12 px label text in muted ink, hairline border, 2px 9px padding, 6 px gap for inner content, never wrapping (version, platform, engine, a speaker's position).
- **Live:** ink text with a cobalt border when the output is up (the engine chip).
- **Trim chip:** a smaller pill (1px 8px) filled with the ground colour that rides each path branch and reads `+12.0 ms`; once a trim is set its text turns ink and its border takes the speaker's series hue.
- **Status pip:** 12 px text preceded by a 7 px dot, both in the state colour (connected in green, headset mode or muted in amber, not connected or not paired in red), pushed to the right of a card head.

### Cards / Containers
- **Corner Style:** surface radius (10px).
- **Background:** raised surface on a hairline border; skeleton cards are dashed with no fill and a 190 px minimum height.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Internal Padding:** 12px 14px, with a 10 px grid gap between rows.
- **Speaker card:** head row (8 px series dot, name at title size, position chip, status pip right); sink line at 12 px muted ink; a headset alert row with a top hairline, amber warning icon, plain text and a Reconnect button pushed right; a volume row (label, series-coloured slider, right-aligned tabular readout); a delay row with the segmented stepper; a button row (Mute, Tone, and a quiet danger Remove pushed right). A card's series hue is set once as `--c` on the article and inherited by everything inside.
- **Status strip:** the same surface at 12px 16px padding, laid out as a wrapping flex row: a two-column fact grid (muted key, 600 value, value coloured by state) and the master control grid with its no-limiting note.
- **Log:** the same surface holding 12 px mono muted ink, 10px 12px padding, 280 px max height with thin scrollbars.

### Inputs / Fields
- **Text, number, select:** raised surface fill, control-edge border, 7 px radius, 6px 9px padding, inheriting the page font; selects drop the native arrow for a 12 px stroked chevron 9 px from the right edge with 28 px of right padding.
- **Range:** full width, 22 px tall, native track with `accent-color` bound to the row's `--c` (a speaker's series hue, falling back to cobalt for the master slider).
- **Switch:** a 38 × 22 px pill, lifted-surface track with a control-edge border and a 16 px muted-ink knob; checked fills the track and border with deep cobalt and slides a white knob 16 px right, both over 200 ms. Marked `role="switch"`.
- **Segmented stepper (Delay):** a control-edge group at 7 px radius with hidden overflow: borderless 4px 9px buttons (−5, −1, +1, +5) on raised surface, 1 px hairline dividers, and a readout cell on the ground colour, 84 px minimum, 13 px tabular, between the pairs.
- **Focus:** the global 2 px cobalt outline.
- **Error / Disabled:** no error styling exists; disabled follows the button rule (45 % opacity).

### Navigation
There is no navigation; the page is one scroll. The top bar is the frame: deep graphite fill, bottom hairline, 10px 24px padding, 14 px gaps, sticky with no shadow. Left, the brand group at 10 px gaps (26 px mark, 9 px state dot that is red when down and green when up, 16 px / 600 name, version chip); centre, status chips (engine, platform); right, the actions (primary Bring up / Rebuild, Take down, Test tones, a quiet danger Quit when the server allows it). At 640 px and below it goes static and wraps, with the actions on their own full-width row. The mark is the brand asset, a flat-fill rendition of `assets/soundscape.svg`; its own colours (navy tile, paper speakers, mint waves) are not system tokens.

### Signal path (signature)
A left-to-right chain of nodes: Your apps, Soundscape, then a fan of one branch per speaker. Nodes are 10 px surfaces with a hairline, 9px 12px padding, a 600 title and a 13 px muted subtitle; speaker nodes keep both single-line with ellipsis while the two trunk nodes let the subtitle wrap. A node whose audio can flow takes a cobalt border, a headset-mode node an amber border. Links, trunk and branch stubs are 2 px hairline-coloured lines at rest; a lit line carries a two-stop gradient (cobalt for the main links and trunk, the speaker's series hue for its branch) whose position slides from hairline to colour over 480 ms with an 80 / 160 / 240 ms stagger from trunk to branch stub to branch link. The sweep runs only when the output flips from down to up; on a page that loads already up the lines are simply on, and under `prefers-reduced-motion` they switch without transition. Each branch carries its trim chip before the link.

### Arrival ruler (signature)
Under Sync: an 18 px axis row with 1 × 5 px control-edge ticks and 12 px labels at 0 ms and at the spread, a label within 20 % of either edge pinned inside the column; then one lane per speaker: a 168 px column with the series dot, the 13 px name and a 12 px muted value beneath it (`+0.0 → +12.0 ms`, or `+12.0 ms · unchanged`), and a 26 px track. The track draws a 1 px base line and a 1 px zero line in control edge, a 2 px span at 60 % opacity in the series hue from measured arrival to position after delay, a 10 px filled mark for the measured arrival and a 16 px mark with a 2 px ring for the position after this pass's delay. A 12 px legend explains the two marks; a small line beneath states the time, confidence and spread. Each track is `role="img"` with the full sentence as its label.

### Settings rows
Flex rows at 10px 0 with a hairline between them: a 500-weight label with a 13 px muted hint beneath on the left, the control (a select at most 46 % wide, or a switch) on the right.

### Doctor checks
`18px minmax(0,1fr)` rows at 8px 0 with hairlines: a 16 px stroked icon coloured by status (green check, amber triangle, red circle-cross) and 13 px text with an optional muted detail and a `fix:` line in muted ink.

### Banners
Full-width 13 px strips at 8px 24px (16 px sides on mobile) with a bottom hairline, filled with 22 % of the state colour mixed into the ground: amber for the stale-version notice, red for offline.

### Toast
Fixed 18 px from the bottom-right (full width with 16 px insets at 560 px and below), 400 px at most: a 10 px surface with a control-edge border, 11px 14px padding, the one shadow, and a three-column grid (optional 12 px spinner, 13 px / 600 title, quiet close button) with a muted 13 px body beneath that scrolls past 120 px. It enters by fading in and rising 8 px over 180 ms and leaves the same way after 6 s, or stays while a job spins. The spinner is a 12 px ring, 2 px control edge with a cobalt top, turning in 0.8 s (1.5 s under reduced motion).

### Empty states
13 px muted-ink text in place of the content (not a boxed placeholder), with an ordered list of steps where there is a next action.

## Do's and Don'ts

### Do:
- **Do** set every number in tabular figures and keep its unit in the same run of text (`12.0 ms`, `48 kHz`, `100%`).
- **Do** carry a state change as a border or ink colour over the 200 ms ease: cobalt when live, amber for headset mode, red for a fault.
- **Do** bind a speaker's series hue once as `--c` on its card, branch and lane so its dot, slider, link, trim chip and ruler marks agree.
- **Do** separate rows and sections with 1 px hairlines and lead each region with its live state before its controls.
- **Do** write in sentence case with plain words: 18 px / 600 section heads, 13 px muted-ink labels, one primary button per region.
- **Do** pair every colour-coded state with its word (connected, headset mode, not connected) so colour is never the only carrier.
- **Do** keep one column below 960 px, stack the signal path below 760 px and drop the ruler track under its label at 560 px and below.

### Don't:
- **Don't** nest a card inside a panel or box a section; a section head is a heading on a baseline hairline.
- **Don't** add a sidebar or a grid of identical metric cards; the page leads with the signal path and the ruler.
- **Don't** use the series hues on buttons, headings, card borders or surfaces, or cobalt on data.
- **Don't** put a shadow on cards, nodes, the bar or menus; the toast carries the only shadow.
- **Don't** introduce a second accent hue; the two cobalt shades are the whole accent.
- **Don't** uppercase or letter-space a label, and don't put an eyebrow or kicker above a heading.
- **Don't** paint gradients on surfaces or text; the only gradient is the hard two-stop edge that sweeps a signal line on.
- **Don't** animate on page load; the signal path sweeps only when the output comes up, and not under reduced motion.

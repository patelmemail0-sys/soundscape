# Changelog

## 0.5.2 - 2026-10-31

* App icon: two speakers with their sound waves meeting in the middle. Shipped as
  `assets/Soundscape.icns` for the macOS app, a PNG for the Linux launcher, and used as the
  dashboard's favicon and header mark.

## 0.5.1 - 2026-10-31

* The Soundscape app no longer hangs as "not responding": it runs `soundscape start --detach`,
  which starts the service in the background (new session, log in ~/Library/Logs) and returns
  once the dashboard answers; opening the app again just reopens the dashboard.
* `soundscape start` serves the dashboard immediately and brings the speakers up as the first
  background job, so the page opens within a second and shows connection progress.

## 0.5.0 - 2026-10-31

One-click everyday use.

* `soundscape start`: joins the speakers, pins the microphone, opens the dashboard and keeps
  a watchdog running (reconnects, rebuilds, re-pins); Quit in the dashboard restores normal
  output. The installer creates Soundscape.app (macOS) or a launcher entry (Linux).
* Microphone pinning: the system input stays on the built-in mic (configurable), enforced on
  `up`, by the daemon, and every 1.5 s inside the relay process; status reports it.
* Every speaker always gets the full mix; stereo split is an advanced command-line flag only
  and is announced loudly when on.

## 0.4.5 - 2026-10-30

Dashboard reliability.

* Every control reports its outcome (delay set, muted, saved, busy, errors) instead of
  failing silently; page errors surface as a toast.
* Quick controls no longer queue behind long jobs; they wait a few seconds then report busy.
  Status polling never blocks.
* Speaker cards use event delegation, fixing broken buttons for speaker names with an
  apostrophe (e.g. "Yogi's JBL Flip 6").
* A banner tells you to restart `soundscape ui` when the server process is older than the
  installed code; the engine setting shows a hint when aggregate mode blocks sync trims.

## 0.4.4 - 2026-10-30

* A speaker stuck in Bluetooth headset mode (mono, 16 kHz, no bass, out of sync) is
  reconnected automatically during `up`, which resets it to music (A2DP) mode.
* `soundscape reconnect <speaker>`, `/api/reconnect`, and a Reconnect button on speaker cards
  that show headset mode; the signal path marks the speaker.

## 0.4.3 - 2026-10-30

The "only one speaker plays" fix.

* The aggregate engine now creates a *stacked* aggregate (a Multi-Output Device). A plain
  aggregate concatenates channels, so stereo apps were heard only on the first speaker.
* Speakers (and BlackHole, for the relay) are aligned to one sample rate before joining; a
  sub-device that cannot follow the aggregate's rate would otherwise stay silent.
* Calibration mutes speakers inside the relay (instant, sample-accurate) instead of sending
  Bluetooth volume commands, which speakers apply slowly and unpredictably.
* `up` explains when the engine setting forces 'aggregate' although BlackHole is installed.

## 0.4.2 - 2026-10-30

Fixes for "only one speaker plays" on macOS.

* Headset-mode detection: a Bluetooth speaker running at 8/16 kHz (hands-free profile) is
  flagged in `status` and the dashboard; its music output is silent or degraded in that state.
* `up` moves the Mac's default microphone off any speaker's own mic (the usual trigger for
  headset mode) and waits up to 6 s for affected speakers to return to music mode.
* Stale public "Soundscape (both speakers)" aggregate devices are removed on every `up`.
* Relay: output buffers are mapped from the aggregate's *active* sub-device order, speakers
  that did not make it into the aggregate are reported, the clock master is a music-mode
  speaker, and `status` shows the live buffer map and frames written per speaker.

## 0.4.1 - 2026-10-30

First real-hardware findings (HK Onyx Studio 8 + JBL Flip 6 on a MacBook).

* Calibration picks the computer's built-in microphone and refuses a speaker's own
  microphone: macOS makes a newly connected speaker's mic the default input, and recording
  through it switches that speaker to headset mode, which produced a bogus 121 ms trim.
* Measurements with an implausible spread (> 300 ms) are rejected instead of applied.
* `soundscape offset reset` and a Reset trims button in the dashboard.
* In relay mode the Master slider is the Mac's volume (BlackHole), so speakers can reach
  full level; the UI says so and `up` warns when it is below 100%.
* Microphone list in the dashboard marks Bluetooth and speaker microphones.
* No limiting: `up` runs the chain at unity gain, setting every untrimmed speaker to its own
  100% and (relay mode) BlackHole to 100%, so each speaker can reach its physical maximum.

## 0.4.0 - 2026-10-30

Web dashboard.

* `soundscape ui` serves a single-page dashboard and opens it in the browser: live signal
  path diagram, per-speaker cards (state, volume, mute, tone, delay stepper), one-click
  microphone calibration with a lag chart, scan/add speakers, settings, doctor, activity log.
* Long operations run as background jobs with progress; quick controls apply inline.
* JSON API under `/api/` (status, devices, sources, doctor, log, job, up, down, scan, add,
  remove, sync, test, config, volume, mute, offset, media); optional token.
* `Config.set_option` shared by CLI and UI; microphone source listing on both backends.

## 0.3.1 - 2026-10-30

Hardening after an adversarial review, parser fuzzing and live fault injection.

* macOS relay: `--output` specs with the colon that device UIDs contain are parsed correctly
  (the relay died on start-up before); the loopback's own output stream is silenced instead of
  receiving speaker audio (feedback); control protocol accepts UIDs with spaces.
* macOS: process checks no longer rely on `/proc` (which macOS lacks), so `status`, the daemon
  and stray-relay cleanup work; mute emulation keeps volume levels through calibration;
  aggregate sub-device lookup uses the id list; cheaper device lookups.
* Calibration playback goes through the audio backend (afplay on macOS).
* Health check: when a speaker drops and comes back on its own, the daemon notices the missing
  delay filter or re-created sink and rebuilds; verified against live PipeWire.
* Positions with hyphens produce matching PipeWire node names; test tones never collide.
* Parsers tolerate truncated or malformed tool output; temp recordings are cleaned up;
  offsets are range-checked; one lifecycle command at a time (lock); friendlier CLI errors.
* State files from other versions load safely.

## 0.3.0 - 2026-10-30

Any number of speakers.

* Positions are free-form names (`left`, `right`, `kitchen`, ...); `add` defaults to
  `speaker3`, `speaker4`, ... and the setup wizard asks for more speakers after the first two.
* `sync --auto` calibrates N speakers in one pass (one chirp per speaker) and delays everyone
  to the slowest; by-ear mode cycles through speakers.
* Test tones rise in pitch by position order; stereo split applies to `left`/`right` and
  gives every other speaker full stereo.
* Engines unchanged structurally: PipeWire, PulseAudio, aggregate and relay all fan out to N.

## 0.2.0 - 2026-10-30

macOS support.

* `soundscape` now runs on macOS: Bluetooth through `blueutil` (or read-only via
  `system_profiler`), audio through a ctypes CoreAudio binding.
* Aggregate engine: CoreAudio aggregate device of both speakers, drift-corrected, as default
  output. Nothing to install.
* Relay engine (with BlackHole): sample-accurate per-speaker delay, live adjustable, plus
  stereo split; `sync --auto` can apply what it measures on a Mac too.
* Microphone recording and test tones on macOS (HAL IOProc capture, `afplay`).
* LaunchAgent for autostart; `install.sh` handles both platforms.
* README: iPhone users AirPlay to the Mac and let Soundscape drive the speakers.
* Calibrator made backend-agnostic; recordings at any rate are decimated to 16 kHz.

## 0.1.0 - 2026-10-30

First release.

* Guided `setup`: doctor checks, scan, pick left/right, pair, trust, connect, join, test tones,
  optional microphone alignment.
* PipeWire engine: second `pipewire` process hosting a `combine-stream` sink (drift
  correction, resampler quality 10, latency compensation) and a sample-accurate `delay`
  filter per speaker, adjustable live. Optional stereo split across the two speakers.
* PulseAudio engine: `module-combine-sink` with the best available resampler; trims via
  port latency offsets.
* `sync --auto`: microphone calibration by chirp cross-correlation; `sync`: by ear with a
  click track; `offset`: direct trims. All remembered.
* `daemon` + systemd user unit: reconnect dropped speakers, rebuild, clean shutdown.
* `remote`: phone web page for volume, mute, sync nudges and media keys.
* `play`, `status --json`, `volume`, `mute`, `test`, `config`, `doctor`.
* 105 unit tests against an emulated BlueZ + sound server; live PipeWire integration test.

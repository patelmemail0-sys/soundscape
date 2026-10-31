# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person at home who owns several Bluetooth speakers and wants music from a single
device to play through all of them at once. Confirmed from the brief: they use an iPhone
or a MacBook, and the MacBook runs Soundscape. Confirmed hardware in their setup: a Harman
Kardon Onyx Studio 8, a JBL Flip 6, a Bose S1 Pro and AirPods.

Inferred (not confirmed by the user): the session is short and happens beside the
speakers, laptop open or phone in hand; they come to the dashboard to bring the speakers
up, check that every one is connected and in sync, set volumes, and leave the music playing.

## Product Purpose

Soundscape joins two or more Bluetooth speakers into one output on a Mac (or a Linux
machine) so every app plays through all of them simultaneously. Success, in the user's
words: the audio quality and EQ are untouched, there is no distortion, lag or latency
between speakers, and playback is completely synchronized. Every speaker can be driven to
its own physical limit; Soundscape never caps a volume.

## Positioning

Deterministic sample-accurate alignment on hardware the user already owns. Soundscape
measures each speaker's Bluetooth latency with the laptop microphone (one chirp per
speaker), then delays everyone to the slowest speaker with sample-exact delay lines inside
its own mixer, while keeping the speakers in music (A2DP) mode by pinning the system
microphone to the built-in one. Nothing in the chain resamples the mix down, splits
channels, or limits level.

## Operating Context

* macOS is the primary platform (MacBook Air; CoreAudio aggregate or the relay engine with
  BlackHole 2ch). Linux runs on PipeWire or PulseAudio. The iPhone cannot drive two
  speakers itself; the documented flow is AirPlay from the iPhone to the Mac.
* Everyday use is the Soundscape app (one click) or `soundscape start`, which starts a
  background service and opens the dashboard in the default browser. The dashboard is also
  reachable from a phone on the same network with a token.
* The dashboard shows and controls: bring up and take down, the live signal path, one card
  per speaker (connection, mode, volume, mute, test tone, delay trim, reconnect, remove),
  master volume, microphone calibration with measured lags, scan and add speakers,
  settings (engine, microphone pinning, latency compensation, default output), the doctor
  checks with fixes, the activity log, and Quit.
* Long operations (scan, add, calibrate, up, down) run as background jobs; the page polls
  status every 2.5 s and the log every 5 s.

## Capabilities and Constraints

* Any number of speakers (two or more to bring up). Confirmed rule: every speaker receives
  the full mix; left/right splitting is an advanced command-line flag the dashboard never
  exposes.
* Confirmed rule: only the built-in MacBook microphone may be the system default input, so
  no speaker drops into headset mode (mono, 16 kHz, no bass, out of sync). Speakers caught
  in headset mode are reconnected.
* Confirmed rule: no volume limiting anywhere; gains are unity and each speaker's own
  slider goes to its hardware maximum.
* The page is a single static HTML file served by a Python standard-library server with no
  build step; it must work in Safari and Chromium on macOS and on a phone browser.
* Terminology: "bring up" / "take down" (join or release the speakers), "engine"
  (aggregate, relay, pipewire, pulse, auto), "trim" or "delay" (per-speaker extra delay in
  ms), "calibrate" (microphone measurement), "headset mode" (HFP), "pin the microphone".
* Undecided: whether the dashboard should ever expose stereo split (currently no).

## Brand Commitments

* Name: Soundscape. Icon: two speakers whose sound waves meet in the middle
  (`assets/soundscape.svg`, `assets/Soundscape.icns`, `assets/soundscape-512.png`).
* Voice, confirmed by the user's own preference: direct, no filler, plain words.

## Evidence on Hand

* Real device names and addresses from the user's setup appear in the logs and screenshots
  they shared (Onyx Studio 8, "Yogi's JBL Flip 6", Bose S1 Pro). They are usable as
  realistic demonstration data when labeled as such.
* No testimonials, customers, benchmarks, pricing or press exist. Do not invent any.

## Product Principles

1. A wrong state shown confidently is worse than no state: connection, mode and sync are
   always visible and never implied.
2. Nothing between the app and the speaker degrades the signal: no limiting, no channel
   splitting, no silent downsampling, and the interface never suggests otherwise.
3. One click to music: the common path (open, bring up, play) needs no reading.
4. Explain the fix, not just the failure: every error and check carries what to do next.
5. The dashboard is a control surface, not a brand page: familiar controls, dense but calm.

## Accessibility & Inclusion

No product-specific requirement was established. Inferred baseline for a web control
surface: keyboard-operable controls, visible focus, labels on every control, color never
the only carrier of state, reduced-motion respected.

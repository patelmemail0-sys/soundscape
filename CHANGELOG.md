# Changelog

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

# Changelog

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

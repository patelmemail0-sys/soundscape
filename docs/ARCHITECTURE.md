# Architecture

Soundscape is a thin, well-tested orchestration layer over three things Linux already has:
BlueZ (Bluetooth), the sound server (PipeWire or PulseAudio), and its command-line tools.
It owns no audio path of its own; that is deliberate. The sound server already does
float32 mixing, per-device clock recovery and Bluetooth encoding better than a Python
process could, and keeping the audio out of Python is what makes "no EQ, no distortion,
no added processing" a structural guarantee instead of a promise.

```
 cli.py ──► session.py ──┬──► bluez.py      (busctl --json, bluetoothctl)   pair / trust / connect
                         ├──► pulse.py      (pactl)                         sinks, cards, volume, default
                         └──► engine.py ──┬► PipeWireEngine ─► pipewire.py  config render, pw-dump, pw-cli
                                          └► PulseEngine    ─► pulse.py     module-combine-sink
 sync.py  (calibration)  ─► tones.py (WAV) + dsp.py (FFT cross-correlation)
 remote.py (web remote)  ─► session.py
 doctor.py (checks)      ─► bluez.py / pulse.py / pipewire.py
 runner.py               every subprocess goes through here; tests swap in a fake
```

## Layers

### `runner.Runner`
One object runs every external command (`run`, `popen`, `which`). The test-suite replaces it
with a `FakeRunner` plus a stateful `World` that emulates bluetoothd and a sound server, so
the real `Session`, both engines, the calibrator and the CLI run end to end without hardware.

### `bluez.BlueZ`
* **Queries and actions** use `busctl --system --json=short` against `org.bluez`:
  `GetManagedObjects` for state, `Pair`/`Connect`/`Disconnect` on `org.bluez.Device1`,
  `set-property Trusted`/`Powered`. busctl ships with systemd, so there is no Python D-Bus
  dependency. Variant wrappers (`{"type": ..., "data": ...}`) are stripped recursively.
* **Discovery** must outlive a single call (BlueZ stops scanning when the requesting client
  disconnects), so `scan()` keeps a `bluetoothctl` process alive on a pipe for N seconds and
  then reads devices via busctl.
* BlueZ error strings (`br-connection-profile-unavailable`, `Authentication Failed`, ...)
  are mapped to plain-English hints in `ERROR_HINTS`.
* `ensure_connected()` is idempotent: pair if unpaired, trust, connect with retries, verify
  `Connected`.

### `pulse.PulseCtl`
A `pactl` wrapper that prefers `pactl -f json` (pactl >= 16) and falls back to parsing the
text output on older systems. It identifies a speaker's sink by Bluetooth address from
either `api.bluez5.address` (PipeWire), `device.string` (PulseAudio) or the sink name
(`bluez_output.AA_BB_...` / `bluez_sink.AA_BB_...`). pactl works against pipewire-pulse too,
which is why volume, mute, default-sink and stream moves are the same on both servers.

### `engine`
An engine turns N connected Bluetooth sinks into one virtual sink and owns its lifetime.
`EngineState` (JSON in `$XDG_STATE_HOME/soundscape/state.json`) records what was created so
`down` can undo it even after a crash, and stray engines are swept before each `up`.

**PipeWireEngine** (default when the server is PipeWire and `pipewire`, `pw-dump`, `pw-cli`
exist). It writes a config and starts a *second* `pipewire` process with
`PIPEWIRE_CONFIG_DIR` pointing at it. That process connects to the user's daemon as a client
(no `core.daemon`, so it never competes for the socket) and hosts:

* one `libpipewire-module-filter-chain` per speaker whose graph is a single builtin `delay`
  node (`max-delay` 1 s). Its capture side is an `Audio/Sink` named
  `soundscape_<position>_delay`; its playback side targets the speaker's sink with
  `node.dont-reconnect = true` so audio can never silently fall back to the laptop speakers.
  The control `delay:Delay (s)` is changed live with `pw-cli set-param <id> Props`.
* one `libpipewire-module-combine-stream` sink named `soundscape` with
  `combine.latency-compensate = true` and `stream.props = { resample.quality = 10 }`, whose
  `stream.rules` target the delay sinks. With `--stereo` each rule adds
  `combine.audio.position = [ FL FL ]` / `[ FR FR ]` so one speaker gets the left channel
  on both drivers and the other the right.

If the config with delay filters fails to produce the sink (very old PipeWire), the engine
retries without them and marks `with_delays = false`; manual trims then report a clear error.

The generated config was validated with `spa-json-dump` and run against PipeWire 1.0.5 and
WirePlumber with null sinks standing in for speakers (`tests/live/`): the sink appears, the
links form as drawn above, the live delay change is visible in the node's `Props`, playback
flows, and teardown restores the previous default sink.

**PulseEngine** (PulseAudio, or PipeWire without the native tools). Loads
`module-combine-sink` via pactl with `sink_name=soundscape slaves=<a>,<b> adjust_time=5` and
the best available resampler (`soxr-vhq` → `speex-float-10` → `speex-float-5` → default).
PulseAudio has no per-output delay, so a trim of *X ms on speaker A* is expressed as a
`set-port-latency-offset` of `+X` on every *other* speaker: PulseAudio treats a port offset
as hidden downstream latency and feeds that port earlier, which is the same thing. Offsets
are normalised so the most-delayed speaker is at 0 and no lead is negative.

### macOS backend (`soundscape/mac/`)
The session code is shared; macOS swaps in objects with the same method names.

* `mac/coreaudio.py`: a ctypes binding of the CoreAudio HAL (`HAL`) and the device logic on
  top (`CoreAudio`): enumerate devices, map a Bluetooth speaker to its output device by UID
  (`AA-BB-CC-DD-EE-FF:output`), default output, volume (`vmvc`, then `volm`), mute, aggregate
  device create/destroy with per-subdevice drift compensation and a chosen clock master.
  Tests inject a fake HAL, so every method is covered without a Mac.
* `mac/bluetooth.py`: `blueutil` for actions (connect, pair, inquiry, unpair) with a
  `system_profiler -json` fallback for listing and for knowing which devices are speakers.
* `mac/audio.py` (`MacAudio`): the `PulseCtl` counterpart. `play_file` uses `afplay`, switching
  the default output temporarily when a specific device is requested; `record` captures the
  default input through a HAL IOProc.
* `mac/engine.py`: `AggregateEngine` (public aggregate of the speakers, first speaker as clock
  master, drift compensation on the others, set as default output) and `RelayEngine`
  (BlackHole becomes the default output; a relay process is spawned).
* `mac/ioproc.py`: the relay process. It creates a *private* aggregate of BlackHole + speakers,
  attaches an IOProc, and on every cycle appends the BlackHole block to a history buffer and
  copies the block `delay_k` frames back to each speaker (stereo split: one channel to both
  drivers). The maths lives in the pure-Python `Mixer`, unit-tested; the process disables the
  garbage collector while the real-time callback is attached. A Unix socket takes
  `delay <uid> <ms>`, `stereo on|off`, `status`, `ping`, `quit`.

### `session.Session`
The one place that knows the whole flow:

```
up():    normalise offsets → tear down stale state → connect & verify each speaker
         → wait for its sink → engine.build → per-speaker volume → save state + config
down():  engine.teardown → restore default sink → delete state
ensure_up(): no-op when healthy; rebuild when a speaker's sink vanished (used by the daemon)
set_offset(): update config, normalise, push every speaker's delay to the running engine
```

Config lives in `$XDG_CONFIG_HOME/soundscape/config.json` (`config.Config`): any number of
speakers, each with a position name (`left`/`right` are special for stereo split; anything
else is free-form, defaulting to `speaker3`, `speaker4`, ...), friendly name, `offset_ms` (extra delay on *that* speaker) and optional volume trim,
plus engine choice and the flags behind `up`'s options.

### `sync.Calibrator`
*Microphone mode*: generates a track with one identical chirp per speaker, 3 s apart
(1.0 s, 4.0 s, 7.0 s, ...), starts recording on the microphone, un-mutes only speaker 0,
plays the track through the Soundscape sink, moves the un-muted speaker to the next one
midway between chirps, and un-mutes everything afterwards. Every chirp travels the exact
same pipeline, so startup jitter of the player cancels out and each arrival gap minus the
nominal spacing is that speaker's lag relative to speaker 0. `dsp.locate` cross-correlates the
recording with the chirp (FFT, numpy if present, pure Python otherwise), refines each peak
parabolically for sub-sample precision, and reports a confidence (peak over window RMS) that
must exceed 4 before anything is applied. When every speaker has a distance from the
microphone recorded (`Speaker.distance_m`, metres), the sound's travel time at 343 m/s is
subtracted from each lag first, so the trims describe the speakers' own latencies rather
than where the laptop stood; otherwise the lags are used as heard and the log says so.
Every speaker is then delayed by ``max(lag) - lag`` so all of them wait for the slowest one.

*By-ear mode*: loops a click track and nudges trims live from the keyboard, cycling through
speakers with `t`; a negative nudge on one speaker is turned into a positive delay on all the
others so delays never go negative.

### `remote.RemoteApp` (the dashboard)
A stdlib `ThreadingHTTPServer` serving `ui.html` (vanilla JS, no build step) and a JSON API.
Quick controls (`volume`, `mute`, `offset`, `media`, `config`, `remove`) apply inline under
one lock; long operations (`up`, `down`, `scan`, `add`, `sync`, `test`) are queued on a
single worker thread as jobs the page polls (`/api/job?id=`), with the backend's log lines
attached to the job for live progress. The session's log callback is captured into a ring
buffer (`/api/log`). Request handling is separated from sockets so it is unit-tested
directly, and the page is driven end to end in headless Chromium against the emulated
backend during development.

The page's visual system is recorded in `DESIGN.md` (tokens in its frontmatter, prose rules
in the body) with a machine-readable sidecar at `.impeccable/design.json`; the surface brief
for the dashboard lives under `.impeccable/surfaces/`. The redesign followed the Impeccable
design skill installed at `.agent/skills/impeccable/` (playbooks under `reference/`).

## Design rules

1. **Never guess an audio path.** `node.dont-reconnect`, explicit `target.object`, and a
   state file that records exactly what was created. If a speaker is gone, say so and
   rebuild; never fall through to another device.
2. **No audio through Python.** Only control-plane commands. Audio quality is the sound
   server's, untouched.
3. **Every failure carries a fix.** `SoundscapeError.hint` is shown by the CLI, and
   `doctor` prints a concrete package or command for each problem.
4. **Idempotent everything.** `up` twice, `add` of a known speaker, `down` with nothing
   running, killing the daemon mid-way: all converge to a consistent state.
5. **Testable without hardware.** `Runner` is the only seam; the `World` fake is the test
   double for the whole OS. The live PipeWire test is the one place hardware-free reality
   is checked, in CI.

## Limits worth knowing

* Absolute Bluetooth latency (100 to 250 ms) is inherent to A2DP; Soundscape aligns the
  speakers *to each other*.
* Reported Bluetooth latency is approximate, so automatic compensation leaves a residual of
  a few to tens of ms that calibration removes. Expect 1 to 3 ms after `sync --auto`.
* Two A2DP streams share one radio. Standard SBC/AAC bitrates are fine on most adapters;
  SBC-XQ at high bitrates on both speakers may not be.
* Stereo split needs the PipeWire engine (Linux) or the relay engine (macOS).
* The macOS aggregate engine cannot trim one speaker; install BlackHole for the relay engine.
* The relay IOProc runs Python inside CoreAudio's real-time thread. It only does memmoves
  on a 1024-frame buffer, but a Swift relay is the obvious next step if glitches show up.
* The PulseAudio engine's trims rely on `set-port-latency-offset`, which pipewire-pulse
  may accept but not act on; on PipeWire systems install `pipewire-bin` to get the native
  engine instead.

# Soundscape

**One audio source, two Bluetooth speakers, in sync.**

Soundscape joins two (or more) ordinary Bluetooth speakers into a single output on a Linux
machine. Everything you play, from Spotify in a browser to a local file, comes out of both
speakers at the same time, aligned to within a few milliseconds, with no EQ or other
processing applied to the audio.

```
  Spotify / browser / mpv / anything
                 |
                 v
     +-----------------------+
     |  "Soundscape" output  |   one virtual sink, float32 mixing, no EQ
     +-----------+-----------+
                 |  drift correction per speaker + latency compensation
        +--------+--------+
        v                 v
  [delay: 0.0 ms]   [delay: 11.5 ms]     <- sample-accurate manual trim, set once by calibration
        |                 |
   Bluetooth A2DP    Bluetooth A2DP
        |                 |
    Kitchen speaker   Living-room speaker
```

## Why this exists

Phones and most desktops will only send audio to one Bluetooth speaker at a time. Vendors
sell "stereo pairing" that works only with two identical speakers of their own brand. On
Linux the sound server (PipeWire or PulseAudio) can drive several A2DP sinks at once, but
getting two speakers connected, combined, routed and *actually in sync* is a pile of
`bluetoothctl`, `pactl` and `pw-cli` incantations that break the moment a speaker drops.
Soundscape wraps that into one tool with a guided setup, a reconnecting daemon, a phone
remote, and a microphone-based calibrator for the sync.

## What you get

| Requirement | How Soundscape handles it |
|---|---|
| Both speakers play the same source | One virtual output that every app sees as a normal speaker; it becomes the default while Soundscape is up and is restored on `down`. |
| Audio quality and EQ untouched | No EQ, no dynamics, no format tricks. Audio is mixed in 32-bit float by the sound server and handed to each speaker's Bluetooth encoder as-is. The only DSP is the drift-correcting resampler, configured for its highest quality. |
| No drift between speakers | Each speaker's stream is rate-matched to its own clock continuously, so they never slide apart over a long session. |
| Speakers in sync | Automatic latency compensation from each speaker's reported delay, plus a sample-accurate manual trim per speaker that `soundscape sync --auto` measures with any microphone and applies for you. Trims are remembered across restarts. |
| Survives dropouts | `soundscape daemon` (or the included systemd unit) reconnects a speaker that drops and rebuilds the output. |
| Works from the couch | `soundscape remote` serves a small web page for your phone: per-speaker volume, mute, sync nudges, play/pause. |

### What it cannot do

Be honest with yourself about Bluetooth before you start:

* **Bluetooth A2DP has 100 to 250 ms of latency.** Soundscape adds a few milliseconds on top
  and keeps the speakers aligned *to each other*, but music will trail a video by that
  amount. That is inherent to A2DP and no software on the sending side can remove it.
* **Sample-exact sync is not possible over two independent A2DP links.** Each speaker has
  its own internal buffer and clock, and its reported latency is approximate. After
  calibration the residual error is typically 1 to 3 ms, well below what listeners perceive
  as an echo (roughly 10 ms for clicks, 20 to 30 ms for music). If you move a speaker across
  the room, sound itself takes about 3 ms per metre, so re-run `sync` after rearranging.
* **Two A2DP streams share one radio.** Most adapters cope at standard SBC or AAC bitrates;
  if you hear dropouts, see [Troubleshooting](docs/TROUBLESHOOTING.md#dropouts).
* **Linux only.** Android, iOS and Windows do not expose multi-A2DP routing to apps, which
  is why a cheap Linux box (a Raspberry Pi works) is the right device for this job.

## Requirements

* Linux with **PipeWire** (recommended, 0.3.65 or newer, 1.0+ ideal) or **PulseAudio**.
  Every mainstream distro since 2022 ships PipeWire by default.
* **BlueZ** (`bluetoothctl`) and **systemd** (`busctl`).
* `pulseaudio-utils` for `pactl`/`paplay`/`parec` (works with PipeWire too).
* Python 3.9+. No Python dependencies; `numpy` is optional and only speeds up calibration.
* Optional: `mpv` or `ffplay` for `soundscape play`, `playerctl` for media keys in the remote.

Debian/Ubuntu/Raspberry Pi OS:

```bash
sudo apt install bluez pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils mpv playerctl
```

Fedora:

```bash
sudo dnf install bluez pipewire pipewire-pulseaudio wireplumber pulseaudio-utils mpv playerctl
```

## Install

```bash
git clone https://github.com/patelmemail0-sys/soundscape.git
cd soundscape
./install.sh            # pipx if available, otherwise pip --user; add --service for autostart
```

Or by hand:

```bash
pipx install .          # or: pip install --user .
```

## Quick start

Put both speakers in pairing mode and run:

```bash
soundscape setup
```

The wizard checks your system, scans, lets you pick the left and right speaker, pairs and
connects them, builds the joint output, plays a low tone on the left and a higher tone on
the right so you can confirm which is which, and offers to align them with your microphone.
After that, everything on the machine plays through both speakers.

Day to day:

```bash
soundscape up                 # connect both and make them the default output
soundscape play ~/Music/*.mp3 # or just press play in any app
soundscape status
soundscape down               # back to your normal output
```

## Getting the sync right

Soundscape aligns the speakers in three layers:

1. **Drift correction** (always on): every output stream is resampled to follow its
   speaker's clock, so two speakers that start together stay together.
2. **Automatic latency compensation** (on by default): the engine delays the speaker that
   reports the lower latency so both match. Bluetooth latency reports are approximate, so a
   residual of a few to tens of milliseconds can remain.
3. **Manual trim**: a per-speaker delay applied sample-accurately. Measure it once:

```bash
soundscape sync --auto          # uses the default microphone
soundscape sync --auto --source alsa_input.usb-Blue_Yeti-00.analog-stereo
```

The calibrator plays a short chirp through the first speaker, then the second, records both
with the microphone, finds each chirp by cross-correlation, and delays the early speaker by
the measured difference. Stand the microphone roughly equidistant from the speakers (every
metre of difference is about 3 ms) and keep the room quiet for seven seconds. Run it again
after moving a speaker. If you want to do it by ear instead:

```bash
soundscape sync                 # loops a click track; nudge with [ ] , . until it is one click
soundscape offset right 12.5    # or set a trim directly, in milliseconds (live)
```

Trims are stored in the config and re-applied on every `up`.

## Stereo across the room

By default both speakers play full stereo, which is what you want for a kitchen and a
living room. If the two speakers *are* your stereo pair, send the left channel to one and
the right channel to the other:

```bash
soundscape up --stereo
```

This needs the PipeWire engine (the default on PipeWire systems).

## Phone remote

```bash
soundscape remote --host 0.0.0.0 --port 8787
```

Open `http://<this-machine>:8787/` on your phone for master and per-speaker volume, mute,
plus/minus sync nudges, and play/pause/next (through `playerctl`). There is no login, so
only expose it on a network you trust; the default bind is `127.0.0.1`.

## Keep it running

```bash
./install.sh --service          # installs and enables a systemd *user* unit
systemctl --user status soundscape
```

The daemon brings the speakers up at login, reconnects them when they drop, rebuilds the
output, and takes everything down cleanly on stop. Powered-off speakers are retried with a
backoff, so you can simply switch them on and off.

## All commands

```
soundscape setup                guided first run
soundscape doctor               check Bluetooth, sound server, tools; print fixes
soundscape scan [--seconds N]   discover speakers in pairing mode
soundscape add ADDR [--position left|right] [--name N]
soundscape remove left|right|ADDR [--forget]
soundscape up [--engine pipewire|pulse] [--stereo] [--no-default] [--no-compensate]
soundscape down
soundscape status [--json]
soundscape volume 40            both speakers
soundscape volume left 70       one speaker (remembered)
soundscape mute [left|right|all] [--off]
soundscape offset right 12.5 [--relative]
soundscape sync [--auto] [--source SRC] [--passes N] [--no-apply]
soundscape test [left|right]    tone on each speaker in turn
soundscape play FILES... [--loop] [--shuffle] [--player mpv|ffplay|paplay]
soundscape remote [--host H] [--port P]
soundscape daemon [--interval S] [--keep]
soundscape config show|path|set KEY VALUE
```

Global flags go before the command: `soundscape --root DIR ...` keeps config and state under
`DIR` (useful for a second profile), `--adapter hci1` picks a Bluetooth adapter, `-v` shows
what is happening.

## How it works

Two engines, chosen automatically:

* **PipeWire engine** (default on PipeWire). Soundscape starts a second `pipewire` process
  with a generated config that lives alongside your normal daemon. It hosts a
  `combine-stream` sink with drift correction, resampler quality 10 and latency compensation,
  and one `filter-chain` per speaker whose only node is a `delay`, so manual trims are
  sample-accurate and adjustable live without restarting anything. Stereo split uses the
  combine stream's channel routing.
* **PulseAudio engine** (PulseAudio, or PipeWire without the `pipewire`/`pw-cli` binaries).
  Loads `module-combine-sink` through `pactl` with the best available resampler (`soxr-vhq`,
  then `speex-float-10`). Manual trims become `set-port-latency-offset` leads on the other
  speaker, which the combine sink's rate control then honours.

Bluetooth is driven through BlueZ's D-Bus API with `busctl --json` (pairing, trust,
connect, status) and a long-lived `bluetoothctl` for discovery. Every external tool is
behind one `Runner` object, which is what lets the test-suite emulate BlueZ, pactl and
PipeWire and run the real orchestration end to end. Details in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Development

```bash
pip install -e '.[dev]'
pytest                       # 100+ tests, no hardware needed
ruff check soundscape tests
```

The suite includes a stateful fake of bluetoothd and a sound server, so `up`, `down`,
reconnects, calibration maths and both engines are exercised for real; the generated
PipeWire config is validated against PipeWire's own parser in CI when available.

## Status

0.1.0. Built and unit-tested against emulated BlueZ/PipeWire/PulseAudio and validated
against PipeWire 1.0.5's config parser and module binaries. It has not yet been run against
physical speakers by the author; the first thing to do with real hardware is
`soundscape doctor`, then `soundscape setup`, and file an issue with the output of
`soundscape -v up` if something misbehaves.

## License

MIT.

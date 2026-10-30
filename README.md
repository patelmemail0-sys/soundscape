# Soundscape

**One audio source, all your Bluetooth speakers, in sync.**

Soundscape joins two, three or more ordinary Bluetooth speakers into a single output on a
**Mac** or a **Linux** machine. Everything you play, from Spotify in a browser to a local file, comes
out of both speakers at the same time, aligned to within a few milliseconds, with no EQ or
other processing applied to the audio. An **iPhone** cannot do this on its own, but it can
AirPlay to the Mac and let Soundscape take it from there (see below).

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
* **Mac or Linux as the hub.** iOS, Android and Windows do not let apps route audio to two
  Bluetooth speakers at once. Use the MacBook (or a Raspberry Pi) as the hub and send the
  phone's audio to it.

## Requirements

### macOS

* macOS 12 or newer, Python 3.9+ (`xcode-select --install` or `brew install python`).
* Optional but recommended:
  * `brew install blueutil` so Soundscape can connect and pair speakers itself. Without it
    you connect them from the Bluetooth menu and Soundscape does the rest.
  * **BlackHole** (free virtual audio device): `brew install blackhole-2ch` or the installer
    from existential.audio. It unlocks the relay engine, which is what makes sync trims and
    stereo split possible. Without it Soundscape uses a plain CoreAudio aggregate device:
    both speakers play, drift-corrected, but a mixed pair cannot be trimmed into sync.
  * `brew install mpv` for `soundscape play` with playlists and URLs (`afplay` is used otherwise).

### Linux

* **PipeWire** (recommended, 0.3.65 or newer, 1.0+ ideal) or **PulseAudio**.
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
./install.sh            # macOS or Linux; add --service for autostart
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

## iPhone

No iOS app can play to two Bluetooth speakers at once; Apple reserves multi-output for
AirPods "Share Audio" and AirPlay 2 speakers. The way that works is to make the Mac the hub:

1. On the Mac: System Settings > General > AirDrop & Handoff > **AirPlay Receiver**: on
   (allow "Current User" or "Anyone on the same network").
2. On the Mac: `soundscape up` (both Bluetooth speakers are now the Mac's output).
3. On the iPhone: open Control Center, tap the AirPlay icon in the audio card, pick the
   MacBook.

Music from any iPhone app now plays through both speakers. Expect about two seconds of
AirPlay buffering on top of Bluetooth's own latency, which is fine for music. If your
speakers are the same model, their maker's own party/stereo mode (JBL PartyBoost, Bose
SimpleSync, Sony Party Connect, UE PartyUp) is the only way to do it straight from the phone.

## Three or more speakers

Positions are just names. `left` and `right` are special only for stereo split; everything
else is up to you:

```bash
soundscape add AA:BB:CC:DD:EE:03 --position kitchen
soundscape add AA:BB:CC:DD:EE:04 --position patio
soundscape up
soundscape volume patio 60
soundscape offset kitchen 4.5
```

`soundscape setup` asks "add another speaker?" after the first two and lets you name each
spot. `sync --auto` calibrates any number of speakers in one pass: it plays one chirp per
speaker, three seconds apart, and delays everyone to match the slowest. `test` plays a rising
pitch per speaker in position order so you can tell them apart.

Bandwidth is the practical limit: every speaker is its own A2DP stream on the same radio.
Two is easy, three usually fine at standard SBC/AAC bitrates, four or more depends on the
adapter and the room. If you hear dropouts with many speakers, lower the codec bitrate
(see [Troubleshooting](docs/TROUBLESHOOTING.md#dropouts)) or add a second Bluetooth adapter.

## Stereo across the room

By default both speakers play full stereo, which is what you want for a kitchen and a
living room. If the two speakers *are* your stereo pair, send the left channel to one and
the right channel to the other:

```bash
soundscape up --stereo
```

This needs the PipeWire engine on Linux or the relay engine (BlackHole) on macOS.

## Dashboard

```bash
soundscape ui
```

Opens a local web page that shows and controls everything:

* the live **signal path**: your apps, the Soundscape mix, each speaker with its delay, lit
  up when audio can flow;
* a **card per speaker** with connection state, volume, mute, test tone and a delay stepper;
* **calibration** with one click and a chart of each speaker's measured lag and the trim
  that was applied;
* **scan and add** speakers with a position name, **settings** (engine, stereo split,
  latency compensation, default output), the **doctor** checks with fixes, and an
  **activity log**.

Scans, calibration and up/down run in the background, so the page keeps updating while
they work. For your phone, expose it on the network with a token:

```bash
soundscape ui --host 0.0.0.0 --port 8787 --token mysecret
```

and open `http://<this-machine>:8787/?token=mysecret`. Without `--token` anyone on the
network could change volumes, so the default bind is `127.0.0.1`. `soundscape remote` is
the same server without opening a browser.

## Keep it running

```bash
./install.sh --service          # systemd user unit on Linux, LaunchAgent on macOS
```

The daemon brings the speakers up at login, reconnects them when they drop, rebuilds the
output, and takes everything down cleanly on stop. Powered-off speakers are retried with a
backoff, so you can simply switch them on and off. Every check also verifies the audio graph
itself: a speaker that comes back on its own without its delay filter, or a sink that was
re-created with a new id, triggers a rebuild within one interval. On macOS check `/tmp/soundscape-daemon.log`;
on Linux `systemctl --user status soundscape`.

## Volume: no limits from Soundscape

The chain runs at unity gain. On `up`, every speaker is set to its own 100% (over Bluetooth
that is the speaker's physical maximum via absolute volume) unless you have trimmed it
deliberately, and on macOS the Mac's own level (BlackHole, in relay mode) is set to 100% so
nothing is lost on the way in. Soundscape never attenuates, compresses or limits: loudness
is set only by the speaker sliders, the speakers' own buttons, or the Mac's volume keys.
If a speaker still seems quieter than when it is connected directly, check macOS
System Settings > Sound > Headphone Safety ("Reduce Loud Audio"), which applies to devices
macOS classifies as headphones.

## macOS notes

* The aggregate device has no system volume slider, so the keyboard volume keys do nothing
  while Soundscape is up with the aggregate engine. Use `soundscape volume 40`, the phone
  remote, or the speakers' own buttons. With the relay engine the keys control BlackHole and
  work normally.
* Both engines leave the audio untouched: CoreAudio mixes in 32-bit float and resamples only
  for drift correction. The relay engine adds exactly one buffer (about 21 ms at 1024 frames)
  of latency and shifts each speaker by whole samples; nothing else.
* First run of `sync --auto` will make macOS ask for microphone access for your terminal.

## All commands

```
soundscape setup                guided first run
soundscape doctor               check Bluetooth, sound server, tools; print fixes
soundscape scan [--seconds N]   discover speakers in pairing mode
soundscape add ADDR [--position left|right|NAME] [--name N]   any number of speakers
soundscape remove POSITION|ADDR|NAME [--forget]
soundscape up [--engine pipewire|pulse] [--stereo] [--no-default] [--no-compensate]
soundscape down
soundscape status [--json]
soundscape volume 40            both speakers
soundscape volume left 70       one speaker (remembered)
soundscape mute [POSITION|all] [--off]
soundscape offset POSITION 12.5 [--relative]
soundscape sync [--auto] [--source SRC] [--passes N] [--no-apply]
soundscape test [left|right]    tone on each speaker in turn
soundscape play FILES... [--loop] [--shuffle] [--player mpv|ffplay|paplay]
soundscape ui [--host H] [--port P] [--token T] [--no-open]   dashboard (remote = same, no browser)
soundscape daemon [--interval S] [--keep]
soundscape config show|path|set KEY VALUE
```

Global flags go before the command: `soundscape --root DIR ...` keeps config and state under
`DIR` (useful for a second profile), `--adapter hci1` picks a Bluetooth adapter, `-v` shows
what is happening.

## How it works

Four engines, chosen automatically by platform:

* **Aggregate engine** (macOS, built in). Creates a CoreAudio aggregate device of the two
  speakers with drift compensation, the first speaker as clock master, and makes it the
  default output. Destroyed again on `down`.
* **Relay engine** (macOS with BlackHole). BlackHole becomes the default output; a small relay
  process builds a private aggregate of BlackHole plus the speakers and copies each block to
  every speaker with a per-speaker delay in whole samples, adjustable live over a Unix socket.
  Stereo split happens here too.

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

Bluetooth is driven through BlueZ's D-Bus API on Linux (`busctl --json` plus a long-lived
`bluetoothctl` for discovery) and through `blueutil` / `system_profiler` on macOS. Every
external tool and every CoreAudio call sits behind one injectable seam, which is what lets
the test-suite emulate BlueZ, pactl, PipeWire and the CoreAudio HAL and run the real
orchestration end to end. Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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

0.4.0. Linux and macOS backends, any number of speakers, unit-tested against emulated BlueZ, PipeWire, PulseAudio
and CoreAudio; the Linux engine is additionally validated against a live PipeWire 1.0.5 in
CI. Neither backend has been run against physical speakers by the author yet. The first
thing to do with real hardware is `soundscape doctor`, then `soundscape setup`, and file an
issue with the output of `soundscape -v up` if something misbehaves.

## License

MIT.

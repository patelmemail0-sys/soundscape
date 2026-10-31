# Troubleshooting

Start with:

```bash
soundscape doctor
soundscape -v up
```

`doctor` prints a fix for every problem it finds. `-v` shows every step of `up`.

## Pairing and connecting

**"has not been discovered yet" / nothing found by `scan`**
Hold the speaker's Bluetooth button until it blinks fast (pairing mode). A speaker that is
already connected to your phone will not show up or will refuse: disconnect it on the phone
first. Try `soundscape scan --seconds 20`.

**"Authentication Failed" / "AuthenticationCanceled"**
Pairing mode timed out before BlueZ got there, or the speaker remembers a stale pairing
with this machine. Put it in pairing mode again; if it keeps failing,
`soundscape remove right --forget` and re-add, and also "forget" the computer on the speaker
if it has such an option.

**"br-connection-profile-unavailable"**
The speaker connected but nobody on the computer offered the A2DP profile. The sound
server's Bluetooth module is missing or not running for your user:

```bash
# PipeWire (Debian/Ubuntu)
sudo apt install libspa-0.2-bluetooth   # or: pipewire-audio
systemctl --user restart wireplumber pipewire pipewire-pulse
# PulseAudio
sudo apt install pulseaudio-module-bluetooth && pactl load-module module-bluetooth-discover
```

**"connected but no audio sink appeared"**
The card is in the wrong profile (headset/HFP or off). Check
`pactl list cards | grep -A20 bluez_card` and switch:
`pactl set-card-profile bluez_card.AA_BB_CC_DD_EE_FF a2dp-sink` (PipeWire) or `a2dp_sink`
(PulseAudio). WirePlumber may also need `bluez5.roles = [ a2dp_sink ]` in
`~/.config/wireplumber/bluetooth.lua.d/` to stop it choosing headset mode.

**Only one speaker ever connects**
Some older or very cheap Bluetooth adapters cannot hold two A2DP links. Try a USB Bluetooth
5.x adapter (CSR8510-based dongles are known to struggle). On a Raspberry Pi, a USB adapter
is much more reliable than the on-board radio for two streams.

## Sound

**Everything still plays from the laptop speakers**
`soundscape status` should say `default output`. If not, run `soundscape up` again (it moves
running streams), or pick "Soundscape (both speakers)" in your sound settings. Some apps
(browsers) remember a device; re-select the output inside the app.

**Dropouts, crackle, stutter** <a name="dropouts"></a>
Two A2DP streams share one radio and often one antenna with Wi-Fi.
1. Move the adapter away from USB 3 ports and the Wi-Fi antenna, or use a short USB
   extension.
2. Keep both speakers within a few metres and in line of sight.
3. Lower the codec bitrate. For PipeWire put this in
   `~/.config/wireplumber/wireplumber.conf.d/50-bluez.conf`:
   ```
   monitor.bluez.properties = {
     bluez5.enable-sbc-xq = false
     bluez5.codecs = [ sbc aac ]
   }
   ```
   then `systemctl --user restart wireplumber`.
4. If Wi-Fi is on 2.4 GHz, move it to 5 GHz.
5. Last resort: a second Bluetooth adapter. Soundscape drives one adapter per run
   (`soundscape --adapter hci1 ...`); pairing each speaker to its own adapter and joining
   them is possible by hand today and on the roadmap for `setup`.

**Three or more speakers: the last one keeps dropping**
Bluetooth bandwidth. Each speaker is a separate A2DP stream on one radio. Lower the codec
bitrate (below), keep speakers close and in line of sight, and prefer a USB Bluetooth 5.x
adapter. Beyond three or four speakers a second adapter is the realistic fix.

**Volume sounds odd, or one speaker is louder**
Set the per-speaker trims: `soundscape volume left 80`. They are remembered. Keep the
master (`soundscape volume 60`) as the thing you change day to day; trims at 100% mean no
gain change on that speaker at all.

## Sync

**The speakers echo each other**
Run `soundscape sync --auto` with the computer's microphone roughly equidistant from both
speakers and the room quiet for seven seconds. If you have no usable microphone,
`soundscape sync` loops a click track and you nudge with `[` `]` (5 ms) and `,` `.` (1 ms)
until it collapses into a single click.

**In sync next to the laptop, out of sync elsewhere in the room**

Calibration aligned the sound where the microphone was: a speaker 2 m further from the
laptop was heard about 6 ms late and got "corrected" for it. Tell Soundscape the distances
(`soundscape distance left 1.2m`, `soundscape distance right 4ft`, or the Distance field on
each speaker card) and calibrate again; the travel time is taken out and the speakers
themselves are aligned. Check with `soundscape distance`.

**"chirps were not detected reliably (confidence ...)"**
Too much noise, speakers too quiet, or the microphone source is wrong.
`pactl list short sources` shows the microphones; pass `--source NAME`. Never pass a
`.monitor` source, that is a loopback of the speakers (Soundscape refuses it).

**A speaker came back but stays silent**
Run `soundscape status`; if the daemon is running it rebuilds within one interval (10 s by
default) because the health check sees the missing delay filter. Without the daemon, run
`soundscape up` again. Both engines re-create everything from scratch, so a rebuild is safe.

**Sync drifts over time**
It should not: each speaker's stream is rate-matched to its own clock. If it does, one
speaker is probably reconnecting silently. Check `journalctl --user -u soundscape` with the
daemon running, and `soundscape status` for a changed sink name.

**Sync changes after a reboot**
Bluetooth latency can differ per connection on some speakers. Re-run `sync --auto`; it takes
ten seconds. Trims are stored per speaker and re-applied on every `up`.

**Video is out of sync with audio**
That is Bluetooth's own 100 to 250 ms. Soundscape cannot remove it. Use your video player's
audio delay setting (mpv: `--audio-delay=-0.2`).

## Engines

**"manual offsets need PipeWire >= 0.3.65"**
Your PipeWire predates the filter-chain `delay` plugin or `combine-stream`. Upgrade
(Debian 12, Ubuntu 23.04+, Fedora 37+ are fine), or force the PulseAudio-style engine:
`soundscape config set engine pulse`.

**PipeWire system but `status` says engine `pulse`**
The native tools are missing. `sudo apt install pipewire-bin` (Fedora: `pipewire-utils`),
then `soundscape up`.

**Leftover sink after a crash**
`soundscape down` cleans up from the state file; `soundscape up` also sweeps stray engine
processes and leftover `module-combine-sink` modules. If something is still stuck:
`pkill -f soundscape-engine.conf` and `pactl list short modules | grep soundscape`.

## macOS

**"is not paired with this Mac" / "paired but not connected"**
Without `blueutil`, Soundscape cannot pair or connect for you. Either pair and connect the
speakers from System Settings > Bluetooth (they reconnect automatically when switched on),
or `brew install blueutil` and run `soundscape add` again.

**Nothing plays / sound still comes from the MacBook**
`soundscape status` should show the aggregate (or BlackHole) as the default output. Some
apps keep their own output choice; pick "Soundscape (both speakers)" or "BlackHole 2ch" in
the app, or restart it.

**Volume keys do nothing**
Normal with an aggregate device. Use `soundscape volume 40`, the phone remote, or install
BlackHole (relay engine) which keeps the keys working.

**"the aggregate engine cannot delay one speaker"**
Install BlackHole (`brew install blackhole-2ch`, then reboot or `sudo killall coreaudiod`)
and run `soundscape up` again; the relay engine is picked automatically.

**Microphone sync says it recorded nothing**
macOS asks for microphone permission per app the first time; allow it for your terminal in
System Settings > Privacy & Security > Microphone and run `soundscape sync --auto` again.

**Crackle with the relay engine**
Increase the buffer: `soundscape config set engine relay` is already in effect; set a larger
block with `SOUNDSCAPE_RELAY_BUFFER=2048 soundscape up` (doubles latency to about 43 ms,
both speakers equally). Closing CPU-heavy apps helps too.

**AirPlay from the iPhone stops or stutters**
AirPlay Receiver needs both devices on the same Wi-Fi and the Mac awake. Disable "Put hard
disks to sleep" and screen lock while playing, or keep the Mac on power.

## The Soundscape app (macOS)

**Nothing happens, or the app shows "not responding"**
Update: versions before 0.5.1 ran the service inside the app process, which macOS reports as
not responding because it has no window. Now the app starts a background service and quits
at once. If the dashboard still does not open, read `~/Library/Logs/Soundscape.log`; the
same thing from a terminal is `soundscape start --detach`.

**How do I stop it?**
Click Quit in the dashboard (top right), or run `soundscape down` and
`pkill -f "soundscape start"`.

## Daemon / systemd / launchd

```bash
systemctl --user status soundscape
journalctl --user -u soundscape -f
```

On macOS the LaunchAgent logs to `/tmp/soundscape-daemon.log`; stop it with
`launchctl unload -w ~/Library/LaunchAgents/com.soundscape.daemon.plist`.

The unit runs `soundscape daemon`, which retries unavailable speakers with a growing backoff
(up to two minutes) and rebuilds when one drops. Stopping the unit runs `down`. If you want
the output to survive a daemon restart, edit the unit to use `daemon --keep`.

For the daemon to start before you log in graphically, enable lingering:
`loginctl enable-linger $USER`.

## Still stuck?

Open an issue with the output of `soundscape doctor`, `soundscape -v up`,
`pactl info`, and `pipewire --version` (or `pulseaudio --version`).

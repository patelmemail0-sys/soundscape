#!/usr/bin/env bash
# Install Soundscape for the current user. Options:
#   --service   also install and enable the systemd user unit (autostart + reconnect)
#   --dev       editable install with test dependencies
set -euo pipefail
cd "$(dirname "$0")"

SERVICE=0; DEV=0
for arg in "$@"; do
  case "$arg" in
    --service) SERVICE=1 ;;
    --dev) DEV=1 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Soundscape needs Linux (BlueZ + PipeWire/PulseAudio)." >&2; exit 1
fi

missing=()
for tool in busctl bluetoothctl pactl; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
if (( ${#missing[@]} )); then
  echo "missing system tools: ${missing[*]}"
  echo "  Debian/Ubuntu: sudo apt install bluez pulseaudio-utils pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth"
  echo "  Fedora:        sudo dnf install bluez pulseaudio-utils pipewire pipewire-pulseaudio wireplumber"
  echo "continuing with the Python install; run 'soundscape doctor' afterwards."
fi

if (( DEV )); then
  python3 -m pip install --user -e '.[dev]'
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force .
else
  python3 -m pip install --user --upgrade .
fi

if ! command -v soundscape >/dev/null 2>&1; then
  echo "note: ~/.local/bin is not on your PATH yet. Add this to your shell profile:"
  echo '  export PATH="$HOME/.local/bin:$PATH"'
fi

if (( SERVICE )); then
  unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$unit_dir"
  cp systemd/soundscape.service "$unit_dir/soundscape.service"
  systemctl --user daemon-reload
  systemctl --user enable soundscape.service
  echo "installed systemd user unit. Start it after 'soundscape setup' with:"
  echo "  systemctl --user start soundscape"
  echo "to start before graphical login: loginctl enable-linger $USER"
fi

echo
echo "done. Next: put both speakers in pairing mode and run:  soundscape setup"

#!/usr/bin/env bash
# Install Soundscape for the current user (Linux or macOS). Options:
#   --service   also install the autostart unit (systemd user unit on Linux, LaunchAgent on macOS)
#   --no-app    skip the one-click launcher (Soundscape.app on macOS, .desktop entry on Linux)
#   --dev       editable install with test dependencies
set -euo pipefail
cd "$(dirname "$0")"

SERVICE=0; DEV=0; APP=1
for arg in "$@"; do
  case "$arg" in
    --service) SERVICE=1 ;;
    --no-app) APP=0 ;;
    --dev) DEV=1 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

OS="$(uname -s)"
case "$OS" in
  Linux)
    missing=()
    for tool in busctl bluetoothctl pactl; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
    if (( ${#missing[@]} )); then
      echo "missing system tools: ${missing[*]}"
      echo "  Debian/Ubuntu: sudo apt install bluez pulseaudio-utils pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth"
      echo "  Fedora:        sudo dnf install bluez pulseaudio-utils pipewire pipewire-pulseaudio wireplumber"
      echo "continuing with the Python install; run 'soundscape doctor' afterwards."
    fi ;;
  Darwin)
    if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      echo "python3 >= 3.9 is needed. Install the Command Line Tools (xcode-select --install) or: brew install python" >&2
      exit 1
    fi
    command -v blueutil >/dev/null 2>&1 || echo "optional: brew install blueutil   (lets Soundscape connect speakers itself)"
    if ! ls /Library/Audio/Plug-Ins/HAL 2>/dev/null | grep -qi blackhole; then
      echo "optional: brew install blackhole-2ch   (enables sync trims + stereo split; needs a reboot or 'sudo killall coreaudiod')"
    fi ;;
  *)
    echo "Soundscape runs on Linux and macOS." >&2; exit 1 ;;
esac

if (( DEV )); then
  python3 -m pip install --user -e '.[dev]'
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force .
else
  python3 -m pip install --user --upgrade . 2>/dev/null || python3 -m pip install --user --upgrade --break-system-packages .
fi

if ! command -v soundscape >/dev/null 2>&1; then
  # pip --user puts commands in a folder the shell does not search by default
  user_bin="$(python3 -m site --user-base 2>/dev/null)/bin"
  [[ -x "$user_bin/soundscape" ]] || user_bin="$HOME/.local/bin"
  profile="$HOME/.bashrc"; [[ "${SHELL##*/}" == "zsh" ]] && profile="$HOME/.zshrc"
  echo
  echo "note: 'soundscape' was installed to $user_bin, which is not on your PATH (the list of"
  echo "folders your shell searches for commands). Run this once to add it:"
  echo
  echo "  echo 'export PATH=\"$user_bin:\$PATH\"' >> $profile && source $profile"
  echo
fi

SOUNDSCAPE_BIN="$(command -v soundscape 2>/dev/null || true)"
[[ -z "$SOUNDSCAPE_BIN" ]] && SOUNDSCAPE_BIN="$(python3 -m site --user-base 2>/dev/null)/bin/soundscape"

if (( APP )) && [[ -x "$SOUNDSCAPE_BIN" ]]; then
  if [[ "$OS" == "Darwin" ]]; then
    app="$HOME/Applications/Soundscape.app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
    cat > "$app/Contents/MacOS/Soundscape" <<LAUNCH
#!/bin/bash
# Soundscape launcher: starts the background service (or just reopens the dashboard) and exits.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
"$SOUNDSCAPE_BIN" start --detach >> "\$HOME/Library/Logs/Soundscape.log" 2>&1
if [ \$? -ne 0 ]; then
  osascript -e 'display alert "Soundscape could not start" message "See ~/Library/Logs/Soundscape.log for details."' >/dev/null 2>&1
fi
exit 0
LAUNCH
    chmod +x "$app/Contents/MacOS/Soundscape"
    cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Soundscape</string>
  <key>CFBundleDisplayName</key><string>Soundscape</string>
  <key>CFBundleIdentifier</key><string>com.soundscape.launcher</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>Soundscape</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict></plist>
PLIST
    mkdir -p "$HOME/Library/Logs"
    echo "installed $app  (open it from Launchpad or Spotlight: it starts Soundscape in the background and opens the dashboard; log: ~/Library/Logs/Soundscape.log)"
  else
    mkdir -p "$HOME/.local/share/applications"
    cat > "$HOME/.local/share/applications/soundscape.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Soundscape
Comment=Play through all your Bluetooth speakers at once
Exec=$SOUNDSCAPE_BIN start --detach
Terminal=false
Categories=AudioVideo;Audio;
DESKTOP
    echo "installed a Soundscape launcher in your applications menu"
  fi
fi

if (( SERVICE )); then
  if [[ "$OS" == "Darwin" ]]; then
    mkdir -p "$HOME/Library/LaunchAgents"
    cp launchd/com.soundscape.daemon.plist "$HOME/Library/LaunchAgents/"
    launchctl unload "$HOME/Library/LaunchAgents/com.soundscape.daemon.plist" 2>/dev/null || true
    launchctl load -w "$HOME/Library/LaunchAgents/com.soundscape.daemon.plist"
    echo "installed LaunchAgent com.soundscape.daemon (log: /tmp/soundscape-daemon.log)"
    echo "run 'soundscape setup' first if you have not; stop with: launchctl unload -w ~/Library/LaunchAgents/com.soundscape.daemon.plist"
  else
    unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$unit_dir"
    cp systemd/soundscape.service "$unit_dir/soundscape.service"
    systemctl --user daemon-reload
    systemctl --user enable soundscape.service
    echo "installed systemd user unit. Start it after 'soundscape setup' with:"
    echo "  systemctl --user start soundscape"
    echo "to start before graphical login: loginctl enable-linger $USER"
  fi
fi

echo
echo "done. First time: put the speakers in pairing mode and run:  soundscape setup"
echo "After that, just launch Soundscape (the app) or run:        soundscape start"

"""Exception types. Every user-facing failure carries a plain-English hint."""

from __future__ import annotations


class SoundscapeError(Exception):
    """Base class. ``hint`` is an optional remediation line shown after the message."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message if not self.hint else f"{self.message}\n  hint: {self.hint}"


class ToolMissing(SoundscapeError):
    def __init__(self, tool: str, hint: str = "") -> None:
        super().__init__(f"required command not found: {tool}", hint or INSTALL_HINTS.get(tool, ""))
        self.tool = tool


class BluetoothError(SoundscapeError):
    pass


class AudioError(SoundscapeError):
    pass


class ConfigError(SoundscapeError):
    pass


INSTALL_HINTS = {
    "busctl": "busctl ships with systemd; on Debian/Ubuntu: sudo apt install systemd",
    "bluetoothctl": "sudo apt install bluez   (Fedora: sudo dnf install bluez)",
    "pactl": "sudo apt install pulseaudio-utils   (works with PipeWire too)",
    "paplay": "sudo apt install pulseaudio-utils",
    "parec": "sudo apt install pulseaudio-utils",
    "pipewire": "sudo apt install pipewire pipewire-pulse wireplumber",
    "pw-cli": "sudo apt install pipewire-bin",
    "pw-dump": "sudo apt install pipewire-bin",
    "mpv": "sudo apt install mpv   (or ffplay via ffmpeg)",
}

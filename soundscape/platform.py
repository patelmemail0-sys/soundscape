"""Which OS are we on, and which backend does that imply."""

from __future__ import annotations

import os
import sys

LINUX, MAC, OTHER = "linux", "mac", "other"


def current() -> str:
    forced = os.environ.get("SOUNDSCAPE_PLATFORM")
    if forced in (LINUX, MAC):
        return forced
    if sys.platform.startswith("linux"):
        return LINUX
    if sys.platform == "darwin":
        return MAC
    return OTHER


def is_mac() -> bool:
    return current() == MAC

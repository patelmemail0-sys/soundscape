"""XDG-style locations. Everything Soundscape writes lives under these three dirs."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


@dataclass
class Paths:
    config_dir: Path = field(default_factory=lambda: _xdg("XDG_CONFIG_HOME", ".config") / "soundscape")
    state_dir: Path = field(default_factory=lambda: _xdg("XDG_STATE_HOME", ".local/state") / "soundscape")
    cache_dir: Path = field(default_factory=lambda: _xdg("XDG_CACHE_HOME", ".cache") / "soundscape")

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def engine_conf(self) -> Path:
        return self.state_dir / "soundscape-engine.conf"

    @property
    def engine_log(self) -> Path:
        return self.state_dir / "engine.log"

    def ensure(self) -> Paths:
        for d in (self.config_dir, self.state_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def under(cls, root: Path) -> Paths:
        """All three dirs under one root (used by tests and --root)."""
        return cls(root / "config", root / "state", root / "cache")

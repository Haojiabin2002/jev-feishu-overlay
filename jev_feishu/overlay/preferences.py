"""Persist only cosmetic values. Never copy credentials or widen app permissions."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json
import os
import tempfile
from .settings import OverlaySettings
from ..errors import ConfigError


def load_preferences(base: Path, settings: OverlaySettings) -> OverlaySettings:
    path = base / ".state" / "overlay-ui.json"
    try:
        if path.is_symlink() or path.parent.is_symlink(): return settings
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) - {"opacity", "width", "height"}: return settings
        updated = replace(settings, **data)
        updated.validate()
        return updated
    except (OSError, ValueError, TypeError, ConfigError):
        return settings


def save_preferences(base: Path, opacity: float, width: int, height: int) -> None:
    values = {"opacity": opacity, "width": width, "height": height}
    OverlaySettings(**values).validate()
    directory = base / ".state"
    if directory.is_symlink(): raise ValueError("UI state directory must not be a symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / "overlay-ui.json"
    if destination.is_symlink(): raise ValueError("UI settings must not be a symlink")
    fd, name = tempfile.mkstemp(prefix=".overlay-ui-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(values, output)
        os.chmod(name, 0o600)
        os.replace(name, destination)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass

"""Persistent user settings stored as TOML in the per-user config directory."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from platformdirs import user_config_dir
from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger(__name__)


class Settings(BaseModel):
    region: Literal[1, 2, 3] = 1
    mains_hz: Literal[50, 60, "auto"] = "auto"
    theme: Literal["dark", "light"] = "dark"
    colormap: Literal["turbo", "viridis", "grey"] = "turbo"
    last_device_id: str | None = None
    last_tune_hz: int = 7_100_000
    token: str | None = None
    prebuffer_s: float = Field(10.0, ge=0, le=60)


class SettingsPatch(BaseModel):
    """Fields the UI may change through PATCH /api/settings."""

    model_config = ConfigDict(extra="forbid")

    region: Literal[1, 2, 3] | None = None
    mains_hz: Literal[50, 60, "auto"] | None = None
    theme: Literal["dark", "light"] | None = None
    colormap: Literal["turbo", "viridis", "grey"] | None = None
    prebuffer_s: float | None = Field(None, ge=0, le=60)


def default_settings_path() -> Path:
    return Path(user_config_dir("qrm-hunter", appauthor=False)) / "settings.toml"


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_settings_path()
        # Set when load() fell back to defaults but the file is still in place, so save()
        # must not silently overwrite the user's settings (and access token) with defaults.
        self._unread_original = False

    def load(self) -> Settings:
        """Never fails: a missing, locked or corrupt file gives defaults (with a logged warning)."""
        try:
            data = tomllib.loads(self.path.read_text(encoding="utf-8"))
            return Settings.model_validate(data)
        except FileNotFoundError:
            return Settings()
        except (tomllib.TOMLDecodeError, ValidationError, UnicodeDecodeError) as exc:
            bad = self.path.with_name(self.path.name + ".bad")
            try:
                os.replace(self.path, bad)
            except OSError as move_exc:  # e.g. Windows: the file is open in an editor or AV scanner
                log.warning("settings file %s is invalid (%s) and could not be moved aside (%s); "
                            "using defaults", self.path, exc, move_exc)
                self._unread_original = True
            else:
                log.warning("settings file %s is invalid (%s); moved to %s, using defaults",
                            self.path, exc, bad)
            return Settings()
        except OSError as exc:  # PermissionError, sharing violation, I/O error
            log.warning("cannot read settings file %s (%s); using defaults", self.path, exc)
            self._unread_original = True
            return Settings()

    def save(self, settings: Settings) -> None:
        if self._unread_original and self.path.exists():
            bak = self.path.with_name(self.path.name + ".bak")
            try:
                shutil.copy2(self.path, bak)
            except OSError as exc:
                log.warning("settings file %s was never read and cannot be backed up (%s); "
                            "not overwriting it", self.path, exc)
                return
            log.warning("settings file %s was never read; backed up to %s before saving", self.path, bak)
        self._unread_original = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = tomli_w.dumps(settings.model_dump(exclude_none=True))
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

"""Persistent user settings stored as TOML in the per-user config directory."""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from platformdirs import user_config_dir
from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)


class Settings(BaseModel):
    region: Literal[1, 2, 3] = 1
    mains_hz: Literal[50, 60, "auto"] = "auto"
    theme: Literal["dark", "light"] = "dark"
    colormap: Literal["turbo", "viridis", "grey"] = "turbo"
    last_device_id: str | None = None
    last_tune_hz: int = 7_100_000
    token: str | None = None


class SettingsPatch(BaseModel):
    """Fields the UI may change through PATCH /api/settings."""

    model_config = ConfigDict(extra="forbid")

    region: Literal[1, 2, 3] | None = None
    mains_hz: Literal[50, 60, "auto"] | None = None
    theme: Literal["dark", "light"] | None = None
    colormap: Literal["turbo", "viridis", "grey"] | None = None


def default_settings_path() -> Path:
    return Path(user_config_dir("qrm-hunter", appauthor=False)) / "settings.toml"


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_settings_path()

    def load(self) -> Settings:
        try:
            data = tomllib.loads(self.path.read_text(encoding="utf-8"))
            return Settings.model_validate(data)
        except FileNotFoundError:
            return Settings()
        except (tomllib.TOMLDecodeError, ValidationError, UnicodeDecodeError) as exc:
            bad = self.path.with_name(self.path.name + ".bad")
            log.warning("settings file %s is unreadable (%s); moved to %s, using defaults", self.path, exc, bad)
            os.replace(self.path, bad)
            return Settings()

    def save(self, settings: Settings) -> None:
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

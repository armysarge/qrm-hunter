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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

log = logging.getLogger(__name__)

CALLSIGN_PATTERN = r"^[A-Za-z0-9/]*$"


def _upper(v: str | None) -> str | None:
    return v.upper() if isinstance(v, str) else v


class Settings(BaseModel):
    region: Literal[1, 2, 3] = 1
    mains_hz: Literal[50, 60, "auto"] = "auto"
    theme: Literal["dark", "light"] = "dark"
    colormap: Literal["turbo", "viridis", "grey"] = "turbo"
    last_device_id: str | None = None
    last_tune_hz: int = 7_100_000
    token: str | None = None
    prebuffer_s: float = Field(10.0, ge=0, le=60)
    recordings_dir: str = Field("", max_length=1000)  # "" = Documents/QRM Hunter/recordings
    callsign: str = Field("", max_length=12, pattern=CALLSIGN_PATTERN)

    _callsign_upper = field_validator("callsign")(_upper)


class SettingsPatch(BaseModel):
    """Fields the UI may change through PATCH /api/settings."""

    model_config = ConfigDict(extra="forbid")

    region: Literal[1, 2, 3] | None = None
    mains_hz: Literal[50, 60, "auto"] | None = None
    theme: Literal["dark", "light"] | None = None
    colormap: Literal["turbo", "viridis", "grey"] | None = None
    prebuffer_s: float | None = Field(None, ge=0, le=60)
    recordings_dir: str | None = Field(None, max_length=1000)
    callsign: str | None = Field(None, max_length=12, pattern=CALLSIGN_PATTERN)

    _callsign_upper = field_validator("callsign")(_upper)


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
            return self._validate_dropping_bad_keys(data)
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

    def _validate_dropping_bad_keys(self, data: dict) -> Settings:
        """Validates `data`; a key with an invalid value is reset to its default (with a warning)
        and every other key is kept. Raises ValidationError only if that cannot make it valid."""
        while True:
            try:
                return Settings.model_validate(data)
            except ValidationError as exc:
                bad = {err["loc"][0] for err in exc.errors() if err["loc"] and err["loc"][0] in data}
                if not bad:
                    raise
                for key in sorted(bad, key=str):
                    # The value itself is not logged: it may be the access token.
                    log.warning("settings file %s: invalid value for %r; using its default",
                                self.path, key)
                data = {k: v for k, v in data.items() if k not in bad}

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

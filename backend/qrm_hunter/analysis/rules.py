"""Explainable rule engine: fuzzy conditions on features, one weighted score per QRM class."""

from __future__ import annotations

import math
import string
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .features import FEATURE_IDS, Features

UNKNOWN_ID = "unknown"
UNKNOWN_THRESHOLD = 0.35
RAMP = 0.10  # membership ramps linearly to 0 over 10 % beyond each bound
CLASS_IDS: tuple[str, ...] = (
    "smps", "pv_inverter", "led_dimmer", "vdsl", "plc", "arcing", "plasma_tv",
    "pc_clock", "ethernet", "ev_charger", "electric_fence", "motor_brush", "vsd", "relay", UNKNOWN_ID,
)
UNKNOWN_INFO = {
    "name": "Unknown",
    "hunt": "general",
    "description": "No known signature matches well. Save a recording so it can be compared later.",
}
BUILTIN_PATH = Path(__file__).with_name("signatures.yaml")
MAX_USER_FILE_BYTES = 1_000_000  # a hand-written signatures file is a few kB


class SignatureError(ValueError):
    """A signatures file cannot be used. The message is shown to the user."""


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)  # NaN/inf bounds would poison scores

    feature: str
    between: tuple[float, float] | None = None
    gt: float | None = None
    lt: float | None = None
    near: tuple[float, float] | None = None  # [value, tolerance]
    weight: float = Field(1.0, gt=0)
    evidence: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Condition":
        if self.feature not in FEATURE_IDS:
            raise ValueError(f"unknown feature {self.feature!r}")
        tests = [t for t in ("between", "gt", "lt", "near") if getattr(self, t) is not None]
        if len(tests) != 1:
            raise ValueError(f"condition on {self.feature} needs exactly one of between/gt/lt/near")
        if self.between is not None and self.between[0] >= self.between[1]:
            raise ValueError(f"between bounds for {self.feature} must be [low, high]")
        if self.near is not None and self.near[1] <= 0:
            raise ValueError(f"near tolerance for {self.feature} must be positive")
        if self.evidence is not None and "__" in self.evidence:
            # str.format can walk attributes; dunder names would reach Python internals
            raise ValueError(f"evidence for {self.feature} must not contain '__'")
        return self


class Signature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    hunt: str = Field(min_length=1)
    description: str = Field(min_length=1)
    conditions: list[Condition] = Field(min_length=1)
    requires: list[str] = []

    @field_validator("requires")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        bad = [f for f in v if f not in FEATURE_IDS]
        if bad:
            raise ValueError(f"unknown required features {bad}")
        return v


class _SignatureFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signatures: list[Signature]

    @model_validator(mode="after")
    def _unique(self) -> "_SignatureFile":
        ids = [s.id for s in self.signatures]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate signature ids {dupes}")
        if UNKNOWN_ID in ids:
            raise ValueError("'unknown' is reserved")
        return self


def parse_signatures(text: str, source: str) -> list[Signature]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SignatureError(f"{source}: not valid YAML ({exc})") from exc
    except (RecursionError, MemoryError) as exc:
        raise SignatureError(f"{source}: nested too deeply or too large to read") from exc
    try:
        return _SignatureFile.model_validate(doc).signatures
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise SignatureError(f"{source}: {where}: {first['msg']}") from exc
    except (RecursionError, MemoryError) as exc:
        raise SignatureError(f"{source}: nested too deeply or too large to check") from exc


def user_signatures_path(settings_path: Path) -> Path:
    return Path(settings_path).with_name("signatures.yaml")


def load_signatures(user_path: Path | None) -> tuple[list[Signature], str | None]:
    """Built-in signatures, overridden or extended by id from the user's file. A user file that
    cannot be used is ignored and described in the returned warning."""
    builtin = parse_signatures(BUILTIN_PATH.read_text(encoding="utf-8"), "built-in signatures")
    if user_path is None or not Path(user_path).exists():
        return builtin, None
    try:
        size = Path(user_path).stat().st_size
        if size > MAX_USER_FILE_BYTES:
            raise SignatureError(f"{size} bytes, larger than the "
                                 f"{MAX_USER_FILE_BYTES // 1_000_000} MB limit")
        user = parse_signatures(Path(user_path).read_text(encoding="utf-8"), str(user_path))
    except (SignatureError, OSError, UnicodeDecodeError, RecursionError, MemoryError) as exc:
        return builtin, f"Ignoring {user_path}: {exc or type(exc).__name__}"
    merged = {s.id: s for s in builtin}
    for s in user:
        merged[s.id] = s
    return list(merged.values()), None


def _up(v: float, edge: float, width: float) -> float:
    if v >= edge:
        return 1.0
    if width <= 0 or v <= edge - width:
        return 0.0
    return (v - (edge - width)) / width


def _down(v: float, edge: float, width: float) -> float:
    if v <= edge:
        return 1.0
    if width <= 0 or v >= edge + width:
        return 0.0
    return ((edge + width) - v) / width


def membership(value: float | None, c: Condition) -> float:
    """1 inside the condition's bounds, falling linearly to 0 beyond them. gt/lt ramp over
    RAMP x |bound| (gt 10 is 0 at 9); between/near ramp over RAMP x the span (between 100-200
    is 0 at 90 and 210). A missing or non-finite value is 0."""
    if value is None or not math.isfinite(value):
        return 0.0
    if c.gt is not None:
        return _up(value, c.gt, RAMP * abs(c.gt))
    if c.lt is not None:
        return _down(value, c.lt, RAMP * abs(c.lt))
    lo, hi = c.between if c.between is not None else (c.near[0] - c.near[1], c.near[0] + c.near[1])
    w = RAMP * (hi - lo)
    return min(_up(value, lo, w), _down(value, hi, w))


@dataclass(frozen=True)
class RuleResult:
    id: str
    score: float
    evidence: list[str] = field(default_factory=list)


def score_signature(sig: Signature, features: Features) -> RuleResult:
    if any(not math.isfinite(features.get(f)) for f in sig.requires):
        return RuleResult(sig.id, 0.0, [])
    args = features.format_args()
    # an unmeasurable feature outside `requires` scores 0 for its condition (even lt) but its
    # weight stays in the total, so missing evidence lowers the score
    total = sum(c.weight for c in sig.conditions)
    score, evidence = 0.0, []
    for c in sig.conditions:
        m = membership(features.get(c.feature), c)
        score += c.weight * m
        if c.evidence and m >= 0.5:
            line = _evidence(c.evidence, features, args)
            if line is not None:
                evidence.append(line)
    return RuleResult(sig.id, score / total, evidence)


def _evidence(template: str, features: Features, args: dict) -> str | None:
    """The filled-in template; None when a feature it names cannot be measured (rather than
    showing "nan"); the template text itself when it cannot be filled in."""
    try:
        names = [f for _, f, _, _ in string.Formatter().parse(template) if f is not None]
        if any(f in FEATURE_IDS and not math.isfinite(features.get(f)) for f in names):
            return None
        return template.format(**args)
    except Exception:  # a user template may be malformed in any way str.format can object to
        return template


def apply_rules(signatures: list[Signature], features: Features) -> dict[str, RuleResult]:
    out = {s.id: score_signature(s, features) for s in signatures}
    top = max((r.score for r in out.values()), default=0.0)
    if top < UNKNOWN_THRESHOLD:
        out[UNKNOWN_ID] = RuleResult(UNKNOWN_ID, 1.0, [f"no known signature scored {UNKNOWN_THRESHOLD:.0%} or more"])
    else:
        out[UNKNOWN_ID] = RuleResult(UNKNOWN_ID, 0.0, [])
    return out


def ranked(results: dict[str, RuleResult]) -> list[RuleResult]:
    return sorted(results.values(), key=lambda r: (-r.score, r.id))

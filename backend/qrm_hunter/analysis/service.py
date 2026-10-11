"""The analysis pipeline: features -> rules -> ranker -> blend, as an explainable result."""

from __future__ import annotations

import math
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from pydantic import BaseModel

from .. import __version__
from .features import FEATURE_VERSION, AnalysisContext, Cancelled, extract_features
from .ranker import MODELS_DIR, Ranker, blend
from .rules import UNKNOWN_ID, UNKNOWN_INFO, Signature, apply_rules, load_signatures

ANALYSIS_TIMEOUT_S = 20.0


class AnalysisSource(BaseModel):
    kind: Literal["live", "recording"]
    center_hz: float
    sample_rate: float
    lo_hz: float
    hi_hz: float
    seconds: float
    recording_id: str | None = None


class Cause(BaseModel):
    id: str
    name: str
    score: float
    rule_score: float
    model_score: float | None
    evidence: list[str]
    hunt: str
    description: str
    beta: bool


class AnalysisResult(BaseModel):
    id: str
    created: str
    source: AnalysisSource
    causes: list[Cause]
    features: dict[str, float | None]
    timing_ms: dict[str, float]
    versions: dict[str, str]
    ranker: str


def make_check(event) -> Callable[[], None]:
    def check() -> None:
        if event.is_set():
            raise Cancelled("the analysis was stopped")
    return check


class Analyser:
    def __init__(self, signatures: list[Signature], ranker: Ranker | None, ranker_status: str) -> None:
        self.signatures = signatures
        self.ranker = ranker
        self.ranker_status = ranker_status

    @classmethod
    def create(cls, user_signatures: Path | None, models_dir: Path = MODELS_DIR) -> tuple["Analyser", str | None]:
        signatures, warning = load_signatures(user_signatures)
        ranker, status = Ranker.load(models_dir)
        return cls(signatures, ranker, status), warning

    def _predict(self, features) -> tuple[dict[str, float] | None, str]:
        """Model probabilities, or None and the reason when the model cannot be used."""
        if self.ranker is None:
            return None, self.ranker_status
        try:
            probs = self.ranker.predict(features)
        except Exception as exc:  # a failing model must never take the rules result down with it
            return None, f"ranker failed ({exc.__class__.__name__}), rules only"
        if not probs or not all(math.isfinite(p) for p in probs.values()):
            return None, "ranker returned invalid probabilities, rules only"
        return probs, self.ranker_status

    def analyse(self, iq: np.ndarray, rate: float, center_hz: float, ctx: AnalysisContext, *,
                kind: Literal["live", "recording"] = "live", lo_hz: float | None = None,
                hi_hz: float | None = None, recording_id: str | None = None,
                check: Callable[[], None] | None = None) -> AnalysisResult:
        check = check or (lambda: None)
        clock = time.perf_counter
        timing: dict[str, float] = {}
        t0 = clock()
        features = extract_features(iq, rate, center_hz, ctx, check)
        timing["features"] = (clock() - t0) * 1000
        check()
        t1 = clock()
        rules = apply_rules(self.signatures, features)
        timing["rules"] = (clock() - t1) * 1000
        check()
        t2 = clock()
        model, ranker_status = self._predict(features)
        timing["ranker"] = (clock() - t2) * 1000
        ranked = blend(rules, model)
        timing["total"] = (clock() - t0) * 1000
        info = {s.id: s for s in self.signatures}
        causes = []
        for c in ranked:
            sig = info.get(c.id)
            meta = UNKNOWN_INFO if c.id == UNKNOWN_ID or sig is None else {
                "name": sig.name, "hunt": sig.hunt, "description": sig.description}
            causes.append(Cause(id=c.id, score=c.score, rule_score=c.rule_score, model_score=c.model_score,
                                evidence=c.evidence, beta=c.model_score is not None, **meta))
        source = AnalysisSource(kind=kind, center_hz=center_hz, sample_rate=rate,
                                lo_hz=center_hz - rate / 2 if lo_hz is None else lo_hz,
                                hi_hz=center_hz + rate / 2 if hi_hz is None else hi_hz,
                                seconds=len(iq) / rate, recording_id=recording_id)
        model_version = "none" if self.ranker is None else self.ranker.version + ("" if model else " (not used)")
        return AnalysisResult(
            id=uuid.uuid4().hex, created=datetime.now(UTC).isoformat(timespec="seconds"), source=source,
            causes=causes, features=features.as_dict(), timing_ms=timing,
            versions={"engine": __version__, "features": str(FEATURE_VERSION),
                      "model": model_version},
            ranker=ranker_status,
        )


_default: Analyser | None = None
_default_lock = threading.Lock()


def default_analyser() -> Analyser:
    """The shared analyser with the built-in signatures and committed model, loaded once."""
    global _default
    with _default_lock:
        if _default is None:
            _default, _ = Analyser.create(None)
        return _default


def analyse(iq: np.ndarray, rate: float, center_hz: float, ctx: AnalysisContext,
            analyser: Analyser | None = None) -> AnalysisResult:
    return (analyser or default_analyser()).analyse(iq, rate, center_hz, ctx)

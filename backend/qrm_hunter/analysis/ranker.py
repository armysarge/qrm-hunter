"""Optional ONNX ranker (beta) and the rule/model blend."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .features import FEATURE_IDS, FEATURE_VERSION, Features
from .rules import RuleResult

MODELS_DIR = Path(__file__).with_name("models")
RULE_WEIGHT, MODEL_WEIGHT = 0.6, 0.4
PROBABILITIES = "probabilities"


class Ranker:
    def __init__(self, session, classes: tuple[str, ...], version: str) -> None:
        self._session = session
        self._input = session.get_inputs()[0].name
        self.classes = classes
        self.version = version

    @classmethod
    def load(cls, models_dir: Path = MODELS_DIR) -> tuple["Ranker | None", str]:
        models_dir = Path(models_dir)
        try:
            meta = json.loads((models_dir / "ranker.json").read_text(encoding="utf-8"))
            classes = tuple(meta["classes"])
            version = str(meta.get("trained", "?"))
            same_features = (meta.get("feature_version") == FEATURE_VERSION
                             and meta.get("features") == list(FEATURE_IDS))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            return None, f"no ranker model ({exc.__class__.__name__})"
        if not same_features:
            return None, "ranker model was trained on a different feature set"
        try:
            import onnxruntime as ort
        except ImportError:
            return None, "onnxruntime is not installed"
        try:
            session = ort.InferenceSession(str(models_dir / "ranker.onnx"), providers=["CPUExecutionProvider"])
            if PROBABILITIES not in [o.name for o in session.get_outputs()]:
                raise ValueError("no probabilities output")
        except Exception as exc:  # missing or corrupt file, unsupported opset, ...
            return None, f"ranker model could not be loaded ({exc})"
        return cls(session, classes, version), "ok"

    def predict(self, features: Features) -> dict[str, float]:
        x = features.to_vector()[None, :].astype(np.float32)
        # only the probabilities are requested: the label output's declared shape makes
        # onnxruntime warn on every run
        (probs,) = self._session.run([PROBABILITIES], {self._input: x})
        return {c: float(p) for c, p in zip(self.classes, np.asarray(probs)[0])}


@dataclass(frozen=True)
class RankedCause:
    id: str
    score: float
    rule_score: float
    model_score: float | None
    evidence: list[str] = field(default_factory=list)


def blend(rules: dict[str, RuleResult], model: dict[str, float] | None) -> list[RankedCause]:
    out = []
    for cid, r in rules.items():
        m = None if model is None else model.get(cid)
        score = r.score if m is None else RULE_WEIGHT * r.score + MODEL_WEIGHT * m
        out.append(RankedCause(cid, score, r.score, m, list(r.evidence)))
    return sorted(out, key=lambda c: (-c.score, c.id))

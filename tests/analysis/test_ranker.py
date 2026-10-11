import builtins
import json
import shutil

import pytest

from qrm_hunter.analysis.features import Features
from qrm_hunter.analysis.ranker import MODELS_DIR, Ranker, blend
from qrm_hunter.analysis.rules import CLASS_IDS, RuleResult


def rules(**scores):
    return {k: RuleResult(k, v, [f"{k} evidence"]) for k, v in scores.items()}


def test_blend_weights_rules_and_model():
    out = blend(rules(smps=0.5, vsd=0.9, unknown=0.0), {"smps": 1.0, "vsd": 0.0, "unknown": 0.0})
    assert [c.id for c in out] == ["smps", "vsd", "unknown"]
    assert out[0].score == pytest.approx(0.6 * 0.5 + 0.4 * 1.0)
    assert (out[0].rule_score, out[0].model_score, out[0].evidence) == (0.5, 1.0, ["smps evidence"])


def test_rules_only_when_there_is_no_model():
    out = blend(rules(smps=0.5, vsd=0.9), None)
    assert [c.id for c in out] == ["vsd", "smps"] and out[0].score == 0.9 and out[0].model_score is None


def test_a_class_unknown_to_the_model_keeps_its_rule_score():
    out = blend(rules(pond_pump=0.8, smps=0.5), {"smps": 1.0})
    pump = next(c for c in out if c.id == "pond_pump")
    assert pump.score == 0.8 and pump.model_score is None


def test_the_committed_model_loads_and_predicts():
    ranker, status = Ranker.load()
    assert status == "ok" and ranker.classes == CLASS_IDS
    probs = ranker.predict(Features(comb_spacing_hz=30_000.0, comb_count=8.0))
    assert set(probs) == set(CLASS_IDS) and sum(probs.values()) == pytest.approx(1.0, abs=1e-3)


def test_missing_onnxruntime_means_rules_only(monkeypatch):
    real = builtins.__import__

    def fake(name, *a, **kw):
        if name == "onnxruntime":
            raise ImportError("no onnxruntime")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    ranker, status = Ranker.load()
    assert ranker is None and status == "onnxruntime is not installed"


def test_a_model_for_other_features_is_skipped(tmp_path):
    shutil.copy(MODELS_DIR / "ranker.onnx", tmp_path / "ranker.onnx")
    meta = json.loads((MODELS_DIR / "ranker.json").read_text(encoding="utf-8"))
    meta["feature_version"] = -1
    (tmp_path / "ranker.json").write_text(json.dumps(meta), encoding="utf-8")
    ranker, status = Ranker.load(tmp_path)
    assert ranker is None and "different feature set" in status


def test_a_model_with_reordered_features_is_skipped(tmp_path):
    shutil.copy(MODELS_DIR / "ranker.onnx", tmp_path / "ranker.onnx")
    meta = json.loads((MODELS_DIR / "ranker.json").read_text(encoding="utf-8"))
    meta["features"] = meta["features"][::-1]
    (tmp_path / "ranker.json").write_text(json.dumps(meta), encoding="utf-8")
    ranker, status = Ranker.load(tmp_path)
    assert ranker is None and "different feature set" in status


def test_a_corrupt_model_file_is_reported(tmp_path):
    shutil.copy(MODELS_DIR / "ranker.json", tmp_path / "ranker.json")
    (tmp_path / "ranker.onnx").write_bytes(b"not a model")
    ranker, status = Ranker.load(tmp_path)
    assert ranker is None and status.startswith("ranker model could not be loaded")


def test_no_model_files(tmp_path):
    ranker, status = Ranker.load(tmp_path)
    assert ranker is None and status.startswith("no ranker model")

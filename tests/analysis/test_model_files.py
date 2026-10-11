import importlib.util
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort

from qrm_hunter.analysis.features import FEATURE_IDS, FEATURE_VERSION
from qrm_hunter.analysis.rules import CLASS_IDS

MODELS = Path(__file__).resolve().parents[2] / "backend" / "qrm_hunter" / "analysis" / "models"
TOOL = Path(__file__).resolve().parents[2] / "tools" / "train_ranker.py"


def test_model_metadata_matches_the_code():
    meta = json.loads((MODELS / "ranker.json").read_text(encoding="utf-8"))
    assert meta["classes"] == list(CLASS_IDS)
    assert meta["features"] == list(FEATURE_IDS)
    assert meta["feature_version"] == FEATURE_VERSION  # bumping the version needs a retrain
    assert meta["clips_per_class"] >= 300 and meta["trained"]


def test_model_is_small_and_runs():
    path = MODELS / "ranker.onnx"
    assert path.stat().st_size < 1_000_000
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    assert sess.get_inputs()[0].name == "features"
    x = np.full((2, len(FEATURE_IDS)), np.nan, np.float32)
    x[1] = 0.0
    probs = sess.run(["probabilities"], {"features": x})[0]
    assert probs.shape == (2, len(CLASS_IDS))
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-3)


def test_training_example_is_a_feature_vector():
    spec = importlib.util.spec_from_file_location("train_ranker", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # lightgbm is imported only inside main()
    vec, label = mod.make_example((CLASS_IDS.index("smps"), 7))
    assert vec.shape == (len(FEATURE_IDS),) and vec.dtype == np.float32
    assert label == CLASS_IDS.index("smps")

import json
import threading
import time

import numpy as np
import pytest

from qrm_hunter.analysis import service
from qrm_hunter.analysis.features import FEATURE_IDS, AnalysisContext, Cancelled, ShortInput
from qrm_hunter.analysis.service import AnalysisResult, Analyser, analyse, make_check
from qrm_hunter.sdr.synthetic import CLIP_CENTER_HZ, CLIP_RATE, render_profile_clip


def test_analyse_ranks_causes_and_reports_everything():
    iq = render_profile_clip("smps", 15.0)
    r = analyse(iq, CLIP_RATE, CLIP_CENTER_HZ, AnalysisContext())
    assert isinstance(r, AnalysisResult)
    assert r.causes[0].id == "smps" and r.causes[0].name.startswith("Switch-mode")
    assert r.causes[0].evidence and r.causes[0].hunt == "home-electronics"
    assert [c.score for c in r.causes] == sorted((c.score for c in r.causes), reverse=True)
    assert r.ranker == "ok" and all(c.beta for c in r.causes)
    assert set(r.features) == set(FEATURE_IDS)
    assert {"features", "rules", "ranker", "total"} <= set(r.timing_ms)
    assert set(r.versions) == {"engine", "features", "model"}
    assert r.source.kind == "live" and r.source.seconds == pytest.approx(5.0)
    json.loads(r.model_dump_json())  # NaN features are None, so this is valid JSON


def test_rules_only_when_the_ranker_is_missing():
    analyser, _ = Analyser.create(None)
    rules_only = Analyser(analyser.signatures, None, "onnxruntime is not installed")
    r = rules_only.analyse(render_profile_clip("vdsl", 15.0), CLIP_RATE, CLIP_CENTER_HZ, AnalysisContext())
    assert r.ranker == "onnxruntime is not installed"
    assert r.causes[0].id == "vdsl" and not any(c.beta for c in r.causes)
    assert all(c.model_score is None for c in r.causes)


class _BrokenRanker:
    version = "x"

    def __init__(self, behaviour):
        self.behaviour = behaviour

    def predict(self, features):
        if self.behaviour == "raise":
            raise RuntimeError("session exploded")
        return {"smps": float("nan"), "vdsl": 0.5}


@pytest.mark.parametrize("behaviour", ["raise", "nan"])
def test_a_failing_ranker_falls_back_to_rules_only(behaviour):
    base, _ = Analyser.create(None)
    analyser = Analyser(base.signatures, _BrokenRanker(behaviour), "ok")
    r = analyser.analyse(render_profile_clip("smps", 15.0), CLIP_RATE, CLIP_CENTER_HZ, AnalysisContext())
    assert r.ranker != "ok" and "rules only" in r.ranker
    assert r.causes[0].id == "smps" and not any(c.beta for c in r.causes)
    assert all(c.model_score is None for c in r.causes)


def test_the_default_analyser_is_loaded_once(monkeypatch):
    monkeypatch.setattr(service, "_default", None)
    calls = []
    real = Analyser.create

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(Analyser, "create", staticmethod(counting))
    threads = [threading.Thread(target=service.default_analyser) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert service.default_analyser() is service.default_analyser() and len(calls) == 1


def test_a_malformed_user_signature_file_gives_a_warning(tmp_path):
    bad = tmp_path / "signatures.yaml"
    bad.write_text("signatures: [[[", encoding="utf-8")
    analyser, warning = Analyser.create(bad)
    assert warning and "signatures.yaml" in warning and analyser.signatures


def test_short_input_and_cancellation():
    with pytest.raises(ShortInput):
        analyse(np.zeros(1000, np.complex64), CLIP_RATE, CLIP_CENTER_HZ, AnalysisContext())
    ev = threading.Event()
    ev.set()
    analyser, _ = Analyser.create(None)
    with pytest.raises(Cancelled):
        analyser.analyse(render_profile_clip(None, seconds=2.5), CLIP_RATE, CLIP_CENTER_HZ, AnalysisContext(),
                         check=make_check(ev))


@pytest.mark.performance
def test_ten_seconds_at_200_khz_takes_under_three_seconds():
    iq = render_profile_clip("smps", 15.0, seconds=10.0, rate=200_000.0)
    analyser, _ = Analyser.create(None)
    t0 = time.perf_counter()
    analyser.analyse(iq, 200_000.0, CLIP_CENTER_HZ, AnalysisContext())
    assert time.perf_counter() - t0 < 3.0

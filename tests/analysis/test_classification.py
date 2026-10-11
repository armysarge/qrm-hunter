"""Classification matrix: every synthetic profile at three strengths must rank its own class first
with the rules alone; pure noise must come out as Unknown."""

from functools import lru_cache

import pytest

from qrm_hunter.analysis.features import AnalysisContext, extract_features
from qrm_hunter.analysis.ranker import Ranker, blend
from qrm_hunter.analysis.rules import UNKNOWN_ID, apply_rules, load_signatures, ranked
from qrm_hunter.sdr.synthetic import CLIP_CENTER_HZ, CLIP_RATE, PROFILE_IDS, render_profile_clip

STRENGTHS = (6.0, 15.0, 25.0)
MIN_MARGIN = 0.1  # own class over the runner-up; the closest measured gap is about 0.17
SIGNATURES, _ = load_signatures(None)


@lru_cache(maxsize=None)
def features(name, strength, center=CLIP_CENTER_HZ):
    iq = render_profile_clip(name, strength, center_hz=center)
    return extract_features(iq, CLIP_RATE, center, AnalysisContext())


@pytest.mark.parametrize("strength", STRENGTHS)
@pytest.mark.parametrize("name", PROFILE_IDS)
def test_rules_rank_the_profiles_own_class_first(name, strength):
    order = ranked(apply_rules(SIGNATURES, features(name, strength)))
    top3 = [(r.id, round(r.score, 2)) for r in order[:3]]
    assert order[0].id == name, top3
    assert order[0].score - order[1].score >= MIN_MARGIN, top3


def test_pure_noise_is_unknown():
    assert ranked(apply_rules(SIGNATURES, features(None, 0.0)))[0].id == UNKNOWN_ID


@pytest.mark.parametrize("name", ["smps", "arcing", "vdsl"])
def test_classification_holds_at_vhf(name):
    order = ranked(apply_rules(SIGNATURES, features(name, 15.0, 145_000_000.0)))
    assert order[0].id == name


RANKER, RANKER_STATUS = Ranker.load()


def blended(name, strength):
    f = features(name, strength)
    return blend(apply_rules(SIGNATURES, f), RANKER.predict(f))


@pytest.mark.parametrize("strength", STRENGTHS)
@pytest.mark.parametrize("name", PROFILE_IDS)
def test_blend_ranks_the_profiles_own_class_in_the_top_two(name, strength):
    assert RANKER_STATUS == "ok"
    top2 = [c.id for c in blended(name, strength)[:2]]
    assert name in top2, top2


def test_pure_noise_is_unknown_with_the_blend():
    assert blended(None, 0.0)[0].id == UNKNOWN_ID

import math

import pytest

from qrm_hunter.analysis.features import Features
from qrm_hunter.analysis.rules import (
    CLASS_IDS,
    UNKNOWN_ID,
    Condition,
    Signature,
    SignatureError,
    apply_rules,
    load_signatures,
    membership,
    parse_signatures,
    ranked,
    score_signature,
    user_signatures_path,
)
from qrm_hunter.sdr.synthetic import PROFILE_IDS


def cond(**kw):
    return Condition(feature=kw.pop("feature", "comb.spacing_hz"), **kw)


@pytest.mark.parametrize("c, value, expected", [
    (cond(gt=10), 10, 1.0), (cond(gt=10), 9.5, 0.5), (cond(gt=10), 9, 0.0), (cond(gt=10), 50, 1.0),
    (cond(lt=10), 10.5, 0.5), (cond(lt=10), 11, 0.0), (cond(lt=10), -5, 1.0),
    (cond(between=(100, 200)), 95, 0.5), (cond(between=(100, 200)), 150, 1.0), (cond(between=(100, 200)), 210, 0.0),
    (cond(near=(4312.5, 86.25)), 4312.5 + 86.25 + 8.625, 0.5), (cond(near=(4312.5, 86.25)), 4312.5, 1.0),
    (cond(gt=0), 0.0, 1.0), (cond(gt=0), -0.001, 0.0), (cond(gt=10), math.nan, 0.0),
])
def test_membership_has_linear_ramps_ten_percent_beyond_the_bounds(c, value, expected):
    assert membership(value, c) == pytest.approx(expected)


def test_a_condition_needs_exactly_one_known_test():
    with pytest.raises(ValueError):
        Condition(feature="comb.spacing_hz")
    with pytest.raises(ValueError):
        Condition(feature="comb.spacing_hz", gt=1, lt=2)
    with pytest.raises(ValueError):
        Condition(feature="toaster.watts", gt=1)
    with pytest.raises(ValueError):
        Condition(feature="comb.spacing_hz", between=(5, 1))


SIG = Signature(id="demo", name="Demo", hunt="general", description="d", requires=["comb.spacing_hz"],
                conditions=[cond(gt=1000, weight=3, evidence="lines every {comb.spacing_hz:.0f} Hz"),
                            cond(feature="mains.depth", gt=0.5, weight=1, evidence="{mains.depth:.0%} deep")])


def test_score_is_the_weighted_mean_with_evidence_for_met_conditions():
    r = score_signature(SIG, Features(comb_spacing_hz=2000.0, mains_depth=0.1))
    assert r.score == pytest.approx(0.75)
    assert r.evidence == ["lines every 2000 Hz"]


def test_unmeasurable_required_feature_scores_zero():
    assert score_signature(SIG, Features(mains_depth=0.9)).score == 0.0


def test_a_bad_evidence_template_falls_back_to_its_text():
    sig = SIG.model_copy(update={"conditions": [cond(gt=1, evidence="{nope.x}")]})
    assert score_signature(sig, Features(comb_spacing_hz=5.0)).evidence == ["{nope.x}"]


def test_unknown_wins_only_below_the_threshold():
    low = apply_rules([SIG], Features(comb_spacing_hz=500.0, mains_depth=0.9))
    assert low["demo"].score < 0.35 and low[UNKNOWN_ID].score == 1.0
    assert ranked(low)[0].id == UNKNOWN_ID
    high = apply_rules([SIG], Features(comb_spacing_hz=5000.0))
    assert high[UNKNOWN_ID].score == 0.0 and ranked(high)[0].id == "demo"


def test_builtin_signatures_cover_every_class():
    sigs, warning = load_signatures(None)
    assert warning is None
    assert [s.id for s in sigs] == list(CLASS_IDS[:-1])
    assert CLASS_IDS == (*PROFILE_IDS, UNKNOWN_ID)
    assert all(s.name and s.hunt and s.description for s in sigs)


def test_user_file_overrides_and_extends_by_id(tmp_path):
    path = user_signatures_path(tmp_path / "settings.toml")
    assert path == tmp_path / "signatures.yaml"
    path.write_text("""
signatures:
  - id: smps
    name: My charger
    hunt: home-electronics
    description: overridden
    conditions: [{feature: comb.count, gt: 4}]
  - id: neighbour_pump
    name: Pond pump
    hunt: motor
    description: added
    conditions: [{feature: imp.rate_hz, between: [90, 110]}]
""", encoding="utf-8")
    sigs, warning = load_signatures(path)
    assert warning is None
    by_id = {s.id: s for s in sigs}
    assert by_id["smps"].name == "My charger" and "neighbour_pump" in by_id
    assert len(sigs) == len(CLASS_IDS)


@pytest.mark.parametrize("text", ["signatures: [[[", "signatures: [{id: x}]",
                                  "signatures:\n  - {id: a, name: A, hunt: h, description: d, conditions: [{feature: nope, gt: 1}]}",
                                  "signatures:\n  - {id: a, name: A, hunt: h, description: d, conditions: [{feature: imp.rate_hz, gt: 1}]}\n"
                                  "  - {id: a, name: A, hunt: h, description: d, conditions: [{feature: imp.rate_hz, gt: 1}]}"])
def test_malformed_user_file_is_reported_and_ignored(tmp_path, text):
    path = tmp_path / "signatures.yaml"
    path.write_text(text, encoding="utf-8")
    sigs, warning = load_signatures(path)
    assert warning and str(path) in warning
    assert [s.id for s in sigs] == list(CLASS_IDS[:-1])
    with pytest.raises(SignatureError):
        parse_signatures(text, "x")


def test_an_unmeasured_optional_feature_scores_zero_but_keeps_its_weight():
    sig = SIG.model_copy(update={"conditions": [cond(gt=1000, weight=3), cond(feature="mains.depth", lt=0.5, weight=1)]})
    assert membership(math.nan, cond(feature="mains.depth", lt=0.5)) == 0.0
    assert score_signature(sig, Features(comb_spacing_hz=2000.0)).score == pytest.approx(0.75)


def test_evidence_naming_an_unmeasured_feature_is_left_out():
    sig = SIG.model_copy(update={"conditions": [cond(gt=1, evidence="{comb.spacing_hz:.0f} Hz, {mains.depth:.0%} deep")]})
    assert score_signature(sig, Features(comb_spacing_hz=5.0)).evidence == []
    assert score_signature(sig, Features(comb_spacing_hz=5.0, mains_depth=0.5)).evidence == ["5 Hz, 50% deep"]


@pytest.mark.parametrize("template", ["{comb[0]}", "{comb.spacing_hz:q}", "{", "{0}"])
def test_any_unformattable_evidence_falls_back_to_its_text(template):
    sig = SIG.model_copy(update={"conditions": [cond(gt=1, evidence=template)]})
    assert score_signature(sig, Features(comb_spacing_hz=5.0)).evidence == [template]


def test_evidence_may_not_reach_dunder_attributes():
    with pytest.raises(ValueError):
        cond(gt=1, evidence="{comb.__class__}")


def test_non_finite_bounds_are_rejected():
    with pytest.raises(ValueError):
        cond(gt=math.inf)
    with pytest.raises(ValueError):
        cond(between=(math.nan, 1.0))


def test_an_oversized_user_file_is_ignored_unread(tmp_path):
    path = tmp_path / "signatures.yaml"
    path.write_text("#" * 1_000_001, encoding="utf-8")
    sigs, warning = load_signatures(path)
    assert warning and str(path) in warning and "MB" in warning
    assert [s.id for s in sigs] == list(CLASS_IDS[:-1])


def test_a_deeply_nested_user_file_is_ignored(tmp_path):
    path = tmp_path / "signatures.yaml"
    path.write_text("signatures: " + "[" * 100_000 + "]" * 100_000, encoding="utf-8")
    sigs, warning = load_signatures(path)
    assert warning and str(path) in warning
    assert [s.id for s in sigs] == list(CLASS_IDS[:-1])

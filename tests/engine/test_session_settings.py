"""Changing one radio setting must not quietly reset another."""

from dataclasses import replace

import pytest

from qrm_hunter.engine.session import RadioSession
from qrm_hunter.engine.state import StatePatch
from qrm_hunter.sdr.synthetic import SyntheticDevice

HF_CEILING_HZ = 28_800_000.0


class DirectSamplingDevice(SyntheticDevice):
    """Has an HF input, which (like an RTL-SDR) cannot be used above 28.8 MHz."""

    def __init__(self):
        super().__init__(realtime=False)
        self.info = replace(self.info, has_direct_sampling=True)

    def configure(self, cfg):
        if cfg.center_hz > HF_CEILING_HZ:
            cfg = replace(cfg, direct_sampling="off")
        return super().configure(cfg)


@pytest.fixture
def session():
    s = RadioSession(lambda _id: DirectSamplingDevice())
    yield s
    s.close()


def test_mode_switch_brings_back_each_modes_bandwidth(session):
    session.open("synthetic", StatePatch(mode="USB", bw_hz=2400.0))
    assert session.update(StatePatch(mode="CW")).bw_hz == 500.0  # never used: the default
    session.update(StatePatch(bw_hz=300.0))
    assert session.update(StatePatch(mode="USB")).bw_hz == 2400.0
    assert session.update(StatePatch(mode="CW")).bw_hz == 300.0


def test_same_mode_again_keeps_the_bandwidth(session):
    session.open("synthetic", StatePatch(mode="AM", bw_hz=8000.0))
    assert session.update(StatePatch(mode="AM")).bw_hz == 8000.0


def test_mode_switch_leaves_frequency_and_gain_alone(session):
    session.open("synthetic", StatePatch(tune_hz=145_000_000.0, gain_db=20.0))
    s = session.update(StatePatch(mode="FM"))
    assert (s.tune_hz, s.gain_db) == (145_000_000.0, 20.0)


def test_hf_input_returns_after_a_trip_above_hf(session):
    session.open("synthetic", StatePatch(tune_hz=7_100_000.0, direct_sampling="q"))
    assert session.state.direct_sampling == "q"
    assert session.update(StatePatch(tune_hz=145_000_000.0)).direct_sampling == "off"
    assert session.update(StatePatch(tune_hz=7_100_000.0)).direct_sampling == "q"


def test_hf_input_choice_survives_a_restart(session):
    session.open("synthetic", StatePatch(tune_hz=7_100_000.0, direct_sampling="i"))
    session.update(StatePatch(tune_hz=145_000_000.0))
    session.close()
    assert session.open("synthetic", StatePatch(tune_hz=7_100_000.0)).direct_sampling == "i"

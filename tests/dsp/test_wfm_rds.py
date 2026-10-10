"""The full broadcast chain: RDS encoder -> MPX -> FM -> complex baseband with noise -> WFM
demodulator -> RDS decoder."""

from datetime import UTC, datetime

import numpy as np
import pytest

from qrm_hunter.dsp.demod import Demodulator
from qrm_hunter.dsp.rds import RdsDecoder
from qrm_hunter.sdr.rds_encoder import DemoStation

SECONDS = 5.0


def run_chain(rate, offset, noise, sizes, seconds=SECONDS, seed=0):
    """Returns the decoder and the time (s) each field first reached its expected value."""
    station = DemoStation(ta_period_s=3.0, ta_on_s=1.5,
                          clock_start=datetime(2026, 10, 10, 10, 0, tzinfo=UTC))
    rng = np.random.default_rng(seed)
    demod = Demodulator(rate, "WFM", offset_hz=offset)
    dec = RdsDecoder(demod.mpx_rate)
    want = {"ps": "QRM DEMO", "rt": station.rt, "pi": "6201",
            "rtplus": {"title": "Noise Floor Blues", "artist": "ZS6SHZ"}}
    first: dict[str, float] = {}
    ta_values = set()
    n_total, n0, k = int(rate * seconds), 0, 0
    gen = 65_536  # the station renders in fixed chunks; the receiver gets `sizes`
    pending = np.empty(0, np.complex64)
    while n0 < n_total:
        n = min(sizes[k % len(sizes)], n_total - n0)
        k += 1
        while pending.size < n:
            start = n0 + pending.size
            x = 0.1 * station.iq(start, gen, rate, offset)
            x += (np.float32(noise) * rng.standard_normal(2 * gen, dtype=np.float32)).view(np.complex64)
            pending = np.concatenate([pending, x])
        demod.process(pending[:n])
        pending = pending[n:]
        dec.process(demod.mpx)
        n0 += n
        for field, value in want.items():
            if field not in first and getattr(dec.state, field) == value:
                first[field] = n0 / rate
        if dec.state.synced:
            ta_values.add(dec.state.ta)
    return dec, first, ta_values


@pytest.mark.parametrize("rate,offset,sizes", [
    (1.024e6, 150_000.0, [16_384]),
    (2.048e6, -310_000.0, [32_768, 7, 100_001, 4096, 1, 55_555]),  # irregular block sizes
])
def test_station_decodes_ps_rt_rtplus_and_ta_within_seconds(rate, offset, sizes):
    # noise sigma 0.01 per component against a 0.1 carrier: about 24 dB C/N in 200 kHz at 1 MS/s
    dec, first, ta_values = run_chain(rate, offset, 0.01 * np.sqrt(rate / 1.024e6), sizes)
    assert set(first) == {"ps", "rt", "pi", "rtplus"}
    assert max(first.values()) < 4.0
    assert ta_values == {True, False}  # TA came on and went off again
    s = dec.state
    assert (s.tp, s.pty, s.pty_name, s.stereo, s.synced) == (True, 10, "Pop Music", True, True)
    assert s.ct is not None and s.ct.startswith("2026-10-10T12:00") and s.ct.endswith("+02:00")

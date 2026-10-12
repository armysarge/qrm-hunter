import threading
import time

import pytest

from qrm_hunter.analysis.features import AnalysisContext
from qrm_hunter.analysis.service import Analyser
from qrm_hunter.dsp.extract import extract_span
from qrm_hunter.engine.session import RadioSession
from qrm_hunter.sdr.synthetic import SyntheticDevice


@pytest.mark.performance
def test_live_view_stays_at_23_fps_while_analysing():
    session = RadioSession(lambda _id: SyntheticDevice(realtime=True), prebuffer_s=12.0)
    analyser, _ = Analyser.create(None)
    session.open("synthetic")
    try:
        deadline = time.monotonic() + 20
        while (snap := session.snapshot_iq()) is None or snap.seconds < 10.0:
            assert time.monotonic() < deadline, "pre-buffer did not fill"
            time.sleep(0.1)
        c = snap.center_hz
        ex = extract_span(snap.iq[-int(10 * snap.sample_rate):], snap.sample_rate, c, c - 80_000, c + 80_000)
        assert ex.rate == pytest.approx(200_000, rel=0.05)
        dropped_before = session.stats.dropped
        done = threading.Event()

        def work():
            try:
                for _ in range(3):
                    analyser.analyse(ex.iq, ex.rate, ex.center_hz, AnalysisContext())
            finally:
                done.set()

        worker = threading.Thread(target=work)
        worker.start()
        time.sleep(1.2)  # the frame rate is measured over one-second windows
        rates = []
        while not done.is_set():
            rates.append(session.stats.frame_rate)
            time.sleep(0.25)
        worker.join()
        assert rates, "the analyses finished before the first measurement"
        assert min(rates) >= 23.0, rates
        assert session.stats.dropped == dropped_before
    finally:
        session.close()

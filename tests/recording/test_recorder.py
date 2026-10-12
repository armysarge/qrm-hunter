import dataclasses
import json
import threading

import numpy as np
import pytest
from helpers import join_tap_threads, wait_until
from qrm_hunter.dsp.extract import RegionError, extract_span
from qrm_hunter.engine.session import RadioSession
from qrm_hunter.engine.state import StatePatch
from qrm_hunter.recording import recorder as recorder_mod
from qrm_hunter.recording.recorder import (
    AlreadyRecording,
    LowDisk,
    NotRecording,
    NotRunning,
    PreBufferOff,
    Recorder,
    RecordingContext,
    ShortBuffer,
)
from qrm_hunter.recording.store import RecordingStore
from qrm_hunter.sdr.base import DeviceError
from qrm_hunter.sdr.sigmf_io import load_iq, open_sigmf
from qrm_hunter.sdr.synthetic import SyntheticDevice

GB = 1024 ** 3
RIGS = []


class Rig:
    def __init__(self, tmp_path, device=None, free=10 * GB, max_s=1800.0, prebuffer_s=10.0):
        self.session = RadioSession(lambda _id: device or SyntheticDevice(realtime=True), prebuffer_s=prebuffer_s)
        RIGS.append(self)
        self.store = RecordingStore(tmp_path / "rec")
        self.events = []
        self.free = free
        self.recorder = Recorder(self.session, lambda: self.store,
                                 lambda: RecordingContext({"band": "40m", "device": "synthetic"}, "turbo"),
                                 self.events.append, free_bytes=lambda _p: self.free, max_s=max_s)

    def stopped_event(self, timeout=10.0):
        ev = wait_until(lambda: next((e for e in self.events if e["type"] == "recording_stopped"), None), timeout)
        if ev is None:
            raise AssertionError("no recording_stopped event")
        return ev

    def meta(self, rec_id):
        return json.loads((self.store.path(rec_id) / "iq.sigmf-meta").read_text(encoding="utf-8"))["global"]

    def recorded_s(self):
        return self.recorder.status()["bytes"] / 4 / self.session.state.sample_rate


@pytest.fixture(autouse=True)
def _close_every_rig():
    """Every radio a test opens is closed afterwards, even if the test fails midway."""
    yield
    while RIGS:
        RIGS.pop().session.close()
    assert join_tap_threads() == []


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def wait_buffer(session, seconds):
    def filled():
        snap = session.snapshot_iq()
        return snap is not None and snap.seconds >= seconds

    if not wait_until(filled, seconds + 10):
        raise AssertionError("pre-buffer did not fill")


def test_snapshot_writes_a_complete_recording(rig):
    rig.session.open("synthetic")
    wait_buffer(rig.session, 2.2)
    c = rig.session.state.center_hz
    info = rig.recorder.snapshot(2.1, c - 20_000, c + 20_000)
    # a 40 kHz region needs 50 kHz (1.25 x width); 2.048 MS/s / 40 gives 51.2 kHz
    assert info.duration_s == pytest.approx(2.1, abs=0.05) and 50_000 <= info.sample_rate < 55_000
    assert info.center_hz == c and info.end_reason == "snapshot" and not info.truncated
    folder = rig.store.path(info.id)
    assert {p.name for p in folder.iterdir()} >= {"iq.sigmf-meta", "iq.sigmf-data", "waterfall.png", "spectrum.json"}
    assert rig.meta(info.id)["qrm_hunter:radio_center_hz"] == c
    assert {"type": "recordings"} in rig.events


def test_snapshot_errors(rig, tmp_path):
    with pytest.raises(ShortBuffer):
        rig.recorder.snapshot(5.0)  # never opened: nothing buffered
    rig.session.open("synthetic")
    wait_buffer(rig.session, 0.1)
    with pytest.raises(ShortBuffer, match="at least 2 s"):
        rig.recorder.snapshot(5.0)
    with pytest.raises(RegionError):
        rig.recorder.snapshot(5.0, 1e9, 1.1e9)
    off = Rig(tmp_path / "off", prebuffer_s=0)
    off.session.open("synthetic")
    with pytest.raises(PreBufferOff):
        off.recorder.snapshot(5.0)


def test_a_buffer_from_the_previous_tuning_is_not_saved(rig, monkeypatch):
    rig.session.open("synthetic")
    wait_buffer(rig.session, 2.2)
    real = rig.session.snapshot_iq
    # what the buffer returns between a retune and the first block of the new tuning
    monkeypatch.setattr(rig.session, "snapshot_iq",
                        lambda seconds=None: dataclasses.replace(real(seconds), gen=real(seconds).gen - 1))
    with pytest.raises(ShortBuffer):
        rig.recorder.snapshot(2.1)
    rig.recorder.start(lead_in_s=2.0)  # starts without the stale lead-in
    assert wait_until(lambda: rig.recorded_s() > 0.2)
    info = rig.recorder.stop()
    assert info.duration_s < 2.0
    assert rig.recorder.last_first_index >= real().end_index - real().iq.size


def test_start_stop_matches_a_snapshot_of_the_same_data(rig):
    rig.session.open("synthetic")
    wait_buffer(rig.session, 0.5)
    rig.recorder.start(lead_in_s=0.5)
    assert wait_until(lambda: rig.recorded_s() > 1.5)
    info = rig.recorder.stop()
    first = rig.recorder.last_first_index
    full = rig.session.snapshot_iq()
    start = first - (full.end_index - full.iq.size)
    recorded = load_iq(open_sigmf(rig.store.path(info.id) / "iq.sigmf-meta"))
    assert start >= 0 and recorded.size > 0.5 * rig.session.state.sample_rate
    assert np.array_equal(recorded, full.iq[start:start + recorded.size])
    assert info.end_reason == "stopped" and not info.truncated


def test_a_region_recording_is_continuous(rig):
    rig.session.open("synthetic")
    wait_buffer(rig.session, 0.5)
    c = rig.session.state.center_hz
    rig.recorder.start(c - 30_000, c + 30_000, lead_in_s=0.5)
    assert wait_until(lambda: rig.recorder.status()["elapsed_s"] > 0.8)
    info = rig.recorder.stop()
    full = rig.session.snapshot_iq()
    start = rig.recorder.last_first_index - (full.end_index - full.iq.size)
    raw_len = round(info.duration_s * rig.session.state.sample_rate)
    expected = extract_span(full.iq[start:start + raw_len], full.sample_rate, full.center_hz, c - 30_000, c + 30_000)
    recorded = load_iq(open_sigmf(rig.store.path(info.id) / "iq.sigmf-meta"))
    n = min(recorded.size, expected.iq.size)
    assert n > 0 and np.max(np.abs(recorded[:n] - expected.iq[:n])) < 2e-4


def test_progress_events_and_status(rig):
    rig.session.open("synthetic")
    rig.recorder.start(lead_in_s=0)
    assert rig.recorder.status()["recording"]

    def progress():
        return [e for e in rig.events if e["type"] == "recording"]

    assert wait_until(lambda: progress() and progress()[-1]["elapsed_s"] >= 1.0)
    elapsed = rig.recorder.status()["elapsed_s"]
    rig.recorder.stop()
    p = progress()
    assert 1 <= len(p) <= elapsed + 1 and p[-1]["bytes"] > 0
    assert not rig.recorder.status()["recording"]
    assert rig.stopped_event()["reason"] == "stopped"


def test_elapsed_is_the_recorded_duration_including_the_lead_in(rig):
    rig.session.open("synthetic")
    wait_buffer(rig.session, 1.2)
    status = rig.recorder.start(lead_in_s=1.0)
    assert status["elapsed_s"] >= 0.9  # the lead-in is already in the file
    assert status["elapsed_s"] == pytest.approx(status["bytes"] / 4 / rig.session.state.sample_rate)

    def progress():
        return [e for e in rig.events if e["type"] == "recording"]

    assert wait_until(lambda: progress())
    ev = progress()[-1]
    assert ev["elapsed_s"] >= 1.9  # lead-in plus at least one second of live data
    assert ev["elapsed_s"] == pytest.approx(ev["bytes"] / 4 / rig.session.state.sample_rate, abs=0.05)
    rig.recorder.stop()


def test_one_recording_at_a_time_and_stop_when_idle(rig):
    with pytest.raises(NotRunning):
        rig.recorder.start()
    rig.session.open("synthetic")
    rig.recorder.start(lead_in_s=0)
    with pytest.raises(AlreadyRecording):
        rig.recorder.start()
    rig.recorder.stop()
    with pytest.raises(NotRecording):
        rig.recorder.stop()
    rig.recorder.start(lead_in_s=0)  # a new recording after the first one ended
    assert rig.recorder.stop().end_reason == "stopped"


def test_a_tap_still_finishing_reads_as_already_recording(rig):
    rig.session.open("synthetic")
    tap = rig.session.open_tap(lambda *block: None, lambda reason: None)
    try:
        with pytest.raises(AlreadyRecording):
            rig.recorder.start(lead_in_s=0)
    finally:
        rig.session.close_tap(tap)


def test_low_disk_refuses_to_start(tmp_path):
    rig = Rig(tmp_path, free=100 * 1024 ** 2)
    rig.session.open("synthetic")
    with pytest.raises(LowDisk, match="500 MB"):
        rig.recorder.start()
    with pytest.raises(LowDisk):
        rig.recorder.snapshot(1.0)
    assert rig.store.list() == []
    assert join_tap_threads() == []


def test_low_disk_during_recording_stops_it(tmp_path, monkeypatch):
    monkeypatch.setattr("qrm_hunter.recording.recorder.DISK_CHECK_S", 0.2)
    rig = Rig(tmp_path)
    rig.session.open("synthetic")
    rig.recorder.start(lead_in_s=0)
    assert wait_until(lambda: rig.recorder.status()["bytes"] > 0)
    rig.free = 1
    ev = rig.stopped_event()
    assert ev["reason"] == "low_disk" and ev["recording"]["truncated"] is True


@pytest.mark.parametrize("lead_in_s", [0, 0.3, 2.0])
def test_the_cap_ends_the_recording_and_counts_the_lead_in(tmp_path, lead_in_s):
    rig = Rig(tmp_path, max_s=0.5)
    rig.session.open("synthetic")
    wait_buffer(rig.session, lead_in_s)
    rig.recorder.start(lead_in_s=lead_in_s)
    ev = rig.stopped_event()
    assert ev["reason"] == "cap" and ev["recording"]["truncated"] is False
    assert 0.45 <= ev["recording"]["duration_s"] <= 0.5


def test_device_loss_finalises_a_truncated_file(tmp_path):
    unplug = threading.Event()  # set once recording, so the loss cannot beat start()

    class Failing(SyntheticDevice):
        def read(self, n):
            if unplug.is_set():
                raise DeviceError("unplugged")
            return super().read(n)

    rig = Rig(tmp_path, device=Failing(realtime=True))
    rig.session.open("synthetic")
    rig.recorder.start(lead_in_s=0)
    assert wait_until(lambda: rig.recorder.status()["bytes"] > 0)
    unplug.set()
    ev = rig.stopped_event()
    assert ev["reason"] == "device_lost"
    info = rig.store.info(ev["recording"]["id"])
    assert info.truncated and info.duration_s > 0
    assert (rig.store.path(info.id) / "waterfall.png").exists()


def test_retune_ends_the_recording_cleanly(rig):
    rig.session.open("synthetic")
    rig.recorder.start(lead_in_s=0)
    assert wait_until(lambda: rig.recorder.status()["bytes"] > 0)
    rig.session.update(StatePatch(center_hz=9_000_000.0))
    ev = rig.stopped_event()
    assert ev["reason"] == "retuned" and ev["recording"]["truncated"] is False
    assert open_sigmf(rig.store.path(ev["recording"]["id"]) / "iq.sigmf-meta").center_hz != 9_000_000.0


def test_closing_the_radio_finalises_the_recording(rig):
    rig.session.open("synthetic")
    rig.recorder.start(lead_in_s=0)
    assert wait_until(lambda: rig.recorder.status()["bytes"] > 0)
    rig.session.close()
    ev = rig.stopped_event()
    assert ev["reason"] == "closed"
    meta = rig.meta(ev["recording"]["id"])
    assert meta["qrm_hunter:truncated"] is True and meta["qrm_hunter:end_reason"] == "closed"
    assert join_tap_threads() == []


def test_a_failed_start_leaves_no_folder_and_no_tap(rig, monkeypatch):
    rig.session.open("synthetic")

    def broken_writer(*args, **kwargs):
        raise OSError("disk unplugged")

    monkeypatch.setattr(recorder_mod, "SigmfWriter", broken_writer)
    with pytest.raises(OSError, match="disk unplugged"):
        rig.recorder.start(lead_in_s=0)
    assert join_tap_threads() == []
    assert list(rig.store.root.iterdir()) == []  # the empty folder is gone
    assert not rig.recorder.status()["recording"]
    monkeypatch.undo()
    rig.recorder.start(lead_in_s=0)
    assert rig.recorder.stop().end_reason == "stopped"

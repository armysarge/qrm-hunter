"""RDS encoder and a demo FM broadcast station, for the synthetic device and the tests.

Groups -> 26-bit blocks with check words -> differential coding -> biphase chips -> 2.4 kHz
shaping -> 57 kHz double-sideband suppressed-carrier subcarrier, locked to 3x the 19 kHz pilot,
added to a mono programme and the pilot to form the MPX, which then frequency-modulates the carrier.
"""

from __future__ import annotations

import cmath
import math
from datetime import UTC, datetime, timedelta, timezone

import numpy as np
from scipy.signal import firwin, lfilter

from ..dsp.rds import BITRATE, CHARSET, MJD_EPOCH, PILOT_HZ, RTPLUS_AID, encode_block

GROUP_BITS = 104
GROUP_SECONDS = GROUP_BITS / BITRATE
SAMPLES_PER_BIT = 32
RDS_RATE = BITRATE * SAMPLES_PER_BIT  # 38 kHz: the internal rate the RDS baseband is rendered at
DEVIATION_HZ = 75_000.0
_ENCODE = {ch: code for code, ch in sorted(CHARSET.items(), reverse=True)}  # lowest code wins


def encode_text(text: str) -> list[int]:
    return [_ENCODE.get(ch, 0x20) for ch in text]


# --- groups (lists of four 16-bit information words) ---------------------------------------------


def _block_b(gtype: int, version: int, tp: bool, pty: int, low5: int) -> int:
    return (gtype << 12) | (version << 11) | (int(tp) << 10) | ((pty & 31) << 5) | (low5 & 0x1F)


def group_0a(pi: int, pty: int, tp: bool, ta: bool, ms: bool, segment: int, ps: str,
             version: int = 0) -> list[int]:
    chars = encode_text(ps.ljust(8)[:8])
    b = _block_b(0, version, tp, pty, (int(ta) << 4) | (int(ms) << 3) | (segment & 3))
    c = pi if version else 0xE0CD  # 0A: "no alternative frequencies" + filler
    return [pi, b, c, (chars[2 * segment] << 8) | chars[2 * segment + 1]]


def radiotext_codes(text: str, version: int = 0) -> list[int]:
    """The RadioText characters as sent: terminated by 0x0D when shorter than the maximum, padded
    with spaces to whole segments."""
    size, seg = (64, 4) if version == 0 else (32, 2)
    codes = encode_text(text[:size])
    if len(codes) < size:
        codes.append(0x0D)
    codes += [0x20] * (-len(codes) % seg)
    return codes


def group_2a(pi: int, pty: int, tp: bool, ab: int, segment: int, text: str, version: int = 0) -> list[int]:
    codes = radiotext_codes(text, version)
    b = _block_b(2, version, tp, pty, ((ab & 1) << 4) | (segment & 0xF))
    if version == 0:
        p = segment * 4
        return [pi, b, (codes[p] << 8) | codes[p + 1], (codes[p + 2] << 8) | codes[p + 3]]
    p = segment * 2
    return [pi, b, pi, (codes[p] << 8) | codes[p + 1]]


def group_3a(pi: int, pty: int, tp: bool, app_group: int, message: int = 0, aid: int = RTPLUS_AID) -> list[int]:
    """Open Data Application announcement: `app_group` = group type code << 1 | version."""
    return [pi, _block_b(3, 0, tp, pty, app_group), message, aid]


def group_rtplus(pi: int, pty: int, tp: bool, app_group: int, toggle: int, running: bool,
                 tags: list[tuple[int, int, int]]) -> list[int]:
    """RT+ tags, each (content type, start, length) into the current RadioText (two per group)."""
    (t1, s1, l1), (t2, s2, l2) = (list(tags) + [(0, 0, 1), (0, 0, 1)])[:2]
    l1, l2 = l1 - 1, l2 - 1  # sent as "additional length"
    b = ((app_group >> 1) << 12) | ((app_group & 1) << 11) | (int(tp) << 10) | ((pty & 31) << 5)
    b |= ((toggle & 1) << 4) | (int(running) << 3) | ((t1 >> 3) & 7)
    c = ((t1 & 7) << 13) | ((s1 & 0x3F) << 7) | ((l1 & 0x3F) << 1) | ((t2 >> 5) & 1)
    d = ((t2 & 0x1F) << 11) | ((s2 & 0x3F) << 5) | (l2 & 0x1F)
    return [pi, b, c, d]


def group_4a(pi: int, pty: int, tp: bool, when: datetime) -> list[int]:
    """Clock time: UTC date and time to the minute plus the local offset in half hours."""
    offset = when.utcoffset() or timedelta(0)
    utc = when.astimezone(UTC)
    mjd = (utc.date() - MJD_EPOCH).days
    halves = round(abs(offset.total_seconds()) / 1800)
    b = _block_b(4, 0, tp, pty, (mjd >> 15) & 3)
    c = ((mjd & 0x7FFF) << 1) | (utc.hour >> 4)
    d = ((utc.hour & 0xF) << 12) | (utc.minute << 6) | (int(offset < timedelta(0)) << 5) | (halves & 0x1F)
    return [pi, b, c, d]


def group_blocks(infos: list[int]) -> list[int]:
    """Four 26-bit blocks. Version B groups (bit 11 of block B) carry offset C' on block 3."""
    third = "C'" if infos[1] >> 11 & 1 else "C"
    return [encode_block(i, o) for i, o in zip(infos, ("A", "B", third, "D"))]


def group_bits(infos: list[int]) -> list[int]:
    return [(blk >> (25 - i)) & 1 for blk in group_blocks(infos) for i in range(26)]


class BitModulator:
    """Data bits -> differential coding -> biphase chips -> shaped baseband at RDS_RATE (streaming)."""

    def __init__(self) -> None:
        self._t = 0
        self._shape = firwin(4 * SAMPLES_PER_BIT + 1, 2400.0, fs=RDS_RATE)
        self._zi = np.zeros(self._shape.size - 1)

    def render(self, bits: list[int]) -> np.ndarray:
        sym = np.empty(len(bits))
        for i, d in enumerate(bits):
            self._t ^= d
            sym[i] = 1.0 if self._t else -1.0
        half = SAMPLES_PER_BIT // 2
        chips = np.repeat(np.stack([sym, -sym], axis=1).ravel(), half)
        out, self._zi = lfilter(self._shape, 1.0, chips, zi=self._zi)
        return out


# --- demo station ------------------------------------------------------------------------------


class DemoStation:
    """A mono FM broadcast station with pilot and RDS.

    `mpx()` and `iq()` take absolute sample indices, so output does not depend on how reads are
    chunked. The RDS bit stream is rendered on demand, one group at a time, and the group
    schedule depends only on the group index (TA and clock time follow the stream's own time).
    """

    RT_AB = 0
    RTPLUS_GROUP = (11 << 1) | 0  # RT+ carried in group 11A

    def __init__(self, pi: int = 0x6201, ps: str = "QRM DEMO", pty: int = 10, tp: bool = True,
                 artist: str = "ZS6SHZ", title: str = "Noise Floor Blues",
                 ta_period_s: float = 30.0, ta_on_s: float = 5.0,
                 clock_start: datetime | None = None, utc_offset: timedelta = timedelta(hours=2),
                 music_level: float = 0.6, pilot_level: float = 0.09, rds_level: float = 0.04) -> None:
        self.pi, self.ps, self.pty, self.tp = pi, ps, pty, tp
        self.rt = f"Now playing: {artist} - {title}"
        start = self.rt.index(artist)
        self.tags = [(1, self.rt.index(title, start + len(artist)), len(title)), (4, start, len(artist))]
        self.ta_period_s, self.ta_on_s = ta_period_s, ta_on_s
        tz = timezone(utc_offset)
        self.clock_start = (clock_start or datetime.now(UTC)).astimezone(tz)
        self._clock_s = self.clock_start.timestamp()
        self.music_level, self.pilot_level, self.rds_level = music_level, pilot_level, rds_level
        self._rt_segments = len(radiotext_codes(self.rt)) // 4
        self._phasors: dict[tuple, np.ndarray] = {}
        self._restart(0)
        self._fm_next = -1
        self._fm_phase = 0.0

    # --- schedule -----------------------------------------------------------------------------

    def _minute(self, t: float) -> int:
        return math.floor((self._clock_s + t) / 60)

    def ta_at(self, seconds: float) -> bool:
        return seconds % self.ta_period_s >= self.ta_period_s - self.ta_on_s

    def group(self, g: int) -> list[int]:
        t = g * GROUP_SECONDS
        pi, pty, tp = self.pi, self.pty, self.tp
        if self._minute(t) != self._minute(t - GROUP_SECONDS):  # clock time: once a minute, on the minute
            return group_4a(pi, pty, tp, self.clock_start + timedelta(seconds=t))
        # Clock-time groups are inserted, not substituted: the rest of the schedule moves along.
        slot = g - (self._minute(t - GROUP_SECONDS) - self._minute(-GROUP_SECONDS))
        if slot % 2 == 0:
            return group_0a(pi, pty, tp, self.ta_at(t), True, (slot // 2) % 4, self.ps)
        k = (slot // 2) % (self._rt_segments + 2)
        if k < self._rt_segments:
            return group_2a(pi, pty, tp, self.RT_AB, k, self.rt)
        if k == self._rt_segments:
            return group_3a(pi, pty, tp, self.RTPLUS_GROUP)
        return group_rtplus(pi, pty, tp, self.RTPLUS_GROUP, 0, True, self.tags)

    # --- rendering ----------------------------------------------------------------------------

    def _restart(self, g: int) -> None:
        self._g = g
        self._mod = BitModulator()
        self._buf = np.empty(0)
        self._buf_start = g * GROUP_BITS * SAMPLES_PER_BIT

    def _rds(self, idx: np.ndarray) -> np.ndarray:
        """The shaped RDS baseband at fractional RDS_RATE sample positions `idx` (ascending)."""
        lo, hi = math.floor(idx[0]), math.ceil(idx[-1]) + 1
        if lo < self._buf_start:
            self._restart(lo // (GROUP_BITS * SAMPLES_PER_BIT))
        while self._buf_start + self._buf.size <= hi:
            self._buf = np.concatenate([self._buf, self._mod.render(group_bits(self.group(self._g)))])
            self._g += 1
        drop = lo - self._buf_start
        if drop > 0:
            self._buf = self._buf[drop:]
            self._buf_start = lo
        return np.interp(idx - self._buf_start, np.arange(self._buf.size), self._buf)

    def _phasor(self, freq: float, rate: float, n0: int, n: int) -> np.ndarray:
        key = (freq, rate, n)
        unit = self._phasors.get(key)
        if unit is None:
            if len(self._phasors) >= 32:
                self._phasors.clear()
            unit = self._phasors[key] = np.exp(2j * np.pi * (freq / rate) * np.arange(n)).astype(np.complex64)
        return unit * np.complex64(cmath.exp(2j * math.pi * math.fmod(freq * (n0 / rate), 1.0)))

    def mpx(self, n0: int, n: int, rate: float) -> np.ndarray:
        """MPX for samples n0..n0+n-1 at `rate`; 1.0 = full (75 kHz) deviation."""
        # A chord whose notes swell and fade at different slow rates.
        music = np.zeros(n, np.float32)
        for f, lfo, w in ((220.0, 0.23, 0.30), (277.2, 0.31, 0.25), (329.6, 0.17, 0.25), (440.0, 0.41, 0.20)):
            env = 0.6 + 0.4 * self._phasor(lfo, rate, n0, n).real
            music += w * env * self._phasor(f, rate, n0, n).real
        pilot = self._phasor(PILOT_HZ, rate, n0, n)
        sub = (pilot * pilot * pilot).real  # 57 kHz, phase-locked to 3x the pilot
        rds = self._rds((n0 + np.arange(n)) * (RDS_RATE / rate)).astype(np.float32)
        return (self.music_level * music + self.pilot_level * pilot.real
                + self.rds_level * rds * sub).astype(np.float32)

    def iq(self, n0: int, n: int, rate: float, offset_hz: float) -> np.ndarray:
        """Unit-amplitude complex baseband of the FM carrier at `offset_hz` from the tuned centre."""
        if n0 != self._fm_next:
            self._fm_phase = 0.0
        phase = self._fm_phase + np.cumsum(self.mpx(n0, n, rate), dtype=np.float64) * (
            2 * math.pi * DEVIATION_HZ / rate)
        self._fm_phase = float(phase[-1] % (2 * math.pi))
        self._fm_next = n0 + n
        ph = (phase % (2 * math.pi)).astype(np.float32)  # float32 trig is about 3x faster
        carrier = np.empty(n, np.complex64)
        carrier.real = np.cos(ph)
        carrier.imag = np.sin(ph)
        return carrier * self._phasor(offset_hz, rate, n0, n)

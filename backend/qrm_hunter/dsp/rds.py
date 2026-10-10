"""RDS (IEC 62106) decoder: MPX -> 57 kHz BPSK -> biphase chips -> bits -> blocks -> groups -> RdsState.

The receive chain:
  mix the 57 kHz subcarrier to baseband (3x the measured pilot frequency when a pilot is present,
  otherwise the nominal 57 kHz) -> decimate to about 19 kHz -> 2.4 kHz low-pass -> carrier phase
  loop (squaring estimator, so the BPSK data does not disturb it) -> chip-matched filter
  (integrate-and-dump over half a bit) -> chip timing from the chip-rate line of the squared
  filter output -> pair chips into biphase symbols -> differential decoding -> block sync on the
  26-bit syndrome -> group decoding.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone

import numpy as np
from scipy.signal import firwin, lfilter

from .demod import PILOT_HZ, Decimator, Mixer, PilotDetector, choose_decimation

BITRATE = 1187.5
CHIP_RATE = 2 * BITRATE  # each biphase symbol is two opposite half-bit chips
SUBCARRIER_HZ = 3 * PILOT_HZ
RTPLUS_AID = 0x4BD7

# --- block coding ------------------------------------------------------------------------------

POLY = 0x5B9  # g(x) = x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1
OFFSETS = {"A": 0x0FC, "B": 0x198, "C": 0x168, "C'": 0x350, "D": 0x1B4}
_BLOCK_INDEX = {"A": 0, "B": 1, "C": 2, "C'": 2, "D": 3}


def _mod_g(v: int) -> int:
    while v.bit_length() > 10:
        v ^= POLY << (v.bit_length() - 11)
    return v


def checkword(info: int) -> int:
    """The 10-bit check word for 16 information bits (before the offset word is added)."""
    return _mod_g((info & 0xFFFF) << 10)


def encode_block(info: int, offset: str) -> int:
    """A 26-bit block: 16 information bits followed by check word XOR offset word."""
    return ((info & 0xFFFF) << 10) | (checkword(info) ^ OFFSETS[offset])


# The parity-check matrix of IEC 62106 Annex B maps a received block r(x) to r(x)·x^325 mod g(x)
# (x^325 = x^-16 in this cyclic code of period 341), which gives the familiar syndromes
# A=0x3D8, B=0x3D4, C=0x25C, C'=0x3CC, D=0x258. It is applied with three byte-sized lookups.
_H_ROWS = [_mod_g(1 << (p + 325)) for p in range(26)]


def _table(lo: int, bits: int) -> list[int]:
    out = []
    for v in range(1 << bits):
        s = 0
        for i in range(bits):
            if v >> i & 1:
                s ^= _H_ROWS[lo + i]
        out.append(s)
    return out


_SYN_LO, _SYN_MID, _SYN_HI = _table(0, 9), _table(9, 9), _table(18, 8)


def syndrome(block: int) -> int:
    return _SYN_LO[block & 0x1FF] ^ _SYN_MID[(block >> 9) & 0x1FF] ^ _SYN_HI[(block >> 18) & 0xFF]


SYNDROMES = {name: syndrome(off) for name, off in OFFSETS.items()}
_SYNDROME_TO_OFFSET = {s: name for name, s in SYNDROMES.items()}

# --- character set and programme types ------------------------------------------------------------

# IEC 62106 Annex E, 0x80-0xFF. 0x20-0x7E are taken as ASCII.
_HIGH = (
    "áàéèíìóòúùÑÇŞß¡Ĳ"
    "âäêëîïôöûüñçşğıĳ"
    "ªα©‰Ğěňőπ€£$←↑→↓"
    "º¹²³±İńűµ¿÷°¼½¾§"
    "ÁÀÉÈÍÌÓÒÚÙŘČŠŽÐĿ"
    "ÂÄÊËÎÏÔÖÛÜřčšžđŀ"
    "ÃÅÆŒŷÝÕØÞŊŔĆŚŹŦð"
    "ãåæœŵýõøþŋŕćśźŧ "
)
assert len(_HIGH) == 128
CHARSET = {**{c: chr(c) for c in range(0x20, 0x7F)}, **{0x80 + i: ch for i, ch in enumerate(_HIGH)}}


def decode_chars(codes) -> str:
    return "".join(CHARSET.get(c, " ") for c in codes)


RDS_PTY = (
    "None", "News", "Current Affairs", "Information", "Sport", "Education", "Drama", "Culture",
    "Science", "Varied", "Pop Music", "Rock Music", "Easy Listening", "Light Classical",
    "Serious Classical", "Other Music", "Weather", "Finance", "Children's Programmes", "Social Affairs",
    "Religion", "Phone-in", "Travel", "Leisure", "Jazz Music", "Country Music", "National Music",
    "Oldies Music", "Folk Music", "Documentary", "Alarm Test", "Alarm",
)
RBDS_PTY = (
    "None", "News", "Information", "Sports", "Talk", "Rock", "Classic Rock", "Adult Hits", "Soft Rock",
    "Top 40", "Country", "Oldies", "Soft", "Nostalgia", "Jazz", "Classical", "Rhythm and Blues",
    "Soft Rhythm and Blues", "Language", "Religious Music", "Religious Talk", "Personality", "Public",
    "College", "Spanish Talk", "Spanish Music", "Hip Hop", "Unassigned", "Unassigned", "Weather",
    "Emergency Test", "Emergency",
)


def pty_name(pty: int, region: int) -> str:
    """Programme type name: the RBDS table in IARU Region 2 (the Americas), the RDS table elsewhere."""
    return (RBDS_PTY if region == 2 else RDS_PTY)[pty & 31]


MJD_EPOCH = date(1858, 11, 17)


def mjd_to_date(mjd: int) -> date:
    return MJD_EPOCH + timedelta(days=mjd)


# --- state ---------------------------------------------------------------------------------------


@dataclass
class RdsState:
    pi: str | None = None  # 4 hex digits
    ps: str | None = None
    rt: str | None = None
    pty: int | None = None
    pty_name: str | None = None
    tp: bool = False
    ta: bool = False
    ms: bool = False
    ct: str | None = None  # local time, ISO 8601 with the station's UTC offset
    rtplus: dict | None = None  # {"title": str | None, "artist": str | None}
    stereo: bool = False
    synced: bool = False
    block_error_rate: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


# --- block sync --------------------------------------------------------------------------------------

SYNC_BLOCKS = 2  # consecutive valid blocks, in order, needed to acquire sync
LOSS_WINDOW = 50  # blocks
LOSS_ERRORS = 25  # bad blocks within the window that drop sync


class BlockSync:
    """Finds block boundaries in the bit stream and assembles groups.

    push() returns a completed group as a 4-tuple of 16-bit information words (None for a block
    that failed its check), or None.
    """

    def __init__(self) -> None:
        self.synced = False
        self._reg = 0
        self._nbits = 0
        self._cand: tuple[int, int, int] | None = None  # (bit position, block index, run length)
        self._block_bits = 0
        self._expect = 0
        self._group: list[int | None] = [None] * 4
        self.window: deque[bool] = deque(maxlen=LOSS_WINDOW)  # True = bad block

    @property
    def error_rate(self) -> float:
        if not self.synced or not self.window:
            return 1.0
        return sum(self.window) / len(self.window)

    def push(self, bit: int) -> tuple | None:
        self._reg = ((self._reg << 1) | bit) & 0x3FFFFFF
        self._nbits += 1
        if not self.synced:
            return self._search()
        self._block_bits += 1
        if self._block_bits < 26:
            return None
        self._block_bits = 0
        idx = self._expect
        self._expect = (idx + 1) % 4
        name = _SYNDROME_TO_OFFSET.get(syndrome(self._reg))
        ok = name is not None and _BLOCK_INDEX[name] == idx
        self.window.append(not ok)
        group = self._store(idx, self._reg >> 10 if ok else None)
        if sum(self.window) >= LOSS_ERRORS:
            self._lose()
        return group

    def _search(self) -> tuple | None:
        if self._nbits < 26:
            return None
        name = _SYNDROME_TO_OFFSET.get(syndrome(self._reg))
        if name is None:
            return None
        idx = _BLOCK_INDEX[name]
        run = 1
        if self._cand is not None:
            pos, prev, prev_run = self._cand
            if self._nbits - pos == 26 and idx == (prev + 1) % 4:
                run = prev_run + 1
        if run == 1:
            self._group = [None] * 4
        self._cand = (self._nbits, idx, run)
        group = self._store(idx, self._reg >> 10)
        if run >= SYNC_BLOCKS:
            self.synced = True
            self._block_bits = 0
            self._expect = (idx + 1) % 4
            self.window.clear()
        return group

    def _store(self, idx: int, info: int | None) -> tuple | None:
        self._group[idx] = info
        if idx != 3:
            return None
        group = tuple(self._group)
        self._group = [None] * 4
        return group

    def _lose(self) -> None:
        self.synced = False
        self._cand = None
        self._group = [None] * 4
        self.window.clear()


# --- group decoding ------------------------------------------------------------------------------------


class GroupDecoder:
    """Turns groups into RdsState fields. `changed` is set whenever a published field changes."""

    def __init__(self, region: int = 1) -> None:
        self.region = region
        self.state = RdsState()
        self.changed = False
        self._ps_seg: list[tuple[int, int] | None] = [None] * 4
        self._ps_seen = [0] * 4
        self._rt: list[int | None] = []
        self._rt_ab: int | None = None
        self._rt_version: int | None = None
        self._rt_end: int | None = None
        self._rtplus_group: int | None = None
        self._rtplus_toggle: int | None = None
        self._tags: dict[int, tuple[int, int]] = {}

    def set(self, **fields) -> None:
        for k, v in fields.items():
            if getattr(self.state, k) != v:
                setattr(self.state, k, v)
                self.changed = True

    def set_region(self, region: int) -> None:
        self.region = region
        if self.state.pty is not None:
            self.set(pty_name=pty_name(self.state.pty, region))

    def decode(self, group: tuple) -> None:
        a, b, c, d = group
        if a is not None:
            self.set(pi=f"{a:04X}")
        if b is None:
            return
        gtype, version = b >> 12, (b >> 11) & 1
        pty = (b >> 5) & 31
        self.set(tp=bool(b >> 10 & 1), pty=pty, pty_name=pty_name(pty, self.region))
        if version == 1 and c is not None:  # block C' repeats the PI code
            self.set(pi=f"{c:04X}")
        if gtype == 0:
            self._group0(b, d)
        elif gtype == 2:
            self._group2(version, b, c, d)
        elif gtype == 3 and version == 0:
            self._group3a(b, d)
        elif gtype == 4 and version == 0:
            self._group4a(b, c, d)
        if self._rtplus_group is not None and (gtype << 1 | version) == self._rtplus_group:
            self._rtplus(b, c, d)

    def _group0(self, b: int, d: int | None) -> None:
        self.set(ta=bool(b >> 4 & 1), ms=bool(b >> 3 & 1))
        if d is None:
            return
        seg = b & 3
        chars = (d >> 8, d & 0xFF)
        if self._ps_seg[seg] == chars:
            self._ps_seen[seg] += 1
        else:
            self._ps_seg[seg], self._ps_seen[seg] = chars, 1
        # Publish only once every segment has been received the same way twice: no flicker
        # while a station changes its PS one segment at a time.
        if all(n >= 2 for n in self._ps_seen):
            self.set(ps=decode_chars([ch for pair in self._ps_seg for ch in pair]))

    def _group2(self, version: int, b: int, c: int | None, d: int | None) -> None:
        ab, seg = (b >> 4) & 1, b & 0xF
        if ab != self._rt_ab or version != self._rt_version:
            # A new text: start again, and drop the RT+ tags that pointed into the old one.
            self._rt = [None] * (64 if version == 0 else 32)
            self._rt_ab, self._rt_version, self._rt_end = ab, version, None
            self._tags.clear()
            self._update_rtplus()
        if version == 0:
            if c is None or d is None:
                return
            pos, chars = seg * 4, (c >> 8, c & 0xFF, d >> 8, d & 0xFF)
        else:
            if d is None:
                return
            pos, chars = seg * 2, (d >> 8, d & 0xFF)
        for i, ch in enumerate(chars):
            self._rt[pos + i] = ch
            if ch == 0x0D and (self._rt_end is None or pos + i < self._rt_end):
                self._rt_end = pos + i
        end = len(self._rt) if self._rt_end is None else self._rt_end
        if all(ch is not None for ch in self._rt[:end]):
            self.set(rt=decode_chars(self._rt[:end]).rstrip())
        self._update_rtplus()

    def _group3a(self, b: int, d: int | None) -> None:
        if d == RTPLUS_AID:
            self._rtplus_group = b & 0x1F

    def _group4a(self, b: int, c: int | None, d: int | None) -> None:
        if c is None or d is None:
            return
        mjd = ((b & 3) << 15) | (c >> 1)
        hour = ((c & 1) << 4) | (d >> 12)
        minute = (d >> 6) & 0x3F
        offset = timedelta(minutes=30 * (d & 0x1F)) * (-1 if d >> 5 & 1 else 1)
        if hour > 23 or minute > 59 or abs(offset) > timedelta(hours=14):
            return
        try:
            day = mjd_to_date(mjd)
        except OverflowError:
            return
        utc = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
        self.set(ct=utc.astimezone(timezone(offset)).isoformat())

    def _rtplus(self, b: int, c: int | None, d: int | None) -> None:
        if c is None or d is None:
            return
        toggle = (b >> 4) & 1
        if toggle != self._rtplus_toggle:  # a new item: forget the old tags
            self._rtplus_toggle = toggle
            self._tags.clear()
        tags = (
            (((b & 7) << 3) | (c >> 13), (c >> 7) & 0x3F, (c >> 1) & 0x3F),
            (((c & 1) << 5) | (d >> 11), (d >> 5) & 0x3F, d & 0x1F),
        )
        for ctype, start, length in tags:
            if ctype:
                self._tags[ctype] = (start, length + 1)
        self._update_rtplus()

    def _tag_text(self, ctype: int) -> str | None:
        tag = self._tags.get(ctype)
        if tag is None:
            return None
        start, length = tag
        chars = self._rt[start:start + length]
        if len(chars) < length or any(ch is None for ch in chars):
            return None
        return decode_chars(chars).strip() or None

    def _update_rtplus(self) -> None:
        title, artist = self._tag_text(1), self._tag_text(4)
        self.set(rtplus={"title": title, "artist": artist} if title or artist else None)


# --- decoder -----------------------------------------------------------------------------------------


@dataclass
class _Chips:
    """Chip pairing: biphase chips within a symbol are always opposite, so the pairing with the
    fewest equal-sign pairs marks the symbol boundary."""

    count: int = 0
    prev: float = 0.0
    violations: list[float] = field(default_factory=lambda: [0.0, 0.0])
    phase: int = 1


class RdsDecoder:
    """Streaming RDS decoder. Feed MPX blocks of any size to process(); read `state`, and
    `changed` (set when a published field changes; the caller clears it)."""

    LOWPASS_HZ = 2400.0
    BASE_RATE = 19_000.0  # about 16 samples per bit after decimation

    def __init__(self, rate: float, region: int = 1) -> None:
        self.rate = float(rate)
        self.pilot = PilotDetector(self.rate)
        self._factors = choose_decimation(self.rate, self.BASE_RATE)
        self.base_rate = self.rate / math.prod(self._factors)
        sps = self.base_rate / BITRATE
        self._lp = firwin(int(4 * sps) | 1, self.LOWPASS_HZ, fs=self.base_rate)
        self._chip_len = max(2, round(self.base_rate / CHIP_RATE))
        self._chunk = max(8, round(2 * sps))  # carrier and timing loops update every 2 bits
        self._cr = CHIP_RATE / self.base_rate  # chips per sample
        self._groups = GroupDecoder(region)
        self.reset()

    @property
    def state(self) -> RdsState:
        return self._groups.state

    @property
    def changed(self) -> bool:
        return self._groups.changed

    @changed.setter
    def changed(self, value: bool) -> None:
        self._groups.changed = value

    def set_region(self, region: int) -> None:
        self._groups.set_region(region)

    def reset(self) -> None:
        region = self._groups.region
        self._groups = GroupDecoder(region)
        self.pilot.reset()
        self._stages = [Decimator(f) for f in self._factors]
        self._mixer = Mixer(SUBCARRIER_HZ, self.rate)
        self._lp_zi = np.zeros(self._lp.size - 1, np.complex64)
        self._buf = np.empty(0, np.complex64)
        self._theta = 0.0  # carrier phase, rad
        self._omega = 0.0  # carrier frequency error tracked by the loop, rad/sample
        self._omega_pilot = 0.0  # 3x the pilot's frequency error, rad/sample
        self._mf_zi = np.zeros(self._chip_len - 1)
        self._line = 0j  # chip-rate line of the squared matched-filter output
        self._u = 0.0  # chip phase (cycles) at the next sample
        self._tau = 0.0  # unwrapped timing phase (cycles)
        self._k_last = -1
        self._m_prev = 0.0
        self._chips = _Chips()
        self._t_prev = 0
        self._sync = BlockSync()
        self._bad_published = 1.0

    def process(self, mpx: np.ndarray) -> None:
        x = np.asarray(mpx, np.float32)
        if x.size == 0:
            return
        self.pilot.process(x)
        # With a pilot, the subcarrier is locked to 3x its frequency: steer the carrier loop by
        # the measured pilot error and leave the loop only the phase. Without one, the loop
        # tracks the free-running subcarrier by itself.
        df = 3 * self.pilot.offset_hz if self.pilot.stereo else 0.0
        self._omega_pilot = 2 * math.pi * df / self.base_rate
        z = self._mixer.mix(x)
        for st in self._stages:
            z = st.process(z)
        if z.size:
            z, self._lp_zi = lfilter(self._lp, 1.0, z, zi=self._lp_zi)
            buf = np.concatenate([self._buf, z.astype(np.complex64)])
            n = buf.size - buf.size % self._chunk
            self._buf = buf[n:]
            if n:
                self._demodulate(buf[:n])
        ber = self._sync.error_rate
        if abs(ber - self._bad_published) >= 0.05 or (ber == 1.0) != (self._bad_published == 1.0):
            self._bad_published = ber
            self._groups.set(block_error_rate=round(ber, 3))
        self._groups.set(stereo=self.pilot.stereo, synced=self._sync.synced)

    def _demodulate(self, z: np.ndarray) -> None:
        L = self._chunk
        ar = np.arange(L)
        r = np.empty(z.size)
        for j in range(0, z.size, L):
            omega = self._omega + self._omega_pilot
            zr = z[j:j + L] * np.exp(-1j * (self._theta + omega * ar))
            # BPSK squared is a pure carrier at twice the phase error: data-independent
            sq = complex(np.sum(zr * zr))
            err = 0.5 * math.atan2(sq.imag, sq.real) if sq != 0 else 0.0
            self._theta = (self._theta + omega * L + 0.25 * err) % (2 * math.pi)
            self._omega += 0.02 * err / L
            r[j:j + L] = zr.real
        m, self._mf_zi = lfilter(np.ones(self._chip_len), 1.0, r, zi=self._mf_zi)
        cr = self._cr
        for j in range(0, m.size, L):
            mc = m[j:j + L]
            u = self._u + cr * ar
            self._line = 0.9 * self._line + complex(np.sum(mc * mc * np.exp(-2j * math.pi * (u % 1.0))))
            # chip-ends (peaks of m^2) sit at u = tau (mod 1); keep tau unwrapped
            tau = -math.atan2(self._line.imag, self._line.real) / (2 * math.pi)
            self._tau += (tau - self._tau + 0.5) % 1.0 - 0.5
            v0 = u[0] - self._tau
            k_hi = math.floor(v0 + cr * (L - 1))
            if k_hi > self._k_last:
                ext = np.concatenate(([self._m_prev], mc))
                for k in range(self._k_last + 1, k_hi + 1):
                    pos = (k - v0) / cr + 1.0  # index into ext; clamped below when tau has moved
                    i = min(max(int(pos), 0), L - 1)
                    frac = min(max(pos - i, 0.0), 1.0)
                    self._chip(ext[i] + (ext[i + 1] - ext[i]) * frac)
                self._k_last = k_hi
            self._u = u[-1] + cr
            self._m_prev = float(mc[-1])

    def _chip(self, c: float) -> None:
        ch = self._chips
        p = ch.count & 1
        ch.count += 1
        if ch.count > 1:
            same = (c > 0) == (ch.prev > 0)
            ch.violations[p] = 0.97 * ch.violations[p] + (1.0 if same else 0.0)
            other = 1 - ch.phase
            if ch.violations[other] + 1.0 < ch.violations[ch.phase]:
                ch.phase = other
            if p == ch.phase:
                t = 1 if ch.prev - c > 0 else 0
                bit = t ^ self._t_prev
                self._t_prev = t
                group = self._sync.push(bit)
                if group is not None:
                    self._groups.decode(group)
        ch.prev = c

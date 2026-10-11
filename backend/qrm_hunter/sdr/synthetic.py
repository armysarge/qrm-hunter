"""A deterministic fake SDR that renders typical HF QRM. Used for tests, demos and development."""

from __future__ import annotations

import cmath
import math
import time
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np

from .base import DeviceConfig, DeviceError, DeviceInfo, apply_limits
from .rds_encoder import DemoStation

SourceKind = Literal["cw", "smps_comb", "buzz", "ofdm", "impulses", "am_broadcast", "wfm_broadcast"]


@dataclass(frozen=True)
class Source:
    """One interference source. freq_hz is the carrier (cw/am/wfm), first line (comb),
    centre (buzz) or lower edge (ofdm); impulses ignore it (they are wideband). A wfm_broadcast
    source is a DemoStation (mono programme, pilot and RDS); its params go to DemoStation."""

    kind: SourceKind
    freq_hz: float
    level_db: float = -40.0
    params: dict = field(default_factory=dict)


def demo_scene() -> list[Source]:
    return [
        Source("smps_comb", 150_000.0, -68.0, {"spacing_hz": 68_000.0, "am_hz": 100.0, "am_depth": 0.6}),
        Source("cw", 7_074_000.0, -50.0),
        Source("buzz", 7_150_000.0, -50.0, {"bw_hz": 120_000.0, "am_hz": 100.0}),
        Source("am_broadcast", 7_245_000.0, -38.0, {"tone_hz": 1000.0, "depth": 0.5}),
        Source("ofdm", 7_300_000.0, -45.0, {"bw_hz": 400_000.0, "spacing_hz": 4312.5}),
        Source("impulses", 0.0, -10.0, {"rate_hz": 100.0, "burst_len": 32}),
        Source("cw", 14_074_000.0, -55.0),
        Source("buzz", 14_200_000.0, -48.0, {"bw_hz": 300_000.0, "am_hz": 100.0}),
        Source("wfm_broadcast", 100_000_000.0, -30.0),
    ]


SYNTHETIC_INFO = DeviceInfo(
    id="synthetic",
    driver="synthetic",
    label="Synthetic demo (no hardware)",
    freq_range=(100_000.0, 1_800_000_000.0),
    sample_rates=(1_024_000.0, 2_048_000.0, 2_400_000.0),
    gain_range=(0.0, 50.0),
)

_GOLDEN_ANGLE = 2.399963
_PHASOR_CACHE_MAX = 32


class SyntheticDevice:
    def __init__(self, sources: list[Source] | None = None, noise_db: float = -45.0,
                 seed: int = 1234, realtime: bool = True, profile: str | None = None) -> None:
        if profile is not None:
            if profile not in PROFILE_IDS:
                raise DeviceError(f"unknown synthetic profile {profile!r}")
            sources = profile_sources(profile)
            self.info = replace(SYNTHETIC_INFO, id=f"synthetic:{profile}",
                                label=f"Synthetic: {PROFILE_LABELS[profile]}")
        else:
            self.info = SYNTHETIC_INFO
        self.sources = list(demo_scene() if sources is None else sources)
        self.noise_db = noise_db
        self.realtime = realtime
        self.overflows = 0
        self._seed = seed
        self._rng = np.random.default_rng(seed)
        self._cfg = DeviceConfig()
        self._open = False
        self._n = 0
        self._t0 = 0.0
        self._paced = 0
        self._ofdm_cache: dict[tuple, np.ndarray] = {}
        self._phasors: dict[tuple, np.ndarray] = {}
        self._stations: dict[int, DemoStation] = {}
        self._walk: dict[int, float] = {}  # per-source random-walk phase (spread lines)

    def open(self) -> None:
        self._open = True
        self._t0 = time.monotonic()
        self._paced = 0

    def close(self) -> None:
        self._open = False

    def configure(self, cfg: DeviceConfig) -> DeviceConfig:
        if not self._open:
            raise DeviceError("device is not open")
        applied = apply_limits(self.info, cfg)
        if applied.sample_rate != self._cfg.sample_rate:
            self._t0 = time.monotonic()
            self._paced = 0
        self._cfg = applied
        return applied

    def read(self, n: int) -> np.ndarray:
        if not self._open:
            raise DeviceError("device is not open")
        rate, fc = self._cfg.sample_rate, self._cfg.center_hz
        n0 = self._n
        sigma = np.float32(np.sqrt(10 ** (self.noise_db / 10) / 2))
        # interleaved re/im pairs viewed as complex64: one RNG call, no float64 temporaries
        x = self._rng.standard_normal(2 * n, dtype=np.float32).view(np.complex64)
        x *= sigma
        for i, src in enumerate(self.sources):
            part = self._render(src, i, n0, n, fc, rate)
            if part is not None:
                x += part
        if self._cfg.gain_db is not None:
            x *= np.float32(10 ** ((self._cfg.gain_db - 30.0) / 20))
        self._n += n
        if self.realtime:
            self._pace(n, rate)
        return x

    # --- rendering -------------------------------------------------------------------------
    #
    # Everything is complex64. Tones are built from a cached unit phasor exp(2j*pi*f*k/rate),
    # k = 0..n-1, rotated by one scalar start phase exp(2j*pi*f*n0/rate) per block, instead of
    # evaluating exp() per sample per block.

    def _phasor(self, freq: float, rate: float, n0: int, n: int) -> np.ndarray:
        key = (freq, rate, n)
        unit = self._phasors.get(key)
        if unit is None:
            if len(self._phasors) >= _PHASOR_CACHE_MAX:
                self._phasors.clear()  # offsets change on every retune; keep the cache bounded
            unit = np.exp(2j * np.pi * (freq / rate) * np.arange(n)).astype(np.complex64)
            self._phasors[key] = unit
        cycles = math.fmod(freq * (n0 / rate), 1.0)
        return unit * np.complex64(cmath.exp(2j * math.pi * cycles))

    def _render(self, src: Source, index: int, n0: int, n: int, fc: float, rate: float):
        part = self._render_source(src, index, n0, n, fc, rate)
        if part is None or not src.params.get("gate_slot_s"):
            return part
        return part * self._gate(src.params, index, n0, n, rate)

    def _render_source(self, src: Source, index: int, n0: int, n: int, fc: float, rate: float):
        amp = np.float32(10 ** (src.level_db / 20))
        p = src.params
        half = rate / 2
        if src.kind == "cw":
            off = src.freq_hz - fc
            if abs(off) >= half:
                return None
            tone = amp * self._phasor(off, rate, n0, n)
            spread = p.get("spread_hz", 0.0)
            return tone if not spread else tone * self._phase_walk(index, n, rate, spread)
        if src.kind == "am_broadcast":
            off = src.freq_hz - fc
            if abs(off) >= half:
                return None
            tone = self._phasor(p.get("tone_hz", 1000.0), rate, n0, n).real
            env = 1 + np.float32(p.get("depth", 0.5)) * tone
            return (amp * env) * self._phasor(off, rate, n0, n)
        if src.kind == "wfm_broadcast":
            off = src.freq_hz - fc
            if abs(off) >= half:
                return None
            station = self._stations.get(index)
            if station is None:
                station = self._stations[index] = DemoStation(**p)
            return amp * station.iq(n0, n, rate, off)
        if src.kind == "smps_comb":
            return self._comb(src, n0, n, fc, rate, amp)
        if src.kind == "buzz":
            return self._buzz(src, n0, n, fc, rate, amp)
        if src.kind == "ofdm":
            return self._ofdm(src, n0, n, fc, rate, amp)
        if src.kind == "impulses":
            return self._impulses(src, index, n, n0, rate, amp)
        raise ValueError(f"unknown source kind {src.kind!r}")

    def _comb(self, src, n0, n, fc, rate, amp):
        p = src.params
        if p.get("fm_dev_hz"):
            return self._comb_fm(src, n0, n, fc, rate, amp)
        s = p.get("spacing_hz", 68_000.0)
        half = rate / 2
        k_lo = max(0, int(np.ceil((fc - half - src.freq_hz) / s)))
        k_hi = int(np.floor((fc + half - src.freq_hz) / s))
        k_hi = min(k_hi, int((p.get("max_hz", 30_000_000.0) - src.freq_hz) // s))
        if k_hi < k_lo:
            return None
        count = k_hi - k_lo + 1
        f0 = src.freq_hz + k_lo * s - fc
        # Equal-amplitude lines at f0 + k*s, line k carrying the constant phase k*golden_angle:
        #   sum_{k<count} r^k = (1 - r^count) / (1 - r),  r = exp(1j*(2*pi*s*t + golden_angle)).
        # A phase that is linear in k is only a time shift, so the lines are coherent: the result
        # is a pulse train repeating every 1/s, not a comb of lines with independent phases.
        r = self._phasor(s, rate, n0, n) * np.complex64(cmath.exp(1j * _GOLDEN_ANGLE))
        r_count = (self._phasor(count * s, rate, n0, n)
                   * np.complex64(cmath.exp(1j * ((count * _GOLDEN_ANGLE) % (2 * math.pi)))))
        den = 1 - r
        small = np.abs(den) < 1e-6
        if small.any():  # r == 1: every line in phase, the sum is count
            den[small] = 1
            lines = (1 - r_count) / den
            lines[small] = count
        else:
            lines = (1 - r_count) / den
        env = self._am(p, n0, n, rate)
        out = (amp * env) * self._phasor(f0, rate, n0, n) * lines
        return self._pm(p, out, n0, n, rate)

    def _comb_fm(self, src, n0, n, fc, rate, amp):
        """A comb whose spacing moves: s(t) = s0 + fm_dev_hz*sin(2*pi*fm_rate_hz*t + fm_phase).
        Line k sits at freq_hz + k*s(t), so its excursion grows with k, as harmonics of a real
        switcher do. A slow fm_rate_hz gives a near-linear drift over a few seconds."""
        p = src.params
        s0 = p.get("spacing_hz", 68_000.0)
        dev, fr, ph = p["fm_dev_hz"], p.get("fm_rate_hz", 1.0), p.get("fm_phase", 0.0)
        half = rate / 2
        # one line of margin at each edge, so a moving line never wraps across the span
        k_lo = max(0, int(np.ceil((fc - half - src.freq_hz) / s0)) + 1)
        k_hi = min(int(np.floor((fc + half - src.freq_hz) / s0)) - 1,
                   int((p.get("max_hz", 30_000_000.0) - src.freq_hz) // s0))
        if k_hi < k_lo:
            return None
        count = k_hi - k_lo + 1
        t = (n0 + np.arange(n)) / rate
        cyc = s0 * t - dev / (2 * np.pi * fr) * (np.cos(2 * np.pi * fr * t + ph) - math.cos(ph))
        theta = 2 * np.pi * np.mod(cyc, 1.0) + _GOLDEN_ANGLE
        base = 2 * np.pi * np.mod((src.freq_hz - fc) * t + k_lo * cyc, 1.0) + k_lo * _GOLDEN_ANGLE
        r = np.exp(1j * theta)
        den = 1 - r
        small = np.abs(den) < 1e-9
        den[small] = 1
        lines = (1 - np.exp(1j * count * theta)) / den
        lines[small] = count
        out = (np.exp(1j * base) * lines).astype(np.complex64) * (amp * self._am(p, n0, n, rate))
        return self._pm(p, out, n0, n, rate)

    def _am(self, p: dict, n0: int, n: int, rate: float) -> np.ndarray:
        """Amplitude envelope: 1 + depth*cos (sine) or on/off chopping with duty am_duty (square)."""
        am_hz, depth = p.get("am_hz", 100.0), p.get("am_depth", 0.6)
        if p.get("am_shape", "sine") == "square":
            frac = np.mod((n0 + np.arange(n)) * (am_hz / rate), 1.0)
            return np.where(frac < p.get("am_duty", 0.5), 1.0, 1.0 - depth).astype(np.float32)
        return 1 + np.float32(depth) * self._phasor(am_hz, rate, n0, n).real

    def _pm(self, p: dict, x: np.ndarray, n0: int, n: int, rate: float) -> np.ndarray:
        """Phase modulation shared by every line (PWM sidebands at multiples of pm_hz)."""
        index = p.get("pm_index", 0.0)
        if not index:
            return x
        t = (n0 + np.arange(n)) / rate
        return x * np.exp(1j * index * np.sin(2 * np.pi * p["pm_hz"] * t)).astype(np.complex64)

    def _phase_walk(self, index: int, n: int, rate: float, fwhm_hz: float) -> np.ndarray:
        """Random-walk phase noise; turns a carrier into a Lorentzian line fwhm_hz wide."""
        sigma = math.sqrt(2 * math.pi * fwhm_hz / rate)
        phase = self._walk.get(index, 0.0) + np.cumsum(self._rng.standard_normal(n) * sigma)
        self._walk[index] = float(phase[-1] % (2 * math.pi))
        return np.exp(1j * phase).astype(np.complex64)

    def _gate(self, p: dict, index: int, n0: int, n: int, rate: float) -> np.ndarray:
        """On/off keying in slots of gate_slot_s seconds; each slot is on with probability gate_p."""
        slot = max(1, int(round(p["gate_slot_s"] * rate)))
        first, last = n0 // slot, (n0 + n - 1) // slot
        on = np.array([np.random.default_rng([self._seed, index, 7919, m]).random() < p.get("gate_p", 0.5)
                       for m in range(first, last + 1)], np.float32)
        return on[(n0 + np.arange(n)) // slot - first]


    def _buzz(self, src, n0, n, fc, rate, amp):
        p = src.params
        bw = p.get("bw_hz", 120_000.0)
        off = src.freq_hz - fc
        if abs(off) - bw / 2 >= rate / 2:
            return None
        # Band-limited white noise drawn directly in the frequency domain: only the in-band bins
        # get complex Gaussian values, scaled by sqrt(n) so each bin has the statistics of the FFT
        # of unit-variance complex white noise (the same result as filtering white noise).
        f = np.fft.fftfreq(n, 1 / rate)
        band = np.flatnonzero(np.abs(f - off) <= bw / 2)
        spec = np.zeros(n, np.complex64)
        spec[band] = self._rng.standard_normal(2 * band.size, dtype=np.float32).view(np.complex64)
        x = np.fft.ifft(spec) * np.float32(amp * np.sqrt(n) / np.sqrt(2 * bw / rate))
        # |sin(pi*am*t)| = |Im exp(2j*pi*(am/2)*t)|: rectified mains
        mains = self._phasor(p.get("am_hz", 100.0) / 2, rate, n0, n).imag
        return x * (np.abs(mains) * np.float32(np.sqrt(2)))

    def _ofdm(self, src, n0, n, fc, rate, amp):
        p = src.params
        bw = p.get("bw_hz", 400_000.0)
        spacing = p.get("spacing_hz", 4312.5)
        off = src.freq_hz - fc
        if off >= rate / 2 or off + bw <= -rate / 2:
            return None
        symbols, symbol_rate = int(p.get("symbols", 64)), float(p.get("symbol_rate", 4000.0))
        notches = tuple((float(a), float(b)) for a, b in p.get("notches", ()))
        key = (rate, bw, spacing, off, symbols, symbol_rate, notches)
        wave = self._ofdm_cache.get(key)
        if wave is None:
            wave = self._ofdm_cache[key] = _ofdm_wave(rate, bw, spacing, self._seed, shift_hz=off,
                                                      symbols=symbols, symbol_rate=symbol_rate,
                                                      notches=notches)
        seg = np.take(wave, np.arange(n) + n0 % wave.size, mode="wrap")
        return (amp * seg) * self._phasor(off, rate, n0, n)


    def _impulses(self, src, index, n, n0, rate, amp):
        """Bursts are generated per burst id from a seeded RNG, so their content does not depend on
        how reads are chunked; a burst straddling a read boundary is split across reads."""
        p = src.params
        length = max(2, int(round(p["burst_s"] * rate))) if "burst_s" in p else int(p.get("burst_len", 32))
        out = np.zeros(n, np.complex64)
        decay = np.exp(-np.arange(length) / (length / 4))
        for s, gain, burst_id in self._burst_starts(p, index, n0, n, rate, length):
            if s >= n0 + n or s + length <= n0:
                continue
            rng = np.random.default_rng([self._seed, index, burst_id])
            burst = ((rng.normal(size=length) + 1j * rng.normal(size=length)) * decay
                     * (float(amp) * gain / np.sqrt(2)))
            a, b = max(s, n0), min(s + length, n0 + n)
            out[a - n0:b - n0] += burst[a - s:b - s]
        return out

    def _burst_starts(self, p, index, n0, n, rate, length):
        """(start sample, amplitude factor, burst id) for every burst that may overlap [n0, n0 + n)."""
        times = p.get("times_s")
        if times:  # explicit click times, repeated every repeat_s seconds
            rep = p.get("repeat_s", 5.0)
            c_lo = max(0, int(np.floor((n0 - length) / rate / rep)))
            c_hi = int(np.floor((n0 + n) / rate / rep))
            return [(int(round((c * rep + t) * rate)), 1.0, c * 1000 + i)
                    for c in range(c_lo, c_hi + 1) for i, t in enumerate(times)]
        r = p.get("rate_hz", 100.0)
        phase, jitter = p.get("phase_s", 0.0), p.get("jitter_s", 0.0)
        p_fire, amp_jitter = p.get("p_fire", 1.0), p.get("amp_jitter", 0.0)
        randomised = jitter > 0 or p_fire < 1 or amp_jitter > 0
        pad = jitter * rate + length
        m_lo = max(0, int(np.floor(((n0 - pad) / rate - phase) * r)))
        m_hi = int(np.ceil(((n0 + n + pad) / rate - phase) * r)) + 1
        out = []
        for m in range(m_lo, m_hi):
            dt, gain = 0.0, 1.0
            if randomised:
                g = np.random.default_rng([self._seed, index, m, 1])
                if g.random() >= p_fire:
                    continue
                dt = g.uniform(-jitter, jitter)
                gain = 1.0 + g.uniform(-amp_jitter, amp_jitter)
            out.append((int(round((phase + m / r + dt) * rate)), gain, m))
        return out


    def _pace(self, n: int, rate: float) -> None:
        self._paced += n
        delay = self._t0 + self._paced / rate - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif delay < -0.5:  # fell far behind (debugger, suspend): resynchronise
            self._t0 = time.monotonic()
            self._paced = 0


def _ofdm_wave(rate: float, bw: float, spacing: float, seed: int, shift_hz: float = 0.0,
               symbols: int = 64, symbol_rate: float = 4000.0,
               notches: tuple[tuple[float, float], ...] = ()) -> np.ndarray:
    """A looped VDSL-like OFDM block: QPSK carriers every `spacing` Hz, new phases each symbol.

    The block is built at baseband (carriers 0..bw, minus any notch [lo, hi) given as offsets from
    the lower edge) and normalised to unit total power, then every spectral bin that would fall
    outside the device span once shifted by `shift_hz` is zeroed.
    """
    rng = np.random.default_rng(seed + 7)
    seg = max(1, round(rate / symbol_rate))
    length = seg * symbols
    t = np.arange(length) / rate
    carriers = np.arange(int(bw // spacing)) * spacing
    phases = rng.integers(0, 4, (symbols, carriers.size)) * (np.pi / 2) + np.pi / 4
    keep = np.ones(carriers.size, bool)
    for lo, hi in notches:
        keep &= ~((carriers >= lo) & (carriers < hi))
    carriers, phases = carriers[keep], phases[:, keep]
    sym = np.arange(length) // seg
    wave = np.zeros(length, np.complex128)
    for j, f in enumerate(carriers):
        wave += np.exp(1j * (2 * np.pi * f * t + phases[sym, j]))
    wave /= np.sqrt(np.mean(np.abs(wave) ** 2))
    spec = np.fft.fft(wave)
    f = np.fft.fftfreq(length, 1 / rate) + shift_hz
    spec[(f < -rate / 2) | (f >= rate / 2)] = 0
    return np.fft.ifft(spec).astype(np.complex64)


# --- QRM profiles ------------------------------------------------------------------------------
#
# One generator per QRM class. The classifier tests, the ranker training tool and the
# "Synthetic: ..." demo devices all build their signals here. strength_db is the source level
# relative to the clip's total noise power (tone power for lines, total power for broadband
# sources). Impulsive sources add IMPULSE_PEAK_DB because their energy arrives in short bursts.

PROFILE_IDS: tuple[str, ...] = (
    "smps", "pv_inverter", "led_dimmer", "vdsl", "plc", "arcing", "plasma_tv",
    "pc_clock", "ethernet", "ev_charger", "electric_fence", "motor_brush", "vsd", "relay",
)
PROFILE_LABELS: dict[str, str] = {
    "smps": "SMPS / charger", "pv_inverter": "Solar PV inverter", "led_dimmer": "LED driver / dimmer",
    "vdsl": "VDSL / xDSL", "plc": "PLC / powerline adapter", "arcing": "Power-line arcing",
    "plasma_tv": "Plasma TV", "pc_clock": "PC / monitor / clock harmonics",
    "ethernet": "Ethernet / USB-C", "ev_charger": "EV / e-bike charger",
    "electric_fence": "Electric fence", "motor_brush": "Motor / brush noise",
    "vsd": "Variable-speed drive", "relay": "Thermostat / relay clicks",
}
CLIP_RATE = 250_000.0
CLIP_SECONDS = 5.0
CLIP_CENTER_HZ = 7_100_000.0
IMPULSE_PEAK_DB = 20.0
PROFILE_HALF_SPAN_HZ = 100_000.0  # narrow profile sources stay within this distance of the centre
_PV_PERIOD_S = 120.0  # the PV drift is a slow sinusoid, near-linear over any few seconds
COMB_MAX_HZ = 2e9  # profile combs reach VHF/UHF (a comb without max_hz stops at 30 MHz)


def _pick(rng: np.random.Generator | None, lo: float, hi: float, default: float) -> float:
    return default if rng is None else float(rng.uniform(lo, hi))


def _offsets(rng: np.random.Generator | None, count: int, min_sep: float,
             default: tuple[float, ...]) -> list[float]:
    """Irregular carrier offsets from the centre, at least min_sep apart."""
    if rng is None:
        return list(default[:count])
    out: list[float] = []
    while len(out) < count:
        f = float(rng.uniform(-PROFILE_HALF_SPAN_HZ, PROFILE_HALF_SPAN_HZ))
        if all(abs(f - g) >= min_sep for g in out):
            out.append(f)
    return out


def profile_sources(name: str, center_hz: float = CLIP_CENTER_HZ, *, strength_db: float = 15.0,
                    noise_db: float = -45.0, mains_hz: int = 50,
                    rng: np.random.Generator | None = None) -> list[Source]:
    if name not in PROFILE_IDS:
        raise ValueError(f"unknown QRM profile {name!r}")
    level = noise_db + strength_db
    peak = level + IMPULSE_PEAK_DB
    ripple = 2.0 * mains_hz
    c = center_hz
    # Combs are harmonics k*s of a switching frequency s, so a line's excursion is k times the
    # spacing deviation. Scaling the deviation by the centre keeps line wobble and drift in Hz
    # the same at HF, VHF and UHF.
    fm_scale = CLIP_CENTER_HZ / max(center_hz, 1e6)
    if name == "smps":
        s = _pick(rng, 15_000, 45_000, 30_000)
        return [Source("smps_comb", 0.0, level, {
            "spacing_hz": s, "am_hz": ripple, "am_depth": _pick(rng, 0.25, 0.5, 0.35), "max_hz": COMB_MAX_HZ,
            "fm_dev_hz": _pick(rng, 0.3, 0.8, 0.5) * fm_scale, "fm_rate_hz": _pick(rng, 0.5, 2.0, 1.0)})]
    if name == "pv_inverter":
        s = _pick(rng, 18_000, 40_000, 25_000)
        drift = _pick(rng, 0.15, 0.4, 0.25) * (1 if rng is None else float(rng.choice([-1, 1])))
        return [Source("smps_comb", 0.0, level, {
            "spacing_hz": s, "am_hz": ripple, "am_depth": _pick(rng, 0.0, 0.1, 0.05), "max_hz": COMB_MAX_HZ,
            "fm_dev_hz": drift * fm_scale * _PV_PERIOD_S / (2 * math.pi), "fm_rate_hz": 1 / _PV_PERIOD_S,
            "fm_phase": 0.0})]
    if name == "led_dimmer":
        s = _pick(rng, 20_000, 60_000, 40_000)
        return [Source("smps_comb", 0.0, level, {
            "spacing_hz": s, "am_hz": ripple, "am_shape": "square", "am_depth": 1.0, "max_hz": COMB_MAX_HZ,
            "am_duty": _pick(rng, 0.3, 0.7, 0.5)})]
    if name == "vdsl":
        bw = _pick(rng, 120_000, 180_000, 150_000)
        lower = -_pick(rng, 70_000, 95_000, 90_000)
        notch = _pick(rng, 20_000, bw - 40_000, 50_000)
        return [Source("ofdm", c + lower, level, {
            "bw_hz": bw, "spacing_hz": 4312.5, "symbol_rate": 4000.0, "symbols": 1000,
            "notches": [(notch, notch + _pick(rng, 8_000, 20_000, 15_000))]})]
    if name == "plc":
        bw = _pick(rng, 150_000, 200_000, 180_000)
        return [Source("ofdm", c - bw / 2, level, {
            "bw_hz": bw, "spacing_hz": 24_414.0625, "symbol_rate": 20_000.0, "symbols": 2000,
            "gate_slot_s": 0.08, "gate_p": _pick(rng, 0.2, 0.45, 0.35)})]
    if name == "arcing":
        return [Source("impulses", 0.0, peak, {
            "rate_hz": ripple, "phase_s": 1 / (4 * mains_hz), "burst_s": 20e-6,
            "jitter_s": _pick(rng, 0.0002, 0.0008, 0.0005), "p_fire": _pick(rng, 0.6, 0.95, 0.85),
            "amp_jitter": 0.5})]
    if name == "plasma_tv":
        s = _pick(rng, 2_000, 5_000, 3_500)
        return [
            Source("smps_comb", 0.0, level, {"spacing_hz": s, "am_hz": ripple, "max_hz": COMB_MAX_HZ,
                                             "am_depth": _pick(rng, 0.6, 0.9, 0.8)}),
            Source("buzz", c + _pick(rng, -20_000, 20_000, 0.0), level - 6,
                   {"bw_hz": _pick(rng, 80_000, 150_000, 120_000), "am_hz": ripple}),
        ]
    if name == "pc_clock":
        count = 4 if rng is None else int(rng.integers(3, 7))
        offs = _offsets(rng, count, 5_000, (-83_100.0, -41_700.0, 12_300.0, 57_900.0))
        trims = (0.0, -3.0, 2.0, -1.0)
        return [Source("cw", c + f, level + _pick(rng, -3, 3, trims[i % 4])) for i, f in enumerate(offs)]
    if name == "ethernet":
        count = 3 if rng is None else int(rng.integers(2, 5))
        offs = _offsets(rng, count, 20_000, (-60_000.0, 25_000.0, 71_000.0))
        spread = _pick(rng, 200, 800, 400)
        return [Source("cw", c + f, level, {"spread_hz": spread}) for f in offs]
    if name == "ev_charger":
        s = _pick(rng, 15_000, 50_000, 25_000)
        return [Source("smps_comb", 0.0, level, {"spacing_hz": s, "am_hz": ripple, "max_hz": COMB_MAX_HZ,
                                                 "am_depth": _pick(rng, 0.75, 0.95, 0.9)})]
    if name == "electric_fence":
        period = _pick(rng, 0.8, 1.6, 1.1)
        return [Source("impulses", 0.0, peak + 6, {"rate_hz": 1 / period,
                                                   "phase_s": _pick(rng, 0.05, 0.6, 0.3),
                                                   "burst_s": 50e-6})]
    if name == "motor_brush":
        r = _pick(rng, 250, 700, 450)
        return [Source("impulses", 0.0, peak, {"rate_hz": r, "burst_s": 15e-6, "jitter_s": 0.08 / r,
                                               "amp_jitter": 0.5})]
    if name == "vsd":
        s = _pick(rng, 2_000, 12_000, 6_000)
        return [Source("smps_comb", 0.0, level, {
            "spacing_hz": s, "am_depth": 0.0, "max_hz": COMB_MAX_HZ, "pm_hz": 2 * _pick(rng, 20, 50, 40),
            "pm_index": _pick(rng, 1.8, 2.5, 2.0)})]
    # relay: a few clicks at irregular intervals (alternately short and long gaps)
    if rng is None:
        times = [0.4, 0.9, 2.9]
    else:
        times, t, short = [], float(rng.uniform(0.1, 0.8)), True
        while t < CLIP_SECONDS - 0.1:
            times.append(t)
            t += float(rng.uniform(0.3, 0.7) if short else rng.uniform(1.6, 2.6))
            short = not short
    return [Source("impulses", 0.0, peak, {"times_s": times, "repeat_s": CLIP_SECONDS,
                                           "burst_s": 200e-6})]


def render_clip(sources: list[Source], *, seconds: float = CLIP_SECONDS, rate: float = CLIP_RATE,
                center_hz: float = CLIP_CENTER_HZ, noise_db: float = -45.0, seed: int = 1) -> np.ndarray:
    """Render `sources` plus noise at any sample rate, as fast as possible."""
    dev = SyntheticDevice(list(sources), noise_db=noise_db, seed=seed, realtime=False)
    dev.info = replace(SYNTHETIC_INFO, sample_rates=(float(rate),))
    dev.open()
    dev.configure(DeviceConfig(center_hz=center_hz, sample_rate=rate))
    total = int(round(seconds * rate))
    parts, done = [], 0
    while done < total:
        k = min(65536, total - done)
        parts.append(dev.read(k))
        done += k
    return np.concatenate(parts) if parts else np.empty(0, np.complex64)


def render_profile_clip(name: str | None, strength_db: float = 15.0, *, seconds: float = CLIP_SECONDS,
                        rate: float = CLIP_RATE, center_hz: float = CLIP_CENTER_HZ,
                        noise_db: float = -45.0, mains_hz: int = 50, seed: int = 1) -> np.ndarray:
    sources = [] if name is None else profile_sources(name, center_hz, strength_db=strength_db,
                                                      noise_db=noise_db, mains_hz=mains_hz)
    return render_clip(sources, seconds=seconds, rate=rate, center_hz=center_hz, noise_db=noise_db, seed=seed)


def profile_infos() -> list[DeviceInfo]:
    return [replace(SYNTHETIC_INFO, id=f"synthetic:{p}", label=f"Synthetic: {PROFILE_LABELS[p]}")
            for p in PROFILE_IDS]

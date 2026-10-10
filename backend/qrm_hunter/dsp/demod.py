"""Stateful AM/SSB/CW/FM demodulator: NCO -> multistage FIR decimation -> channel filter -> detector -> AGC."""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from scipy.signal import firwin, lfilter, lfilter_zi, upfirdn

Mode = Literal["AM", "USB", "LSB", "CW", "FM"]
DEFAULT_BW: dict[str, float] = {"AM": 6000.0, "USB": 2700.0, "LSB": 2700.0, "CW": 500.0,
                                 "FM": 10_000.0}
CW_PITCH_HZ = 700.0
TARGET_AUDIO_RATE = 12_000.0
_CHANNEL_TAPS = 129
_AGC_TARGET = 0.3
FM_DEVIATION_HZ = 2500.0  # amateur narrowband FM; full-scale audio at this deviation
FM_AUDIO_CUTOFF_HZ = 3000.0  # 1-pole de-emphasis / hiss filter after the discriminator


def _prime_factors(n: int) -> list[int]:
    out, p = [], 2
    while p * p <= n:
        while n % p == 0:
            out.append(p)
            n //= p
        p += 1
    if n > 1:
        out.append(n)
    return out


def _stages(d: int, max_factor: int) -> list[int] | None:
    primes = _prime_factors(d)
    if any(p > max_factor for p in primes):
        return None
    stages, cur = [], 1
    for p in sorted(primes, reverse=True):
        if cur * p <= max_factor:
            cur *= p
        else:
            stages.append(cur)
            cur = p
    stages.append(cur)
    return sorted((s for s in stages if s > 1), reverse=True) or [1]


def choose_decimation(in_rate: float, target: float = TARGET_AUDIO_RATE, max_factor: int = 16) -> list[int]:
    """Pick a total decimation near in_rate/target that splits into stages of at most max_factor."""
    ideal = in_rate / target
    best: tuple[float, list[int]] | None = None
    for d in range(max(1, int(ideal * 0.85)), int(ideal * 1.15) + 2):
        stages = _stages(d, max_factor)
        if stages is None:
            continue
        err = abs(in_rate / d - target)
        if best is None or err < best[0]:
            best = (err, stages)
    if best is None:
        raise ValueError(f"cannot decimate {in_rate} Hz to about {target} Hz")
    return best[1]


class Decimator:
    """FIR low-pass + downsample by `factor`, with state carried between calls."""

    def __init__(self, factor: int, cutoff: float = 0.8) -> None:
        self.factor = factor
        self.taps = firwin(10 * factor + 1, cutoff / factor) if factor > 1 else np.array([1.0])
        self._hist = np.zeros(self.taps.size - 1, np.complex64)
        self._phase = 0  # index in the next block of the first sample to keep

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.complex64)
        if self.factor == 1:
            return x
        if x.size == 0:
            return np.empty(0, np.complex64)
        buf = np.concatenate([self._hist, x])
        n_win = buf.size - self.taps.size + 1  # number of full FIR windows
        count = max(0, -(-(n_win - self._phase) // self.factor))
        if count:
            # taps-1 == 10*factor, so full-convolution index (taps-1 + m*factor) is output
            # row (10 + m) of the decimated upfirdn result on buf[phase:].
            full = upfirdn(self.taps, buf[self._phase:], up=1, down=self.factor)
            y = full[10:10 + count]
        else:
            y = np.empty(0, np.complex64)
        self._hist = buf[buf.size - (self.taps.size - 1):].copy()
        self._phase = (self._phase - x.size) % self.factor
        return y.astype(np.complex64)


class Demodulator:
    def __init__(self, in_rate: float, mode: Mode = "AM", bw_hz: float | None = None,
                 offset_hz: float = 0.0, agc: bool = True) -> None:
        self.in_rate = float(in_rate)
        factors = choose_decimation(self.in_rate)
        self._stages = [Decimator(f) for f in factors]
        self.out_rate = self.in_rate / math.prod(factors)
        self.agc_enabled = agc
        self._nco_phase = 0.0
        self._rot_key: tuple[int, float] | None = None
        self._rot = np.empty(0, np.complex64)
        self._bfo_phase = 0.0
        self._gain = 1.0
        self._dc_zi = np.zeros(1)
        self._dc_primed = False
        self._fm_prev = np.complex64(0)
        self._fm_lp_zi = np.zeros(1)
        self.offset_hz = 0.0
        self.set_offset(offset_hz)
        self.set_mode(mode, bw_hz)

    def set_offset(self, hz: float) -> None:
        if abs(hz) >= self.in_rate / 2:
            raise ValueError(f"offset {hz} Hz is outside the ±{self.in_rate / 2} Hz span")
        self.offset_hz = float(hz)

    def set_mode(self, mode: Mode, bw_hz: float | None = None) -> None:
        if mode not in DEFAULT_BW:
            raise ValueError(f"unknown mode {mode!r}")
        bw = float(DEFAULT_BW[mode] if bw_hz is None else bw_hz)
        if mode in ("AM", "FM"):
            lo, hi = -bw / 2, bw / 2
        elif mode == "USB":
            lo, hi = 100.0, bw + 100.0
        elif mode == "LSB":
            lo, hi = -(bw + 100.0), -100.0
        else:  # CW, after the BFO shift
            lo, hi = CW_PITCH_HZ - bw / 2, CW_PITCH_HZ + bw / 2
        if not bw >= 50.0 or max(abs(lo), abs(hi)) >= 0.45 * self.out_rate:
            raise ValueError(f"bandwidth {bw_hz} Hz invalid for mode {mode} (need >= 50 Hz and "
                             f"passband edge < {0.45 * self.out_rate:.0f} Hz)")
        self.mode = mode
        self.bw_hz = bw
        centre, half = (lo + hi) / 2, (hi - lo) / 2
        n = np.arange(_CHANNEL_TAPS)
        lp = firwin(_CHANNEL_TAPS, half, fs=self.out_rate)
        self._chan = (lp * np.exp(2j * np.pi * centre * n / self.out_rate)).astype(np.complex64)
        self._chan_zi = np.zeros(_CHANNEL_TAPS - 1, np.complex64)
        self._dc_primed = False

    def process(self, iq: np.ndarray) -> np.ndarray:
        x = np.asarray(iq, np.complex64)
        step = -2 * np.pi * self.offset_hz / self.in_rate
        key = (x.size, step)
        if self._rot_key != key:  # cache the per-block rotator; block sizes are usually constant
            self._rot = np.exp(1j * step * np.arange(x.size)).astype(np.complex64)
            self._rot_key = key
        x = x * self._rot * np.complex64(np.exp(1j * self._nco_phase))
        self._nco_phase = float((self._nco_phase + step * x.size) % (2 * np.pi))
        for stage in self._stages:
            x = stage.process(x)
        if x.size == 0:
            return np.empty(0, np.int16)
        if self.mode == "CW":
            bstep = 2 * np.pi * CW_PITCH_HZ / self.out_rate
            x = x * np.exp(1j * (self._bfo_phase + bstep * np.arange(x.size))).astype(np.complex64)
            self._bfo_phase = float((self._bfo_phase + bstep * x.size) % (2 * np.pi))
        y, self._chan_zi = lfilter(self._chan, 1.0, x, zi=self._chan_zi)
        if self.mode == "AM":
            env = np.abs(y)
            if not self._dc_primed:  # start the DC blocker at the carrier level: no first-block thump
                self._dc_zi = lfilter_zi([1.0, -1.0], [1.0, -0.995]) * float(np.mean(env))
                self._dc_primed = True
            audio, self._dc_zi = lfilter([1.0, -1.0], [1.0, -0.995], env, zi=self._dc_zi)
        elif self.mode == "FM":
            audio = self._fm_detect(y)
        else:
            audio = y.real
        if self.agc_enabled and self.mode != "FM":  # FM audio level is set by the deviation
            audio = self._agc(np.asarray(audio, np.float64))
        return np.clip(np.rint(np.asarray(audio) * 32767), -32768, 32767).astype(np.int16)

    def _fm_detect(self, y: np.ndarray) -> np.ndarray:
        """Quadrature discriminator (phase step between samples) -> de-emphasis -> DC block."""
        prev = np.concatenate(([self._fm_prev], y[:-1]))
        self._fm_prev = np.complex64(y[-1])
        freq = np.angle(y * np.conj(prev)) * (self.out_rate / (2 * np.pi * FM_DEVIATION_HZ))
        a = 1.0 - math.exp(-2 * math.pi * FM_AUDIO_CUTOFF_HZ / self.out_rate)
        lp, self._fm_lp_zi = lfilter([a], [1.0, a - 1.0], freq, zi=self._fm_lp_zi)
        # The DC blocker removes the constant term a mistuned carrier produces.
        if not self._dc_primed:
            self._dc_zi = lfilter_zi([1.0, -1.0], [1.0, -0.995]) * float(np.mean(lp))
            self._dc_primed = True
        audio, self._dc_zi = lfilter([1.0, -1.0], [1.0, -0.995], lp, zi=self._dc_zi)
        return audio * _AGC_TARGET

    def _agc(self, a: np.ndarray) -> np.ndarray:
        out = np.empty_like(a)
        sub = max(1, int(self.out_rate * 0.01))
        for i in range(0, a.size, sub):
            seg = a[i:i + sub]
            desired = _AGC_TARGET / (float(np.max(np.abs(seg))) + 1e-9)
            if desired < self._gain:
                self._gain = desired  # fast attack
            else:
                self._gain += (desired - self._gain) * 0.02  # slow release
            self._gain = min(self._gain, 1e5)
            out[i:i + sub] = seg * self._gain
        return out

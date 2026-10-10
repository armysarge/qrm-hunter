"""Stateful AM/SSB/CW/FM/WFM demodulator: NCO -> multistage FIR decimation -> channel filter -> detector -> AGC.

Broadcast FM (WFM) decimates to an MPX rate of about 250 kHz, discriminates to the MPX signal (kept
in `mpx` for the RDS decoder), detects the 19 kHz stereo pilot, then low-passes, decimates and
de-emphasises the mono sum to about 32 kHz audio.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from scipy.signal import firwin, lfilter, lfilter_zi, oaconvolve, upfirdn

Mode = Literal["AM", "USB", "LSB", "CW", "FM", "WFM"]
DEFAULT_BW: dict[str, float] = {"AM": 6000.0, "USB": 2700.0, "LSB": 2700.0, "CW": 500.0,
                                 "FM": 10_000.0, "WFM": 180_000.0}
CW_PITCH_HZ = 700.0
TARGET_AUDIO_RATE = 12_000.0
# Wider FM channels (12.5/25 kHz VHF/UHF spacing) need more audio-rate headroom for the channel filter.
FM_AUDIO_RATE = 24_000.0
_CHANNEL_TAPS = 129
_AGC_TARGET = 0.3
FM_DEVIATION_HZ = 2500.0  # full-scale audio deviation at a 10 kHz channel; scales with bandwidth
FM_AUDIO_CUTOFF_HZ = 3000.0  # 1-pole de-emphasis / hiss filter after the discriminator

WFM_MPX_RATE = 250_000.0
WFM_AUDIO_RATE = 32_000.0
WFM_MIN_RATE = 1_000_000.0
WFM_DEVIATION_HZ = 75_000.0  # MPX = 1.0 at full broadcast deviation
WFM_AUDIO_CUTOFF_HZ = 15_000.0
WFM_AUDIO_GAIN = 0.5
_WFM_CHANNEL_TAPS = 129
PILOT_HZ = 19_000.0


def deemphasis_for(region: int) -> float:
    """Broadcast FM de-emphasis time constant: 75 us in IARU Region 2 (the Americas), 50 us elsewhere."""
    return 75e-6 if region == 2 else 50e-6


def _target_rate(mode: str) -> float:
    return {"FM": FM_AUDIO_RATE, "WFM": WFM_MPX_RATE}.get(mode, TARGET_AUDIO_RATE)


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
    """FIR low-pass (span*factor + 1 taps) + downsample by `factor`, with state carried between calls."""

    def __init__(self, factor: int, cutoff: float = 0.8, span: int = 10, dtype=np.complex64) -> None:
        self.factor = factor
        self.span = span
        self.dtype = dtype
        self.taps = firwin(span * factor + 1, cutoff / factor) if factor > 1 else np.array([1.0])
        self._hist = np.zeros(self.taps.size - 1, dtype)
        self._phase = 0  # index in the next block of the first sample to keep

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, self.dtype)
        if self.factor == 1:
            return x
        if x.size == 0:
            return np.empty(0, self.dtype)
        buf = np.concatenate([self._hist, x])
        n_win = buf.size - self.taps.size + 1  # number of full FIR windows
        count = max(0, -(-(n_win - self._phase) // self.factor))
        if count:
            # taps-1 == span*factor, so full-convolution index (taps-1 + m*factor) is output
            # row (span + m) of the decimated upfirdn result on buf[phase:].
            full = upfirdn(self.taps, buf[self._phase:], up=1, down=self.factor)
            y = full[self.span:self.span + count]
        else:
            y = np.empty(0, self.dtype)
        self._hist = buf[buf.size - (self.taps.size - 1):].copy()
        self._phase = (self._phase - x.size) % self.factor
        return y.astype(self.dtype)


class Mixer:
    """Multiplies blocks by exp(-j*2*pi*freq*k/rate) with a continuous phase. The per-block
    rotator is cached (block sizes are usually constant), so each block costs one multiply."""

    def __init__(self, freq_hz: float, rate: float) -> None:
        self.step = 2 * math.pi * freq_hz / rate
        self.phase = 0.0
        self._n = -1
        self._rot = np.empty(0, np.complex64)

    def mix(self, x: np.ndarray) -> np.ndarray:
        n = x.size
        if n != self._n:
            self._rot = np.exp(-1j * self.step * np.arange(n)).astype(np.complex64)
            self._n = n
        y = x * self._rot * np.complex64(np.exp(-1j * self.phase))
        self.phase = (self.phase + self.step * n) % (2 * math.pi)
        return y


class PilotDetector:
    """Measures the 19 kHz stereo pilot in 20 ms windows: level (MPX units, 1.0 = full deviation),
    frequency offset from the window-to-window phase step, and a hysteretic on/off flag."""

    WINDOW_S = 0.02
    ON_LEVEL = 0.03  # a broadcast pilot is 0.08-0.10
    OFF_LEVEL = 0.02

    def __init__(self, rate: float) -> None:
        self.rate = float(rate)
        self._win = max(1, int(self.rate * self.WINDOW_S))
        self.reset()

    def reset(self) -> None:
        self._mixer = Mixer(PILOT_HZ, self.rate)
        self._acc = 0j
        self._count = 0
        self._last_angle: float | None = None
        self.level = 0.0
        self.offset_hz = 0.0
        self.stereo = False

    def process(self, x: np.ndarray) -> None:
        n = x.size
        if n == 0:
            return
        prod = self._mixer.mix(x)
        i = 0
        while i < n:
            take = min(n - i, self._win - self._count)
            self._acc += complex(prod[i:i + take].sum())
            self._count += take
            i += take
            if self._count == self._win:
                self._window(self._acc / self._win)
                self._acc, self._count = 0j, 0

    def _window(self, z: complex) -> None:
        amp = 2 * abs(z)
        self.level += 0.3 * (amp - self.level)
        angle = math.atan2(z.imag, z.real)
        if self._last_angle is not None and amp > self.OFF_LEVEL:
            step = (angle - self._last_angle + math.pi) % (2 * math.pi) - math.pi
            df = step / (2 * math.pi * self._win / self.rate)
            self.offset_hz += 0.2 * (df - self.offset_hz)
        self._last_angle = angle
        if self.stereo and self.level < self.OFF_LEVEL:
            self.stereo = False
        elif not self.stereo and self.level > self.ON_LEVEL:
            self.stereo = True


class Demodulator:
    def __init__(self, in_rate: float, mode: Mode = "AM", bw_hz: float | None = None,
                 offset_hz: float = 0.0, agc: bool = True, deemphasis_s: float = 50e-6) -> None:
        self.in_rate = float(in_rate)
        self._target_rate = 0.0
        self._stages: list[Decimator] = []
        self.stage_rate = 0.0  # rate after the decimation stages (the MPX rate for WFM)
        self.out_rate = 0.0  # audio rate
        self.deemphasis_s = float(deemphasis_s)
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
        self.mpx = np.empty(0, np.float32)  # WFM: the latest block's MPX, for the RDS decoder
        self.mpx_rate = 0.0
        self.stereo = False  # WFM: 19 kHz pilot present
        self.offset_hz = 0.0
        self.set_offset(offset_hz)
        self.set_mode(mode, bw_hz)

    def _plan_stages(self, target: float) -> tuple[list[Decimator], float]:
        factors = choose_decimation(self.in_rate, target)
        return [Decimator(f) for f in factors], self.in_rate / math.prod(factors)

    def set_offset(self, hz: float) -> None:
        if abs(hz) >= self.in_rate / 2:
            raise ValueError(f"offset {hz} Hz is outside the ±{self.in_rate / 2} Hz span")
        self.offset_hz = float(hz)

    def set_mode(self, mode: Mode, bw_hz: float | None = None) -> None:
        if mode not in DEFAULT_BW:
            raise ValueError(f"unknown mode {mode!r}")
        if mode == "WFM" and self.in_rate < WFM_MIN_RATE:
            raise ValueError("Broadcast FM needs a sample rate of at least 1 MS/s")
        bw = float(DEFAULT_BW[mode] if bw_hz is None else bw_hz)
        target = _target_rate(mode)
        if target != self._target_rate:
            stages, stage_rate = self._plan_stages(target)
        else:
            stages, stage_rate = self._stages, self.stage_rate
        if mode in ("AM", "FM", "WFM"):
            lo, hi = -bw / 2, bw / 2
        elif mode == "USB":
            lo, hi = 100.0, bw + 100.0
        elif mode == "LSB":
            lo, hi = -(bw + 100.0), -100.0
        else:  # CW, after the BFO shift
            lo, hi = CW_PITCH_HZ - bw / 2, CW_PITCH_HZ + bw / 2
        if not bw >= 50.0 or max(abs(lo), abs(hi)) >= 0.45 * stage_rate:
            # nothing has been changed yet: the demodulator stays exactly as it was
            raise ValueError(f"bandwidth {bw_hz} Hz invalid for mode {mode} (need >= 50 Hz and "
                             f"passband edge < {0.45 * stage_rate:.0f} Hz)")
        self._stages, self.stage_rate, self._target_rate = stages, stage_rate, target
        self.mode = mode
        self.bw_hz = bw
        self._dc_primed = False
        self.mpx = np.empty(0, np.float32)
        self.stereo = False
        if mode == "WFM":
            self._setup_wfm(bw)
            return
        self.out_rate = stage_rate
        centre, half = (lo + hi) / 2, (hi - lo) / 2
        n = np.arange(_CHANNEL_TAPS)
        lp = firwin(_CHANNEL_TAPS, half, fs=self.out_rate)
        self._chan = (lp * np.exp(2j * np.pi * centre * n / self.out_rate)).astype(np.complex64)
        self._chan_zi = np.zeros(_CHANNEL_TAPS - 1, np.complex64)

    def _setup_wfm(self, bw: float) -> None:
        rate = self.mpx_rate = self.stage_rate
        # Real taps (the channel is centred on the carrier), applied by overlap-add: a 129-tap
        # direct-form filter at 256 kHz would cost more than the rest of the chain.
        self._wfm_taps = firwin(_WFM_CHANNEL_TAPS, bw / 2, fs=rate).astype(np.float32)
        self._wfm_hist = np.zeros(_WFM_CHANNEL_TAPS - 1, np.complex64)
        self._fm_prev = np.complex64(0)
        factor = max(1, round(rate / WFM_AUDIO_RATE))
        self.out_rate = rate / factor
        cutoff = min(WFM_AUDIO_CUTOFF_HZ, 0.47 * self.out_rate)
        self._audio_dec = Decimator(factor, cutoff=cutoff / (rate / 2) * factor, span=16, dtype=np.float32)
        self._pilot = PilotDetector(rate)
        self._deemph_zi = np.zeros(1)
        self.set_deemphasis(self.deemphasis_s)

    def set_deemphasis(self, tau_s: float) -> None:
        """De-emphasis time constant for WFM audio (50 us or 75 us)."""
        self.deemphasis_s = float(tau_s)
        if self.out_rate > 0:
            self._deemph_a = math.exp(-1.0 / (self.deemphasis_s * self.out_rate))

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
        if self.mode == "WFM":
            return self._wfm(x)
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

    def _wfm(self, x: np.ndarray) -> np.ndarray:
        """Channel filter -> discriminator (MPX) -> pilot detection -> 15 kHz low-pass and decimate
        -> de-emphasis -> DC block. The audio level is set by the deviation, not by AGC."""
        if x.size == 0:
            self.mpx = np.empty(0, np.float32)
            return np.empty(0, np.int16)
        buf = np.concatenate([self._wfm_hist, x])
        y = oaconvolve(buf, self._wfm_taps, mode="valid").astype(np.complex64)
        self._wfm_hist = buf[buf.size - (_WFM_CHANNEL_TAPS - 1):]
        prev = np.concatenate(([self._fm_prev], y[:-1]))
        self._fm_prev = np.complex64(y[-1])
        scale = np.float32(self.mpx_rate / (2 * np.pi * WFM_DEVIATION_HZ))
        self.mpx = np.angle(y * np.conj(prev)).astype(np.float32) * scale
        self._pilot.process(self.mpx)
        self.stereo = self._pilot.stereo
        mono = self._audio_dec.process(self.mpx)
        if mono.size == 0:
            return np.empty(0, np.int16)
        a = self._deemph_a
        de, self._deemph_zi = lfilter([1.0 - a], [1.0, -a], mono, zi=self._deemph_zi)
        if not self._dc_primed:  # a mistuned carrier gives a constant offset: start the blocker there
            self._dc_zi = lfilter_zi([1.0, -1.0], [1.0, -0.995]) * float(np.mean(de))
            self._dc_primed = True
        audio, self._dc_zi = lfilter([1.0, -1.0], [1.0, -0.995], de, zi=self._dc_zi)
        return np.clip(np.rint(audio * (WFM_AUDIO_GAIN * 32767)), -32768, 32767).astype(np.int16)

    def _fm_detect(self, y: np.ndarray) -> np.ndarray:
        """Quadrature discriminator (phase step between samples) -> de-emphasis -> DC block."""
        prev = np.concatenate(([self._fm_prev], y[:-1]))
        self._fm_prev = np.complex64(y[-1])
        deviation = FM_DEVIATION_HZ * max(1.0, self.bw_hz / 10_000.0)
        freq = np.angle(y * np.conj(prev)) * (self.out_rate / (2 * np.pi * deviation))
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

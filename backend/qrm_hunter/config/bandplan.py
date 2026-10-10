"""HF, VHF and UHF amateur band plans for IARU Regions 1, 2 and 3, plus the FM broadcast band."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Band:
    name: str
    lo_hz: int
    hi_hz: int
    group: str = "HF"  # "HF", "VHF", "UHF" or "BC" (broadcast)

    @property
    def default_hz(self) -> int:
        """Band centre rounded to the nearest kHz."""
        return round((self.lo_hz + self.hi_hz) / 2 / 1000) * 1000


_UPPER = [
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("10m", 28_000_000, 29_700_000),
]

_LOWER = {
    1: [("160m", 1_810_000, 2_000_000), ("80m", 3_500_000, 3_800_000),
        ("60m", 5_351_500, 5_366_500), ("40m", 7_000_000, 7_200_000)],
    2: [("160m", 1_800_000, 2_000_000), ("80m", 3_500_000, 4_000_000),
        ("60m", 5_330_500, 5_406_500), ("40m", 7_000_000, 7_300_000)],
    3: [("160m", 1_800_000, 2_000_000), ("80m", 3_500_000, 3_900_000),
        ("60m", 5_351_500, 5_366_500), ("40m", 7_000_000, 7_300_000)],
}


_VHF_UHF = {
    1: [("6m", 50_000_000, 52_000_000, "VHF"), ("4m", 70_000_000, 70_500_000, "VHF"),
        ("2m", 144_000_000, 146_000_000, "VHF"), ("70cm", 430_000_000, 440_000_000, "UHF"),
        ("23cm", 1_240_000_000, 1_300_000_000, "UHF")],
    2: [("6m", 50_000_000, 54_000_000, "VHF"), ("2m", 144_000_000, 148_000_000, "VHF"),
        ("1.25m", 222_000_000, 225_000_000, "VHF"), ("70cm", 420_000_000, 450_000_000, "UHF"),
        ("23cm", 1_240_000_000, 1_300_000_000, "UHF")],
    3: [("6m", 50_000_000, 54_000_000, "VHF"), ("2m", 144_000_000, 148_000_000, "VHF"),
        ("70cm", 430_000_000, 440_000_000, "UHF"), ("23cm", 1_240_000_000, 1_300_000_000, "UHF")],
}


# Not an amateur band: broadcast FM, for listening (WFM with RDS).
_BROADCAST = {
    1: [("FM BC", 87_500_000, 108_000_000, "BC")],
    2: [("FM BC", 88_000_000, 108_000_000, "BC")],
    3: [("FM BC", 87_500_000, 108_000_000, "BC")],
}


def bands_for_region(region: int) -> list[Band]:
    """All bands for a region, in frequency order."""
    if region not in _LOWER:
        raise ValueError(f"unknown IARU region {region!r} (expected 1, 2 or 3)")
    rows = _LOWER[region] + _UPPER + _VHF_UHF[region] + _BROADCAST[region]
    return sorted((Band(*row) for row in rows), key=lambda b: b.lo_hz)


def band_at(hz: float, region: int) -> Band | None:
    return next((b for b in bands_for_region(region) if b.lo_hz <= hz <= b.hi_hz), None)

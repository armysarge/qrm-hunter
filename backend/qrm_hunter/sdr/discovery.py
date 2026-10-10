"""Finds attached SDRs and builds device objects from their ids."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable

from .base import DeviceError, DeviceInfo, SdrDevice
from .replay import ReplayDevice
from .rtlsdr_dev import RtlSdrDevice, list_rtlsdr
from .soapy_dev import SoapyDevice, args_from_device_id, enumerate_soapy
from .synthetic import SYNTHETIC_INFO, SyntheticDevice

log = logging.getLogger(__name__)

Backend = tuple[str, Callable[[], list[DeviceInfo]]]
BACKENDS: list[Backend] = [
    ("synthetic", lambda: [SYNTHETIC_INFO]),
    ("rtlsdr", list_rtlsdr),
    ("soapy", enumerate_soapy),
]


def discover(backends: Iterable[Backend] | None = None) -> list[DeviceInfo]:
    found: list[DeviceInfo] = []
    for name, list_fn in backends if backends is not None else BACKENDS:
        try:
            found.extend(list_fn())
        except Exception as exc:  # a broken driver must never hide the others
            log.info("device backend %s unavailable: %s", name, exc)
    return found


def create_device(device_id: str, *, realtime: bool = True) -> SdrDevice:
    if device_id == "synthetic":
        return SyntheticDevice(realtime=realtime)
    if device_id.startswith("rtlsdr:"):
        try:
            index = int(device_id.removeprefix("rtlsdr:"))
        except ValueError as exc:
            raise DeviceError(f"bad RTL-SDR id {device_id!r}") from exc
        return RtlSdrDevice(index)
    if device_id.startswith("soapy:"):
        return SoapyDevice(args_from_device_id(device_id))
    if device_id.startswith("replay:"):
        return ReplayDevice(Path(device_id.removeprefix("replay:")), realtime=realtime)
    raise DeviceError(f"unknown device id {device_id!r}")

import pytest

from qrm_hunter.config.bandplan import band_at, bands_for_region

NAMES = ["160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m"]


@pytest.mark.parametrize("region", [1, 2, 3])
def test_every_region_has_the_ten_hf_bands_in_order(region):
    assert [b.name for b in bands_for_region(region) if b.group == "HF"] == NAMES


@pytest.mark.parametrize("region,names", [
    (1, ["6m", "4m", "2m", "70cm", "23cm"]),
    (2, ["6m", "2m", "1.25m", "70cm", "23cm"]),
    (3, ["6m", "2m", "70cm", "23cm"]),
])
def test_vhf_uhf_bands_follow_hf_in_frequency_order(region, names):
    bands = bands_for_region(region)
    assert [b.name for b in bands if b.group in ("VHF", "UHF")] == names
    assert [b.lo_hz for b in bands] == sorted(b.lo_hz for b in bands)
    assert band_at(145_500_000, region).name == "2m"


@pytest.mark.parametrize("region,lo", [(1, 87_500_000), (2, 88_000_000), (3, 87_500_000)])
def test_fm_broadcast_band_per_region(region, lo):
    bc = [b for b in bands_for_region(region) if b.group == "BC"]
    assert [(b.name, b.lo_hz, b.hi_hz) for b in bc] == [("FM BC", lo, 108_000_000)]
    assert band_at(100_000_000, region).group == "BC"


def test_region1_40m_edges():
    band = band_at(7_100_000, 1)
    assert band is not None
    assert (band.name, band.lo_hz, band.hi_hz) == ("40m", 7_000_000, 7_200_000)


def test_region2_40m_is_wider_than_region1():
    assert band_at(7_250_000, 2).name == "40m"
    assert band_at(7_250_000, 1) is None


@pytest.mark.parametrize("region", [1, 2, 3])
def test_default_frequency_lies_inside_its_band(region):
    for b in bands_for_region(region):
        assert b.lo_hz <= b.default_hz <= b.hi_hz
        assert b.default_hz % 1000 == 0


def test_unknown_region_rejected():
    with pytest.raises(ValueError):
        bands_for_region(4)

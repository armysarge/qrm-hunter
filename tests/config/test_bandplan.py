import pytest

from qrm_hunter.config.bandplan import band_at, bands_for_region

NAMES = ["160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m"]


@pytest.mark.parametrize("region", [1, 2, 3])
def test_every_region_has_the_ten_hf_bands_in_order(region):
    assert [b.name for b in bands_for_region(region)] == NAMES


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

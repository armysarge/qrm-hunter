import random
from datetime import UTC, datetime, timedelta, timezone

import numpy as np
import pytest

from qrm_hunter.dsp.rds import (
    LOSS_WINDOW,
    OFFSETS,
    SYNDROMES,
    BlockSync,
    GroupDecoder,
    RdsDecoder,
    checkword,
    decode_chars,
    encode_block,
    pty_name,
    syndrome,
)
from qrm_hunter.sdr.rds_encoder import (
    DemoStation,
    encode_text,
    group_0a,
    group_2a,
    group_3a,
    group_4a,
    group_bits,
    group_rtplus,
)

PI, PTY = 0x6201, 10


# --- block coding ----------------------------------------------------------------------------


def test_check_words_match_the_generator_matrix():
    # IEC 62106 Annex B generator matrix: first and last rows of the check-bit part
    assert checkword(1 << 15) == 0x077
    assert checkword(1) == 0x1B9
    assert checkword(0) == 0


def test_syndromes_of_the_offset_words():
    assert SYNDROMES == {"A": 0x3D8, "B": 0x3D4, "C": 0x25C, "C'": 0x3CC, "D": 0x258}


@pytest.mark.parametrize("offset", list(OFFSETS))
def test_encoded_blocks_give_their_offset_syndrome_and_bit_errors_change_it(offset):
    rng = random.Random(offset)
    for _ in range(50):
        block = encode_block(rng.getrandbits(16), offset)
        assert syndrome(block) == SYNDROMES[offset]
        for bit in range(26):  # the code detects every single-bit error
            assert syndrome(block ^ (1 << bit)) != SYNDROMES[offset]


# --- block sync --------------------------------------------------------------------------------


def stream(groups):
    return [b for g in groups for b in group_bits(g)]


def ps_groups(ps="QRM DEMO", ta=False, version=0):
    return [group_0a(PI, PTY, True, ta, True, seg, ps, version) for seg in range(4)]


def test_block_sync_acquires_at_any_bit_offset_and_returns_groups():
    rng = random.Random(1)
    bits = [rng.getrandbits(1) for _ in range(37)] + stream(ps_groups() * 3)
    sync = BlockSync()
    groups = [g for g in (sync.push(b) for b in bits) if g is not None]
    assert sync.synced
    assert sync.error_rate == 0.0
    assert groups[-1] == tuple(ps_groups()[3])
    assert len(groups) >= 10  # at most the first, partial group is lost


def test_block_sync_needs_consecutive_blocks_in_order():
    sync = BlockSync()
    for b in [(encode_block(PI, "A") >> (25 - i)) & 1 for i in range(26)]:
        sync.push(b)
    assert not sync.synced  # one valid block is not enough
    for b in [(encode_block(0x1234, "C") >> (25 - i)) & 1 for i in range(26)]:
        sync.push(b)
    assert not sync.synced  # A then C is out of order


def test_block_sync_is_lost_after_too_many_bad_blocks():
    sync = BlockSync()
    for b in stream(ps_groups() * 2):
        sync.push(b)
    assert sync.synced
    rng = random.Random(2)
    for i in range(26 * LOSS_WINDOW):
        sync.push(rng.getrandbits(1))
        if not sync.synced:
            break
    assert not sync.synced
    assert sync.error_rate == 1.0
    assert i < 26 * LOSS_WINDOW - 1


# --- group decoding ------------------------------------------------------------------------------


def decode_all(groups, region=1, dec=None):
    dec = dec or GroupDecoder(region)
    for g in groups:
        dec.decode(tuple(g))
    return dec


@pytest.mark.parametrize("version", [0, 1])
def test_ps_is_published_only_after_every_segment_is_seen_twice(version):
    dec = decode_all(ps_groups(version=version))
    assert dec.state.ps is None and dec.state.pi == "6201"
    decode_all(ps_groups(version=version)[:3], dec=dec)
    assert dec.state.ps is None
    decode_all(ps_groups(version=version)[3:], dec=dec)
    assert dec.state.ps == "QRM DEMO"
    # a new name replaces the old one only once it is complete and confirmed: no mixed names
    decode_all(ps_groups("NEW NAME", version=version), dec=dec)
    assert dec.state.ps == "QRM DEMO"
    decode_all(ps_groups("NEW NAME", version=version), dec=dec)
    assert dec.state.ps == "NEW NAME"


def rt_groups(text, ab=0, version=0):
    seg = 4 if version == 0 else 2
    n = -(-min(len(text) + 1, 64 if version == 0 else 32) // seg)
    return [group_2a(PI, PTY, True, ab, i, text, version) for i in range(n)]


def test_radiotext_ends_at_carriage_return_and_ab_flag_clears_the_buffer():
    dec = decode_all(rt_groups("Now playing: a rather long title for a test"))
    assert dec.state.rt == "Now playing: a rather long title for a test"
    dec = decode_all(rt_groups("Short", ab=1)[:1], dec=dec)  # "Shor" only: not complete yet
    assert dec.state.rt == "Now playing: a rather long title for a test"
    dec = decode_all(rt_groups("Short", ab=1), dec=dec)
    assert dec.state.rt == "Short"  # nothing left over from the longer text


def test_radiotext_without_terminator_is_published_when_all_64_characters_arrive():
    text = "x" * 64
    dec = decode_all(rt_groups(text)[:15])
    assert dec.state.rt is None
    assert decode_all(rt_groups(text)[15:], dec=dec).state.rt == text


def test_radiotext_version_b_carries_32_characters():
    dec = decode_all(rt_groups("Version B text", version=1))
    assert dec.state.rt == "Version B text"


def test_rtplus_extracts_artist_and_title_and_clears_on_text_change():
    text = "Now playing: ZS6SHZ - Noise Floor Blues"
    app = (11 << 1) | 0
    tags = [(1, 22, 17), (4, 13, 6)]
    dec = decode_all(rt_groups(text) + [group_3a(PI, PTY, True, app),
                                        group_rtplus(PI, PTY, True, app, 0, True, tags)])
    assert dec.state.rtplus == {"title": "Noise Floor Blues", "artist": "ZS6SHZ"}
    decode_all(rt_groups("Next song", ab=1)[:1], dec=dec)
    assert dec.state.rtplus is None


def test_rtplus_is_ignored_without_its_oda_announcement():
    text = "Now playing: ZS6SHZ - Noise Floor Blues"
    dec = decode_all(rt_groups(text) + [group_rtplus(PI, PTY, True, 22, 0, True, [(1, 22, 17)])])
    assert dec.state.rtplus is None


def test_ta_tp_ms_flags():
    dec = decode_all([group_0a(PI, PTY, True, True, False, 0, "X")])
    assert (dec.state.tp, dec.state.ta, dec.state.ms) == (True, True, False)
    decode_all([group_0a(PI, PTY, False, False, True, 1, "X")], dec=dec)
    assert (dec.state.tp, dec.state.ta, dec.state.ms) == (False, False, True)


@pytest.mark.parametrize("region,name", [(1, "Pop Music"), (2, "Country"), (3, "Pop Music")])
def test_pty_names_follow_the_region(region, name):
    dec = decode_all(ps_groups()[:1], region=region)
    assert dec.state.pty == 10 and dec.state.pty_name == name
    assert pty_name(31, region) == ("Emergency" if region == 2 else "Alarm")


def test_region_change_renames_the_pty():
    dec = decode_all(ps_groups()[:1], region=1)
    dec.set_region(2)
    assert dec.state.pty_name == "Country"


@pytest.mark.parametrize("offset", [timedelta(hours=2), timedelta(hours=-5), timedelta(hours=5, minutes=30)])
def test_clock_time_round_trip(offset):
    when = datetime(2026, 10, 10, 23, 45, tzinfo=UTC).astimezone(timezone(offset))
    dec = decode_all([group_4a(PI, PTY, True, when)])
    assert datetime.fromisoformat(dec.state.ct) == when
    assert dec.state.ct == when.isoformat()


def test_character_set():
    assert decode_chars(encode_text("QRM Demo!")) == "QRM Demo!"
    assert decode_chars([0x80, 0x8D, 0xA9, 0x91, 0xFF]) == "áß€ä "
    assert encode_text(" ") == [0x20]


def test_changed_flag():
    dec = GroupDecoder()
    dec.decode(tuple(ps_groups()[0]))
    assert dec.changed
    dec.changed = False
    dec.decode(tuple(ps_groups()[0]))
    assert not dec.changed  # nothing new


# --- the signal chain ------------------------------------------------------------------------------


def test_decoder_round_trip_from_mpx_with_irregular_blocks():
    rate = 256_000.0
    st = DemoStation(ta_period_s=2.0, ta_on_s=1.0)
    mpx = st.mpx(0, int(rate * 4.0), rate)
    mpx += 0.05 * np.random.default_rng(0).standard_normal(mpx.size).astype(np.float32)
    dec = RdsDecoder(rate)
    sizes, i, k, ta_seen = [1, 4096, 17, 9000, 333], 0, 0, set()
    while i < mpx.size:
        dec.process(mpx[i:i + sizes[k % len(sizes)]])
        i += sizes[k % len(sizes)]
        k += 1
        ta_seen.add(dec.state.ta)
    s = dec.state
    assert (s.pi, s.ps, s.pty, s.pty_name, s.tp, s.stereo, s.synced) == (
        "6201", "QRM DEMO", 10, "Pop Music", True, True, True)
    assert s.rt == "Now playing: ZS6SHZ - Noise Floor Blues"
    assert s.rtplus == {"title": "Noise Floor Blues", "artist": "ZS6SHZ"}
    assert ta_seen == {True, False}
    assert s.ct is not None and s.block_error_rate < 0.1


@pytest.mark.parametrize("ppm,pilot", [(-300, 0.0), (300, 0.09)])
def test_decoder_tracks_clock_error_with_and_without_pilot(ppm, pilot):
    rate = 256_000.0
    st = DemoStation(pilot_level=pilot)
    mpx = st.mpx(0, int(rate * 3.0), rate * (1 + ppm * 1e-6))  # subcarrier and bit rate both off
    dec = RdsDecoder(rate)
    for i in range(0, mpx.size, 5000):
        dec.process(mpx[i:i + 5000])
    assert dec.state.ps == "QRM DEMO"
    assert dec.state.stereo is (pilot > 0)


def test_noise_alone_never_syncs():
    rate = 256_000.0
    dec = RdsDecoder(rate)
    x = 0.3 * np.random.default_rng(5).standard_normal(int(rate * 2)).astype(np.float32)
    for i in range(0, x.size, 8192):
        dec.process(x[i:i + 8192])
    assert dec.state.pi is None and dec.state.ps is None and not dec.state.stereo

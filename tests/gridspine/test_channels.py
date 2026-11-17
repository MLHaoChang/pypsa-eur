"""GC-07 part 1: the channel frame and its CSV, COMTRADE and PSCAD readers."""

import struct

import numpy as np
import pytest

from gridspine.readback.channels import (
    ChannelFrame,
    read_comtrade,
    read_csv,
    read_pscad,
)
from gridspine.schema.contracts import ContractError

# A small COMTRADE recording, written by hand: two analog channels and one
# digital, three samples at 1 kHz. Analog 1 is recorded in secondary volts
# (PS = S) with a = 0.5, b = -1 and a 100:1 ratio; analog 2 in primary with
# a = 2, b = 0.25.
CFG_1999 = """\
TEST STATION,REC1,1999
3,2A,1D
1,VA,A,,V,0.5,-1,0,-32767,32767,10000,100,S
2,IA,A,,A,2,0.25,0,-32767,32767,1,1,P
1,TRIP,,,0
50
1
1000,3
01/01/2026,00:00:00.000000
01/01/2026,00:00:00.001000
{ft}
1
"""
DAT_ASCII = "1,0,10,0,0\n2,1000,12,-2,1\n3,2000,-4,4,0\n"
# Hand-computed: VA = (0.5 x - 1) * 10000 / 100; IA = 2 x + 0.25
VA = [
    (0.5 * 10 - 1) * 100,
    (0.5 * 12 - 1) * 100,
    (0.5 * -4 - 1) * 100,
]  # 400, 500, -300
IA = [0.25, -3.75, 8.25]


def _write(tmp_path, ft="ASCII", cfg=CFG_1999, dat=None, name="rec"):
    (tmp_path / f"{name}.cfg").write_text(cfg.format(ft=ft))
    if isinstance(dat, bytes):
        (tmp_path / f"{name}.dat").write_bytes(dat)
    else:
        (tmp_path / f"{name}.dat").write_text(DAT_ASCII if dat is None else dat)
    return tmp_path / f"{name}.cfg"


def _binary(analog_fmt="h", rows=((10, 0, 0), (12, -2, 1), (-4, 4, 0))):
    out = b""
    for i, (a1, a2, d) in enumerate(rows):
        out += (
            struct.pack("<II", i + 1, i * 1000)
            + struct.pack("<" + analog_fmt * 2, a1, a2)
            + struct.pack("<H", d)
        )
    return out


def test_comtrade_ascii_scales_a_x_plus_b_to_primary(tmp_path):
    f = read_comtrade(_write(tmp_path))
    assert VA == [400.0, 500.0, -300.0]
    np.testing.assert_allclose(f.channel("VA"), VA)
    np.testing.assert_allclose(f.channel("IA"), IA)
    np.testing.assert_array_equal(f.channel("TRIP"), [0, 1, 0])
    np.testing.assert_allclose(f.time, [0.0, 0.001, 0.002])
    assert f.units == {"VA": "V", "IA": "A", "TRIP": "status"}


def test_comtrade_secondary_kept_when_primary_false(tmp_path):
    f = read_comtrade(_write(tmp_path), primary=False)
    np.testing.assert_allclose(f.channel("VA"), [4.0, 5.0, -3.0])
    np.testing.assert_allclose(f.channel("IA"), IA)


@pytest.mark.parametrize(
    "ft,fmt", [("BINARY", "h"), ("BINARY32", "i"), ("FLOAT32", "f")]
)
def test_comtrade_binary_matches_ascii(tmp_path, ft, fmt):
    f = read_comtrade(_write(tmp_path, ft=ft, dat=_binary(fmt)))
    np.testing.assert_allclose(f.channel("VA"), VA)
    np.testing.assert_allclose(f.channel("IA"), IA)
    np.testing.assert_array_equal(f.channel("TRIP"), [0, 1, 0])


def test_comtrade_primary_file_read_as_secondary_divides_by_the_ratio(tmp_path):
    cfg = CFG_1999.replace(",1,1,P", ",400,5,P")
    np.testing.assert_allclose(
        read_comtrade(_write(tmp_path, cfg=cfg)).channel("IA"), IA
    )
    np.testing.assert_allclose(
        read_comtrade(_write(tmp_path, cfg=cfg), primary=False).channel("IA"),
        [x * 5 / 400 for x in IA],
    )


def test_comtrade_2013_nanosecond_stamps_and_99999_is_a_value(tmp_path):
    cfg = (
        CFG_1999.replace(",1999\n", ",2013\n")
        .replace("1\n1000,3\n", "0\n0,3\n")
        .replace("00:00:00.000000", "00:00:00.000000000")
    )
    f = read_comtrade(
        _write(tmp_path, cfg=cfg, dat="1,0,10,0,0\n2,250,99999,-2,1\n3,750,-4,4,0\n")
    )
    np.testing.assert_allclose(f.time, [0.0, 250e-9, 750e-9])
    assert f.channel("VA")[1] == pytest.approx((0.5 * 99999 - 1) * 100)


def test_comtrade_time_from_stamps_when_no_rate(tmp_path):
    cfg = CFG_1999.replace("1\n1000,3\n", "0\n0,3\n").replace("{ft}\n1\n", "{ft}\n2\n")
    f = read_comtrade(
        _write(tmp_path, cfg=cfg, dat="1,0,10,0,0\n2,250,12,-2,1\n3,750,-4,4,0\n")
    )
    # stamps in microseconds times timemult 2
    np.testing.assert_allclose(f.time, [0.0, 0.0005, 0.0015])


def test_comtrade_two_rates(tmp_path):
    cfg = CFG_1999.replace("1\n1000,3\n", "2\n1000,2\n500,3\n")
    f = read_comtrade(_write(tmp_path, cfg=cfg))
    np.testing.assert_allclose(f.time, [0.0, 0.001, 0.002])
    cfg = CFG_1999.replace("1\n1000,3\n", "2\n1000,1\n500,3\n")
    f = read_comtrade(_write(tmp_path, cfg=cfg))
    np.testing.assert_allclose(f.time, [0.0, 0.001, 0.003])


@pytest.mark.parametrize(
    "dat,match",
    [
        ("1,0,10,0,0\n2,1000,12,-2\n3,2000,-4,4,0\n", "row 2 has 4 fields"),
        ("1,0,10,0,0\n2,1000,,-2,1\n3,2000,-4,4,0\n", "'VA' is missing at row 2"),
        ("1,0,10,0,0\n2,1000,99999,-2,1\n3,2000,-4,4,0\n", "'VA' is missing at row 2"),
        ("1,0,10,0,0\n2,1000,12,-2,1\n", "states 3 samples, the data file has 2"),
        (
            "1,0,10,0,0\n2,1000,12,-2,2\n3,2000,-4,4,0\n",
            "'TRIP' has a value other than 0 or 1",
        ),
        ("1,0,10,0,0\n2,1000,x,-2,1\n3,2000,-4,4,0\n", "not a number"),
    ],
)
def test_comtrade_ascii_refusals(tmp_path, dat, match):
    with pytest.raises(ContractError, match=match):
        read_comtrade(_write(tmp_path, dat=dat))


def test_comtrade_binary_missing_value_refused(tmp_path):
    with pytest.raises(ContractError, match="'IA' is missing at sample 2"):
        read_comtrade(
            _write(
                tmp_path,
                ft="BINARY",
                dat=_binary(rows=((10, 0, 0), (12, -0x8000, 1), (-4, 4, 0))),
            )
        )
    with pytest.raises(ContractError, match="not a whole number"):
        read_comtrade(_write(tmp_path, ft="BINARY", dat=_binary()[:-1]))


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("3,2A,1D", "4,2A,1D", "4 channels stated"),
        ("3,2A,1D", "3,2A", "TT,##A,##D"),
        ("{ft}\n", "BINARY64\n", "data file type 'BINARY64'"),
        ("2,IA,A,", "2,VA,A,", r"\['VA'\] appear more than once"),
        (",10000,100,S", ",10000,0,S", "needs both above zero"),
        (",10000,100,S", ",10000,100,X", "PS 'X'; it must be P or S"),
        ("{ft}\n1\n", "{ft}\n0\n", "timemult must be above zero"),
        ("1,VA,A,,V,0.5,", "1,VA,A,,V,a,", "multiplier a is not a number"),
    ],
)
def test_comtrade_cfg_refusals(tmp_path, old, new, match):
    assert old in CFG_1999
    with pytest.raises(ContractError, match=match):
        read_comtrade(_write(tmp_path, cfg=CFG_1999.replace(old, new)))


def test_comtrade_truncated_cfg_and_missing_dat(tmp_path):
    with pytest.raises(ContractError, match="ends before the data file type line"):
        read_comtrade(
            _write(tmp_path, cfg="\n".join(CFG_1999.splitlines()[:10]) + "\n")
        )
    cfg = _write(tmp_path)
    cfg.with_suffix(".dat").unlink()
    with pytest.raises(ContractError, match="data file missing"):
        read_comtrade(cfg)


def test_csv_reader(tmp_path):
    p = tmp_path / "rms.csv"
    p.write_text("time [s],V_POC [pu],P [MW],flag []\n0,1.0,10,0\n0.01,0.5,9,1\n")
    f = read_csv(p)
    np.testing.assert_allclose(f.time, [0, 0.01])
    assert f.units == {"V_POC": "pu", "P": "MW", "flag": ""}
    np.testing.assert_allclose(f.channel("V_POC"), [1.0, 0.5])
    p.write_text("t,V [pu]\n0,1\n1,2\n")
    assert read_csv(p).units == {"V": "pu"}


@pytest.mark.parametrize(
    "text,match",
    [
        ("seconds,V [pu]\n0,1\n1,1\n", "first column must be the time"),
        ("time [ms],V [pu]\n0,1\n1,1\n", "first column must be the time"),
        ("time,V\n0,1\n1,1\n", "'V' has no unit"),
        ("time,V [pu],V [kV]\n0,1,1\n1,1,1\n", r"\['V'\] appear more than once"),
        ("time,V [pu],V [pu]\n0,1,1\n1,1,1\n", r"\['V'\] appear more than once"),
        ("time,V [pu]\n0,1\n1,\n", "'V' has a missing or non-numeric value at row 1"),
        ("time,V [pu]\n0,1\n,1\n", "time has a missing or non-numeric value at row 1"),
        ("time,V [pu]\n0,1\n0,1\n", "strictly increasing; row 1"),
        ("time\n0\n1\n", "needs a time column and at least one channel"),
    ],
)
def test_csv_refusals(tmp_path, text, match):
    p = tmp_path / "bad.csv"
    p.write_text(text)
    with pytest.raises(ContractError, match=match):
        read_csv(p)


def _pscad(tmp_path, n_channels):
    inf = tmp_path / "run.inf"
    inf.write_text(
        "".join(
            f'PGB({i}) Output Desc="ch{i}" Group="Main" Max=2.0 Min=-2.0 Units="{"kV" if i % 2 else "kA"}"\n'
            for i in range(1, n_channels + 1)
        )
    )
    t = np.array([0.0, 5e-5, 1e-4])
    for k in range(1, (n_channels + 9) // 10 + 1):
        cols = [t] + [
            t * 0 + i for i in range(10 * (k - 1) + 1, min(10 * k, n_channels) + 1)
        ]
        np.savetxt(tmp_path / f"run_{k:02d}.out", np.column_stack(cols))
    return inf


def test_pscad_reader_spans_out_files(tmp_path):
    f = read_pscad(_pscad(tmp_path, 12))
    assert list(f.channels) == [f"ch{i}" for i in range(1, 13)]
    np.testing.assert_allclose(f.channel("ch11"), [11, 11, 11])
    np.testing.assert_allclose(f.time, [0, 5e-5, 1e-4])
    assert f.units["ch1"] == "kV" and f.units["ch2"] == "kA"


def test_pscad_refusals(tmp_path):
    inf = _pscad(tmp_path, 12)
    (tmp_path / "run_02.out").write_text("0 11\n5e-5 11\n1e-4 11\n")
    with pytest.raises(
        ContractError, match="2 columns, expected the time and 2 channels"
    ):
        read_pscad(inf)
    (tmp_path / "run_02.out").write_text("0 11 12\n6e-5 11 12\n1e-4 11 12\n")
    with pytest.raises(ContractError, match="time column differs"):
        read_pscad(inf)
    (tmp_path / "run_02.out").unlink()
    with pytest.raises(ContractError, match="run_02.out: .out file missing"):
        read_pscad(inf)
    with pytest.raises(ContractError, match="need 2 .out file"):
        read_pscad(inf, outs=[tmp_path / "run_01.out"])
    inf.write_text('PGB(1) Output Desc="a"\nPGB(3) Output Desc="b"\n')
    with pytest.raises(ContractError, match="no gap"):
        read_pscad(inf)
    inf.write_text('PGB(1) Output Desc="a"\nPGB(2) Output Desc="a"\n')
    with pytest.raises(ContractError, match="appear more than once"):
        read_pscad(inf)
    inf.write_text('PGB(1) Output Desc="a"\nPGB(1) Output Desc="b"\n')
    with pytest.raises(ContractError, match=r"PGB\(1\) appears twice"):
        read_pscad(inf)
    inf.write_text('PGB(1) Output Units="kV"\n')
    with pytest.raises(ContractError, match="no Desc name"):
        read_pscad(inf)
    inf.write_text("nothing here\n")
    with pytest.raises(ContractError, match="no PGB"):
        read_pscad(inf)


def test_frame_refusals_and_helpers():
    t = [0.0, 0.1, 0.2]
    with pytest.raises(ContractError, match="at least two time points"):
        ChannelFrame([0.0], {}, {})
    with pytest.raises(ContractError, match="strictly increasing; row 2"):
        ChannelFrame([0.0, 0.1, 0.1], {}, {})
    with pytest.raises(ContractError, match="time has a non-finite value at row 1"):
        ChannelFrame([0.0, np.nan, 0.2], {}, {})
    with pytest.raises(ContractError, match="every channel needs a unit"):
        ChannelFrame(t, {"v": [1, 1, 1]}, {})
    with pytest.raises(ContractError, match="'v' has 2 values for 3"):
        ChannelFrame(t, {"v": [1, 1]}, {"v": "pu"})
    with pytest.raises(ContractError, match="'v' has a non-finite value at row 1"):
        ChannelFrame(t, {"v": [1, np.inf, 1]}, {"v": "pu"})
    with pytest.raises(ContractError, match="non-empty text"):
        ChannelFrame(t, {" ": [1, 1, 1]}, {" ": "pu"})
    with pytest.raises(ContractError, match="unit of 'v' must be text"):
        ChannelFrame(t, {"v": [1, 1, 1]}, {"v": None})
    f = ChannelFrame(t, {"v": [0.0, 1.0, 2.0]}, {"v": "pu"}, "x")
    assert "v" in f and "w" not in f
    with pytest.raises(ContractError, match="no channel 'w'"):
        f.channel("w")
    np.testing.assert_allclose(f.resample([0.05, 0.15]).channel("v"), [0.5, 1.5])
    with pytest.raises(ContractError, match="outside the recorded"):
        f.resample([0.0, 0.3])
    with pytest.raises(ContractError, match="outside the recorded"):
        f.resample([-0.1, 0.1])
    with pytest.raises(ValueError):
        f.channel("v")[0] = 5.0


# --- one targeted test per guard (GK A2 B8) ---------------------------------------------------------------------


def test_readers_take_str_paths(tmp_path):
    assert "VA" in read_comtrade(str(_write(tmp_path)))
    p = tmp_path / "a.csv"
    p.write_text("time,V [pu]\n0,1\n1,1\n")
    assert "V" in read_csv(str(p))
    inf = _pscad(tmp_path, 2)
    assert "ch2" in read_pscad(str(inf))
    assert "ch2" in read_pscad(str(inf), outs=[str(tmp_path / "run_01.out")])


def _cfg(**swap):
    cfg = CFG_1999
    for old, new in swap.values():
        assert old in cfg
        cfg = cfg.replace(old, new)
    return cfg


ANALOG_ONLY = """\
ST,REC
1,1A,0D
1,V,,,kV,1,0,0,-32767,32767
50
1
100,2
01/01/2026
01/01/2026
{ft}
"""


def test_comtrade_without_digitals_short_lines_and_no_timemult(tmp_path):
    # two-field station line (no revision), 10-field analog line, one-field start time, no timemult line
    f = read_comtrade(_write(tmp_path, cfg=ANALOG_ONLY, dat="1,0,3\n2,10000,4\n"))
    np.testing.assert_allclose(f.channel("V"), [3, 4])
    np.testing.assert_allclose(f.time, [0.0, 0.01])
    f = read_comtrade(
        _write(
            tmp_path,
            cfg=ANALOG_ONLY + "\n",
            ft="BINARY",
            dat=struct.pack("<IIhIIh", 1, 0, 3, 2, 0, 4),
        )
    )
    np.testing.assert_allclose(f.channel("V"), [3, 4])
    digital_only = ANALOG_ONLY.replace(
        "1,1A,0D\n1,V,,,kV,1,0,0,-32767,32767\n", "1,0A,1D\n1,TRIP\n"
    )
    f = read_comtrade(_write(tmp_path, cfg=digital_only, dat="1,0,1\n2,10000,0\n"))
    np.testing.assert_array_equal(f.channel("TRIP"), [1, 0])


def test_comtrade_nrates_zero_without_endsamp(tmp_path):
    cfg = CFG_1999.replace("1\n1000,3\n", "0\n0\n")
    f = read_comtrade(_write(tmp_path, cfg=cfg, dat="1,0,10,0,0\n2,500,12,-2,1\n"))
    np.testing.assert_allclose(f.time, [0.0, 0.0005])


def test_comtrade_zero_rate_falls_back_to_stamps(tmp_path):
    cfg = CFG_1999.replace("1\n1000,3\n", "1\n0,3\n")
    f = read_comtrade(
        _write(tmp_path, cfg=cfg, dat="1,0,10,0,0\n2,300,12,-2,1\n3,700,-4,4,0\n")
    )
    np.testing.assert_allclose(f.time, [0.0, 0.0003, 0.0007])


def test_comtrade_missing_stamp_needs_a_rate(tmp_path):
    rows = b"".join(
        struct.pack("<IIhhH", i + 1, ts, 10, 0, 0)
        for i, ts in enumerate((0, 0xFFFFFFFF, 2000))
    )
    f = read_comtrade(
        _write(tmp_path, ft="BINARY", dat=rows)
    )  # rate given: the stamps are not needed
    np.testing.assert_allclose(f.time, [0.0, 0.001, 0.002])
    cfg = CFG_1999.replace("1\n1000,3\n", "0\n0,3\n")
    with pytest.raises(
        ContractError, match="no sampling rate and a missing time stamp at sample 2"
    ):
        read_comtrade(_write(tmp_path, cfg=cfg, ft="BINARY", dat=rows))
    with pytest.raises(
        ContractError, match="no sampling rate and a missing time stamp at sample 2"
    ):
        read_comtrade(
            _write(tmp_path, cfg=cfg, dat="1,0,10,0,0\n2,,12,-2,1\n3,700,-4,4,0\n")
        )


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("3,2A,1D", "3,xA,1D", "TT,##A,##D"),
        ("3,2A,1D", "3,-1A,4D", "-1 analog"),
        ("3,2A,1D", "3,4A,-1D", "-1 digital"),
        (
            "1,VA,A,,V,0.5,-1,0,-32767,32767,10000,100,S",
            "1,VA,A,,V,0.5,-1,0,-32767",
            "analog channel 1 line has 9",
        ),
        ("1,TRIP,,,0", "1", "digital channel 1 line has no name"),
        ("1000,3", "1000", "sample rate 1 must be 'samp,endsamp'"),
        ("1\n1000,3\n", "2\n1000,2\n500,2\n", "endsamp past the previous one"),
        ("1\n1000,3\n", "1\n-5,3\n", "rate from 0"),
        ("1,VA,A,,V,0.5,", "1,VA,A,,V,nan,", "multiplier a is not finite"),
        (",10000,100,S", ",0,100,S", "needs both above zero"),
    ],
)
def test_comtrade_cfg_guards(tmp_path, old, new, match):
    assert old in CFG_1999
    with pytest.raises(ContractError, match=match):
        read_comtrade(_write(tmp_path, cfg=CFG_1999.replace(old, new)))


def test_comtrade_unreadable_cfg(tmp_path):
    with pytest.raises(ContractError, match="cannot read"):
        read_comtrade(tmp_path / "absent.cfg")


def test_csv_unreadable_and_nameless(tmp_path):
    p = tmp_path / "e.csv"
    p.write_text("")
    with pytest.raises(ContractError, match="not a readable CSV"):
        read_csv(p)
    with pytest.raises(ContractError, match="not a readable CSV"):
        read_csv(tmp_path / "absent.csv")
    p.write_text("time,[pu]\n0,1\n1,1\n")
    with pytest.raises(ContractError, match="has no unit"):
        read_csv(p)


def test_pscad_guards(tmp_path):
    inf = _pscad(tmp_path, 2)
    (tmp_path / "run_01.out").write_text("0 a b\n")
    with pytest.raises(ContractError, match="not a whitespace-separated numeric table"):
        read_pscad(inf)
    inf.write_text('PGB(1) Output Desc=" "\n')
    with pytest.raises(ContractError, match="no Desc name"):
        read_pscad(inf)


def test_frame_shape_name_and_resample_edges():
    with pytest.raises(ContractError, match="at least two time points"):
        ChannelFrame([[0.0, 0.1], [0.2, 0.3]], {}, {})
    with pytest.raises(ContractError, match="non-empty text"):
        ChannelFrame([0.0, 0.1], {5: [1, 1]}, {5: "pu"})
    f = ChannelFrame([0.0, 0.1, 0.2], {"v": [0.0, 1.0, 2.0]}, {"v": "pu"})
    assert f.source == "channel frame"
    np.testing.assert_allclose(
        f.resample([0.0, 0.2]).channel("v"), [0.0, 2.0]
    )  # the ends are inside

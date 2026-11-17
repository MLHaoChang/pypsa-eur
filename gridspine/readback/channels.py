"""
The channel frame: one in-memory form for time-domain results (GC-07 part 1).

RMS and EMT tools and field recorders each write their own file format. Every
reader here turns one of them into a ``ChannelFrame``: a strictly increasing
time index in seconds and named channels, each with its unit, so the judge and
the benchmark never see a file format. The unit is carried, not converted: a
``pu`` channel stays ``pu`` and a ``kV`` channel stays ``kV``; the benchmark
refuses to compare two channels whose units differ.

Readers:

``read_csv``
    A header row, the first column the time (``time`` or ``t``, optionally
    ``[s]``), every other column ``name [unit]``. This is also the form a
    PSS/E ``.out`` takes once exported to CSV.
``read_comtrade``
    IEEE C37.111 (1999 and 2013) ``.cfg`` + ``.dat``, data file type ``ASCII``,
    ``BINARY`` (int16), ``BINARY32`` (int32) or ``FLOAT32``. An analog value is
    ``a * x + b`` in the channel's unit, on the side the channel was recorded
    (``PS``), then converted with ``primary / secondary`` to primary values
    (``primary=True``, the default) or to secondary values (``primary=False``)
    when it was recorded on the other side. Digital channels become 0/1
    channels with unit ``status``. The time, in seconds from the recording's start time, comes from
    the sampling rates when the file gives them (``nrates >= 1`` and every rate
    above zero), each segment at its own rate; otherwise from each sample's
    time stamp times ``timemult``, in microseconds, or nanoseconds when the
    start time carries nine fraction digits (2013 revision). ``99999`` marks a
    missing ASCII sample before the 2013 revision; a blank one does in any.
``read_pscad``
    A PSCAD ``.inf`` (one ``PGB(n) ... Desc="..." Units="..."`` line per
    channel) and its ``.out`` files (whitespace-separated, the time first and
    then up to ten channels each, channel ``n`` in file ``(n - 1) // 10 + 1``).

Nothing is guessed: a missing value, a duplicate channel name, a short file or
a non-increasing time is a ``ContractError`` naming the file, channel or row,
never a silent NaN.

numpy/pandas only.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError

TIME_NAMES = ("time", "t")
_HEADER = re.compile(r"^\s*(?P<name>.*?)\s*\[(?P<unit>[^\]]*)\]\s*$")
#: COMTRADE data file types this reader takes, with the analog sample's dtype.
COMTRADE_TYPES = {"ASCII": None, "BINARY": "<i2", "BINARY32": "<i4", "FLOAT32": "<f4"}
#: The value a binary COMTRADE file writes for a missing analog sample.
_COMTRADE_MISSING = {"BINARY": -0x8000, "BINARY32": -0x80000000}
#: Channels per PSCAD ``.out`` file.
PSCAD_CHANNELS_PER_FILE = 10
_PGB = re.compile(r"^\s*PGB\((?P<n>\d+)\)\s+(?P<rest>.*)$")
_ATTR = re.compile(r'(\w+)="([^"]*)"')


@dataclasses.dataclass(frozen=True)
class ChannelFrame:
    """
    Time in seconds and named channels with their units.

    ``time`` is finite and strictly increasing; every channel has one finite
    value per time point and a unit (which may be ``""`` for a dimensionless
    channel); ``source`` says where the frame was read from.
    """

    time: np.ndarray
    channels: dict
    units: dict
    source: str = ""

    def __post_init__(self):
        time = np.asarray(self.time, dtype=float)
        if not self.source:
            object.__setattr__(self, "source", "channel frame")
        where = self.source
        if time.ndim != 1 or time.size < 2:
            raise ContractError(
                f"{where}: needs at least two time points, got {time.size}"
            )
        if not np.all(np.isfinite(time)):
            raise ContractError(
                f"{where}: time has a non-finite value at row {int(np.argmin(np.isfinite(time)))}"
            )
        steps = np.diff(time)
        if np.any(steps <= 0):
            row = int(np.argmax(steps <= 0)) + 1
            raise ContractError(
                f"{where}: time must be strictly increasing; row {row} is at {time[row]!r} s "
                f"after {time[row - 1]!r} s"
            )
        if set(self.channels) != set(self.units):
            raise ContractError(
                f"{where}: every channel needs a unit; channels {sorted(self.channels)} "
                f"but units for {sorted(self.units)}"
            )
        channels = {}
        for name, values in self.channels.items():
            if not isinstance(name, str) or not name.strip():
                raise ContractError(
                    f"{where}: a channel name must be non-empty text, got {name!r}"
                )
            if not isinstance(self.units[name], str):
                raise ContractError(
                    f"{where}: unit of {name!r} must be text, got {self.units[name]!r}"
                )
            arr = np.asarray(values, dtype=float)
            if arr.shape != time.shape:
                raise ContractError(
                    f"{where}: channel {name!r} has {arr.size} values for {time.size} time points"
                )
            if not np.all(np.isfinite(arr)):
                row = int(np.argmin(np.isfinite(arr)))
                raise ContractError(
                    f"{where}: channel {name!r} has a non-finite value at row {row} "
                    f"(t = {time[row]!r} s)"
                )
            arr.setflags(write=False)
            channels[name] = arr
        time.setflags(write=False)
        object.__setattr__(self, "time", time)
        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "units", dict(self.units))

    def __contains__(self, name) -> bool:
        return name in self.channels

    def channel(self, name: str) -> np.ndarray:
        if name not in self.channels:
            raise ContractError(
                f"{self.source}: no channel {name!r}; "
                f"channels are {sorted(self.channels)}"
            )
        return self.channels[name]

    def resample(self, times) -> ChannelFrame:
        """
        The frame linearly interpolated at ``times``, which must lie within
        this frame's time span (no extrapolation).
        """
        times = np.asarray(times, dtype=float)
        if times[0] < self.time[0] or times[-1] > self.time[-1]:
            raise ContractError(
                f"{self.source}: cannot resample to "
                f"{times[0]!r}..{times[-1]!r} s outside the recorded "
                f"{self.time[0]!r}..{self.time[-1]!r} s"
            )
        return ChannelFrame(
            times,
            {n: np.interp(times, self.time, v) for n, v in self.channels.items()},
            self.units,
            self.source,
        )


def _refuse_duplicates(names, where):
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ContractError(f"{where}: channel name(s) {dup} appear more than once")


def _split_header(col, where):
    m = _HEADER.match(str(col))
    if not m or not m["name"]:
        raise ContractError(
            f"{where}: column {col!r} has no unit; write it as 'name [unit]' "
            "('[]' for a dimensionless channel)"
        )
    return m["name"], m["unit"].strip()


def read_csv(path) -> ChannelFrame:
    """
    A CSV whose first column is the time in seconds and every other column
    is ``name [unit]``.
    """
    path = Path(path)
    where = str(path.name)
    try:
        header = next(
            csv.reader(
                io.StringIO(path.read_text(encoding="utf-8").partition("\n")[0])
            ),
            [],
        )
        df = pd.read_csv(path)
    except (
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
        UnicodeDecodeError,
        OSError,
    ) as exc:
        raise ContractError(f"{where}: not a readable CSV ({exc})") from exc
    if df.shape[1] < 2:
        raise ContractError(f"{where}: needs a time column and at least one channel")
    first = str(df.columns[0])
    m = _HEADER.match(first)
    tname, tunit = (m["name"], m["unit"].strip()) if m else (first.strip(), "s")
    if tname.lower() not in TIME_NAMES or tunit != "s":
        raise ContractError(
            f"{where}: the first column must be the time in seconds ('time' or 'time [s]'), "
            f"got {first!r}"
        )
    names = [_split_header(col, where)[0] for col in header[1:]]
    _refuse_duplicates(names, where)
    channels, units = {}, {}
    for col in df.columns[1:]:
        name, unit = _split_header(col, where)
        values = pd.to_numeric(df[col], errors="coerce")
        if values.isna().any():
            row = int(values.isna().to_numpy().argmax())
            raise ContractError(
                f"{where}: channel {name!r} has a missing or non-numeric value at row {row}"
            )
        channels[name], units[name] = values.to_numpy(float), unit
    time = pd.to_numeric(df.iloc[:, 0], errors="coerce")
    if time.isna().any():
        raise ContractError(
            f"{where}: time has a missing or non-numeric value at row "
            f"{int(time.isna().to_numpy().argmax())}"
        )
    return ChannelFrame(time.to_numpy(float), channels, units, where)


# --- COMTRADE ------------------------------------------------------------------------------------------------


def _cfg_fields(line):
    return [f.strip() for f in line.rstrip("\r\n").split(",")]


def _num(text, what, where):
    try:
        v = float(text)
    except ValueError:
        raise ContractError(f"{where}: {what} is not a number: {text!r}") from None
    if not math.isfinite(v):
        raise ContractError(f"{where}: {what} is not finite: {text!r}")
    return v


@dataclasses.dataclass(frozen=True)
class _Analog:
    name: str
    unit: str
    a: float
    b: float
    ratio: float  # the factor from the recorded side to the side asked for


def _parse_cfg(text, where, primary):
    lines = [ln for ln in text.splitlines()]
    pos = 0

    def take(what):
        nonlocal pos
        if pos >= len(lines):
            raise ContractError(f"{where}: ends before the {what} line")
        pos += 1
        return _cfg_fields(lines[pos - 1])

    station = take("station")
    rev = station[2] if len(station) > 2 else "1991"
    counts = take("channel count")
    if len(counts) < 3:
        raise ContractError(
            f"{where}: channel count line must be 'TT,##A,##D', got {','.join(counts)!r}"
        )
    try:
        total, n_a, n_d = (
            int(counts[0]),
            int(counts[1].rstrip("Aa")),
            int(counts[2].rstrip("Dd")),
        )
    except ValueError:
        raise ContractError(
            f"{where}: channel count line must be 'TT,##A,##D', got {','.join(counts)!r}"
        ) from None
    if total != n_a + n_d or n_a < 0 or n_d < 0:
        raise ContractError(
            f"{where}: {total} channels stated but {n_a} analog + {n_d} digital"
        )
    analogs, digitals = [], []
    for i in range(n_a):
        f = take(f"analog channel {i + 1}")
        if len(f) < 10:
            raise ContractError(
                f"{where}: analog channel {i + 1} line has {len(f)} fields, needs at least 10"
            )
        name, unit = f[1], f[4]
        a, b = (
            _num(f[5], f"analog channel {name!r} multiplier a", where),
            _num(f[6], f"analog {name!r} offset b", where),
        )
        ratio = 1.0
        recorded = f[12].upper() if len(f) >= 13 else "P"
        if recorded not in ("P", "S"):
            raise ContractError(
                f"{where}: analog {name!r} has PS {f[12]!r}; it must be P or S"
            )
        if (
            recorded == "S"
        ) == primary:  # the asked-for side differs from the recorded one
            p, s = (
                _num(f[10], f"analog {name!r} primary", where),
                _num(f[11], f"analog {name!r} secondary", where),
            )
            if p == 0 or s == 0:
                raise ContractError(
                    f"{where}: analog {name!r} has primary {p!r} and secondary {s!r}; "
                    "converting between them needs both above zero"
                )
            ratio = p / s if primary else s / p
        analogs.append(_Analog(name, unit, a, b, ratio))
    for i in range(n_d):
        f = take(f"digital channel {i + 1}")
        if len(f) < 2:
            raise ContractError(f"{where}: digital channel {i + 1} line has no name")
        digitals.append(f[1])
    take("line frequency")
    nrates = int(_num(take("sample-rate count")[0], "nrates", where))
    rates = []
    for i in range(nrates):
        f = take(f"sample rate {i + 1}")
        if len(f) < 2:
            raise ContractError(f"{where}: sample rate {i + 1} must be 'samp,endsamp'")
        samp, end = _num(f[0], "samp", where), int(_num(f[1], "endsamp", where))
        if samp < 0 or end <= (rates[-1][1] if rates else 0):
            raise ContractError(
                f"{where}: sample rate {i + 1} ({f[0]},{f[1]}) needs a rate from 0 and an "
                "endsamp past the previous one"
            )
        rates.append((samp, end))
    if nrates == 0:  # the standard writes one '0,endsamp' line after nrates = 0
        f = take("sample rate")
        rates.append((0.0, int(_num(f[1], "endsamp", where)) if len(f) > 1 else 0))
    start = take("start time")
    fraction = start[1].rpartition(".")[2] if len(start) > 1 else ""
    # C37.111-2013: the start time's fraction digits set the time-stamp unit (6 = us, 9 = ns).
    stamp_s = 1e-9 if len(fraction) == 9 else 1e-6
    take("trigger time")
    ft = take("data file type")[0].upper()
    if ft not in COMTRADE_TYPES:
        raise ContractError(
            f"{where}: data file type {ft!r}; this reader takes {sorted(COMTRADE_TYPES)}"
        )
    timemult = 1.0
    if pos < len(lines) and lines[pos].strip():
        timemult = _num(take("timemult")[0], "timemult", where)
        if timemult <= 0:
            raise ContractError(
                f"{where}: timemult must be above zero, got {timemult!r}"
            )
    names = [a.name for a in analogs] + digitals
    _refuse_duplicates(names, where)
    return analogs, digitals, rates, ft, timemult * stamp_s, rev


def _rate_time(rates):
    """Sample times from the sampling rates, each segment at its own rate."""
    segments, t0, start = [], 0.0, 0
    for samp, end in rates:
        seg = t0 + np.arange(end - start) / samp
        segments.append(seg)
        t0, start = seg[-1] + 1.0 / samp, end
    return np.concatenate(segments)


def read_comtrade(cfg, dat=None, primary: bool = True) -> ChannelFrame:
    """
    An IEEE C37.111 recording: ``cfg`` the configuration file, ``dat``
    its data file (default: the ``.dat`` next to it).
    """
    cfg = Path(cfg)
    dat = Path(dat) if dat is not None else cfg.with_suffix(".dat")
    where = cfg.name
    try:
        text = cfg.read_text(encoding="latin-1")
    except OSError as exc:
        raise ContractError(f"{where}: cannot read ({exc})") from exc
    analogs, digitals, rates, ft, timemult, rev = _parse_cfg(text, where, primary)
    # 99999 marks a missing ASCII sample before the 2013 revision, which leaves it blank.
    ascii_missing = ("",) if rev == "2013" else ("", "99999")
    n_a, n_d = len(analogs), len(digitals)
    dwhere = dat.name
    if not dat.is_file():
        raise ContractError(f"{dwhere}: data file missing next to {where}")
    if ft == "ASCII":
        rows = [r for r in dat.read_text(encoding="latin-1").splitlines() if r.strip()]
        width = 2 + n_a + n_d
        raw_a, raw_d, stamps = (
            np.empty((len(rows), n_a)),
            np.empty((len(rows), n_d)),
            np.empty(len(rows)),
        )
        for i, r in enumerate(rows):
            f = _cfg_fields(r)
            if len(f) != width:
                raise ContractError(
                    f"{dwhere}: row {i + 1} has {len(f)} fields, expected {width}"
                )
            stamps[i] = (
                _num(f[1], f"row {i + 1} time stamp", dwhere) if f[1] else math.nan
            )
            for j in range(n_a):
                if f[2 + j] in ascii_missing:
                    raise ContractError(
                        f"{dwhere}: analog {analogs[j].name!r} is missing at row {i + 1}"
                    )
                raw_a[i, j] = _num(
                    f[2 + j], f"analog {analogs[j].name!r} at row {i + 1}", dwhere
                )
            for j in range(n_d):
                raw_d[i, j] = _num(
                    f[2 + n_a + j], f"digital {digitals[j]!r} at row {i + 1}", dwhere
                )
    else:
        words = math.ceil(n_d / 16)
        fields = [("n", "<u4"), ("ts", "<u4"), ("a", COMTRADE_TYPES[ft], (n_a,))]
        record = np.dtype(fields + [("d", "<u2", (words,))] if words else fields)
        blob = dat.read_bytes()
        if len(blob) % record.itemsize:
            raise ContractError(
                f"{dwhere}: {len(blob)} bytes is not a whole number of "
                f"{record.itemsize}-byte samples"
            )
        data = np.frombuffer(blob, dtype=record)
        raw_a = data["a"].astype(float).reshape(len(data), n_a)
        if ft in _COMTRADE_MISSING:
            missing = data["a"].reshape(len(data), n_a) == _COMTRADE_MISSING[ft]
            if missing.any():
                i, j = (int(x) for x in np.argwhere(missing)[0])
                raise ContractError(
                    f"{dwhere}: analog {analogs[j].name!r} is missing at sample {i + 1}"
                )
        raw_d = np.empty((len(data), n_d))
        for j in range(n_d):  # digital j is bit j % 16 of status word j // 16
            raw_d[:, j] = (
                data["d"].reshape(len(data), words)[:, j // 16] >> (j % 16)
            ) & 1
        stamps = data["ts"].astype(float)
        if np.any(data["ts"] == 0xFFFFFFFF):
            stamps[data["ts"] == 0xFFFFFFFF] = math.nan
    stated = rates[-1][1]
    if stated and stated != len(stamps):
        raise ContractError(
            f"{dwhere}: {where} states {stated} samples, the data file has {len(stamps)}"
        )
    use_rates = all(samp > 0 for samp, _ in rates)
    if not use_rates and np.isnan(stamps).any():
        raise ContractError(
            f"{dwhere}: no sampling rate and a missing time stamp at sample "
            f"{int(np.isnan(stamps).argmax()) + 1}"
        )
    time = _rate_time(rates) if use_rates else stamps * timemult
    channels, units = {}, {}
    for j, a in enumerate(analogs):
        channels[a.name] = (a.a * raw_a[:, j] + a.b) * a.ratio
        units[a.name] = a.unit
    for j, name in enumerate(digitals):
        if not np.isin(raw_d[:, j], (0.0, 1.0)).all():
            raise ContractError(
                f"{dwhere}: digital {name!r} has a value other than 0 or 1"
            )
        channels[name], units[name] = raw_d[:, j], "status"
    return ChannelFrame(time, channels, units, where)


# --- PSCAD ---------------------------------------------------------------------------------------------------


def _parse_inf(path):
    where = path.name
    found = {}
    for k, line in enumerate(path.read_text(encoding="latin-1").splitlines(), start=1):
        m = _PGB.match(line)
        if not m:
            continue
        attrs = dict(_ATTR.findall(m["rest"]))
        if "Desc" not in attrs or not attrs["Desc"].strip():
            raise ContractError(f"{where}: line {k} (PGB({m['n']})) has no Desc name")
        n = int(m["n"])
        if n in found:
            raise ContractError(f"{where}: PGB({n}) appears twice")
        found[n] = (attrs["Desc"].strip(), attrs.get("Units", "").strip())
    if not found:
        raise ContractError(f"{where}: no PGB(n) channel lines")
    if sorted(found) != list(range(1, len(found) + 1)):
        raise ContractError(
            f"{where}: channels must be PGB(1)..PGB({len(found)}) with no gap, "
            f"got {sorted(found)}"
        )
    names = [found[n][0] for n in sorted(found)]
    _refuse_duplicates(names, where)
    return [found[n] for n in sorted(found)]


def _read_out(path, expect):
    where = path.name
    try:
        data = np.loadtxt(io.StringIO(path.read_text(encoding="latin-1")), ndmin=2)
    except ValueError as exc:
        raise ContractError(
            f"{where}: not a whitespace-separated numeric table ({exc})"
        ) from exc
    if data.shape[1] != expect + 1:
        raise ContractError(
            f"{where}: {data.shape[1]} columns, expected the time and {expect} channels"
        )
    return data


def read_pscad(inf, outs=None) -> ChannelFrame:
    """
    A PSCAD run: ``inf`` the ``.inf`` file, ``outs`` its ``.out`` files in
    order (default: ``<stem>_01.out``, ``<stem>_02.out``, ... next to it).
    """
    inf = Path(inf)
    specs = _parse_inf(inf)
    n_files = math.ceil(len(specs) / PSCAD_CHANNELS_PER_FILE)
    if outs is None:
        outs = [inf.with_name(f"{inf.stem}_{i:02d}.out") for i in range(1, n_files + 1)]
    outs = [Path(p) for p in outs]
    if len(outs) != n_files:
        raise ContractError(
            f"{inf.name}: {len(specs)} channels need {n_files} .out file(s), got {len(outs)}"
        )
    channels, units, time = {}, {}, None
    for i, out in enumerate(outs):
        if not out.is_file():
            raise ContractError(f"{out.name}: .out file missing for {inf.name}")
        block = specs[i * PSCAD_CHANNELS_PER_FILE : (i + 1) * PSCAD_CHANNELS_PER_FILE]
        data = _read_out(out, len(block))
        if time is None:
            time = data[:, 0]
        elif not np.array_equal(data[:, 0], time):
            raise ContractError(
                f"{out.name}: its time column differs from {outs[0].name}'s"
            )
        for j, (name, unit) in enumerate(block):
            channels[name], units[name] = data[:, j + 1], unit
    return ChannelFrame(time, channels, units, inf.name)

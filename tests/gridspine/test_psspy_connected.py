# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
GC-12 PR-B: the PSS/E scripts' ``connected`` export (battery contract v2).

The scripts are checked with ``ast`` and their pure helpers run without psspy
(GK condition S1). GK A2 conditions T1, T3, T4, T5, T8, T9 and T11 in
.scratch/grid-compliance/issues/12-contract-connected-channel.md; each
oracle is a hand-written literal.
"""

import ast
import math

import pytest

from gridspine.handoff.psspy_scripts import (
    EPS_S,
    check_script,
    write_scripts,
)
from gridspine.handoff.test_battery import (
    CHANNELS,
    CONNECTED,
    build_battery,
    contract_channels,
)
from gridspine.readback.channels import STATUS_NOT_READ, read_csv
from gridspine.readback.pack import judge_pack
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_pack import PROFILE, RATINGS, _by_id, _rows
from tests.gridspine.test_pack_contract2 import (
    FAULT,
    _fault_channels,
    _pack2,
    _status,
    _write,
)
from tests.gridspine.test_psspy_scripts import FILES, KW

FAULT_ID = "fault_3ph_skmin_xrmin_v020"
#: the time step the tests' fake case returns for dsrval('DELT'): the poll and sample grid
DELT = 0.001
# apply 0.50135, clear 0.65155: both off the 1 ms poll and sample grid (GK A2 T3, GK A P2)
T_FAULT, DURATION = 0.50135, 0.1502


def _battery2(overrides=None):
    return build_battery(
        300.0,
        1000.0,
        3.0,
        10.0,
        z0_over_z1=1.5,
        x0_over_r0=4.0,
        overrides=overrides,
        contract_version="2",
    )


@pytest.fixture(scope="module")
def pack2(tmp_path_factory):
    cases = _battery2({FAULT_ID: {"duration_s": DURATION, "t_event_s": T_FAULT}})
    d = tmp_path_factory.mktemp("pack2")
    out = write_scripts(cases, str(d), **KW, contract_version="2")
    texts = {cid: (d / name).read_text(encoding="utf-8") for cid, name in out.items()}
    return {c.case_id: c for c in cases}, texts


def _assigned(text, name):
    """The literal a top-level ``NAME = ...`` binds."""
    for n in ast.parse(text).body:
        if isinstance(n, ast.Assign) and [ast.unparse(t) for t in n.targets] == [name]:
            return ast.literal_eval(n.value)
    raise AssertionError(f"{name} not bound")


def _helpers(text, psspy=None, **env):
    """The script's top-level functions, run with ``env`` and a fake ``psspy``."""
    tree = ast.parse(text)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    ns = {"POLL_DT_S": DELT, "EPS_S": EPS_S, "psspy": psspy, **env}
    exec(compile(ast.Module(fns, []), "helpers", "exec"), ns)
    return ns


# --- T1 / T8: the header is the read-back dialect, connected only on v2 ---------------------------------------


def test_every_v2_script_header_is_its_case_channels_with_units(pack2):
    cases, texts = pack2
    assert len(texts) == len(cases)
    for cid, text in texts.items():
        chans = cases[cid].channels
        assert chans[-1] == CONNECTED and set(chans) <= set(contract_channels("2"))
        assert _assigned(text, "COLUMNS") == ["time", *chans]
        header = _assigned(text, "HEADER")
        assert header[0] == "time [s]" and header[-1] == "connected [status]"
        assert header[1:] == [f"{c} [{CHANNELS[c].unit}]" for c in chans]
        assert "status" not in _assigned(text, "COLUMNS")  # GK A2 T9: no text column
        # the script polls what it declares, and writes the CSV and its sidecar
        assert "_poll(0.0)" in text.splitlines()
        assert _assigned(text, "EPS_S") == EPS_S
        assert f"open(os.path.join(HERE, '{cid}.status.txt'), 'w')" in text


def test_a_v1_script_never_names_connected(tmp_path):
    out = write_scripts(
        build_battery(300.0, 1000.0, 3.0, 10.0, z0_over_z1=1.5, x0_over_r0=4.0),
        str(tmp_path),
        **KW,
    )
    for name in out.values():
        text = (tmp_path / name).read_text()
        assert "connected" not in text and "status.txt" not in text
        assert "psspy.brnint" not in text and "psspy.macint" not in text


@pytest.mark.parametrize(
    ("version", "kw", "reason"),
    [
        ("2", {"plant_branch": None}, "need plant_branch and plant_machine"),
        ("2", {"plant_machine": None}, "need plant_branch and plant_machine"),
        ("2", {"status_machines": ()}, "needs at least one status machine"),
        ("2", {"status_machines": [(5, "toolong")]}, "1 or 2 characters"),
        ("1", {"status_machines": [(201, "1")]}, "has no 'connected' channel"),
        ("3", {}, "not one of"),
    ],
    ids=[
        "no_branch",
        "no_machine",
        "no_machines",
        "bad_machine",
        "v1_machines",
        "unknown",
    ],
)
def test_refusals(tmp_path, version, kw, reason):
    cases = (
        _battery2()
        if version == "2"
        else build_battery(300.0, 1000.0, 3.0, 10.0, z0_over_z1=1.5, x0_over_r0=4.0)
    )
    with pytest.raises(ContractError, match=reason):
        write_scripts(cases, str(tmp_path), **{**KW, **kw}, contract_version=version)


def test_a_v1_battery_is_refused_at_v2(tmp_path):
    cases = build_battery(300.0, 1000.0, 3.0, 10.0, z0_over_z1=1.5, x0_over_r0=4.0)
    with pytest.raises(ContractError, match="connected"):
        write_scripts(cases, str(tmp_path), **KW, contract_version="2")


def test_the_checker_refuses_a_sidecar_the_pack_does_not_list(pack2):
    _, texts = pack2
    allowed = set(FILES.values()) | {f"{FAULT_ID}.out", f"{FAULT_ID}.csv"}
    with pytest.raises(ContractError, match="status.txt"):
        check_script(texts[FAULT_ID], allowed)
    check_script(texts[FAULT_ID], allowed | {f"{FAULT_ID}.status.txt"})


# --- T3: every event time is an exact run stop ------------------------------------------------------------------


class _RunLog:
    """A fake psspy for ``_run``: logs each run stop and each status poll."""

    def __init__(self):
        self.log = []

    def run(self, opt, t, nprt, nplt, crtplt):
        assert nplt == 1  # a sample every time step: the polls are never coarser
        self.log.append(("run", t))

    def brnint(self, *a):
        self.log.append(("poll", self.log[-1][1]))
        return 0, 1

    def macint(self, *a):
        return 0, 1


def test_the_run_stops_hold_the_fault_apply_and_clear_times_off_the_grid(pack2):
    cases, texts = pack2
    text = texts[FAULT_ID]
    env = {"T_EVENT": cases[FAULT_ID].t_event_s, "T_END": cases[FAULT_ID].t_end_s}
    assert env["T_EVENT"] == T_FAULT
    t_apply, t_clear = T_FAULT, T_FAULT + DURATION
    for t in (t_apply, t_clear):  # off the poll grid (= the sample grid, DELT)
        assert abs(t / DELT - round(t / DELT)) > 0.1
    targets = [
        eval(ast.unparse(n.value.args[0]), env)  # noqa: S307 - a literal sum of the script's own names
        for n in ast.parse(text).body
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and ast.unparse(n.value.func) == "_run"
    ]
    assert targets == [t_apply, t_clear, env["T_END"]]
    fake = _RunLog()
    polls = {"t_now": 0.0, "t": [], "v": [], "why": []}
    ns = _helpers(
        text,
        fake,
        STATUS_MACHINES=[(201, "1")],
        POLLS=polls,
        BR_FROM=101,
        BR_TO=900,
        BR_CKT="1",
    )
    for t in targets:
        ns["_run"](t)
    stops = [t for kind, t in fake.log if kind == "run"]
    assert t_apply in stops and t_clear in stops
    assert stops == sorted(set(stops)) and stops[-1] == env["T_END"]
    # a poll follows every run stop, the event stops included
    assert fake.log == [e for t in stops for e in (("run", t), ("poll", t))]
    assert polls["t"] == stops
    # the grid between: 0.501 after the apply, 0.651 before the clear
    assert stops[stops.index(t_apply) - 1] == pytest.approx(0.501)
    assert stops[stops.index(t_apply) + 1] == pytest.approx(0.502)
    assert stops[stops.index(t_clear) - 1] == pytest.approx(0.651)
    # the fault is applied after the run to T_EVENT and cleared after the run to its end
    lines = text.splitlines()
    assert (
        lines.index("_run(T_EVENT)")
        < lines.index(
            next(ln for ln in lines if ln.startswith("psspy.dist_bus_fault("))
        )
        < lines.index(f"_run(T_EVENT + {DURATION!r})")
        < lines.index("psspy.dist_clear_fault(1)")
    )


def test_the_poll_step_is_the_case_time_step(pack2):
    # GK A P2: POLL_DT_S is DELT, read after strt, before the first poll and run
    lines = pack2[1][FAULT_ID].splitlines()
    i = lines.index("ierr, DELT = psspy.dsrval('DELT')")
    assert lines.index("psspy.strt(0, OUT)") < i
    assert lines[i + 1 : i + 4] == [
        "if ierr or not DELT > 0:",
        "    raise SystemExit('time step DELT not read: connected cannot be polled')",
        "POLL_DT_S = DELT  # one poll per time step, so per output sample",
    ]
    assert i < lines.index("_poll(0.0)") < lines.index("_run(T_EVENT)")
    runs = [ln for ln in lines if "psspy.run(" in ln]
    assert runs == ["        psspy.run(0, t, 1000, 1, 0)"]  # only inside _run, NPLT = 1


def test_tpause_literals(pack2):
    tp = _helpers(pack2[1][FAULT_ID])["_tpause"]
    assert tp(0.0, 0.012, 0.005) == [0.005, 0.01, 0.012]
    assert tp(0.012, 0.02, 0.005) == [0.015, 0.02]  # 0.02 once, not again from the grid
    assert tp(0.5, 0.5013, 0.005) == [0.5013]
    assert tp(0.5013, 0.5013, 0.005) == [0.5013]


# --- T4 / T5: poll every machine, AND, latch, a failed poll is blank ---------------------------------------------


class _FakePsspy:
    """Status replies per poll: ``replies[k]`` is ``{element: (ierr, status)}``."""

    def __init__(self, replies):
        self.replies, self.k, self.calls = replies, -1, []

    def brnint(self, i, j, ckt, what):
        self.k += 1
        self.calls.append(("brnint", i, j, ckt, what))
        return self.replies[self.k]["branch"]

    def macint(self, bus, mid, what):
        self.calls.append(("macint", bus, mid, what))
        return self.replies[self.k][(bus, mid)]


def _poll_run(text, replies, machines):
    fake = _FakePsspy(replies)
    polls = {"t_now": 0.0, "t": [], "v": [], "why": []}
    ns = _helpers(
        text,
        fake,
        STATUS_MACHINES=machines,
        POLLS=polls,
        BR_FROM=101,
        BR_TO=900,
        BR_CKT="1",
    )
    for k in range(len(replies)):
        ns["_poll"](k * 0.005)
    return polls, fake


TWO = [(201, "1"), (202, "2")]


def test_two_machines_one_tripping_reads_0(pack2):
    ok = {"branch": (0, 1), TWO[0]: (0, 1), TWO[1]: (0, 1)}
    trip = {"branch": (0, 1), TWO[0]: (0, 1), TWO[1]: (0, 0)}
    polls, fake = _poll_run(pack2[1][FAULT_ID], [ok, trip], TWO)
    assert polls["v"] == [1, 0] and polls["why"] == []
    assert ("macint", 202, "2", "STATUS") in fake.calls
    assert ("brnint", 101, 900, "1", "STATUS") in fake.calls
    assert sum(c[0] == "macint" for c in fake.calls) == 4  # each machine, each poll


def test_a_failed_poll_is_blank_with_its_reason_never_1(pack2):
    failed = {"branch": (0, 1), TWO[0]: (3, 1), TWO[1]: (0, 1)}
    odd = {"branch": (0, 2), TWO[0]: (0, 1), TWO[1]: (0, 1)}
    polls, _ = _poll_run(pack2[1][FAULT_ID], [failed, odd], TWO)
    assert polls["v"] == [None, None]
    assert polls["why"] == [
        "t=0.0 s: not read (machine 201 1: ierr=3, status=1)",
        "t=0.005 s: not read (PoC branch: ierr=0, status=2)",
    ]


@pytest.mark.parametrize(
    ("readings", "want"),
    [
        ([("b", 0, 1), ("m1", 0, 1), ("m2", 0, 1)], 1),
        ([("b", 0, 1), ("m1", 0, 1), ("m2", 0, 0)], 0),  # AND, not OR
        ([("b", 0, 0), ("m1", 0, 1)], 0),  # the PoC breaker open
        ([("b", 1, 0), ("m1", 0, 1)], None),  # a failed read is not a 0 either
        ([("b", 0, 1), ("m1", 5, 1)], None),  # ierr != 0 is blank, never 1
        ([("b", 0, 0), ("m1", 5, 1)], 0),  # a read 0 is a trip even beside a failure
        ([("b", 0, 1), ("m1", 0, 2)], None),  # ierr 0 but not 0/1: failed, never 1
        ([("b", 0, -1), ("m1", 0, 1)], None),
    ],
)
def test_combine_literals(pack2, readings, want):
    assert _helpers(pack2[1][FAULT_ID])["_combine"](readings)[0] == want


@pytest.mark.parametrize(
    ("polls", "want"),
    [
        ([1, 1, 0, 1, 1], [1, 1, 0, 0, 0]),  # latched
        (
            [1, None, 0, None, 1],
            [1, None, 0, 0, 0],
        ),  # a blank never sets it; a later 0 does
        ([None, 1, None], [None, 1, None]),  # a blank stays blank: not 1, not 0
        ([1, None, 1], [1, None, 1]),
    ],
)
def test_latch_literals(pack2, polls, want):
    assert _helpers(pack2[1][FAULT_ID])["_latch"](polls) == want


def test_a_trip_between_two_samples_shows_on_the_earlier_only_within_a_poll_step(pack2):
    # GK A P4: sample t reads 0 only when a 0 poll falls in (t, t + POLL_DT]
    conn = _helpers(pack2[1][FAULT_ID])["_connected"]
    times = [0.0, 0.0005, 0.001]  # a sample between two polls (POLL_DT = 1 ms)
    # the 0 poll at 0.001 is in (0.0005, 0.0015]: the sample at 0.0005 reads 0
    assert conn(times, [0.0, 0.001], [1, 0]) == [1, 0, 0]
    # the first 0 poll at 0.002 is outside (0.0005, 0.0015]: it still reads 1
    assert conn(times, [0.0, 0.001, 0.002], [1, 1, 0]) == [1, 1, 1]


def test_each_sample_takes_the_first_poll_at_or_after_it(pack2):
    conn = _helpers(pack2[1][FAULT_ID])["_connected"]
    times = [0.0, 0.002, 0.005, 0.005, 0.007, 0.01, 0.012]
    got = conn(times, [0.0, 0.005, 0.01], [1, 1, 0])
    assert got == [1, 1, 1, 1, 0, 0, ""]  # after the last poll: not read
    assert conn([0.0, 0.003, 0.006], [0.0, 0.005, 0.01], [1, None, 1]) == [1, "", 1]


# --- T11: the converter's CSV is read by channels.read_csv ------------------------------------------------------


def test_the_converter_csv_reads_back_with_literal_values(pack2, tmp_path):
    cases, texts = pack2
    text = texts[FAULT_ID]
    ns = _helpers(text)
    n = 5
    rec = {
        "t_s": [0.0, 0.005, 0.5013, 0.505, 0.51],
        "raw_v_poc_pu": [
            1.0,
            1.0,
            0.0,
            0.0,
            0.2,
        ],  # a V = 0 row: the current stays defined
        "raw_theta_poc_deg": [0.0] * n,
        "raw_theta_src": [0.0] * n,
        "raw_f_hz": [0.0] * n,
        "raw_p_poc_mw": [-40.0] * n,
        "raw_q_poc_mvar": [0.0] * n,
        "raw_id_pu": [0.5] * n,
        "raw_iq_pu": [0.0] * n,
        "raw_i_term_pu": [0.5, 0.5, 1.1, 1.1, 0.0],
        # polls: a failed one at 0.005, the trip at 0.51
        "poll_t": [0.0, 0.005, 0.5013, 0.505, 0.51],
        "poll_v": [1, None, 1, 1, 0],
    }
    data = ns["_convert"](rec, 50.0, 80.0, 80.0)
    columns, header = _assigned(text, "COLUMNS"), _assigned(text, "HEADER")
    csv_text = ns["_csv_text"](data, columns, header)
    path = tmp_path / f"{FAULT_ID}.csv"
    path.write_text(csv_text)
    frame = read_csv(path, blank_status=(CONNECTED,))
    assert frame.time.tolist() == [0.0, 0.005, 0.5013, 0.505, 0.51]
    assert frame.channel("v_poc_pu").tolist() == [1.0, 1.0, 0.0, 0.0, 0.2]
    assert frame.channel("i_poc_pu").tolist() == [0.5, 0.5, 1.1, 1.1, 0.0]
    # GK A P6: the V = 0 rows carry a finite current (ITERM), never a blank
    assert all(math.isfinite(x) for x in frame.channel("i_poc_pu")[2:4])
    assert frame.channel("p_poc_mw").tolist() == [40.0] * n
    assert frame.channel(CONNECTED).tolist() == [1.0, STATUS_NOT_READ, 1.0, 1.0, 0.0]
    assert frame.units[CONNECTED] == "status" and frame.units["i_poc_pu"] == "pu"
    assert csv_text.splitlines()[2].endswith(",")  # the failed poll is a blank cell
    assert set(frame.channels) == set(cases[FAULT_ID].channels)


def test_a_blank_current_cell_is_refused_not_read_as_0(tmp_path):
    # GK A P6 / T12 "blank I read as 0": i_poc_pu is not a blank_status channel
    path = tmp_path / "blank_i.csv"
    path.write_text(
        "time [s],v_poc_pu [pu],i_poc_pu [pu],connected [status]\n"
        "0.0,1.0,0.5,1.0\n"
        "0.1,0.0,,1.0\n"
    )
    with pytest.raises(
        ContractError, match="'i_poc_pu' has a missing or non-numeric value at row 1"
    ):
        read_csv(path, blank_status=(CONNECTED,))


def test_the_old_header_is_refused_and_the_new_one_read(tmp_path):
    old = tmp_path / "old.csv"
    old.write_text("t_s,v_poc_pu,status\n0.0,1.0,\n")
    with pytest.raises(ContractError, match="first column must be the time"):
        read_csv(old, blank_status=(CONNECTED,))
    bare = tmp_path / "bare.csv"
    bare.write_text("time [s],v_poc_pu\n0.0,1.0\n")
    with pytest.raises(ContractError, match="has no unit"):
        read_csv(bare)
    text_col = tmp_path / "text.csv"
    text_col.write_text("time [s],v_poc_pu [pu],status []\n0.0,1.0,ok\n")
    with pytest.raises(ContractError, match="non-numeric"):
        read_csv(text_col)
    new = tmp_path / "new.csv"
    new.write_text("time [s],v_poc_pu [pu],connected [status]\n0.0,1.0,1.0\n0.1,0.9,\n")
    frame = read_csv(new, blank_status=(CONNECTED,))
    assert frame.channel(CONNECTED).tolist() == [1.0, STATUS_NOT_READ]


# --- T9: the sidecar is the case's own file, read as a reason, never as data --------------------------------------


def _sidecar_judge(tmp_path, connected, sidecar):
    pack = _pack2(tmp_path, [FAULT])
    _write(pack / "results" / f"{FAULT.case_id}.csv", _fault_channels(), connected)
    (pack / "results" / f"{FAULT.case_id}.status.txt").write_text(sidecar)
    j = judge_pack(pack, PROFILE, RATINGS)
    return j, _by_id(j)[FAULT.case_id]


def test_the_sidecar_is_a_reason_on_the_stays_connected_row(tmp_path):
    side = "t=0.3 s: not read (PoC branch: ierr=1, status=0)\n"
    j, c = _sidecar_judge(tmp_path, _status((0.3, None), (0.31, 1.0)), side)
    sc = _rows(c)["stays_connected"]
    assert sc["status"] == "unknown"
    assert "status poll failed at t=0.3" in sc["detail"]
    assert f"{FAULT.case_id}.status.txt: t=0.3 s: not read (PoC branch" in sc["detail"]
    assert j["header"]["extra_files"] == []


def test_a_numeric_sidecar_is_never_read_as_a_sample(tmp_path):
    # all-1 samples pass; a sidecar of zeros changes no verdict, only the detail
    _, plain = _sidecar_judge(tmp_path / "a", _status(), "")
    _, noisy = _sidecar_judge(tmp_path / "b", _status(), "0\n0\n0\n0\n0\n0\n0\n")
    assert _rows(plain)["stays_connected"]["status"] == "pass"
    assert _rows(noisy)["stays_connected"]["status"] == "pass"
    assert plain["status"] == noisy["status"]
    assert _rows(noisy)["stays_connected"]["detail"].endswith(
        f"{FAULT.case_id}.status.txt: 0; 0; 0; 0; 0; 0; 0"
    )
    assert "status.txt" not in (_rows(plain)["stays_connected"]["detail"] or "")


def test_a_long_sidecar_is_capped_and_marked_truncated(tmp_path):
    # GK A P5: 300 lines of 30 characters; the detail quotes 2,000 of them
    line = "t=0.123 s: not read (ierr=1)"
    _, c = _sidecar_judge(tmp_path, _status(), "\n".join([line] * 300) + "\n")
    detail = _rows(c)["stays_connected"]["detail"]
    quoted = detail.split(f"{FAULT.case_id}.status.txt: ", 1)[1]
    total = len("; ".join([line] * 300))
    assert quoted.endswith(f" ... (truncated, {total} characters)")
    assert len(quoted) == 2000 + len(f" ... (truncated, {total} characters)")
    assert _rows(c)["stays_connected"]["status"] == "pass"


def test_a_sidecar_of_exactly_the_cap_is_not_truncated(tmp_path):
    _, c = _sidecar_judge(tmp_path, _status(), "x" * 2000 + "\n")
    detail = _rows(c)["stays_connected"]["detail"]
    assert detail.endswith(f"{FAULT.case_id}.status.txt: " + "x" * 2000)


def test_no_sidecar_adds_nothing(tmp_path):
    pack = _pack2(tmp_path, [FAULT])
    _write(pack / "results" / f"{FAULT.case_id}.csv", _fault_channels(), _status())
    c = _by_id(judge_pack(pack, PROFILE, RATINGS))[FAULT.case_id]
    assert "status.txt" not in str(c)


def test_a_sidecar_without_a_stays_connected_row_goes_to_the_case_detail(tmp_path):
    # an unreadable result has no rows: the sidecar reason lands in the case detail
    pack = _pack2(tmp_path, [FAULT])
    (pack / "results" / f"{FAULT.case_id}.csv").write_text(
        "t_s,v_poc_pu,status\n0.0,1.0,\n"
    )
    (pack / "results" / f"{FAULT.case_id}.status.txt").write_text("t=0.0 s: not read\n")
    c = _by_id(judge_pack(pack, PROFILE, RATINGS))[FAULT.case_id]
    assert c["rows"] == [] and c["status"] == "unknown"
    assert c["detail"].startswith(f"{FAULT.case_id}.csv unreadable")
    assert c["detail"].endswith(f"; {FAULT.case_id}.status.txt: t=0.0 s: not read")


def test_another_case_s_sidecar_is_still_an_extra_file(tmp_path):
    pack = _pack2(tmp_path, [FAULT])
    _write(pack / "results" / f"{FAULT.case_id}.csv", _fault_channels(), _status())
    (pack / "results" / "other_case.status.txt").write_text("x\n")
    j = judge_pack(pack, PROFILE, RATINGS)
    assert j["header"]["extra_files"] == ["other_case.status.txt"]


def test_status_machines_default_to_the_plant_machine(tmp_path):
    case = next(c for c in _battery2() if c.kind == "flat")
    out = write_scripts([case], str(tmp_path), **KW, contract_version="2")
    text = (tmp_path / out[case.case_id]).read_text()
    assert _assigned(text, "STATUS_MACHINES") == [KW["plant_machine"]]

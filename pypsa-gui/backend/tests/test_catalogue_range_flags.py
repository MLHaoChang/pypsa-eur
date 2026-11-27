"""Import range flags (expert-catalogue 01a-3b; gatekeeper C9, R2, R10 and conditions a-f).

Every import response carries ``range_flags`` (capped) and ``range_flags_total``:
the stored values a write would refuse, and each min above its max. The values
load unchanged, the flags are never persisted, and a read route recomputes them.
"""

from __future__ import annotations

import io
import json
import pathlib
import tempfile
import threading
import time
import uuid
import zipfile

import pandas as pd
import pypsa
import pytest

from db.models import Project
from services import dirty_state, excel_workbook
from services.asset_schema import ranges as R
from services.pypsa_service import PyPSAService
from tests.test_model_import import _dir as _model_dir
from tests.test_model_import import _import as _model_import
from tests.test_model_import import _preview as _model_preview
from tests.test_model_import import fake  # noqa: F401

MIME = excel_workbook.MIME
PAIR_MESSAGE = "Smallest size (10.0) is above Largest size (5.0)."


def _flagged() -> pypsa.Network:
    """Bus B0 at v_nom=0 (out of range) and G1 with p_nom_min above p_nom_max."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "B1", v_nom=110.0)
    n.add("Bus", "B0", v_nom=0.0)
    n.add("Generator", "G1", bus="B1", p_nom=20.0, p_nom_min=10.0, p_nom_max=5.0)
    n.add("Load", "L1", bus="B1", p_set=5.0)
    return n


def _kinds(rows) -> set[tuple[str, str, str, str]]:
    return {(r["kind"], r["component"], r["id"], r["field"]) for r in rows}


BOTH = {
    ("out_of_range", "Bus", "B0", "v_nom"),
    ("min_above_max", "Generator", "G1", "p_nom_min"),
}


def _assert_both(body: dict) -> None:
    assert BOTH <= _kinds(body["range_flags"]), body["range_flags"]
    assert body["range_flags_total"] == len(body["range_flags"])


def _nc_bytes(n: pypsa.Network) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "n.nc"
        n.export_to_netcdf(str(path))
        return path.read_bytes()


def _import_nc(client, n: pypsa.Network):
    return client.post(
        "/api/io/import/netcdf",
        files={"file": ("n.nc", _nc_bytes(n), "application/x-netcdf")},
    )


def _live(client, component: str, name: str) -> dict:
    """The stored row, as the open network serves it."""
    (row,) = [
        r for r in client.get(f"/api/network/{component}").json() if r["name"] == name
    ]
    return row


def _project_dir(session_local, *, name: str | None = None, pid: str | None = None):
    from services import project_registry

    with session_local() as db:
        if pid is not None:
            row = db.get(Project, uuid.UUID(pid))
        else:
            row = db.query(Project).filter(Project.name == name).one()
        return project_registry.project_dir(row)


def _assert_nothing_persisted(directory: pathlib.Path, messages: list[str]) -> None:
    """R10: no file under the project names the flags or carries a flag's message."""
    files = [p for p in directory.rglob("*") if p.is_file()]
    assert files
    for path in files:
        data = path.read_bytes()
        assert b"range_flags" not in data, path
        for message in messages:
            assert message.encode() not in data, path


# ── flags(n) ─────────────────────────────────────────────────────────────────


def test_flags_lists_out_of_range_then_min_above_max_and_changes_nothing():
    n = _flagged()
    before = (n.buses.copy(), n.generators.copy())
    rows = R.flags(n)
    assert [r["kind"] for r in rows] == ["out_of_range", "min_above_max"]
    assert rows[1] == {
        "component": "Generator",
        "id": "G1",
        "field": "p_nom_min",
        "value": "10.0",
        "message": PAIR_MESSAGE,
        "kind": "min_above_max",
    }
    pd.testing.assert_frame_equal(n.buses, before[0])
    pd.testing.assert_frame_equal(n.generators, before[1])
    # problems() rows stay as they were: no kind.
    assert all("kind" not in r for r in R.problems(n))


def test_storage_unit_efficiency_dispatch_zero_is_out_of_range():
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=110.0)
    n.add("StorageUnit", "SU", bus="B1", efficiency_dispatch=0.0)
    assert _kinds(R.flags(n)) == {
        ("out_of_range", "StorageUnit", "SU", "efficiency_dispatch")
    }


@pytest.mark.parametrize(
    "lo, hi",
    [
        (10.0, float("inf")),  # an infinite max
        (float("inf"), 5.0),  # an infinite min
        (float("-inf"), 5.0),
        (float("nan"), 5.0),  # a NaN side
        (10.0, float("nan")),
        (5.0, 5.0),  # equal is fine
    ],
)
def test_a_pair_flags_only_finite_min_above_finite_max(lo, hi):
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=110.0)
    n.add("Generator", "G1", bus="B1")
    n.generators.loc["G1", ["p_nom_min", "p_nom_max"]] = [lo, hi]
    # (an infinite p_nom_min is its own out_of_range row; never a pair flag)
    assert [r for r in R.flags(n) if r["kind"] == "min_above_max"] == []


@pytest.mark.parametrize(
    "cls, attr, lo, hi, extra",
    [
        ("Link", "links", "p_nom_min", "p_nom_max", {"bus0": "B1", "bus1": "B2"}),
        ("Link", "links", "p_min_pu", "p_max_pu", {"bus0": "B1", "bus1": "B2"}),
        ("StorageUnit", "storage_units", "p_nom_min", "p_nom_max", {"bus": "B1"}),
        ("StorageUnit", "storage_units", "p_min_pu", "p_max_pu", {"bus": "B1"}),
        (
            "Transformer",
            "transformers",
            "s_nom_min",
            "s_nom_max",
            {"bus0": "B1", "bus1": "B2", "x": 0.1, "s_nom": 1.0},
        ),
        (
            "Line",
            "lines",
            "s_nom_min",
            "s_nom_max",
            {"bus0": "B1", "bus1": "B2", "x": 0.1},
        ),
        ("Store", "stores", "e_nom_min", "e_nom_max", {"bus": "B1"}),
        ("Store", "stores", "e_min_pu", "e_max_pu", {"bus": "B1"}),
        ("Generator", "generators", "p_min_pu", "p_max_pu", {"bus": "B1"}),
    ],
)
def test_each_pair_flags_a_min_above_its_max(cls, attr, lo, hi, extra):
    n = pypsa.Network()
    n.add("Bus", ["B1", "B2"], v_nom=110.0)
    n.add(cls, "X", **extra)
    getattr(n, attr).loc["X", [lo, hi]] = [0.9, 0.5]
    rows = [r for r in R.flags(n) if r["kind"] == "min_above_max"]
    assert [(r["component"], r["id"], r["field"], r["value"]) for r in rows] == [
        (cls, "X", lo, "0.9")
    ]
    assert "(0.9) is above " in rows[0]["message"]
    assert rows[0]["message"].endswith("(0.5).")


def test_import_flags_caps_the_rows_and_keeps_the_total():
    n = pypsa.Network()
    n.add("Bus", [f"B{i}" for i in range(5000)], v_nom=0.0)
    t = time.monotonic()
    out = R.import_flags(n)
    assert time.monotonic() - t < 5
    assert len(out["range_flags"]) == R.FLAG_CAP == 200
    assert out["range_flags_total"] == 5000


def test_import_flags_answers_none_when_the_sweep_fails(monkeypatch, caplog):
    def broken(n):
        raise RuntimeError("sweep broke")

    monkeypatch.setattr(R, "flags", broken)
    assert R.import_flags(_flagged()) == {"range_flags": [], "range_flags_total": None}
    assert "range flags sweep failed" in caplog.text


# ── every import path ────────────────────────────────────────────────────────


def test_a_netcdf_import_flags_and_loads_unchanged(client):
    r = _import_nc(client, _flagged())
    assert r.status_code == 200, r.text
    _assert_both(r.json())
    assert _live(client, "buses", "B0")["v_nom"] == 0.0
    g = _live(client, "generators", "G1")
    assert (g["p_nom_min"], g["p_nom_max"]) == (10.0, 5.0)


def test_a_csv_import_flags_and_loads_unchanged(client):
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as d:
        _flagged().export_to_csv_folder(d)
        with zipfile.ZipFile(buf, "w") as zf:
            for p in pathlib.Path(d).iterdir():
                zf.write(p, p.name)
    r = client.post(
        "/api/io/import/csv",
        files={"file": ("n.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    _assert_both(r.json())
    assert _live(client, "buses", "B0")["v_nom"] == 0.0
    assert _live(client, "generators", "G1")["p_nom_min"] == 10.0


MATPOWER = """function mpc = case3
mpc.baseMVA = 100;
mpc.bus = [
1 3 0 0 0 0 1 1 0 110 1 1.1 0.9;
2 1 50 0 0 0 1 1 0 110 1 1.1 0.9;
3 1 0 0 0 0 1 1 0 0 1 1.1 0.9;
];
mpc.gen = [
1 0 0 0 0 1 100 1 10 20 0 0 0 0 0 0 0 0 0 0 0;
];
mpc.branch = [
1 2 0.01 0.1 0 100 0 0 0 0 1 -360 360;
];
"""


def test_a_matpower_import_flags_and_loads_unchanged(client):
    r = client.post(
        "/api/io/import/matpower",
        files={"file": ("case3.m", MATPOWER.encode(), "text/plain")},
    )
    assert r.status_code == 200, r.text
    rows = r.json()["range_flags"]
    assert ("out_of_range", "Bus", "bus_3", "v_nom") in _kinds(rows)
    # Pmin 20 above Pmax 10: p_min_pu 2.0 above p_max_pu 1.0.
    assert ("min_above_max", "Generator", "gen_1", "p_min_pu") in _kinds(rows)
    assert _live(client, "buses", "bus_3")["v_nom"] == 0.0
    assert _live(client, "generators", "gen_1")["p_min_pu"] == 2.0


def _older_layout() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    sheets = {
        "buses": [["name", "v_nom"], ["B1", 110.0], ["B0", 0.0]],
        "generators": [
            ["name", "bus", "p_nom", "p_nom_min", "p_nom_max"],
            ["G1", "B1", 20.0, 10.0, 5.0],
        ],
    }
    for sheet, rows in sheets.items():
        ws = wb.create_sheet(sheet)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_a_legacy_excel_import_flags_and_loads_unchanged(client):
    r = client.post(
        "/api/io/import/excel", files={"file": ("old.xlsx", _older_layout(), MIME)}
    )
    assert r.status_code == 200, r.text
    _assert_both(r.json())
    assert _live(client, "buses", "B0")["v_nom"] == 0.0
    assert _live(client, "generators", "G1")["p_nom_max"] == 5.0


def _workbook() -> bytes:
    solver = {
        "multi_investment_periods": False,
        "investment_periods": [],
        "discount_rate": 0.07,
    }
    return excel_workbook._write(
        _flagged(), {"solver_config.json": solver}, template=False
    )


def _workbook_import(client, data: bytes, name: str):
    import hashlib

    return client.post(
        "/api/workbook/import",
        files={"file": ("wb.xlsx", data, MIME)},
        data={"expected_sha256": hashlib.sha256(data).hexdigest(), "name": name},
    )


def test_a_workbook_preview_and_import_flag_alike_and_load_unchanged(client, _auth_db):
    _, session_local = _auth_db
    data = _workbook()
    preview = client.post(
        "/api/workbook/preview", files={"file": ("wb.xlsx", data, MIME)}
    )
    assert preview.status_code == 200, preview.text
    _assert_both(preview.json())
    assert "range_flags" not in preview.json()["summary"]
    r = _workbook_import(client, data, "WB flags")
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_both(body)
    key = lambda row: json.dumps(row, sort_keys=True)  # noqa: E731
    assert sorted(preview.json()["range_flags"], key=key) == sorted(
        body["range_flags"], key=key
    )
    dest = _project_dir(session_local, pid=body["project_id"])
    saved = pypsa.Network(str(dest / "network.nc"))
    assert saved.buses.at["B0", "v_nom"] == 0.0
    assert saved.generators.at["G1", "p_nom_min"] == 10.0
    _assert_nothing_persisted(dest, [PAIR_MESSAGE])


def test_a_model_import_preview_and_import_flag_and_load_unchanged(
    client,
    fake,  # noqa: F811
    _auth_db,
):
    _, session_local = _auth_db
    named = ("m.fake", b"flagged")
    preview = _model_preview(client, named)
    assert preview.status_code == 200, preview.text
    rows = preview.json()["range_flags"]
    assert {
        ("out_of_range", "Bus", "South", "v_nom"),
        ("min_above_max", "Generator", "G1", "p_nom_min"),
    } <= _kinds(rows)
    assert preview.json()["range_flags_total"] == len(rows)
    json.dumps(rows)  # plain JSON from the child
    r = _model_import(client, named, sha=preview.json()["sha256"], name="Flagged")
    assert r.status_code == 201, r.text
    assert r.json()["range_flags"] == rows
    dest = _model_dir(session_local, r.json()["project_id"])
    saved = pypsa.Network(str(dest / "network.nc"))
    assert saved.buses.at["South", "v_nom"] == 0.0
    assert saved.generators.at["G1", "p_nom_max"] == 5.0
    _assert_nothing_persisted(dest, [PAIR_MESSAGE, "range_flags_total"])


def test_a_large_import_is_capped_and_small(client):
    n = pypsa.Network()
    n.add("Bus", [f"B{i}" for i in range(5000)], v_nom=0.0)
    r = _import_nc(client, n)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["range_flags"]) == 200 and body["range_flags_total"] == 5000
    assert len(r.content) < 100_000


def test_a_failing_sweep_never_fails_the_import(client, monkeypatch):
    def broken(n):
        raise RuntimeError("sweep broke")

    monkeypatch.setattr(R, "flags", broken)
    r = _import_nc(client, _flagged())
    assert r.status_code == 200, r.text
    assert r.json()["range_flags"] == [] and r.json()["range_flags_total"] is None
    assert _live(client, "buses", "B0")["v_nom"] == 0.0


# ── R10: never persisted, recomputed on reopen ───────────────────────────────


def test_the_flags_are_never_saved_and_come_back_on_reopen(
    client, _auth_db, install_network
):
    _, session_local = _auth_db
    r = _import_nc(client, _flagged())
    assert r.status_code == 200, r.text
    messages = [row["message"] for row in r.json()["range_flags"]]
    assert client.post("/api/projects/R10 flags").status_code == 200
    dest = _project_dir(session_local, name="R10 flags")
    _assert_nothing_persisted(dest, messages + ["range_flags_total"])
    assert _kinds(R.flags(pypsa.Network(str(dest / "network.nc")))) == BOTH
    # Open another project, then reopen this one: the route recomputes the flags.
    other = pypsa.Network()
    other.add("Bus", "X", v_nom=20.0)
    install_network(other)
    assert client.post("/api/projects/Other").status_code == 200
    assert client.post("/api/projects/R10 flags/activate").status_code == 200
    got = client.get("/api/network/range_flags").json()["flags"]
    assert {row["message"] for row in got} == set(messages)


# ── the read route ───────────────────────────────────────────────────────────


def _saved_as(client, install_network, n, name):
    install_network(n)
    assert client.post(f"/api/projects/{name}").status_code == 200


def test_both_routes_answer_revision_and_flags(client, session_ctx, install_network):
    _saved_as(client, install_network, _flagged(), "Flags A")
    active = client.get("/api/network/range_flags")
    scoped = client.get("/api/projects/Flags A/network/range_flags")
    assert active.status_code == scoped.status_code == 200
    assert active.json() == scoped.json()
    assert set(active.json()) == {"revision", "flags"}
    assert active.json()["revision"] == dirty_state.revision(session_ctx(client))
    assert _kinds(active.json()["flags"]) == BOTH


def test_the_path_scoped_route_serves_its_own_project(client, install_network):
    b = pypsa.Network()
    b.add("Bus", "Bonly", v_nom=0.0)
    _saved_as(client, install_network, b, "Flags B")
    _saved_as(client, install_network, _flagged(), "Flags A")
    assert client.get("/api/network/meta").json()["loaded_project"] == "Flags A"
    got = client.get("/api/projects/Flags B/network/range_flags").json()["flags"]
    assert _kinds(got) == {("out_of_range", "Bus", "Bonly", "v_nom")}
    assert _kinds(client.get("/api/network/range_flags").json()["flags"]) == BOTH


def test_the_revision_is_read_before_the_sweep(
    client, session_ctx, install_network, monkeypatch
):
    _saved_as(client, install_network, _flagged(), "Flags rev")
    real = R.flags

    def editing(n):
        dirty_state.bump_revision()
        return real(n)

    monkeypatch.setattr(R, "flags", editing)
    body = client.get("/api/network/range_flags").json()
    assert body["revision"] == dirty_state.revision(session_ctx(client)) - 1


def test_the_route_takes_no_mutation_lock(client, install_network):
    _saved_as(client, install_network, _flagged(), "Flags lock")
    held, release = threading.Event(), threading.Event()

    def hold():
        with PyPSAService.get_lock():
            held.set()
            release.wait(30)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(5)
    out: dict = {}
    reader = threading.Thread(
        target=lambda: out.setdefault("r", client.get("/api/network/range_flags")),
        daemon=True,
    )
    try:
        reader.start()
        reader.join(5)
        assert "r" in out and out["r"].status_code == 200
    finally:
        release.set()
        holder.join(5)


def test_the_meta_payload_is_unchanged(client, install_network):
    _saved_as(client, install_network, _flagged(), "Flags meta")
    assert set(client.get("/api/network/meta").json()) == {
        "name",
        "loaded_project",
        "snapshot_count",
        "bus_count",
        "display_names",
    }


@pytest.mark.parametrize("lo, hi", [("sk_min_mva", "sk_max_mva"), ("rx_min", "rx_max")])
def test_the_grid_strength_pairs_flag_on_a_generator(lo, hi):
    """DGS X8: the deferred sk_min <= sk_max (and rx) cross-check is a flag."""
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=110.0)
    n.add("Generator", "G", bus="B1")
    n.generators[lo] = 9.0
    n.generators[hi] = 3.0
    rows = [r for r in R.flags(n) if r["kind"] == "min_above_max"]
    assert [(r["component"], r["id"], r["field"]) for r in rows] == [
        ("Generator", "G", lo)
    ]


def test_a_failing_sweep_on_the_read_route_is_no_500(
    client, install_network, monkeypatch
):
    """No lock: an edit may remove a row mid-sweep. The read logs it and
    answers flags None ("read again"), never a 500."""
    _saved_as(client, install_network, _flagged(), "Flags race")

    def boom(_n):
        raise KeyError("row removed mid-sweep")

    monkeypatch.setattr(R, "flags", boom)
    for url in (
        "/api/network/range_flags",
        "/api/projects/Flags race/network/range_flags",
    ):
        r = client.get(url)
        assert r.status_code == 200, r.text
        assert r.json()["flags"] is None
        assert isinstance(r.json()["revision"], int)

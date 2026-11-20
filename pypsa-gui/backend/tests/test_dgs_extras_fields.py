"""
DGS-EXTRAS-1: the harmonic screen's inputs on a Load or Generator (`rated_mva`, `harmonic_source`,
`harmonic_spectrum`) and the slack's external-grid strength (`sk_max_mva`, `sk_min_mva`, `rx_max`, `rx_min`),
through the same write path and checks as a process unit's.
"""

from __future__ import annotations

import math
import re

import pypsa
import pytest

from services.asset_schema import fields as F

GEN = {
    "name": "pv",
    "bus": "b",
    "carrier": "solar",
    "p_nom": 10.0,
    "rated_mva": 12.0,
    "harmonic_source": "active-front-end",
    "harmonic_spectrum": "5:3, 7:2",
}
SLACK = {
    "name": "grid",
    "bus": "b",
    "carrier": "AC",
    "p_nom": 100.0,
    "control": "Slack",
    "sk_max_mva": 3000.0,
    "sk_min_mva": 1200.0,
    "rx_max": 0.1,
    "rx_min": 0.15,
}
LOAD = {
    "name": "pump",
    "bus": "b",
    "p_set": 3.0,
    "rated_mva": 4.5,
    "harmonic_source": "6-pulse",
    "harmonic_spectrum": "5:20, 7:14",
}

PRE_DGS_EXTRAS_SHA256 = {
    "Bus": "d504cb0b4da3db5b05967f320113b47d8f4947dd60371ee8a2faad4c05a68e85",
    "Carrier": "a1285884064f19c0e275e8aa3d07e9ef2cb6e8924d4f50c79c22bf725dcd962d",
    "GlobalConstraint": "0ce876213a5b687d73814bfb9ae47be96be6bce48c5e92b90ab106f423894a17",
    "Line": "c6e8afe62a68bfbb5648197501d0493fa172cc1fc94ce32a95a6ad7c03b3dc45",
    "Link": "5f2d6bb286c637254baeff134d32e74d3c8d727da15e8b6f8254e7c1221b83e0",
    "ShuntImpedance": "cfe3cac8aa4bd4593c2b65fd4c0583e8c2524fa5663d21f6f9016a008d033097",
    "StorageUnit": "b1c244222129e35e87f29e25e36e487161bcf8018acf88de0d70767b96a040ba",
    "Store": "52f631a4f0341c8c625463fcdd807cb64f0f038cd80a8ac9992f04c2904e3e09",
    "Transformer": "0a78f88682086f610bb0e4f99efa07b3a51ba8584a447ca2e92a6f0d59ada993",
}


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    return n


@pytest.mark.parametrize(
    "route, body, attr",
    [
        ("generators", GEN, "generators"),
        ("generators", SLACK, "generators"),
        ("loads", LOAD, "loads"),
    ],
)
def test_the_new_inputs_round_trip(client, install_network, route, body, attr):
    n = _net()
    install_network(n)
    r = client.post(f"/api/network/{route}", json=body)
    assert r.status_code in (200, 201), r.text
    df = getattr(n, attr)
    for k, v in body.items():
        if k in ("name", "bus", "carrier", "p_nom", "p_set", "control"):
            continue
        assert df.at[body["name"], k] == v, (k, df.at[body["name"], k])


@pytest.mark.parametrize("route, body", [("generators", GEN), ("loads", LOAD)])
@pytest.mark.parametrize(
    "patch",
    [
        {"harmonic_source": "24-pulse"},
        {"harmonic_spectrum": "5:20, 5:10"},
        {"harmonic_spectrum": "51:1"},
        {"rated_mva": -1.0},
    ],
)
def test_bad_harmonic_inputs_are_refused(client, install_network, route, body, patch):
    install_network(_net())
    r = client.post(f"/api/network/{route}", json={**body, **patch})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "patch",
    [{"sk_max_mva": -1.0}, {"sk_min_mva": -5.0}, {"rx_max": -0.1}, {"rx_min": -1.0}],
)
def test_negative_grid_strength_is_refused(client, install_network, patch):
    install_network(_net())
    r = client.post("/api/network/generators", json={**SLACK, **patch})
    assert r.status_code == 422, r.text


def test_an_edit_writes_the_new_inputs(client, install_network):
    n = _net()
    install_network(n)
    assert client.post(
        "/api/network/loads", json={"name": "pump", "bus": "b", "p_set": 3.0}
    ).status_code in (200, 201)
    r = client.put(
        "/api/network/loads/pump",
        json={
            "name": "pump",
            "bus": "b",
            "p_set": 3.0,
            "harmonic_source": "12-pulse",
            "rated_mva": 4.0,
        },
    )
    assert r.status_code == 200, r.text
    assert n.loads.at["pump", "harmonic_source"] == "12-pulse"
    assert n.loads.at["pump", "rated_mva"] == 4.0


def test_a_generator_without_the_inputs_gets_no_column(client, install_network):
    n = _net()
    install_network(n)
    body = {"name": "g", "bus": "b", "carrier": "AC", "p_nom": 1.0}
    assert client.post("/api/network/generators", json=body).status_code in (200, 201)
    for k in ("rated_mva", "sk_max_mva"):
        assert k not in n.generators.columns or math.isnan(
            float(n.generators.at["g", k])
        )


def test_grid_strength_shows_only_on_the_slack():
    by_key = {f.key: f for f in F.fields_for("Generator")}
    for k in ("sk_max_mva", "sk_min_mva", "rx_max", "rx_min"):
        assert by_key[k].reveal_when == {"control": "Slack"}
        assert by_key[k].blank == "clear"
    for k in ("harmonic_source", "harmonic_spectrum"):
        assert by_key[k].blank == "empty"
        assert {f.key: f for f in F.fields_for("Load")}[k].blank == "empty"
    assert by_key["harmonic_source"].options == F.HARMONIC_SOURCES
    assert {f.key: f for f in F.fields_for("Load")}["rated_mva"].blank == "clear"


# ── GK B X1–X7 ────────────────────────────────────────────────────────────────

NEW = {
    "Generator": {
        "rated_mva": 12.0,
        "harmonic_source": "6-pulse",
        "harmonic_spectrum": "5:3, 7:2",
        "sk_max_mva": 3000.0,
        "sk_min_mva": 1200.0,
        "rx_max": 0.1,
        "rx_min": 0.15,
    },
    "Load": {
        "rated_mva": 4.5,
        "harmonic_source": "6-pulse",
        "harmonic_spectrum": "5:20, 7:14",
    },
}


def _plain_net() -> pypsa.Network:
    n = _net()
    n.add("Generator", "g", bus="b", carrier="AC", p_nom=10.0, control="Slack")
    n.add("Load", "l", bus="b", p_set=3.0)
    return n


def test_x1_the_new_inputs_move_no_fingerprint(session_ctx, client, install_network):
    from services.dispatch_status import dispatch_digest
    from services.results.finance_case import owner_assets_digest
    from services.workflow_tools import _network_input_fingerprint

    n = _plain_net()
    install_network(n)
    ctx = session_ctx(client)

    # The investment case's digest reads the network through two parts: the
    # solve's result tables and the owner assets' investment columns.
    commercial = {
        "value_flows": {
            "asset_owners": [
                {"asset_id": "g", "component": "Generator", "owner": "site"}
            ]
        }
    }

    def fp():
        return (
            _network_input_fingerprint(ctx),
            _network_input_fingerprint(ctx, exact=True),
            dispatch_digest(n),
            owner_assets_digest(n, commercial),
        )

    before = fp()
    for cls, attr, name in (("Generator", "generators", "g"), ("Load", "loads", "l")):
        for k, v in NEW[cls].items():
            getattr(n, attr).loc[name, k] = v
    assert fp() == before


def _wire(updates: dict) -> dict:
    return {
        k: str(v) if isinstance(v, float) and not math.isfinite(v) else v
        for k, v in updates.items()
    }


def _refusal(call) -> str:
    """The message a chat tool refuses with; fails the test if it accepts."""
    from fastapi import HTTPException
    from pydantic import ValidationError

    try:
        out = call()
    except HTTPException as exc:
        assert exc.status_code == 422, exc.detail
        return str(exc.detail)
    except ValidationError as exc:
        return str(exc)
    pytest.fail(f"accepted: {out!r}")


@pytest.mark.parametrize(
    "cls, name, col, goods, bad, label, rule",
    [
        ("Generator", "g", "sk_max_mva", (3000.0, 2500.0, 2000.0), -1.0, "sk_max_mva", r"finite number >= 0"),
        ("Generator", "g", "harmonic_source", ("6-pulse", "12-pulse", "linear"), "24-pulse", "Harmonic source", r"is not one of"),
        ("Generator", "g", "rx_min", (0.15, 0.2, 0.25), float("nan"), "rx_min", r"finite number"),
        ("Generator", "g", "sk_min_mva", (1200.0, 1100.0, 1000.0), float("inf"), "sk_min_mva", r"finite number"),
        ("Generator", "g", "rated_mva", (12.0, 11.0, 10.0), True, "rated_mva", r"(expected a|valid) number"),
        ("Load", "l", "harmonic_spectrum", ("5:20", "7:14", "5:20, 7:14"), "5:20, 5:10", "Harmonic spectrum", r"order 5 appears twice"),
        ("Load", "l", "rated_mva", (4.5, 4.0, 3.5), -1.0, "rated_mva", r"finite number >= 0"),
    ],
)  # fmt: skip
def test_x2_bulk_and_chat_refuse_like_the_single_edit(
    bound_ctx, client, install_network, cls, name, col, goods, bad, label, rule
):
    """Each path first ACCEPTS a valid value (so a refusal below is not a missing
    row or a missing field), then refuses the bad one by its column and rule and
    leaves the stored value as it was."""
    from services import chat_tools

    n = _plain_net()
    install_network(n)
    df = lambda: getattr(n, {"Generator": "generators", "Load": "loads"}[cls])  # noqa: E731

    r = client.patch(
        "/api/network/_bulk",
        json={"component_class": cls, "names": [name], "updates": {col: goods[0]}},
    )
    assert r.status_code == 200, r.text
    assert df().at[name, col] == goods[0]
    with bound_ctx(client) as ctx:
        assert ctx.network is n
        chat_tools.bulk_update_components(cls, [name], {col: goods[1]})
        assert df().at[name, col] == goods[1]
        chat_tools.update_component(cls, name, attrs={col: goods[2]})
        assert df().at[name, col] == goods[2]

    r = client.patch(
        "/api/network/_bulk",
        # JSON has no NaN or inf: the wire carries them as text, which the float check reads.
        json={"component_class": cls, "names": [name], "updates": _wire({col: bad})},
    )
    assert r.status_code == 422, r.text
    messages = [r.json()["detail"]]
    with bound_ctx(client):
        messages.append(
            _refusal(lambda: chat_tools.bulk_update_components(cls, [name], {col: bad}))
        )
        messages.append(
            _refusal(lambda: chat_tools.update_component(cls, name, attrs={col: bad}))
        )
    for msg in messages:
        assert label in msg and re.search(rule, msg), msg
    assert df().at[name, col] == goods[2]


def test_x2_a_float_input_refuses_a_tick(client, install_network):
    install_network(_net())
    r = client.post("/api/network/generators", json={**SLACK, "sk_max_mva": True})
    assert r.status_code == 422, r.text


def test_x3_the_new_inputs_make_no_process_unit():
    from services.solver.process_uc import process_mask

    from gridspine.drivers.campus_study import harmonic_units

    n = _plain_net()
    n.add("Bus", "b2", carrier="AC")
    n.add("Link", "k", bus0="b", bus1="b2", p_nom=5.0)
    for k, v in NEW["Generator"].items():
        n.generators.loc["g", k] = v
    for k, v in NEW["Load"].items():
        n.loads.loc["l", k] = v
    mask = process_mask(n.links)
    assert list(mask.index) == ["k"] and not mask.any()
    assert harmonic_units(n, "b").empty


def test_x4_untouched_loads_and_generators_gain_no_column(tmp_path):
    from services.adequacy.eh_columns import normalise_eh_columns

    n = _plain_net()
    before = {a: list(getattr(n, a).columns) for a in ("generators", "loads")}
    normalise_eh_columns(n)
    assert {a: list(getattr(n, a).columns) for a in ("generators", "loads")} == before
    p = tmp_path / "a.nc"
    n.export_to_netcdf(p)
    m = pypsa.Network(str(p))
    normalise_eh_columns(m)
    for a in ("generators", "loads"):
        assert not set(getattr(m, a).columns) & {k for c in NEW.values() for k in c}


@pytest.mark.parametrize(
    "cls, attr, other, name",
    [("Load", "loads", "generators", "l"), ("Generator", "generators", "loads", "g")],
)
def test_x4_a_bulk_write_lands_on_its_own_frame(
    client, install_network, cls, attr, other, name
):
    n = _plain_net()
    install_network(n)
    r = client.patch(
        "/api/network/_bulk",
        json={"component_class": cls, "names": [name], "updates": {"rated_mva": 4.5}},
    )
    assert r.status_code == 200, r.text
    assert getattr(n, attr).at[name, "rated_mva"] == 4.5
    assert "rated_mva" not in getattr(n, other).columns


@pytest.mark.parametrize("fmt", ["netcdf", "csv"])
def test_x5_blank_reads_as_not_set_after_a_round_trip(tmp_path, fmt):
    from services.adequacy.eh_columns import normalise_eh_columns

    n = _plain_net()
    n.add("Load", "l2", bus="b", p_set=1.0)
    n.loads["harmonic_source"] = ["6-pulse", ""]
    n.loads["harmonic_spectrum"] = ["5:20", ""]
    n.loads["rated_mva"] = [4.5, float("nan")]
    if fmt == "netcdf":
        n.export_to_netcdf(tmp_path / "a.nc")
        m = pypsa.Network(str(tmp_path / "a.nc"))
    else:
        n.export_to_csv_folder(tmp_path / "csv")
        m = pypsa.Network(str(tmp_path / "csv"))
    normalise_eh_columns(m)
    assert m.loads.at["l2", "harmonic_source"] == ""
    assert m.loads.at["l2", "harmonic_spectrum"] == ""
    assert math.isnan(float(m.loads.at["l2", "rated_mva"]))
    assert m.loads.at["l", "harmonic_source"] == "6-pulse"
    assert m.loads.at["l", "rated_mva"] == 4.5


def _check_set_and_blank(m: pypsa.Network) -> None:
    for cls, attr, set_name, blank_name in (
        ("Generator", "generators", "g", "g2"),
        ("Load", "loads", "l", "l2"),
    ):
        df = getattr(m, attr)
        for k, v in NEW[cls].items():
            assert df.at[set_name, k] == v, (cls, k, df.at[set_name, k])
            blank = df.at[blank_name, k]
            if isinstance(v, str):
                assert blank == "", (cls, k, blank)
            else:
                assert math.isnan(float(blank)), (cls, k, blank)


def test_x5_the_new_inputs_survive_save_reload_and_a_bundle(
    client, install_network, session_ctx
):
    n = _plain_net()
    n.add("Generator", "g2", bus="b", carrier="AC", p_nom=1.0)
    n.add("Load", "l2", bus="b", p_set=1.0)
    install_network(n, name="dgs_rt")
    for cls, name in (("Generator", "g"), ("Load", "l")):
        r = client.patch(
            "/api/network/_bulk",
            json={"component_class": cls, "names": [name], "updates": NEW[cls]},
        )
        assert r.status_code == 200, r.text
    _check_set_and_blank(n)

    r = client.post("/api/projects/dgs_rt", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    assert client.get("/api/projects/dgs_rt").status_code == 200
    reloaded = session_ctx(client).network
    assert reloaded is not n
    _check_set_and_blank(reloaded)

    bundle = client.get("/api/projects/dgs_rt/bundle")
    assert bundle.status_code == 200, bundle.text
    r = client.post(
        "/api/projects/import_bundle?name=dgs_rt_copy",
        files={"file": ("x.pypsaproj.zip", bundle.content, "application/zip")},
    )
    assert r.status_code in (200, 201), r.text
    assert client.get("/api/projects/dgs_rt_copy").status_code == 200
    imported = session_ctx(client).network
    assert imported is not reloaded and imported.name == "dgs_rt_copy"
    _check_set_and_blank(imported)


def test_x6_an_overlong_spectrum_is_a_typed_422(client, install_network):
    from services.adequacy.eh_columns import SPECTRUM_MAX_CHARS

    install_network(_net())
    long = "5:20," + "0" * SPECTRUM_MAX_CHARS
    r = client.post("/api/network/loads", json={**LOAD, "harmonic_spectrum": long})
    assert r.status_code == 422 and "Harmonic spectrum: it is" in r.text, r.text


def test_x6_the_spectrum_cap_is_inclusive_and_only_for_spectra():
    from services.adequacy.eh_columns import SPECTRUM_MAX_CHARS, _checked_text

    at_cap = "5:1." + "0" * (SPECTRUM_MAX_CHARS - 4)
    assert len(at_cap) == SPECTRUM_MAX_CHARS
    assert _checked_text("harmonic_spectrum", at_cap) == at_cap
    with pytest.raises(ValueError, match="Harmonic spectrum: it is"):
        _checked_text("harmonic_spectrum", at_cap + "0")
    # A ride-through curve shares the parser path but not the spectrum cap.
    curve = "20:1." + "0" * SPECTRUM_MAX_CHARS
    assert _checked_text("ride_through_curve", curve) == curve


def test_x7_the_rerecording_moved_only_generator_and_load():
    """sha256 of every other class in asset_schema_payload_pre_catalogue.json, as recorded before DGS-EXTRAS-1."""
    import hashlib
    import json
    import pathlib

    fixture = (
        pathlib.Path(__file__).parent
        / "fixtures"
        / "asset_schema_payload_pre_catalogue.json"
    )
    recorded = json.loads(fixture.read_text("utf-8"))
    now = {
        c: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()
        for c, v in recorded.items()
        if c not in ("Generator", "Load")
    }
    assert now == PRE_DGS_EXTRAS_SHA256

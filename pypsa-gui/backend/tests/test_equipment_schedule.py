"""The campus equipment schedule (DC review 09): every electrical asset with
its rating, quantity, governing criterion, source and capex; declared
ratings below the chosen ones flagged, never overwritten."""

import io
import json
import math

import openpyxl
import pandas as pd
import pytest
import yaml

from db.models import User
from gridspine.drivers import campus_study as cs
from gridspine.static.campus_flow import SizingCriteria
from services import campus_electrical_service as ce
from services import equipment_schedule as es
from services import project_registry
from tests.test_campus_electrical_service import hub_network, user_and_db  # noqa: F401 - fixtures


def t(v, source="assumed"):
    return {"value": v, "source": source}


def _spec(declared_cable_i_ka=1.2, mv1_rated=20.0):
    trafo = {
        "hv_bus": "PCC",
        "lv_bus": "MV1",
        "sn_mva": t(40.0),
        "vn_hv_kv": t(110.0),
        "vn_lv_kv": t(20.0),
        "vk_percent": t(12.0),
        "vkr_percent": t(0.4),
        "pfe_kw": t(25.0),
        "i0_percent": t(0.05),
    }
    return {
        "campus": {
            "name": "SCHEDULE",
            "f_hz": 50,
            "pcc": {
                "bus": "PCC",
                "vn_kv": 110.0,
                "vm_pu": t(1.0),
                "sk_max_mva": t(3000.0),
                "sk_min_mva": t(2000.0),
                "rx_max": t(0.1),
                "rx_min": t(0.12),
            },
            "buses": {
                "MV1": {"vn_kv": 20.0, "ik_rated_ka": t(mv1_rated)},
                "MV2": {"vn_kv": 20.0},
            },
            "transformers": {"TR1": dict(trafo), "TR2": dict(trafo)},
            "cables": {
                "CB1": {
                    "from_bus": "MV1",
                    "to_bus": "MV2",
                    "length_km": t(2.0),
                    "r_ohm_per_km": t(0.0754),
                    "x_ohm_per_km": t(0.11),
                    "c_nf_per_km": t(330.0),
                    "max_i_ka": t(declared_cable_i_ka),
                    "parallel": 2,
                }
            },
            "units": {
                "DC_LOAD": {
                    "kind": "load",
                    "bus": "MV2",
                    "p_mw": t(50.0),
                    "pf": t(0.98),
                },
                "BESS1": {
                    "kind": "bess",
                    "bus": "MV1",
                    "p_mw": t(20.0),
                    "e_mwh": t(40.0),
                    "s_mva": t(22.0),
                    "k_sc": t(1.2),
                    "rx_sc": t(0.1),
                },
                "GEN1": {
                    "kind": "genset",
                    "bus": "MV2",
                    "p_mw": t(10.0),
                    "s_mva": t(12.5),
                    "xd_pp": t(0.15),
                    "rx_sc": t(0.07),
                },
            },
        }
    }


def _run(tmp_path, rated=True, **spec_kw):
    run = tmp_path / "run"
    run.mkdir()
    (run / cs.CAMPUS_YAML).write_text(yaml.safe_dump(_spec(**spec_kw)))
    pd.DataFrame(
        [
            {
                "group": "TR1+TR2",
                "units": 2,
                "unit_rating_mva": 40.0,
                "max_s_intact_mva": 46.0,
                "max_s_n1_mva": 52.0,
                "required_unit_mva": 62.4,
                "recommended_unit_mva": 63.0,
                "adequate": False,
                "utilisation": 1.3,
                "worst_unit": "TR1",
                "worst_period": 2040,
                "worst_hour": 19,
                "unconverged_hours": 0,
                "margin": 0.2,
                "n_minus_1": True,
            }
        ]
    ).to_csv(run / cs.SIZING_TRAFO_CSV, index=False)
    pd.DataFrame(
        [
            {
                "direction": "capacitive",
                "required_mvar": 5.0,
                "recommended_mvar": 6.0,
                "worst_period": 2030,
                "worst_hour": 7,
                "margin": 0.2,
            },
            {
                "direction": "inductive",
                "required_mvar": 0.0,
                "recommended_mvar": 0.0,
                "worst_period": None,
                "worst_hour": None,
                "margin": 0.2,
            },
        ]
    ).to_csv(run / cs.SIZING_COMP_CSV, index=False)
    pd.DataFrame(
        [
            {
                "bus": b,
                "vn_kv": kv,
                "ikss_max_ka": ik,
                "ip_max_ka": ip,
                "ikss_min_ka": ik / 2,
                "rated_ka": r,
                "period": p,
            }
            for b, kv, ik, ip, r, p in [
                ("PCC", 110.0, 15.0, 37.0, math.nan, 2030),
                ("MV1", 20.0, 21.0, 52.0, 20.0, 2040),
                ("MV2", 20.0, 10.0, 22.0, math.nan, 2040),
            ]
        ]
    ).to_csv(run / cs.SHORT_CIRCUIT_CSV, index=False)
    pd.DataFrame(
        [(2030, 7, "intact", "CB1", 0.40), (2040, 19, "N-1:TR1", "CB1", 0.62)],
        columns=["period", "hour", "case", "cable", "i_ka"],
    ).to_csv(run / cs.LF_LINE_CSV, index=False)
    if rated:
        cs.rate_campus(run, criteria=SizingCriteria(margin=0.2))
    return run


def _by(rows, kind):
    return [r for r in rows if r["kind"] == kind]


def test_every_kind_with_rating_source_and_criterion(tmp_path):
    out = es.build(_run(tmp_path))
    rows = out["rows"]
    assert [r["kind"] for r in rows] == sorted(
        (r["kind"] for r in rows), key=es.KINDS.index
    )
    assert {r["kind"] for r in rows} == {
        "transformer",
        "reactive",
        "filter",
        "switchgear",
        "cable",
        "genset",
        "bess",
    }
    assert all(
        set(r)
        == {*es.COLUMNS, "kind_label"}
        | ({"rated_peak_ka"} if r["kind"] == "switchgear" else set())
        for r in rows
    )
    assert all(r["kind_label"] == es.KIND_LABELS[r["kind"]] for r in rows)

    (tr,) = _by(rows, "transformer")
    assert (tr["asset"], tr["rating"], tr["unit"], tr["quantity"], tr["source"]) == (
        "TR1+TR2",
        63.0,
        "MVA",
        2,
        "computed",
    )
    assert tr["governing"].startswith(
        "Loading with one transformer out 52.0 MVA + 20% margin = 62.4 MVA per unit, period 2040 hour 19"
    )
    assert (tr["declared_rating"], tr["flag"], tr["location"]) == (
        40.0,
        "declared_below_chosen",
        "PCC / MV1",
    )
    from gridspine.templates.campus_assets import (
        library_entries,
        load_asset_library,
        value,
    )

    (e,) = library_entries(
        load_asset_library(), "transformers", hv_kv=110.0, lv_kv=20.0, s_mva=63.0
    )
    assert tr["library_id"] == e["id"] and tr["capex_eur"] == pytest.approx(
        2 * value(e["capex_eur"])
    )

    cap, ind = _by(rows, "reactive")
    assert (cap["rating"], cap["quantity"], cap["unit"]) == (6.0, 1, "Mvar")
    assert "5.0 Mvar + 20% margin, period 2030 hour 7" in cap["governing"]
    assert ind["quantity"] == 0 and ind["governing"].startswith("none needed")

    (f,) = _by(rows, "filter")
    assert (
        f["source"] == "not sized"
        and f["governing"] == "not sized yet: filter sizing is not part of this study"
        and f["rating"] is None
    )

    sg = {r["asset"]: r for r in _by(rows, "switchgear")}
    mv1 = sg["MV1 switchgear"]
    assert (mv1["rating"], mv1["unit"], mv1["declared_rating"], mv1["flag"]) == (
        31.5,
        "kA",
        20.0,
        "declared_below_chosen",
    )
    assert (
        mv1["governing"]
        == "Thermal short-circuit current 21.36 kA, period 2040. Fault duration 1 s · rated duration 1 s."
    )
    # MV1: TR1, TR2 lv ends, two cable runs, BESS1 = 5 bays (one per connection)
    assert mv1["quantity"] == 5
    assert mv1["library_id"] == "SG_20_31p5"
    assert (
        sg["MV2 switchgear"]["flag"] is None
        and sg["MV2 switchgear"]["declared_rating"] is None
    )

    (cb,) = _by(rows, "cable")
    assert (
        cb["unit"] == "mm² Al"
        and cb["source"] == "computed"
        and cb["library_id"].startswith("CB_20_")
    )
    assert (
        "Design 620 A with TR1 out of service, period 2040 hour 19." in cb["governing"]
    )
    assert cb["declared_rating"] == pytest.approx(2400.0)  # 2 runs x 1.2 kA, in A
    assert cb["flag"] is None
    assert cb["location"] == "MV1 - MV2, 2 km"

    gen, bess = _by(rows, "genset")[0], _by(rows, "bess")[0]
    assert (gen["rating"], gen["unit"], gen["source"]) == (12.5, "MVA", "declared")
    assert bess["governing"] == "declared in the campus file; 40 MWh"
    assert not _by(rows, "load")
    assert out["missing"] == []
    assert {a["name"] for a in out["assumptions"]} >= {
        "derating",
        "vd_limit_pct",
        "mu",
        "margin",
    }


def test_switchgear_capex_is_bays_times_the_library_price(tmp_path):
    from gridspine.templates.campus_assets import load_asset_library, value

    lib = {e["id"]: e for e in load_asset_library()["switchgear"]}
    mv1 = next(
        r for r in es.build(_run(tmp_path))["rows"] if r["asset"] == "MV1 switchgear"
    )
    assert mv1["capex_eur"] == pytest.approx(5 * value(lib["SG_20_31p5"]["capex_eur"]))


def test_a_declared_cable_below_the_chosen_is_flagged_and_kept(tmp_path):
    run = _run(tmp_path, declared_cable_i_ka=0.1)
    (cb,) = _by(es.build(run)["rows"], "cable")
    assert cb["flag"] == "declared_below_chosen" and cb[
        "declared_rating"
    ] == pytest.approx(200.0)
    assert (
        yaml.safe_load((run / cs.CAMPUS_YAML).read_text())["campus"]["cables"]["CB1"][
            "max_i_ka"
        ]["value"]
        == 0.1
    )


def test_a_run_from_before_ratings_says_what_is_missing(tmp_path):
    out = es.build(_run(tmp_path, rated=False))
    assert not _by(out["rows"], "switchgear") and not _by(out["rows"], "cable")
    assert out["missing"] == [
        "switchgear and cable ratings: run the DC Grid study again"
    ]
    assert out["assumptions"] == []


def test_the_bay_count_is_listed_as_an_assumption(tmp_path):
    out = es.build(_run(tmp_path))
    assert {**es.BAYS_ASSUMPTION, "label": "Switchgear bays"} in out["assumptions"]
    assert all(
        a["label"] == es.ASSUMPTION_LABELS[a["name"]] for a in out["assumptions"]
    )
    assert {a["source"] for a in out["assumptions"] if a["name"] == "bays"} == {
        "assumed"
    }


def test_the_workbook_rows_equal_the_json_rows(tmp_path):
    out = es.build(_run(tmp_path))
    wb = openpyxl.load_workbook(io.BytesIO(es.to_xlsx(out)))
    head, *body = list(wb["Schedule"].values)
    assert len(body) == len(out["rows"])
    for got, want in zip(body, out["rows"]):
        assert list(got) == [
            es.KIND_LABELS[want["kind"]] if c == "kind" else want[c] for c in es.COLUMNS
        ]


def test_the_workbook_has_the_rows_and_the_assumptions(tmp_path):
    out = es.build(_run(tmp_path))
    wb = openpyxl.load_workbook(io.BytesIO(es.to_xlsx(out)))
    assert wb.sheetnames == ["Schedule", "Assumptions"]
    sheet = list(wb["Schedule"].values)
    assert (
        sheet[0][:3] == ("Kind", "Asset", "Location") and sheet[0][-1] == "Capex (EUR)"
    )
    assert len(sheet) == len(out["rows"]) + 1
    assert len(list(wb["Assumptions"].values)) == len(out["assumptions"]) + 1


# ── over HTTP ─────────────────────────────────────────────────────────────


@pytest.fixture
def hub(client, _auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project_registry.create_root(db, user, "Schedule Hub")


BASE = "/api/campus-electrical/Schedule Hub/equipment-schedule"


def test_no_study_yet_is_an_empty_schedule_that_says_so(client, hub):
    resp = client.get(BASE)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "rows": [],
        "assumptions": [],
        "missing": ["no DC Grid study has run: run it in Campus electrical"],
        "stale": False,
    }
    x = client.get(BASE + ".xlsx")
    assert x.status_code == 200
    assert x.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml"
    )
    assert "Schedule_Hub_equipment_schedule.xlsx" in x.headers[
        "content-disposition"
    ].replace(" ", "_")
    assert openpyxl.load_workbook(io.BytesIO(x.content)).sheetnames == [
        "Schedule",
        "Assumptions",
    ]


def test_the_schedule_of_the_published_run_over_http(
    client, hub, monkeypatch, tmp_path
):
    run = _run(tmp_path)
    monkeypatch.setattr(ce, "campus_dir", lambda project: tmp_path)
    body = client.get(BASE).json()
    assert {r["kind"] for r in body["rows"]} >= {"switchgear", "cable", "transformer"}
    assert body["rows"] == es.build(run)["rows"]
    for verb in ("post", "put", "delete"):
        assert getattr(client, verb)(BASE).status_code == 405, verb


def test_another_orgs_project_is_404(other_org_client, hub):
    assert other_org_client.get(BASE).status_code == 404
    assert other_org_client.get(BASE + ".xlsx").status_code == 404


# ── branches the mutants found untested ─────────────────────────────────────


def _sizing(run, **kw):
    row = {
        "group": "TR1+TR2",
        "units": 2,
        "unit_rating_mva": 40.0,
        "max_s_intact_mva": 46.0,
        "max_s_n1_mva": 52.0,
        "required_unit_mva": 62.4,
        "recommended_unit_mva": 63.0,
        "adequate": False,
        "utilisation": 1.3,
        "worst_unit": "TR1",
        "worst_period": 2040,
        "worst_hour": 19,
        "unconverged_hours": 0,
        "margin": 0.2,
        "n_minus_1": True,
        **kw,
    }
    pd.DataFrame([row]).to_csv(run / cs.SIZING_TRAFO_CSV, index=False)
    return es._transformers(run, _spec()["campus"], {"transformers": []})[0]


@pytest.mark.parametrize(
    "kw, flow",
    [
        ({}, "Loading with one transformer out 52.0 MVA"),
        (
            {"max_s_n1_mva": 46.0},
            "Loading with one transformer out 46.0 MVA",
        ),  # equal: the outage case is named
        ({"max_s_n1_mva": 45.0}, "Loading, all in service 46.0 MVA"),
        ({"n_minus_1": False}, "Loading, all in service 46.0 MVA"),
        ({"max_s_n1_mva": math.nan}, "Loading, all in service 46.0 MVA"),
    ],
)
def test_the_transformer_criterion_names_the_case_that_set_it(tmp_path, kw, flow):
    assert _sizing(_run(tmp_path, rated=False), **kw)["governing"].startswith(
        flow + " + 20% margin"
    )


@pytest.mark.parametrize(
    "declared, flag",
    [(40.0, "declared_below_chosen"), (63.0, None), (80.0, None), (math.nan, None)],
)
def test_the_transformer_flag(tmp_path, declared, flag):
    tr = _sizing(_run(tmp_path, rated=False), unit_rating_mva=declared)
    assert tr["flag"] == flag
    assert tr["declared_rating"] == (None if math.isnan(declared) else declared)


def test_the_transformer_price_needs_hv_lv_and_rating_to_match(tmp_path):
    run = _run(tmp_path, rated=False)
    _sizing(run)

    def entry(i, hv, lv, s):
        return {
            "id": i,
            "hv_kv": t(hv),
            "lv_kv": t(lv),
            "s_mva": t(s),
            "capex_eur": t(1000.0),
        }

    near = [
        entry("HV", 132.0, 20.0, 63.0),
        entry("LV", 110.0, 33.0, 63.0),
        entry("S", 110.0, 20.0, 80.0),
    ]
    spec = _spec()["campus"]
    (tr,) = es._transformers(run, spec, {"transformers": near})
    assert tr["library_id"] is None and tr["capex_eur"] is None
    (tr,) = es._transformers(
        run, spec, {"transformers": [*near, entry("OK", 110.0, 20.0, 63.0)]}
    )
    assert (tr["library_id"], tr["capex_eur"]) == ("OK", 2000.0)


def test_empty_sections_of_the_campus_file_give_no_rows():
    spec = {"transformers": None, "cables": None, "units": None}
    assert es._bays(spec) == {} and es._units(spec) == []


@pytest.mark.parametrize("keep", [cs.SWITCHGEAR_RATING_CSV, cs.CABLE_RATING_CSV])
def test_one_rating_table_alone_is_still_missing(tmp_path, keep):
    run = _run(tmp_path)
    other = (
        cs.CABLE_RATING_CSV
        if keep == cs.SWITCHGEAR_RATING_CSV
        else cs.SWITCHGEAR_RATING_CSV
    )
    (run / other).unlink()
    out = es.build(run)
    assert out["missing"] == [
        "switchgear and cable ratings: run the DC Grid study again"
    ]
    assert not _by(out["rows"], "switchgear") and not _by(out["rows"], "cable")


@pytest.mark.parametrize(
    "governing, words",
    [
        ("voltage drop", "Voltage drop 2.50 % 90% of the limit"),
        (
            "short-circuit withstand",
            "Short-circuit withstand: thermal current 12.0 kA needs 128 mm²",
        ),
        ("ampacity", "Current-carrying capacity, 90% used"),
    ],
)
def test_the_cable_criterion_text_names_its_number(tmp_path, governing, words):
    run = tmp_path
    pd.DataFrame(
        [
            {
                "cable": "CB1",
                "from_bus": "MV1",
                "to_bus": "MV2",
                "length_km": 2.0,
                "declared_runs": 1,
                "declared_i_ka": 0.4,
                "design_i_ka": 0.3,
                "design_case": "intact, period 2030 hour 7",
                "ith_ka": 12.0,
                "chosen_id": "C",
                "conductor": "Al",
                "runs": 1,
                "section_mm2": 150.0,
                "chosen_i_ka": 0.4,
                "capex_eur": 10.0,
                "governing": governing,
                "utilisation": 0.9,
                "vd_pct": 2.5,
                "s_min_mm2": 127.66,
                "flag": None,
            }
        ]
    ).to_csv(run / cs.CABLE_RATING_CSV, index=False)
    (cb,) = es._cables(run)
    assert (
        cb["governing"]
        == f"{words}. Design 300 A with all in service, period 2030 hour 7."
    )


# --------------------------------------------------------------------------
# GK A on #1981: every run shape reads, ids stay text, NaN never reads as judged
# --------------------------------------------------------------------------


def _dumps(out):
    return json.dumps(out, allow_nan=False)  # what the route serialises; NaN would 500


def test_a_run_that_rated_no_cable_reads(tmp_path):
    # writer -> reader: no load flow reached the cable, so rate_campus writes the not_rated shape
    run = _run(tmp_path, rated=False)
    pd.DataFrame(columns=["period", "hour", "case", "cable", "i_ka"]).to_csv(
        run / cs.LF_LINE_CSV, index=False
    )
    cs.rate_campus(run, criteria=SizingCriteria(margin=0.2))
    assert "conductor" not in pd.read_csv(run / cs.CABLE_RATING_CSV).columns
    out = es.build(run)
    (cb,) = _by(out["rows"], "cable")
    assert cb["rating"] is None and cb["capex_eur"] is None and cb["library_id"] is None
    assert (
        cb["governing"] == "no load flow reached this cable"
        and cb["flag"] == "not_rated"
    )
    assert cb["quantity"] == 2 and cb["unit"] == "mm²"
    assert cb["declared_rating"] == pytest.approx(2400.0) and cb["declared_unit"] == "A"
    _dumps(out)


def test_a_cable_no_library_cable_passes_reads(tmp_path):
    run = _run(tmp_path, rated=False)
    pd.DataFrame(
        [(2040, 19, "N-1:TR1", "CB1", 50.0)],
        columns=["period", "hour", "case", "cable", "i_ka"],
    ).to_csv(run / cs.LF_LINE_CSV, index=False)
    cs.rate_campus(run, criteria=SizingCriteria(margin=0.2))
    (cb,) = _by(es.build(run)["rows"], "cable")
    assert (
        cb["flag"] == "no_library_cable"
        and cb["rating"] is None
        and cb["capex_eur"] is None
    )
    assert cb["governing"].startswith("no library cable at 20 kV passes")
    assert cb["quantity"] == 2  # the declared runs


def test_a_switchgear_beyond_the_ladder_reads(tmp_path):
    run = _run(tmp_path, rated=False)
    sc = pd.read_csv(run / cs.SHORT_CIRCUIT_CSV)
    sc.loc[sc.bus == "MV1", ["ikss_max_ka", "ip_max_ka"]] = [90.0, 200.0]
    sc.to_csv(run / cs.SHORT_CIRCUIT_CSV, index=False)
    cs.rate_campus(run, criteria=SizingCriteria(margin=0.2))
    mv1 = next(r for r in es.build(run)["rows"] if r["asset"] == "MV1 switchgear")
    assert (
        mv1["flag"] == "beyond_ladder"
        and mv1["rating"] is None
        and mv1["capex_eur"] is None
    )
    assert mv1["governing"] == (
        "Above the largest standard rating: thermal short-circuit current 108.96 kA with the 20 % margin, "
        "largest rating 40 kA at 20 kV"
    )


def test_a_bus_with_no_fault_level_reads_not_evaluated(tmp_path):
    run = _run(tmp_path, rated=False)
    sc = pd.read_csv(run / cs.SHORT_CIRCUIT_CSV)
    sc.loc[sc.bus == "MV1", "ikss_max_ka"] = math.nan
    sc.to_csv(run / cs.SHORT_CIRCUIT_CSV, index=False)
    cs.rate_campus(run, criteria=SizingCriteria(margin=0.2))
    out = es.build(run)
    mv1 = next(r for r in out["rows"] if r["asset"] == "MV1 switchgear")
    assert (
        mv1["rating"] is None and mv1["capex_eur"] is None and mv1["library_id"] is None
    )
    assert mv1["flag"] == "not_evaluated"
    assert mv1["governing"] == "no fault level was computed at this bus"
    (cb,) = _by(out["rows"], "cable")  # MV1 is the cable's from bus
    assert cb["flag"] == "withstand_not_evaluated"
    assert "nan" not in _dumps(out).lower()


def test_ids_and_names_stay_text(tmp_path):
    run = _run(tmp_path)
    sg = pd.read_csv(run / cs.SWITCHGEAR_RATING_CSV, dtype={"bus": str})
    sg.loc[sg.bus == "MV1", ["bus", "chosen_id"]] = ["010", "1234"]
    sg.to_csv(run / cs.SWITCHGEAR_RATING_CSV, index=False)
    cb = pd.read_csv(run / cs.CABLE_RATING_CSV)
    cb[["cable", "chosen_id"]] = ["010", "1234"]
    cb.to_csv(run / cs.CABLE_RATING_CSV, index=False)
    rows = es.build(run)["rows"]
    sw = next(r for r in rows if r["asset"] == "010 switchgear")
    assert sw["library_id"] == "1234" and sw["location"].startswith("010 (")
    (c,) = _by(rows, "cable")
    assert (c["asset"], c["library_id"]) == ("010", "1234")


def test_nan_figures_read_not_evaluated(tmp_path):
    run = _run(tmp_path)
    cb = pd.read_csv(run / cs.CABLE_RATING_CSV)
    cb[["utilisation", "design_i_ka"]] = math.nan
    cb.to_csv(run / cs.CABLE_RATING_CSV, index=False)
    tr = pd.read_csv(run / cs.SIZING_TRAFO_CSV)
    tr["recommended_unit_mva"] = math.nan
    tr.to_csv(run / cs.SIZING_TRAFO_CSV, index=False)
    out = es.build(run)
    (c,) = _by(out["rows"], "cable")
    assert (
        ". Design not evaluated with " in c["governing"] and "nan" not in c["governing"]
    )
    for t in _by(out["rows"], "transformer"):
        assert t["rating"] is None and t["capex_eur"] is None and t["flag"] is None
    assert "nan" not in _dumps(out).lower()
    es.to_xlsx(out)


def test_each_declared_rating_names_its_unit(tmp_path):
    rows = es.build(_run(tmp_path))["rows"]
    units = {
        r["kind"]: r["declared_unit"] for r in rows if r["declared_rating"] is not None
    }
    assert units == {"transformer": "MVA", "switchgear": "kA", "cable": "A"}
    assert all(r["declared_unit"] is None for r in rows if r["declared_rating"] is None)


def test_the_schedule_prices_from_the_library_the_study_kept(tmp_path):
    from gridspine.templates.campus_assets import load_asset_library

    run = _run(tmp_path)
    lib = load_asset_library()
    for e in lib["transformers"]:
        e["capex_eur"] = {"value": 7.0, "source": "assumed"}
    (run / es.RATINGS_LIBRARY_FILE).write_text(yaml.safe_dump(lib))
    priced = [
        t["capex_eur"]
        for t in _by(es.build(run)["rows"], "transformer")
        if t["capex_eur"]
    ]
    assert priced and all(
        c == pytest.approx(7.0 * t)
        for c, t in zip(
            priced,
            [
                t["quantity"]
                for t in _by(es.build(run)["rows"], "transformer")
                if t["capex_eur"]
            ],
        )
    )


def test_a_changed_library_marks_the_schedule_stale(client, hub, monkeypatch, tmp_path):
    run = _run(tmp_path)
    monkeypatch.setattr(ce, "campus_dir", lambda project: tmp_path)
    monkeypatch.setattr(ce, "_stale", lambda project, d: False)
    (run / es.RATINGS_LIBRARY_FILE).write_bytes(
        ce.effective_library(tmp_path).read_bytes()
    )
    assert client.get(BASE).json()["stale"] is False
    changed = (
        ce.effective_library(tmp_path).read_bytes()
        + b"\n# a price edited since the study ran\n"
    )
    (run / es.RATINGS_LIBRARY_FILE).write_bytes(changed)
    assert client.get(BASE).json()["stale"] is True
    (run / es.RATINGS_LIBRARY_FILE).unlink()
    monkeypatch.setattr(ce, "_stale", lambda project, d: True)
    assert client.get(BASE).json()["stale"] is True


def test_a_study_run_writes_the_ratings_and_keeps_its_library(user_and_db):  # noqa: F811 - the fixture
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Rated Hub")
    hub_network(priced=True).export_to_netcdf(
        str(project_registry.ensure_project_dir(row) / "network.nc")
    )
    ce.draft(row)
    ce.run(row, {"k": 1, "pf": 0.95, "margin": 0.1, "n_minus_1": True})
    run = ce.campus_dir(row) / "run"
    for name in (
        cs.SWITCHGEAR_RATING_CSV,
        cs.CABLE_RATING_CSV,
        cs.RATING_ASSUMPTIONS_JSON,
    ):
        assert (run / name).is_file(), name
    assert (run / es.RATINGS_LIBRARY_FILE).read_bytes() == ce.effective_library(
        ce.campus_dir(row)
    ).read_bytes()
    out = es.for_project(row)
    assert (
        out["missing"] == []
        and out["stale"] is False
        and _by(out["rows"], "switchgear")
    )


def test_the_voltage_drop_names_its_limit_and_the_case_its_outage(tmp_path):
    run = _run(tmp_path)
    cb = pd.read_csv(run / cs.CABLE_RATING_CSV)
    cb[["governing", "vd_pct", "design_case"]] = [
        "voltage drop",
        2.9,
        "N-1:TR1, period 2040 hour 19",
    ]
    cb.to_csv(run / cs.CABLE_RATING_CSV, index=False)
    (c,) = _by(es.build(run)["rows"], "cable")
    assert c["governing"].startswith("Voltage drop 2.90 % of the 3 % limit. Design ")
    assert c["governing"].endswith(" with TR1 out of service, period 2040 hour 19.")


@pytest.mark.parametrize(
    "engine, words",
    [
        ("Ik'' 21.00 kA, period 3", "Initial short-circuit current 21.00 kA, period 3"),
        ("Ib 21.00 kA, period 3", "Breaking current 21.00 kA, period 3"),
        ("Ith 21.36 kA, period 3", "Thermal short-circuit current 21.36 kA, period 3"),
        (
            "Ith over 3 s referred to 1 s 25.98 kA, period 3",
            "Thermal short-circuit current over 3 s referred to 1 s 25.98 kA, period 3",
        ),
        ("ip 53.74 kA, period 3", "Peak short-circuit current 53.74 kA, period 3"),
    ],
)
def test_every_engine_quantity_reads_in_words(engine, words):
    assert es._plain(engine) == words


def test_the_peak_beyond_the_largest_rating_reads_in_words():
    text = (
        "ip 101.00 kA with the 0% margin, above the largest rated peak 100 kA at 33 kV"
    )
    assert es._switchgear_text(text, "beyond_ladder") == (
        "Above the largest standard rating: peak short-circuit current 101.00 kA with the 0 % margin, "
        "largest rated peak 100 kA at 33 kV"
    )


def test_every_kind_and_assumption_has_words():
    assert set(es.KIND_LABELS) == set(es.KINDS)
    from gridspine.static.campus_ratings import (
        CableAssumptions,
        SwitchgearAssumptions,
        assumptions_table,
    )

    names = {
        a["name"]
        for a in assumptions_table(SwitchgearAssumptions(), CableAssumptions())
    }
    assert names | {"design_cases", "margin", "bays"} <= set(es.ASSUMPTION_LABELS)


def test_a_partly_evaluated_bus_names_its_skipped_period_and_keeps_both_flags(tmp_path):
    run = _run(tmp_path, rated=False)
    sc = pd.read_csv(run / cs.SHORT_CIRCUIT_CSV)
    extra = sc[sc.bus == "MV1"].assign(period=2050, ikss_max_ka=math.nan)
    pd.concat([sc, extra]).to_csv(run / cs.SHORT_CIRCUIT_CSV, index=False)
    cs.rate_campus(run, criteria=SizingCriteria(margin=0.2))
    mv1 = next(r for r in es.build(run)["rows"] if r["asset"] == "MV1 switchgear")
    assert mv1["flag"] == "declared_below_chosen; partly_evaluated"
    assert es.flags_of(mv1["flag"]) == ["declared_below_chosen", "partly_evaluated"]
    assert mv1["governing"].endswith(
        "period 2040. Period 2050 had no fault level. Fault duration 1 s · rated duration 1 s."
    )
    assert mv1["rating"] == 31.5


def test_flags_of():
    assert es.flags_of(None) == [] and es.flags_of("a") == ["a"]
    assert es.flags_of("beyond_ladder; partly_evaluated") == [
        "beyond_ladder",
        "partly_evaluated",
    ]


def test_a_switchgear_row_carries_its_rated_peak(tmp_path):
    rows = es.build(_run(tmp_path))["rows"]
    mv1 = next(r for r in rows if r["asset"] == "MV1 switchgear")
    assert (mv1["rating"], mv1["rated_peak_ka"]) == (31.5, 78.75)  # 2.5 x 31.5 at 50 Hz


def test_a_bus_voltage_the_study_left_out_comes_from_the_campus_file(tmp_path):
    run = _run(tmp_path)
    sg = pd.read_csv(run / cs.SWITCHGEAR_RATING_CSV, dtype={"bus": str})
    sg["vn_kv"] = math.nan
    sg.loc[sg.bus == "MV2", "bus"] = "AUX"  # a bus the campus file does not list
    sg.to_csv(run / cs.SWITCHGEAR_RATING_CSV, index=False)
    rows = {r["asset"]: r for r in es.build(run)["rows"] if r["kind"] == "switchgear"}
    assert rows["MV1 switchgear"]["location"] == "MV1 (20 kV)"
    assert rows["AUX switchgear"]["location"] == "AUX"  # the table says "Not stated"


def test_an_assumption_the_word_map_does_not_know_never_shows_its_engine_name(tmp_path):
    run = _run(tmp_path)
    path = run / cs.RATING_ASSUMPTIONS_JSON
    path.write_text(
        json.dumps([*json.loads(path.read_text()), {"name": "zeta_x", "value": 1}])
    )
    (a,) = [a for a in es.build(run)["assumptions"] if a["name"] == "zeta_x"]
    assert a["label"] == es.OTHER_ASSUMPTION == "Other study setting"
    assert es.ASSUMPTION_LABELS["mu"] == "Breaking-current factor"


def test_several_skipped_periods_read_as_one_sentence():
    assert es._no_fault_level("2040") == "Period 2040 had no fault level."
    assert es._no_fault_level("2040, 2050") == "Periods 2040, 2050 had no fault level."


def test_no_criterion_chains_clauses_or_writes_mm2(tmp_path):
    for rated in (True, False):
        (tmp_path / str(rated)).mkdir()
        out = es.build(_run(tmp_path / str(rated), rated=rated))
        for r in out["rows"]:
            if r["kind"] in ("switchgear", "cable"):
                assert "; " not in r["governing"], r["governing"]
            assert "mm2" not in (r["governing"] or "") + (r["unit"] or "")

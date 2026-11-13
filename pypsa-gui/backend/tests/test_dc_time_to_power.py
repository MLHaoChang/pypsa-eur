"""DC 27: the time-to-power fact (owner decision DC27-TTP-1, GK A plan G1-G10).

First power is the first MW the grid delivers to a data-centre load in service:
a stated connection date wins; else the staged build's first serving stage.
Months are calendar months from the stated project start, a partial month
rounded up. A missing input reads not available with its reason, never 0.
"""

from datetime import date
from pathlib import Path

import pytest

from project_templates import eh_templates as T
from services import dc_time_to_power as D

PHASES = [
    {"cod": "2027-01-01", "it_mw": 12.0},
    {"cod": "2030-01-01", "it_mw": 24.0},
    {"cod": "2035-01-01", "it_mw": 36.0},
]


def stage(period, start, end, mw, is_set=True, max_mw=None):
    return {
        "period": str(period),
        "start": start,
        "end": end,
        "energized_mw": mw,
        "max_mw": mw if max_mw is None else max_mw,
        "set": is_set,
    }


STAGES = [
    stage(2027, "2027-01-01", "2030-01-01", 15.0),
    stage(2030, "2030-01-01", "2035-01-01", 55.0),
    stage(2035, "2035-01-01", "2045-01-01", 55.0),
]


def spec(project_start="2026-01-01", connection_date=None, phases=PHASES):
    return {
        "project_start": project_start,
        "connection_date": connection_date,
        "phases": phases,
    }


def ttp(s=None, stages=STAGES, stage_reason=None):
    return D.time_to_power(spec() if s is None else s, stages, stage_reason)


# ── months ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a, b, months",
    [
        ("2026-01-01", "2027-01-01", 12),
        ("2026-01-01", "2026-01-01", 0),
        ("2026-01-01", "2026-03-04", 3),  # 2 months 3 days rounds up
        ("2026-01-01", "2026-01-02", 1),
        ("2026-01-15", "2026-02-14", 1),
        ("2026-01-15", "2026-02-15", 1),
        ("2026-01-15", "2026-02-16", 2),
        ("2026-01-31", "2026-02-28", 1),  # month-end clamp
        ("2024-01-31", "2024-02-29", 1),
        ("2026-01-31", "2026-03-01", 2),
        ("2026-12-15", "2027-01-14", 1),  # across a year end
        ("2026-12-15", "2029-01-16", 26),
    ],
)
def test_months_are_calendar_months_rounded_up(a, b, months):
    assert D.months_between(date.fromisoformat(a), date.fromisoformat(b)) == months


def test_add_months_clamps_to_the_month_end():
    assert D.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert D.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


# ── the staged build ────────────────────────────────────────────────────────


def test_staged_first_power_is_the_first_serving_stage():
    out = ttp()
    assert out == {
        "status": "available",
        "months": 12,
        "first_power": "2027-01-01",
        "source": "staged_build",
        "stage": "2027",
        "earliest_first_power": "2027-01-01",
        "resolution_months": 0,
    }


def test_a_zero_mw_stage_is_not_serving_and_the_next_one_is():
    stages = [stage(2027, "2027-01-01", "2030-01-01", 0.0), *STAGES[1:]]
    out = ttp(stages=stages)
    assert (
        out["months"],
        out["stage"],
        out["first_power"],
        out["resolution_months"],
    ) == (48, "2030", "2030-01-01", 0)


def test_an_energised_stage_without_load_in_service_is_not_serving():
    phases = [
        {"cod": "2030-06-01", "it_mw": 24.0},
        {"cod": "2035-01-01", "it_mw": 36.0},
    ]
    out = ttp(spec(phases=phases))
    # 2027 is energised but no phase is in service by its end; 2030 serves from the COD.
    assert (out["stage"], out["first_power"], out["months"]) == (
        "2030",
        "2030-06-01",
        53,
    )


def test_a_phase_in_service_before_the_stage_starts_powers_at_the_stage_start():
    phases = [{"cod": "2025-01-01", "it_mw": 12.0}]
    stages = [stage(2027, "2027-01-01", "2030-01-01", 15.0)]
    out = ttp(spec(project_start="2024-01-01", phases=phases), stages)
    assert (out["first_power"], out["months"]) == ("2027-01-01", 36)


def test_a_phase_cod_inside_the_last_period_is_served_there():
    phases = [{"cod": "2044-12-31", "it_mw": 36.0}]
    out = ttp(spec(phases=phases))
    assert (out["stage"], out["first_power"], out["resolution_months"]) == (
        "2035",
        "2044-12-31",
        0,
    )
    beyond = [{"cod": "2045-01-01", "it_mw": 36.0}]
    assert ttp(spec(phases=beyond))["reason"] == "no_serving_stage"


# ── the connection date wins ───────────────────────────────────────────────


def test_a_set_connection_date_wins_over_the_staged_build():
    out = ttp(spec(connection_date="2028-07-15"))
    assert out == {
        "status": "available",
        "months": 31,
        "first_power": "2028-07-15",
        "source": "connection_date",
        "stage": None,
        "earliest_first_power": "2028-07-15",
        "resolution_months": 0,
    }


def test_a_connection_date_wins_even_where_the_staged_build_reads_nothing():
    out = ttp(
        spec(connection_date="2027-03-01"),
        [],
        {"reason": "import_link_missing", "detail": None},
    )
    assert (out["status"], out["months"], out["source"]) == (
        "available",
        14,
        "connection_date",
    )


def test_a_connection_date_on_the_project_start_is_a_real_zero():
    out = ttp(spec(connection_date="2026-01-01"))
    assert (out["status"], out["months"]) == ("available", 0)


# ── not available, each with its own reason (G9) ───────────────────────────


def test_no_dc_load_spec():
    out = D.time_to_power(None, STAGES)
    assert (
        out["status"] == "not_available"
        and out["reason"] == "no_dc_load_spec"
        and "months" not in out
    )


def test_no_project_start():
    out = ttp(spec(project_start=None, connection_date="2027-01-01"))
    assert (out["status"], out["reason"]) == ("not_available", "no_project_start")
    assert "months" not in out


def test_no_serving_stage():
    stages = [stage(y, f"{y}-01-01", f"{y + 1}-01-01", 0.0) for y in (2027, 2030)]
    out = ttp(stages=stages)
    assert (out["status"], out["reason"]) == ("not_available", "no_serving_stage")
    assert ttp(stages=[])["reason"] == "no_serving_stage"


def test_power_before_start():
    out = ttp(spec(project_start="2027-01-02"))
    assert (out["status"], out["reason"]) == ("not_available", "power_before_start")
    assert ttp(spec(project_start="2027-01-01"))["months"] == 0
    assert (
        ttp(spec(project_start="2028-01-01", connection_date="2027-12-31"))["reason"]
        == "power_before_start"
    )


def test_a_placeholder_stage_before_the_first_serving_stage_is_energisation_unset():
    stages = [stage(2028, "2028-01-01", "2030-01-01", 15.0, is_set=False), *STAGES[1:]]
    out = ttp(spec(phases=[{"cod": "2028-01-01", "it_mw": 12.0}, *PHASES[1:]]), stages)
    assert (out["status"], out["reason"], out["detail"]) == (
        "not_available",
        "energisation_unset",
        "2028",
    )


def test_a_placeholder_stage_after_the_first_serving_stage_does_not_matter():
    stages = [*STAGES[:2], stage(2036, "2036-01-01", "2045-01-01", 55.0, is_set=False)]
    assert ttp(stages=stages)["months"] == 12


@pytest.mark.parametrize(
    "reason", ["import_link_missing", "import_link_ambiguous", "import_unsized"]
)
def test_a_stage_refusal_is_carried_by_name(reason):
    out = ttp(stages=[], stage_reason={"reason": reason, "detail": None})
    assert (out["status"], out["reason"]) == ("not_available", reason)
    assert out["reason"] in D.REASONS


def test_every_reason_has_words():
    assert set(D.REASONS) == {
        "no_dc_load_spec",
        "no_project_start",
        "no_serving_stage",
        "power_before_start",
        "energisation_unset",
        "import_link_missing",
        "import_link_ambiguous",
        "import_unsized",
    }


# ── stages read from the saved network (G2, G5, G6, G1) ───────────────────


def test_staged_network_stages():
    n = T.BUILDERS["eh_datacenter_staged"]()
    meta = T.TEMPLATE_META["eh_datacenter_staged"]
    assert D.import_stages(n, meta) == STAGES


def test_single_year_network_is_one_stage_of_the_modelled_year():
    n = T.BUILDERS["eh_datacenter"]()
    assert D.import_stages(n, {}) == [stage("all", "2030-01-01", "2031-01-01", 40.0)]


def test_an_extendable_import_without_a_schedule_is_unsized():
    n = T.BUILDERS["eh_datacenter"]()
    n.links.loc["grid_import", "p_nom_extendable"] = True
    with pytest.raises(D.StagesUnavailable) as exc:
        D.import_stages(n, {})
    assert exc.value.reason == "import_unsized"


def test_the_import_link_is_found_by_its_role_not_its_name():
    n = T.BUILDERS["eh_datacenter_staged"]()
    n.links.loc["grid_import", "eh_role"] = ""
    with pytest.raises(D.StagesUnavailable) as exc:
        D.import_stages(n, {})
    assert exc.value.reason == "import_link_missing"
    n = T.BUILDERS["eh_datacenter_staged"]()
    n.links.rename(index={"grid_import": "poc_feed"}, inplace=True)
    for frame in n.links_t.values():
        frame.rename(columns={"grid_import": "poc_feed"}, inplace=True)
    assert D.import_stages(n, T.TEMPLATE_META["eh_datacenter_staged"]) == STAGES


def test_two_import_links_with_schedules_are_ambiguous():
    n = T.BUILDERS["eh_datacenter_staged"]()
    row = n.links.loc["grid_import"].copy()
    n.add("Link", "grid_import_2", **{k: row[k] for k in ("bus0", "bus1", "p_nom")})
    n.links.loc["grid_import_2", "eh_role"] = "grid_import"
    n.links_t["eh_energization_mw"]["grid_import_2"] = n.links_t["eh_energization_mw"][
        "grid_import"
    ]
    # An unscheduled third import is not named: the ambiguity is between the schedules.
    n.add("Link", "spare_feed", **{k: row[k] for k in ("bus0", "bus1", "p_nom")})
    n.links.loc["spare_feed", "eh_role"] = "grid_import"
    with pytest.raises(D.StagesUnavailable) as exc:
        D.import_stages(n, {})
    assert (exc.value.reason, exc.value.detail) == (
        "import_link_ambiguous",
        "grid_import, grid_import_2",
    )


def test_two_unscheduled_import_links_are_ambiguous():
    n = T.BUILDERS["eh_datacenter"]()
    row = n.links.loc["grid_import"].copy()
    n.add("Link", "grid_import_2", **{k: row[k] for k in ("bus0", "bus1", "p_nom")})
    n.links.loc["grid_import_2", "eh_role"] = "grid_import"
    with pytest.raises(D.StagesUnavailable) as exc:
        D.import_stages(n, {})
    assert (exc.value.reason, exc.value.detail) == (
        "import_link_ambiguous",
        "grid_import, grid_import_2",
    )


def test_the_last_period_ends_at_its_own_years():
    n = T.BUILDERS["eh_datacenter_staged"]()
    n.investment_period_weightings.loc[2035, "years"] = 7.0
    assert D.import_stages(n, {})[-1]["end"] == "2042-01-01"


def test_a_placeholder_energisation_period_is_marked_unset():
    planning = T.Planning((2028, 2030, 2035), 2045)
    n = T.BUILDERS["eh_datacenter_staged"](planning=planning)
    meta = T._dc_staged_meta(T._dc_plan(planning))
    stages = D.import_stages(n, meta)
    assert [(s["period"], s["set"]) for s in stages] == [
        ("2028", False),
        ("2030", True),
        ("2035", True),
    ]


# ── the stated dates: create body, stored format, fingerprints (G7, G8) ─────


def test_the_staged_preset_states_its_project_start_as_its_financial_close():
    s = T.dc_load_spec("eh_datacenter_staged", {})
    assert s.project_start == date(2026, 1, 1) and s.connection_date is None
    assert T._dc_finance(T._dc_plan())["financial_close"] == "2026-01-01"
    rows = {r["item"]: r for r in T.dc_load_meta("eh_datacenter_staged", s)["ledger"]}
    assert (
        rows["project start"]["value"] == "2026-01-01"
        and rows["project start"]["source"] == "assumed"
    )
    assert "grid connection date" not in rows
    assert T.dc_load_spec("eh_datacenter", {}).project_start is None


def test_the_create_body_takes_both_dates_and_names_a_bad_one():
    from services.dc_load_profile import DcLoadError

    s = T.dc_load_spec(
        "eh_datacenter_staged",
        {"project_start": "2025-06-01", "connection_date": "2027-03-01"},
    )
    assert (s.project_start, s.connection_date) == (date(2025, 6, 1), date(2027, 3, 1))
    rows = {
        r["item"]: r["value"]
        for r in T.dc_load_meta("eh_datacenter_staged", s)["ledger"]
    }
    assert rows["grid connection date"] == "2027-03-01"
    with pytest.raises(DcLoadError) as exc:
        T.dc_load_spec("eh_datacenter_staged", {"connection_date": "soon"})
    assert exc.value.field == "dc_load.connection_date"


def test_a_spec_without_the_dates_dumps_as_before():
    from services.dc_load_profile import meta_block

    s = T.dc_load_spec("eh_datacenter", {})
    dump = s.model_dump(mode="json")
    assert "project_start" not in dump and "connection_date" not in dump
    assert "null" not in str(meta_block(s, None)["spec"].get("project_start", ""))
    assert "project_start" not in meta_block(s, None)["spec"]
    items = {r["item"] for r in meta_block(s, None)["ledger"]}
    assert not items & {"project start", "grid connection date"}


@pytest.mark.parametrize("tid", sorted(T.TEMPLATE_META))
def test_native_sidecars_are_byte_identical(tmp_path, tid):
    import filecmp

    T.write_sidecars(tmp_path, tid)
    shipped = Path(T.__file__).parent / tid
    for f in sorted(tmp_path.rglob("*")):
        if f.is_file():
            assert filecmp.cmp(f, shipped / f.relative_to(tmp_path), shallow=False), (
                f.name
            )


def _fingerprint(n, tid, tmp_path):
    from types import SimpleNamespace

    from services.workflow_tools import _network_input_fingerprint

    return _network_input_fingerprint(
        SimpleNamespace(
            network=n,
            solver_state={"solver_config": T.solver_config(tid)},
            user_ts={},
            storage_dir=str(tmp_path),
        )
    )


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_the_dates_feed_no_solve_input(tmp_path, tid):
    a = T.dc_load_spec(tid, {})
    b = T.dc_load_spec(
        tid, {"project_start": "2020-02-02", "connection_date": "2031-03-03"}
    )
    na, nb = T.BUILDERS[tid](load_spec=a), T.BUILDERS[tid](load_spec=b)
    assert _fingerprint(na, tid, tmp_path) == _fingerprint(nb, tid, tmp_path)
    # The investment case's inputs (finance, commercial, solver sidecars) are the same files.
    T.write_sidecars(tmp_path / "a", tid, load_spec=a)
    T.write_sidecars(tmp_path / "b", tid, load_spec=b)
    for name in (
        T.SOLVER_FILE,
        *([T.ISLAND_FILE] if (tmp_path / "a" / T.ISLAND_FILE).exists() else []),
    ):
        assert (tmp_path / "a" / name).read_bytes() == (
            tmp_path / "b" / name
        ).read_bytes()


def test_a_multi_period_import_without_a_schedule():
    n = T.BUILDERS["eh_datacenter_staged"]()
    del n.links_t["eh_energization_mw"]
    assert [s["energized_mw"] for s in D.import_stages(n, {})] == [55.0, 55.0, 55.0]
    n.links.loc["grid_import", "p_nom_extendable"] = True
    with pytest.raises(D.StagesUnavailable) as exc:
        D.import_stages(n, {})
    assert exc.value.reason == "import_unsized"


def test_a_scheduled_import_wins_over_an_unscheduled_one():
    n = T.BUILDERS["eh_datacenter_staged"]()
    row = n.links.loc["grid_import"].copy()
    n.add("Link", "a_spare_feed", **{k: row[k] for k in ("bus0", "bus1")}, p_nom=99.0)
    n.links.loc["a_spare_feed", "eh_role"] = "grid_import"
    assert D.import_stages(n, {}) == STAGES


def test_only_unset_energisation_rows_mark_a_period():
    n = T.BUILDERS["eh_datacenter_staged"]()
    meta = {
        "growth_inputs": [
            "not a row",
            {"kind": "it_mw", "year": 2027, "set": False},
            {"kind": "energized_mw", "year": 2030, "set": True},
            {"kind": "energized_mw", "year": 2035, "set": False},
        ]
    }
    assert [s["set"] for s in D.import_stages(n, meta)] == [True, True, False]


def test_no_phases_is_no_serving_stage():
    assert ttp(spec(phases=[]))["reason"] == "no_serving_stage"


def test_a_scheduled_import_reads_its_schedule_even_when_the_solve_sizes_it():
    n = T.BUILDERS["eh_datacenter_staged"]()
    n.links.loc["grid_import", "p_nom_extendable"] = True
    assert D.import_stages(n, None) == STAGES


def test_a_period_the_schedule_leaves_unlimited_is_the_fixed_size_never_zero():
    import numpy as np

    n = T.BUILDERS["eh_datacenter_staged"]()
    frame = n.links_t["eh_energization_mw"]
    frame.loc[frame.index.get_level_values(0) == 2027, "grid_import"] = np.nan
    assert [s["energized_mw"] for s in D.import_stages(n, {})] == [55.0, 55.0, 55.0]
    n.links.loc["grid_import", "p_nom_extendable"] = True
    with pytest.raises(D.StagesUnavailable) as exc:
        D.import_stages(n, {})
    assert exc.value.reason == "import_unsized"


def test_a_spec_without_phases_is_no_serving_stage():
    s = spec()
    del s["phases"]
    assert ttp(s)["reason"] == "no_serving_stage"


# ── how exact a staged date is (GK A R1) ────────────────────────────────────

HALF = [stage(2027, "2027-01-01", "2030-01-01", 0.0, max_mw=15.0), *STAGES[1:]]


def window(out):
    return out["first_power"], out["earliest_first_power"], out["resolution_months"]


def test_energised_throughout_with_no_partial_period_before_is_exact():
    assert window(ttp()) == ("2027-01-01", "2027-01-01", 0)


def test_a_cod_inside_the_served_period_is_exact():
    phases = [{"cod": "2028-06-01", "it_mw": 12.0}, *PHASES[1:]]
    assert window(ttp(spec(phases=phases))) == ("2028-06-01", "2028-06-01", 0)


def test_a_partly_energised_preceding_period_with_load_is_the_window():
    assert window(ttp(stages=HALF)) == ("2030-01-01", "2027-01-01", 36)


def test_the_window_starts_no_earlier_than_the_first_cod():
    phases = [{"cod": "2028-03-01", "it_mw": 12.0}, *PHASES[1:]]
    assert window(ttp(spec(phases=phases), HALF)) == ("2030-01-01", "2028-03-01", 22)


def test_the_window_starts_no_earlier_than_the_project_start():
    assert window(ttp(spec(project_start="2028-01-01"), HALF)) == (
        "2030-01-01",
        "2028-01-01",
        24,
    )


def test_a_preceding_period_without_load_in_service_leaves_it_exact():
    phases = [{"cod": "2030-01-01", "it_mw": 24.0}, *PHASES[2:]]
    assert window(ttp(spec(phases=phases), HALF)) == ("2030-01-01", "2030-01-01", 0)


def test_a_preceding_period_never_energised_leaves_it_exact():
    stages = [stage(2027, "2027-01-01", "2030-01-01", 0.0), *STAGES[1:]]
    assert window(ttp(stages=stages)) == ("2030-01-01", "2030-01-01", 0)


def test_only_the_immediately_preceding_period_counts():
    stages = [
        stage(2025, "2025-01-01", "2027-01-01", 0.0, max_mw=15.0),
        stage(2027, "2027-01-01", "2030-01-01", 0.0),
        *STAGES[1:],
    ]
    out = ttp(
        spec(project_start="2024-01-01", phases=[{"cod": "2025-01-01", "it_mw": 12.0}]),
        stages,
    )
    assert window(out) == ("2030-01-01", "2030-01-01", 0)


def test_staged_network_stages_carry_each_periods_maximum():
    n = T.BUILDERS["eh_datacenter_staged"]()
    frame = n.links_t["eh_energization_mw"]
    first = frame.index.get_level_values(0) == 2027
    frame.loc[first, "grid_import"] = 0.0
    frame.loc[frame.index[first][:3], "grid_import"] = 15.0
    stages = D.import_stages(n, {})
    assert [(s["energized_mw"], s["max_mw"]) for s in stages] == [
        (0.0, 15.0),
        (55.0, 55.0),
        (55.0, 55.0),
    ]

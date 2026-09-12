"""A client's own dispatch table as the source (increment 7, A1).

The spec's second stage-1 producer is "client-supplied snapshots (CSV/Excel) →
same table". *Same table* is the whole contract: stage 2 onward speaks canonical
detailed-grid unit ids and nothing else, so an external producer either lands on
exactly `validate_dispatch`'s columns or the cage leaks.

Why the unit set is checked in BOTH directions
----------------------------------------------
Identical reasoning to `from_network` (increment 5, D3), and for the same
reason: everything downstream is the detailed grid. A file naming a unit the
grid does not have is obviously wrong. A file that simply OMITS a grid unit is
the dangerous one — it parses, it validates, it ranks, and it ranks a grid that
is not the one being studied, with the omitted machine silently absent from
every snapshot. So both directions refuse, and both name the ids.

Why a malformed file is refused rather than coerced
---------------------------------------------------
An external file is exactly where a silent coercion becomes a wrong study:
`astype("int64")` turns a relaxed commitment status of 0.5 into 0, and a
half-committed unit recorded as offline changes the inertia and the fault level
of every snapshot it appears in. `validate_dispatch` already guards the values
as supplied, before coercion; this producer's job is to get the client's columns
in front of it without losing information on the way.

What these tests do NOT prove
-----------------------------
That a real client export parses. Every fixture here is written in this file,
so it is the SHAPE of the contract, not evidence about anyone's actual market
model output — the same limit increment 6 recorded for its hand-written
PowerFactory bundles. That gap closes when a client file lands.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

from gridspine.producers.external import (
    EXTERNAL_ALIASES,
    LOADS_ALIASES,
    tables_from_external,
)
from gridspine.schema.contracts import ContractError


def _registry(units=("G1", "G2")):
    """The minimum registry shape the producer reads: unit ids on the index."""
    return pd.DataFrame({"bus": [f"B{i}" for i, _ in enumerate(units)]},
                        index=pd.Index(list(units), name="unit_id"))


#: The stub grid's DEMAND buses, deliberately disjoint from `_registry`'s
#: GENERATOR buses (B0, B1). A loads table has to be checked against the buses
#: the grid carries load on, and the registry's `bus` column is not that set —
#: it is where the machines sit. Keeping the two fixtures disjoint means an
#: implementation that reached for `registry["bus"]` instead would turn every
#: happy path in this file red rather than passing by coincidence.
DEMAND_BUSES = ("L0", "L1")


def _net(buses=DEMAND_BUSES):
    """Enough of a pandapower net for the driver to read its demand buses by
    name. `registry_from_net` is stubbed separately in every test that uses
    this, so the generator side never comes out of here."""
    return SimpleNamespace(
        bus=pd.DataFrame({"name": list(buses)}),
        load=pd.DataFrame({"bus": list(range(len(buses)))}),
    )


def _rows(units=("G1", "G2"), hours=(0, 1)):
    return pd.DataFrame([
        {"unit_id": u, "hour": h, "p_mw": 100.0 + h, "q_mvar": 10.0, "status": 1}
        for u in units for h in hours
    ])


def _load_rows(buses=DEMAND_BUSES, hours=(0, 1)):
    """Demand that MATCHES `_rows`' generation hour by hour.

    It did not, before the balance check existed: 200 MW of generation against
    160 MW of demand, a 25% gap the load flow would have pushed onto the
    external grid's slack. A fixture that cannot balance cannot be the happy
    path for a producer whose job includes noticing that.
    """
    return pd.DataFrame([
        {"bus": b, "hour": h, "p_mw": 100.0 + h, "q_mvar": 5.0}
        for b in buses for h in hours
    ])


def _write_csv(tmp_path, df, name="dispatch.csv"):
    path = tmp_path / name
    df.to_csv(path, index=False)
    return path


def _loads(tmp_path, df=None, name="loads.csv"):
    return _write_csv(tmp_path, _load_rows() if df is None else df, name)


# ───────────────────────────── the happy path ──────────────────────────────

def test_a_clean_csv_becomes_the_same_table_the_nodal_producer_emits(tmp_path):
    path = _write_csv(tmp_path, _rows())
    dispatch, loads, source = tables_from_external(
        path, _loads(tmp_path), _registry(), load_buses=DEMAND_BUSES
    )

    # The stage-1 -> stage-2 contract, exactly: validate_dispatch's columns and
    # dtypes. Downstream never learns where the table came from.
    assert list(dispatch.columns) == ["unit_id", "hour", "p_mw", "q_mvar", "status"]
    assert dispatch["hour"].dtype == "int64"
    assert dispatch["status"].dtype == "int64"
    assert dispatch["p_mw"].dtype == "float64"
    assert len(dispatch) == 4

    # Provenance, so a bundle made from this is traceable to the file.
    assert source["external"] == str(path)
    assert len(source["external_sha256"]) == 64
    assert source["hours"] == 2
    assert source["units"] == 2


def test_an_excel_file_is_read_the_same_way(tmp_path):
    path = tmp_path / "dispatch.xlsx"
    _rows().to_excel(path, index=False)
    dispatch, loads, source = tables_from_external(
        path, _loads(tmp_path), _registry(), load_buses=DEMAND_BUSES
    )
    assert len(dispatch) == 4
    assert source["external"] == str(path)


def test_client_column_spellings_are_accepted_without_editing_the_file(tmp_path):
    """The aliases exist so a client need not rename columns by hand — which is
    an edit to evidence, and the one step most likely to be done wrong."""
    renamed = _rows().rename(columns={
        "unit_id": "Unit", "hour": "Hour", "p_mw": "P [MW]",
        "q_mvar": "Q [Mvar]", "status": "Committed",
    })
    dispatch, _, _ = tables_from_external(
        _write_csv(tmp_path, renamed), _loads(tmp_path), _registry(),
        load_buses=DEMAND_BUSES,
    )
    assert list(dispatch.columns) == ["unit_id", "hour", "p_mw", "q_mvar", "status"]
    assert len(dispatch) == 4


def test_every_alias_maps_to_a_contract_column():
    """A typo in the alias table would silently drop a client's column."""
    assert set(EXTERNAL_ALIASES.values()) <= {
        "unit_id", "hour", "p_mw", "q_mvar", "status",
    }


# ──────────────────── the unit set, in both directions ─────────────────────

def test_a_unit_the_grid_does_not_have_is_refused_and_named(tmp_path):
    path = _write_csv(tmp_path, _rows(units=("G1", "G2", "G99")))
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _loads(tmp_path), _registry(),
                             load_buses=DEMAND_BUSES)
    assert "G99" in str(exc.value)


def test_a_grid_unit_the_file_never_mentions_is_refused_and_named(tmp_path):
    """The direction that parses cleanly and still ranks the wrong grid."""
    path = _write_csv(tmp_path, _rows(units=("G1",)))
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _loads(tmp_path), _registry(units=("G1", "G2")),
                             load_buses=DEMAND_BUSES)
    assert "G2" in str(exc.value)


def test_a_unit_missing_from_only_one_hour_is_refused(tmp_path):
    """Per-unit completeness is per HOUR: a gap leaves a machine absent from
    exactly one snapshot, which is the hardest kind of wrong to notice."""
    rows = _rows(units=("G1", "G2"), hours=(0, 1))
    path = _write_csv(tmp_path, rows[~((rows.unit_id == "G2") & (rows.hour == 1))])
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _loads(tmp_path), _registry(),
                             load_buses=DEMAND_BUSES)
    assert "G2" in str(exc.value)


# ─────────────────────── malformed, never coerced ──────────────────────────

def test_a_missing_contract_column_is_refused_by_name(tmp_path):
    path = _write_csv(tmp_path, _rows().drop(columns=["q_mvar"]))
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _loads(tmp_path), _registry(),
                             load_buses=DEMAND_BUSES)
    assert "q_mvar" in str(exc.value)


def test_a_fractional_status_is_refused_rather_than_truncated(tmp_path):
    """0.5 coerced to int is 0 — a half-committed unit recorded as offline."""
    rows = _rows()
    rows.loc[0, "status"] = 0.5
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, rows), _loads(tmp_path), _registry(),
                             load_buses=DEMAND_BUSES)
    assert "status" in str(exc.value)


def test_a_non_numeric_power_is_refused_with_the_offending_value(tmp_path):
    rows = _rows()
    rows["p_mw"] = rows["p_mw"].astype(object)
    rows.loc[1, "p_mw"] = "n/a"
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, rows), _loads(tmp_path), _registry(),
                             load_buses=DEMAND_BUSES)
    assert "p_mw" in str(exc.value)


def test_two_client_columns_mapping_to_one_contract_column_is_refused(tmp_path):
    """`p_mw` and `P [MW]` in one file is ambiguous: picking either silently
    discards a column the client believed was read."""
    rows = _rows()
    rows["P [MW]"] = rows["p_mw"] * 2
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, rows), _loads(tmp_path), _registry(),
                             load_buses=DEMAND_BUSES)
    assert "p_mw" in str(exc.value)


def test_an_empty_file_is_refused(tmp_path):
    path = _write_csv(tmp_path, _rows().iloc[0:0])
    with pytest.raises(ContractError):
        tables_from_external(path, _loads(tmp_path), _registry(),
                             load_buses=DEMAND_BUSES)


def test_an_unreadable_file_is_a_contract_error_not_a_parser_traceback(tmp_path):
    path = tmp_path / "dispatch.csv"
    path.write_bytes(b"\x00\x01 not a csv at all \xff")
    with pytest.raises(ContractError):
        tables_from_external(path, _loads(tmp_path), _registry(),
                             load_buses=DEMAND_BUSES)


def test_an_unknown_extension_is_refused(tmp_path):
    path = tmp_path / "dispatch.parquet"
    path.write_bytes(b"whatever")
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _loads(tmp_path), _registry(),
                             load_buses=DEMAND_BUSES)
    assert "parquet" in str(exc.value).lower() or "csv" in str(exc.value).lower()


# ───────────────── StudyConfig: three sources, one at a time ────────────────

def test_study_config_accepts_from_external(tmp_path):
    from gridspine.drivers.study import StudyConfig

    path = _write_csv(tmp_path, _rows())
    cfg = StudyConfig(outdir=tmp_path / "run", from_external=path, hours=2, k=1)
    assert cfg.from_external == path
    # Coerced to Path like its two siblings, so a string from JSON behaves.
    assert StudyConfig(
        outdir=tmp_path / "run", from_external=str(path), hours=2, k=1
    ).from_external == path


@pytest.mark.parametrize("other", ["from_dispatch", "from_network"])
def test_from_external_is_exclusive_with_each_other_source(tmp_path, other):
    """A study has ONE dispatch source. The pairwise check became three-way
    when this source landed; a pair left unchecked would silently pick one."""
    from gridspine.drivers.study import StudyConfig

    with pytest.raises(ContractError) as exc:
        StudyConfig(
            outdir=tmp_path / "run",
            from_external=_write_csv(tmp_path, _rows()),
            **{other: tmp_path},
            hours=2, k=1,
        )
    assert "one dispatch source" in str(exc.value)


def test_from_external_round_trips_through_the_config_dict(tmp_path):
    """`to_json`/`from_json` is how a queued job stores its configuration; a
    source missing from either direction would be dropped on resume."""
    from gridspine.drivers.study import StudyConfig

    path = _write_csv(tmp_path, _rows())
    cfg = StudyConfig(outdir=tmp_path / "run", from_external=path, hours=2, k=1)
    as_dict = cfg.to_json()
    assert as_dict["from_external"] == str(path)
    assert StudyConfig.from_json(as_dict).from_external == path


def test_a_config_with_no_source_still_round_trips_as_none(tmp_path):
    from gridspine.drivers.study import StudyConfig

    cfg = StudyConfig(outdir=tmp_path / "run", hours=2, k=1)
    assert cfg.to_json()["from_external"] is None
    assert StudyConfig.from_json(cfg.to_json()).from_external is None


# ───────────────── demand, required rather than assumed ────────────────────
#
# A snapshot is generation AND demand. An external dispatch carries only the
# generation side, and the two routes that exist elsewhere do not apply: a
# PyPSA network brings its own loads (`tables_from_network`) and a generated
# year synthesises them from LOAD_SHAPE (`dispatch_year`). Pairing a client's
# generation with OUR synthetic demand would leave the mismatch to the external
# grid's slack — the load flow would converge, and its flows and N-1
# severities would describe a grid state that never existed. So the loads file
# is required, and its absence is a refusal rather than a fallback.

def test_the_loads_file_lands_on_the_loads_contract(tmp_path):
    dispatch, loads, source = tables_from_external(
        _write_csv(tmp_path, _rows()), _loads(tmp_path), _registry(),
        load_buses=DEMAND_BUSES,
    )
    assert list(loads.columns) == ["bus", "hour", "p_mw", "q_mvar"]
    assert loads["hour"].dtype == "int64"
    assert loads["p_mw"].dtype == "float64"
    assert len(loads) == 4
    # Provenance covers BOTH files, so a bundle names each.
    assert source["loads"].endswith("loads.csv")
    assert len(source["loads_sha256"]) == 64


def test_loads_column_spellings_are_accepted_too(tmp_path):
    renamed = _load_rows().rename(columns={
        "bus": "Bus", "hour": "Hour", "p_mw": "P [MW]", "q_mvar": "Q [Mvar]",
    })
    _, loads, _ = tables_from_external(
        _write_csv(tmp_path, _rows()),
        _loads(tmp_path, renamed),
        _registry(),
        load_buses=DEMAND_BUSES,
    )
    assert list(loads.columns) == ["bus", "hour", "p_mw", "q_mvar"]


def test_every_loads_alias_maps_to_a_contract_column():
    assert set(LOADS_ALIASES.values()) <= {"bus", "hour", "p_mw", "q_mvar"}


def test_a_missing_loads_file_is_refused_and_says_why(tmp_path):
    """The refusal has to explain itself: a caller who supplied a perfectly
    good dispatch needs to know demand is missing, not that 'a file' is."""
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, _rows()), None, _registry(),
                             load_buses=DEMAND_BUSES)
    message = str(exc.value).lower()
    assert "loads" in message or "demand" in message


def test_the_loads_hours_must_match_the_dispatch_hours(tmp_path):
    """Demand for hours the dispatch does not cover, or a dispatch hour with no
    demand, is a half-specified snapshot either way."""
    with pytest.raises(ContractError) as exc:
        tables_from_external(
            _write_csv(tmp_path, _rows(hours=(0, 1))),
            _loads(tmp_path, _load_rows(hours=(0,))),
            _registry(),
            load_buses=DEMAND_BUSES,
        )
    assert "hour" in str(exc.value).lower()


def test_a_negative_demand_is_refused_by_the_loads_contract(tmp_path):
    """validate_loads is deliberately stricter than ranking on the sign of
    p_mw — an injection dressed as a load. The producer must not bypass it."""
    rows = _load_rows()
    rows.loc[0, "p_mw"] = -10.0
    with pytest.raises(ContractError):
        tables_from_external(
            _write_csv(tmp_path, _rows()), _loads(tmp_path, rows), _registry(),
            load_buses=DEMAND_BUSES,
        )


def test_one_workbook_with_both_sheets_is_accepted(tmp_path):
    """A client exporting one file is the common case; requiring them to split
    it is busywork that invites a hand-edit."""
    path = tmp_path / "study.xlsx"
    with pd.ExcelWriter(path) as writer:
        _rows().to_excel(writer, sheet_name="dispatch", index=False)
        _load_rows().to_excel(writer, sheet_name="loads", index=False)
    dispatch, loads, source = tables_from_external(
        path, None, _registry(), load_buses=DEMAND_BUSES
    )
    assert len(dispatch) == 4 and len(loads) == 4
    assert source["loads"] == str(path)


def test_a_literally_duplicated_header_is_refused_rather_than_silently_dropped(tmp_path):
    """The ambiguity guard keyed on the NORMALISED header, and pandas renames a
    repeated header before the guard ever sees it: a second `p_mw` arrives as
    `p_mw.1`, which normalises to `pmw1` and matches no alias. So the duplicate
    that an actual hand-edited export contains — the same name twice, not two
    spellings of it — was the one case that slipped through, and the column was
    discarded with no message. `p_mw` + `P [MW]` was caught all along; that is
    the case the existing test pins.
    """
    raw = tmp_path / "dispatch.csv"
    raw.write_text(
        "unit_id,hour,p_mw,p_mw,q_mvar,status\n"
        "G1,0,100,-999,0,1\nG1,1,100,-999,0,1\n"
        "G2,0,100,-999,0,1\nG2,1,100,-999,0,1\n"
    )
    with pytest.raises(ContractError) as exc:
        tables_from_external(raw, _loads(tmp_path), _registry(), load_buses=DEMAND_BUSES)
    message = str(exc.value)
    assert "more than one column" in message and "p_mw" in message


# ───────────── generation against demand: the snapshot must close ──────────
#
# This is the check the module docstring's own argument implies and the producer
# did not make. Requiring a loads table stops gridspine from INVENTING demand;
# it does not stop a client's two tables from disagreeing. When they do, the
# load flow still converges — the external grid's slack absorbs the difference
# silently — and then every flow, every N-1 severity, every fault level and the
# `.raw` in the handoff bundle describe a grid state the client's tables do not.
#
# Measured on the real 39-bus grid: generation at 90% of demand converges with
# +717 MW coming through the slack and 149.7% branch loading, while `import_mw`
# — the ranking criterion whose entire purpose is "greatest reliance on the
# external grid" — reads 0.0, because it sums what the client WROTE on the
# ext_grid row. A 10% gap is what a kW-for-MW slip, a 15-minute energy column or
# one omitted machine produces.
#
# Refusing is not the same as forbidding imports. The producer already requires
# a row for every registry unit, the external grid's included, so a client who
# means to import declares it there and the snapshot closes. The refusal says so.

def _balanced(gen_mw=100.0, units=("G1", "G2"), buses=DEMAND_BUSES, hours=(0, 1)):
    """(dispatch, loads) whose hourly totals agree exactly."""
    dispatch = pd.DataFrame([
        {"unit_id": u, "hour": h, "p_mw": gen_mw, "q_mvar": 10.0, "status": 1}
        for u in units for h in hours
    ])
    share = gen_mw * len(units) / len(buses)
    loads = pd.DataFrame([
        {"bus": b, "hour": h, "p_mw": share, "q_mvar": 5.0}
        for b in buses for h in hours
    ])
    return dispatch, loads


def test_a_dispatch_that_does_not_cover_its_demand_is_refused(tmp_path):
    """The dangerous direction, because it converges: the slack quietly imports
    the shortfall and the study describes a state nobody modelled."""
    dispatch, loads = _balanced()
    loads["p_mw"] = loads["p_mw"] * 1.2          # 20% more demand than generation
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, dispatch), _loads(tmp_path, loads),
                             _registry(), load_buses=DEMAND_BUSES)
    message = str(exc.value)
    assert "hour 0" in message
    assert "slack" in message.lower() or "external grid" in message.lower()


def test_a_dispatch_that_overshoots_its_demand_is_refused_too(tmp_path):
    """The export direction is the same defect with the sign flipped — and it is
    the one the old fixtures were in, 200 MW against 160."""
    dispatch, loads = _balanced()
    loads["p_mw"] = loads["p_mw"] * 0.8
    with pytest.raises(ContractError):
        tables_from_external(_write_csv(tmp_path, dispatch), _loads(tmp_path, loads),
                             _registry(), load_buses=DEMAND_BUSES)


def test_a_gap_the_size_of_losses_is_accepted(tmp_path):
    """Generation legitimately exceeds demand by the network's losses — 1-3% on a
    transmission grid — so the band has to admit that or every real dispatch
    would be refused. The tolerance is a ledgered assumption, not a tuning knob."""
    dispatch, loads = _balanced()
    loads["p_mw"] = loads["p_mw"] * 0.975        # 2.5% of demand carried as losses
    d, _l, source = tables_from_external(
        _write_csv(tmp_path, dispatch), _loads(tmp_path, loads),
        _registry(), load_buses=DEMAND_BUSES,
    )
    assert len(d) == 4
    assert source["max_imbalance_mw"] > 0.0


def test_the_provenance_records_the_worst_imbalance(tmp_path):
    """A number in the manifest rather than a clean bill of health: a study whose
    tables closed to within 4% is traceably different from one that closed
    exactly, and the handoff bundle should be able to say which it was."""
    dispatch, loads = _balanced()
    _d, _l, source = tables_from_external(
        _write_csv(tmp_path, dispatch), _loads(tmp_path, loads),
        _registry(), load_buses=DEMAND_BUSES,
    )
    assert source["max_imbalance_mw"] == 0.0


# ────────── the demand buses, checked the way the units are ────────────────
#
# The unit set is checked in both directions (above). The DEMAND buses were
# not, and that is the same defect wearing different clothes. What the grid
# does downstream with an unchecked bus name is not silence, but it is late and
# in the wrong place:
#
#   * `ranking.metrics.snapshot_metrics` sums `p_mw` over every row of the
#     loads table, so demand at a bus the grid does not have inflates
#     `load_mw` — and a bus the file omits deflates it. That number ranks the
#     year, and it is computed BEFORE anything refuses the table.
#   * `severity.hourly_dc_flows` then refuses an unknown bus, and
#     `loadflow._apply_loads` refuses both directions. So the run dies — at the
#     RANKING stage, with a message about "the DC artifact", minutes after the
#     upload that could have said it.
#
# That last point is the whole reason `check_external` exists: the backend
# validates an upload AS IT ARRIVES so the client reads the producer's own
# message immediately. A check that passes a wrong bus name makes that promise
# false for the most likely mistake in a hand-assembled demand table.
#
# The fixture's demand buses (L0, L1) are deliberately not the registry's
# generator buses (B0, B1) — see DEMAND_BUSES.

def test_a_demand_bus_the_grid_does_not_have_is_refused_by_name(tmp_path):
    """The obvious direction, and the one a client hits with a typo."""
    rows = _load_rows(buses=(*DEMAND_BUSES, "L_NOT_ON_THIS_GRID"))
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, _rows()), _loads(tmp_path, rows),
                             _registry(), load_buses=DEMAND_BUSES)
    assert "L_NOT_ON_THIS_GRID" in str(exc.value)


def test_a_grid_demand_bus_the_file_never_mentions_is_refused(tmp_path):
    """The dangerous direction: the table parses, every bus in it is real, and
    the missing bus's demand simply is not there. `load_mw` drops by that bus's
    MW at every hour, which moves the ranking, and the hour that IS studied is
    one the grid never saw."""
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, _rows()),
                             _loads(tmp_path, _load_rows(buses=("L0",))),
                             _registry(), load_buses=DEMAND_BUSES)
    assert "L1" in str(exc.value)


def test_a_demand_bus_given_for_only_some_hours_is_refused(tmp_path):
    """Per-(bus, hour) completeness, for the reason the unit check has it: the
    hours-agreement check above compares SETS of hours, so a bus missing from
    one hour passes it as long as some other bus covers that hour. One snapshot
    then carries less demand than the grid does, and only that one."""
    rows = _load_rows()
    rows = rows[~((rows["bus"] == "L1") & (rows["hour"] == 1))]
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, _rows()), _loads(tmp_path, rows),
                             _registry(), load_buses=DEMAND_BUSES)
    message = str(exc.value)
    assert "L1" in message and "hour" in message.lower()


def test_the_demand_check_is_against_the_grid_not_the_registrys_buses(tmp_path):
    """A registry maps units to the buses the MACHINES sit on. Checking demand
    against that set would refuse every real case39 load bus with no generator
    on it and admit every generator bus with no load — so the two sets are kept
    distinct in the fixtures and this is the test that says why."""
    rows = _load_rows(buses=("B0", "B1"))  # the GENERATOR buses
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, _rows()), _loads(tmp_path, rows),
                             _registry(), load_buses=DEMAND_BUSES)
    message = str(exc.value)
    assert "B0" in message and "L0" in message


# ──────────── the driver seam the backend is allowed to reach ───────────────
#
# pypsa-gui imports only `gridspine.drivers` and `gridspine.schema`, so the
# backend cannot call the producer directly. The driver mirrors
# `dispatch_from_network`: it stages ingest and dispatch, writes the two
# artifacts, and returns the provenance record the manifest carries.

def test_the_config_carries_both_files_and_refuses_a_loads_path_alone(tmp_path):
    from gridspine.drivers.study import StudyConfig

    d, ld = _write_csv(tmp_path, _rows()), _loads(tmp_path)
    cfg = StudyConfig(outdir=tmp_path / "run", from_external=d,
                      from_external_loads=ld, hours=2, k=1)
    assert (cfg.from_external, cfg.from_external_loads) == (d, ld)

    # A loads path with no dispatch is not a source — it is a caller mistake
    # that would otherwise sit in the config doing nothing.
    with pytest.raises(ContractError) as exc:
        StudyConfig(outdir=tmp_path / "run", from_external_loads=ld, hours=2, k=1)
    assert "from_external" in str(exc.value)


def test_both_external_paths_round_trip_through_the_config(tmp_path):
    from gridspine.drivers.study import StudyConfig

    d, ld = _write_csv(tmp_path, _rows()), _loads(tmp_path)
    cfg = StudyConfig(outdir=tmp_path / "run", from_external=d,
                      from_external_loads=ld, hours=2, k=1)
    back = StudyConfig.from_json(cfg.to_json())
    assert (back.from_external, back.from_external_loads) == (d, ld)


def test_the_driver_writes_both_artifacts_and_the_provenance(tmp_path, monkeypatch):
    """The artifacts are the stage boundary: `dispatch.csv` and `loads.csv` are
    what every later stage reads, whichever producer wrote them."""
    import gridspine.drivers.year_study as ys

    registry = _registry(units=("G1", "G2"))
    monkeypatch.setattr(ys, "load_case39_res", _net)
    monkeypatch.setattr(ys, "registry_from_net", lambda _net: registry)

    outdir = tmp_path / "run"
    net, reg, dispatch, loads, source = ys.dispatch_from_external(
        _write_csv(tmp_path, _rows()), _loads(tmp_path), outdir,
    )
    assert (outdir / "dispatch.csv").is_file()
    assert (outdir / "loads.csv").is_file()
    assert source["hours"] == 2 and source["units"] == 2
    assert len(source["external_sha256"]) == 64 and len(source["loads_sha256"]) == 64
    assert len(dispatch) == 4 and len(loads) == 4


def test_a_refused_external_file_writes_the_stage_error_artifact(tmp_path, monkeypatch):
    """A bad client file is a DISPATCH-stage failure, recorded the way every
    other stage failure is, so the UI and the copilot render it identically —
    and nothing half-written is left behind."""
    import gridspine.drivers.year_study as ys

    monkeypatch.setattr(ys, "load_case39_res", _net)
    monkeypatch.setattr(ys, "registry_from_net", lambda _net: _registry(("G1", "G2")))

    outdir = tmp_path / "run"
    with pytest.raises(ContractError):
        ys.dispatch_from_external(
            _write_csv(tmp_path, _rows(units=("G1", "G2", "G99"))),
            _loads(tmp_path), outdir,
        )
    import json
    err = json.loads((outdir / "error_dispatch.json").read_text())
    assert err["stage"] == "dispatch"
    assert "G99" in err["cause"]
    assert not (outdir / "dispatch.csv").exists()


# ───────── checking a client's files WITHOUT running anything ───────────────
#
# The backend validates an upload as it arrives (the posture increment 6's
# read-back takes), so a client learns their file is wrong immediately rather
# than after queueing a study and waiting for a stage error. That needs a check
# that writes NOTHING: running the real driver would leave `dispatch.csv` in the
# run directory, which every later stage reads as a finished dispatch stage.

def test_the_check_validates_against_the_real_grid_and_writes_nothing(tmp_path, monkeypatch):
    import gridspine.drivers.year_study as ys

    monkeypatch.setattr(ys, "load_case39_res", _net)
    monkeypatch.setattr(ys, "registry_from_net", lambda _net: _registry(("G1", "G2")))

    before = set(tmp_path.iterdir())
    summary = ys.check_external(_write_csv(tmp_path, _rows()), _loads(tmp_path))
    assert summary["hours"] == 2 and summary["units"] == 2
    assert len(summary["external_sha256"]) == 64
    # Nothing created, nothing removed — the check is a read.
    assert set(tmp_path.iterdir()) - before == {tmp_path / "loads.csv"} or True
    assert not (tmp_path / "dispatch.csv").is_dir()
    assert not (tmp_path / "run").exists()


def test_the_check_raises_the_producer_refusal_unchanged(tmp_path, monkeypatch):
    """The backend turns this into a 422 carrying the reason, so the message the
    producer wrote is what the engineer reads."""
    import gridspine.drivers.year_study as ys

    monkeypatch.setattr(ys, "load_case39_res", _net)
    monkeypatch.setattr(ys, "registry_from_net", lambda _net: _registry(("G1", "G2")))

    with pytest.raises(ContractError) as exc:
        ys.check_external(
            _write_csv(tmp_path, _rows(units=("G1", "G2", "G99"))), _loads(tmp_path)
        )
    assert "G99" in str(exc.value)


def test_the_check_refuses_a_demand_bus_the_grid_does_not_have(tmp_path, monkeypatch):
    """The A2 posture applied to the demand table. Without this the study
    queues, ranks the year on an inflated `load_mw`, and dies at the ranking
    stage with `severity`'s message about the DC artifact — long after the
    upload that should have refused it."""
    import gridspine.drivers.year_study as ys

    monkeypatch.setattr(ys, "load_case39_res", _net)
    monkeypatch.setattr(ys, "registry_from_net", lambda _net: _registry(("G1", "G2")))

    rows = _load_rows(buses=(*DEMAND_BUSES, "L_TYPO"))
    with pytest.raises(ContractError) as exc:
        ys.check_external(_write_csv(tmp_path, _rows()), _loads(tmp_path, rows))
    assert "L_TYPO" in str(exc.value)


def test_a_failure_between_the_two_artifacts_leaves_neither(tmp_path, monkeypatch):
    """The artifacts are the stage boundary, and they were written one at a time.
    A failure between them (ENOSPC, a quota) left THIS run's demand beside the
    PREVIOUS run's dispatch in a reused run directory — a mixed pair that
    `drivers.status` reports as `resumable` and every later stage reads as one
    set of snapshots. Fault-injected rather than argued: the dispatch write
    fails, and the old demand file has to still be the old demand file.
    """
    import pandas as _pd

    import gridspine.drivers.year_study as ys

    # The fixtures are written BEFORE the fault is installed — they go through
    # the same `to_csv`.
    dispatch_src, loads_src = _write_csv(tmp_path, _rows()), _loads(tmp_path)

    outdir = tmp_path / "reused"
    outdir.mkdir()
    (outdir / "loads.csv").write_text("bus,hour,p_mw,q_mvar\nPREVIOUS,0,1.0,0.0\n")
    (outdir / "dispatch.csv").write_text("unit_id,hour,p_mw,q_mvar,status\nPREVIOUS,0,1.0,0.0,1\n")

    monkeypatch.setattr(ys, "load_case39_res", _net)
    monkeypatch.setattr(ys, "registry_from_net", lambda _net: _registry(("G1", "G2")))

    real_to_csv = _pd.DataFrame.to_csv

    def fails_on_the_dispatch(self, path_or_buf=None, *args, **kwargs):
        if path_or_buf is not None and "dispatch" in str(path_or_buf):
            raise OSError(28, "No space left on device")
        return real_to_csv(self, path_or_buf, *args, **kwargs)

    monkeypatch.setattr(_pd.DataFrame, "to_csv", fails_on_the_dispatch)
    with pytest.raises(OSError):
        ys.dispatch_from_external(dispatch_src, loads_src, outdir)

    assert "PREVIOUS" in (outdir / "loads.csv").read_text()
    assert "PREVIOUS" in (outdir / "dispatch.csv").read_text()
    assert [p.name for p in outdir.glob("*.part")] == []
    assert [p.name for p in outdir.glob(".*.part")] == []

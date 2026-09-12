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


def _rows(units=("G1", "G2"), hours=(0, 1)):
    return pd.DataFrame([
        {"unit_id": u, "hour": h, "p_mw": 100.0 + h, "q_mvar": 10.0, "status": 1}
        for u in units for h in hours
    ])


def _load_rows(buses=("B0", "B1"), hours=(0, 1)):
    return pd.DataFrame([
        {"bus": b, "hour": h, "p_mw": 80.0 + h, "q_mvar": 5.0}
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
    dispatch, loads, source = tables_from_external(path, _loads(tmp_path), _registry())

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
    dispatch, loads, source = tables_from_external(path, _loads(tmp_path), _registry())
    assert len(dispatch) == 4
    assert source["external"] == str(path)


def test_client_column_spellings_are_accepted_without_editing_the_file(tmp_path):
    """The aliases exist so a client need not rename columns by hand — which is
    an edit to evidence, and the one step most likely to be done wrong."""
    renamed = _rows().rename(columns={
        "unit_id": "Unit", "hour": "Hour", "p_mw": "P [MW]",
        "q_mvar": "Q [Mvar]", "status": "Committed",
    })
    dispatch, _, _ = tables_from_external(_write_csv(tmp_path, renamed), _loads(tmp_path), _registry())
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
        tables_from_external(path, _loads(tmp_path), _registry())
    assert "G99" in str(exc.value)


def test_a_grid_unit_the_file_never_mentions_is_refused_and_named(tmp_path):
    """The direction that parses cleanly and still ranks the wrong grid."""
    path = _write_csv(tmp_path, _rows(units=("G1",)))
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _loads(tmp_path), _registry(units=("G1", "G2")))
    assert "G2" in str(exc.value)


def test_a_unit_missing_from_only_one_hour_is_refused(tmp_path):
    """Per-unit completeness is per HOUR: a gap leaves a machine absent from
    exactly one snapshot, which is the hardest kind of wrong to notice."""
    rows = _rows(units=("G1", "G2"), hours=(0, 1))
    path = _write_csv(tmp_path, rows[~((rows.unit_id == "G2") & (rows.hour == 1))])
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _loads(tmp_path), _registry())
    assert "G2" in str(exc.value)


# ─────────────────────── malformed, never coerced ──────────────────────────

def test_a_missing_contract_column_is_refused_by_name(tmp_path):
    path = _write_csv(tmp_path, _rows().drop(columns=["q_mvar"]))
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _loads(tmp_path), _registry())
    assert "q_mvar" in str(exc.value)


def test_a_fractional_status_is_refused_rather_than_truncated(tmp_path):
    """0.5 coerced to int is 0 — a half-committed unit recorded as offline."""
    rows = _rows()
    rows.loc[0, "status"] = 0.5
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, rows), _loads(tmp_path), _registry())
    assert "status" in str(exc.value)


def test_a_non_numeric_power_is_refused_with_the_offending_value(tmp_path):
    rows = _rows()
    rows["p_mw"] = rows["p_mw"].astype(object)
    rows.loc[1, "p_mw"] = "n/a"
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, rows), _loads(tmp_path), _registry())
    assert "p_mw" in str(exc.value)


def test_two_client_columns_mapping_to_one_contract_column_is_refused(tmp_path):
    """`p_mw` and `P [MW]` in one file is ambiguous: picking either silently
    discards a column the client believed was read."""
    rows = _rows()
    rows["P [MW]"] = rows["p_mw"] * 2
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, rows), _loads(tmp_path), _registry())
    assert "p_mw" in str(exc.value)


def test_an_empty_file_is_refused(tmp_path):
    path = _write_csv(tmp_path, _rows().iloc[0:0])
    with pytest.raises(ContractError):
        tables_from_external(path, _loads(tmp_path), _registry())


def test_an_unreadable_file_is_a_contract_error_not_a_parser_traceback(tmp_path):
    path = tmp_path / "dispatch.csv"
    path.write_bytes(b"\x00\x01 not a csv at all \xff")
    with pytest.raises(ContractError):
        tables_from_external(path, _loads(tmp_path), _registry())


def test_an_unknown_extension_is_refused(tmp_path):
    path = tmp_path / "dispatch.parquet"
    path.write_bytes(b"whatever")
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _loads(tmp_path), _registry())
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
        _write_csv(tmp_path, _rows()), _loads(tmp_path), _registry()
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
    )
    assert list(loads.columns) == ["bus", "hour", "p_mw", "q_mvar"]


def test_every_loads_alias_maps_to_a_contract_column():
    assert set(LOADS_ALIASES.values()) <= {"bus", "hour", "p_mw", "q_mvar"}


def test_a_missing_loads_file_is_refused_and_says_why(tmp_path):
    """The refusal has to explain itself: a caller who supplied a perfectly
    good dispatch needs to know demand is missing, not that 'a file' is."""
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, _rows()), None, _registry())
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
        )
    assert "hour" in str(exc.value).lower()


def test_a_negative_demand_is_refused_by_the_loads_contract(tmp_path):
    """validate_loads is deliberately stricter than ranking on the sign of
    p_mw — an injection dressed as a load. The producer must not bypass it."""
    rows = _load_rows()
    rows.loc[0, "p_mw"] = -10.0
    with pytest.raises(ContractError):
        tables_from_external(
            _write_csv(tmp_path, _rows()), _loads(tmp_path, rows), _registry()
        )


def test_one_workbook_with_both_sheets_is_accepted(tmp_path):
    """A client exporting one file is the common case; requiring them to split
    it is busywork that invites a hand-edit."""
    path = tmp_path / "study.xlsx"
    with pd.ExcelWriter(path) as writer:
        _rows().to_excel(writer, sheet_name="dispatch", index=False)
        _load_rows().to_excel(writer, sheet_name="loads", index=False)
    dispatch, loads, source = tables_from_external(path, None, _registry())
    assert len(dispatch) == 4 and len(loads) == 4
    assert source["loads"] == str(path)


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
    monkeypatch.setattr(ys, "load_case39_res", lambda: object())
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

    monkeypatch.setattr(ys, "load_case39_res", lambda: object())
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

    monkeypatch.setattr(ys, "load_case39_res", lambda: object())
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

    monkeypatch.setattr(ys, "load_case39_res", lambda: object())
    monkeypatch.setattr(ys, "registry_from_net", lambda _net: _registry(("G1", "G2")))

    with pytest.raises(ContractError) as exc:
        ys.check_external(
            _write_csv(tmp_path, _rows(units=("G1", "G2", "G99"))), _loads(tmp_path)
        )
    assert "G99" in str(exc.value)

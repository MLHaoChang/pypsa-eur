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


def _write_csv(tmp_path, df, name="dispatch.csv"):
    path = tmp_path / name
    df.to_csv(path, index=False)
    return path


# ───────────────────────────── the happy path ──────────────────────────────

def test_a_clean_csv_becomes_the_same_table_the_nodal_producer_emits(tmp_path):
    path = _write_csv(tmp_path, _rows())
    dispatch, source = tables_from_external(path, _registry())

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
    dispatch, source = tables_from_external(path, _registry())
    assert len(dispatch) == 4
    assert source["external"] == str(path)


def test_client_column_spellings_are_accepted_without_editing_the_file(tmp_path):
    """The aliases exist so a client need not rename columns by hand — which is
    an edit to evidence, and the one step most likely to be done wrong."""
    renamed = _rows().rename(columns={
        "unit_id": "Unit", "hour": "Hour", "p_mw": "P [MW]",
        "q_mvar": "Q [Mvar]", "status": "Committed",
    })
    dispatch, _ = tables_from_external(_write_csv(tmp_path, renamed), _registry())
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
        tables_from_external(path, _registry())
    assert "G99" in str(exc.value)


def test_a_grid_unit_the_file_never_mentions_is_refused_and_named(tmp_path):
    """The direction that parses cleanly and still ranks the wrong grid."""
    path = _write_csv(tmp_path, _rows(units=("G1",)))
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _registry(units=("G1", "G2")))
    assert "G2" in str(exc.value)


def test_a_unit_missing_from_only_one_hour_is_refused(tmp_path):
    """Per-unit completeness is per HOUR: a gap leaves a machine absent from
    exactly one snapshot, which is the hardest kind of wrong to notice."""
    rows = _rows(units=("G1", "G2"), hours=(0, 1))
    path = _write_csv(tmp_path, rows[~((rows.unit_id == "G2") & (rows.hour == 1))])
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _registry())
    assert "G2" in str(exc.value)


# ─────────────────────── malformed, never coerced ──────────────────────────

def test_a_missing_contract_column_is_refused_by_name(tmp_path):
    path = _write_csv(tmp_path, _rows().drop(columns=["q_mvar"]))
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _registry())
    assert "q_mvar" in str(exc.value)


def test_a_fractional_status_is_refused_rather_than_truncated(tmp_path):
    """0.5 coerced to int is 0 — a half-committed unit recorded as offline."""
    rows = _rows()
    rows.loc[0, "status"] = 0.5
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, rows), _registry())
    assert "status" in str(exc.value)


def test_a_non_numeric_power_is_refused_with_the_offending_value(tmp_path):
    rows = _rows()
    rows["p_mw"] = rows["p_mw"].astype(object)
    rows.loc[1, "p_mw"] = "n/a"
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, rows), _registry())
    assert "p_mw" in str(exc.value)


def test_two_client_columns_mapping_to_one_contract_column_is_refused(tmp_path):
    """`p_mw` and `P [MW]` in one file is ambiguous: picking either silently
    discards a column the client believed was read."""
    rows = _rows()
    rows["P [MW]"] = rows["p_mw"] * 2
    with pytest.raises(ContractError) as exc:
        tables_from_external(_write_csv(tmp_path, rows), _registry())
    assert "p_mw" in str(exc.value)


def test_an_empty_file_is_refused(tmp_path):
    path = _write_csv(tmp_path, _rows().iloc[0:0])
    with pytest.raises(ContractError):
        tables_from_external(path, _registry())


def test_an_unreadable_file_is_a_contract_error_not_a_parser_traceback(tmp_path):
    path = tmp_path / "dispatch.csv"
    path.write_bytes(b"\x00\x01 not a csv at all \xff")
    with pytest.raises(ContractError):
        tables_from_external(path, _registry())


def test_an_unknown_extension_is_refused(tmp_path):
    path = tmp_path / "dispatch.parquet"
    path.write_bytes(b"whatever")
    with pytest.raises(ContractError) as exc:
        tables_from_external(path, _registry())
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

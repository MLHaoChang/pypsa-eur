import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.schema.network import unit_registry, validate_canonical


def test_valid_ids_pass():
    validate_canonical(
        buses=pd.Series(["BUS_01", "BUS_02"]),
        unit_names=pd.Series(["G_BUS_01"]),
    )


def test_duplicate_bus_name_rejected():
    with pytest.raises(ContractError, match="duplicate"):
        validate_canonical(pd.Series(["B1", "B1"]), pd.Series(["G1"]))


def test_null_name_rejected():
    with pytest.raises(ContractError, match="null"):
        validate_canonical(pd.Series(["B1", None]), pd.Series(["G1"]))


def test_name_longer_than_12_chars_rejected():
    with pytest.raises(ContractError, match="12"):
        validate_canonical(pd.Series(["THIRTEEN_CHAR"]), pd.Series(["G1"]))


def test_unit_name_colliding_with_other_unit_rejected():
    with pytest.raises(ContractError, match="duplicate"):
        validate_canonical(pd.Series(["B1"]), pd.Series(["G1", "G1"]))


def test_unit_registry_merges_gen_and_ext_grid():
    reg = unit_registry(
        gen_names=pd.Series(["G_B1"]), gen_buses=pd.Series(["B1"]),
        ext_names=pd.Series(["SLK_B2"]), ext_buses=pd.Series(["B2"]),
    )
    assert reg.loc["G_B1", "kind"] == "gen"
    assert reg.loc["SLK_B2", "kind"] == "ext_grid"
    assert reg.loc["SLK_B2", "bus"] == "B2"


def test_empty_name_rejected():
    with pytest.raises(ContractError, match="empty"):
        validate_canonical(pd.Series(["B1", ""]), pd.Series(["G1"]))


def test_unit_registry_rejects_gen_colliding_with_ext_grid():
    with pytest.raises(ContractError, match="duplicate unit ids"):
        unit_registry(
            gen_names=pd.Series(["SLK_B1"]), gen_buses=pd.Series(["B1"]),
            ext_names=pd.Series(["SLK_B1"]), ext_buses=pd.Series(["B1"]),
        )


def test_name_with_quote_rejected():
    """raw_writer emits NAME unescaped into the .raw record stream, so a quote
    in a canonical ID splits one record into two. Rejected at ingest, per the
    module docstring, rather than escaped at the writer."""
    with pytest.raises(ContractError, match="characters outside"):
        validate_canonical(pd.Series(["B1", "B1'X"]), pd.Series(["G1"]))


def test_name_with_comma_rejected():
    with pytest.raises(ContractError, match="characters outside"):
        validate_canonical(pd.Series(["B1", "B1,X"]), pd.Series(["G1"]))


def test_name_with_newline_rejected():
    with pytest.raises(ContractError, match="characters outside"):
        validate_canonical(pd.Series(["B1", "B1\nX"]), pd.Series(["G1"]))


def test_unit_name_with_leading_equals_rejected():
    """A leading '=' makes the name a formula when a downstream CSV is opened
    in a spreadsheet. Checked on the unit series too, not just buses."""
    with pytest.raises(ContractError, match="characters outside"):
        validate_canonical(pd.Series(["B1"]), pd.Series(["=1+1"]))


def test_hyphen_and_underscore_names_accepted():
    validate_canonical(pd.Series(["BUS_01", "G_BUS-2"]), pd.Series(["SLK_BUS_01"]))


def test_trailing_newline_rejected():
    """Anchoring regression: `$` matches just before a final newline, so a
    `re.match(r"[A-Za-z0-9_-]+$", ...)` spelling accepts "B1\\n" and lets a
    forged .raw record through. Only a full match rejects it."""
    with pytest.raises(ContractError, match="characters outside"):
        validate_canonical(pd.Series(["B1", "B2\n"]), pd.Series(["G1"]))

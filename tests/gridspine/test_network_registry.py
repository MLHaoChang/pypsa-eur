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

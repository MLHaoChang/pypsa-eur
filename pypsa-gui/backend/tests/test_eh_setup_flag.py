# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""``eh_setup._flag`` reads a cell by the one flag rule (``process_chain.truthy``), not by ``bool()``."""

import math

import numpy as np
import pandas as pd
import pytest

from gridspine.drivers.process_chain import truthy
from services.adequacy.eh_setup import _flag

VALUES = [
    "yes", " Yes ", "TRUE", "true", "1", "1.0", " 1 ", 1, 1.0, True,
    "0", "", "no", "false", "2", None, 0, False, math.nan, np.True_, np.False_, pd.NA,
]  # fmt: skip


@pytest.mark.parametrize("value", VALUES, ids=repr)
def test_flag_equals_shared_rule(value):
    df = pd.DataFrame({"eh_poc": [value]}, index=["b"], dtype=object)
    assert _flag(df, "b", "eh_poc") is truthy(value)


@pytest.mark.parametrize("value", ["no", "false", "0", "False"])
def test_text_false_is_false(value):
    df = pd.DataFrame({"eh_poc": [value]}, index=["b"])
    assert _flag(df, "b", "eh_poc") is False


def test_missing_column_is_false():
    assert _flag(pd.DataFrame(index=["b"]), "b", "eh_poc") is False


@pytest.mark.parametrize("value", [[1, 2], [], ["yes", "no"]], ids=repr)
def test_array_like_cell_is_false_not_an_error(value):
    df = pd.DataFrame({"eh_poc": [value]}, index=["b"], dtype=object)
    assert _flag(df, "b", "eh_poc") is False

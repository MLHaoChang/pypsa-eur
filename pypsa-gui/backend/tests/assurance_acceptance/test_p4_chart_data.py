"""A4.1/A4.3: a chart draws only the values the solver would use."""

import pandas as pd
import pytest
from fastapi import HTTPException

from services import chat_tools


def test_a_series_shadowed_static_value_is_never_drawn(baseline):
    ctx = baseline[0]
    n = ctx.network
    n.add("Generator", "G2", bus="B1", p_nom=10, p_max_pu=0.9)
    # G2's series overrides its static 0.9; G1 has no series (static 1.0).
    n.generators_t.p_max_pu = pd.DataFrame({"G2": [0.25, 0.5]}, index=n.snapshots)
    with pytest.raises(HTTPException) as refused:
        chat_tools.create_chart("Generator", "p_max_pu", ["G1", "G2"])
    assert refused.value.detail["error_kind"] == "chart_data_partial"

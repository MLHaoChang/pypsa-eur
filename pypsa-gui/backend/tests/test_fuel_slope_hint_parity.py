"""The generator form's slope hint (homer-parity 02i) is worded exactly as ``fuel_curve.slope_warning``.

The wording lives twice: ``fuel_curve.slope_warning`` and ``frontend/src/layout/properties/fuelSlopeHint.ts``
(``SLOPE_HINT_CASES``, which its vitest checks against the frontend function). This reads that table out of the
TypeScript file and checks it against the backend, so either side drifting fails a test.
"""

import re
from pathlib import Path

import pytest

from services import fuel_curve

TS = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/layout/properties/fuelSlopeHint.ts"
)
CASES = re.findall(r"\['([0-9.]+)', '([^']*)'\]", TS.read_text())


def test_table_is_read():
    assert len(CASES) >= 4


@pytest.mark.parametrize("slope,text", CASES)
def test_frontend_cases_match_backend_warning(slope, text):
    row = {"fuel_curve_slope": float(slope), "fuel_price": 10.0}
    assert fuel_curve.slope_warning(row) == text

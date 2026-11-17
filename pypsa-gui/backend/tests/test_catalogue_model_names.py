"""`frontend/src/catalogue/modelNames.generated.json` is current with the Parameter catalogue."""

import sys

from tests.test_packaging_requirements import BACKEND


def test_generated_model_names_are_current():
    sys.path.insert(0, str(BACKEND / "smoke"))
    try:
        import catalogue_model_names as gen
    finally:
        sys.path.pop(0)
    assert gen.TARGET.read_text("utf-8") == gen.render(), "run python smoke/catalogue_model_names.py --write"

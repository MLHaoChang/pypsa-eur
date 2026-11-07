"""DC 22: the New project form's defaults (frontend pages/hubDesign/dcLoadForm.ts)
are the template's own, and the body the form sends builds for every option."""

import pytest

from project_templates import eh_templates as T
from services.dc_architecture import ARCHITECTURES
from services.dc_load_profile import DcLoadError

# Mirrors DC_DEFAULTS in frontend/src/pages/hubDesign/dcLoadForm.ts.
FORM = {
    "pue": 1.3,
    "it_mw": 36.0,
    "single_year": 2030,
    "phases": [(2027, 12.0), (2030, 24.0), (2035, 36.0)],
}


def test_form_defaults_are_the_presets():
    single = T.dc_load_preset("eh_datacenter")
    assert single["pue_at_design"] == FORM["pue"]
    assert single["phases"][0]["it_mw"] == FORM["it_mw"]
    assert single["phases"][0]["cod"].startswith(str(FORM["single_year"]))
    staged = T.dc_load_preset("eh_datacenter_staged")
    assert [(int(p["cod"][:4]), p["it_mw"]) for p in staged["phases"]] == FORM["phases"]


@pytest.mark.parametrize("arch", ARCHITECTURES)
@pytest.mark.parametrize("mode", ["genset", "bess", "hybrid"])
def test_form_body_builds(arch, mode):
    body = {
        "pue_at_design": 1.3,
        "architecture": arch,
        "backup": {"mode": mode},
        "phases": [{"cod": "2030-01-01", "it_mw": 36.0}],
    }
    if mode == "bess":
        # No gensets: the default 36 MW peaks above the weak grid, refused on the
        # IT load field (the form shows it beside "IT load"); a smaller load builds.
        with pytest.raises(DcLoadError) as exc:
            T.dc_load_spec("eh_datacenter", body)
        assert exc.value.field.startswith("dc_load.phases")
        body["phases"] = [{"cod": "2030-01-01", "it_mw": 20.0}]
    spec = T.dc_load_spec("eh_datacenter", body)
    assert spec.architecture == arch and spec.backup.mode == mode
    staged_body = {k: v for k, v in body.items() if k != "phases"}
    if mode == "bess":
        # The staged plan's early years exceed the energized import without gensets:
        # refused on the IT load field; the user picks gensets or hybrid instead.
        with pytest.raises(DcLoadError) as exc:
            T.dc_load_spec("eh_datacenter_staged", staged_body)
        assert exc.value.field.startswith("dc_load.phases")
        return
    assert len(T.dc_load_spec("eh_datacenter_staged", staged_body).phases) == 3

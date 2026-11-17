"""DC grid-readiness walkthrough 01 and 02: the create refusal for a peak above
the energized import is plain words. It names no program internals (no
``Timestamp('…')`` tuple), leaves out a firm source that adds nothing, and for
Battery backup says why and names each way out with its number."""

import re

import pytest

from project_templates import eh_templates as T
from services.dc_load_profile import DcLoadError

BESS = {"pue_at_design": 1.3, "architecture": "ac", "backup": {"mode": "bess"}}


def _refusal(template_id: str, body: dict) -> str:
    with pytest.raises(DcLoadError) as exc:
        T.dc_load_spec(template_id, body)
    assert exc.value.field == "dc_load.phases"
    return str(exc.value)


def test_staged_refusal_has_no_code_text():
    msg = _refusal("eh_datacenter_staged", BESS)
    assert "Timestamp" not in msg and "(2027," not in msg
    # The staged plan reuses one modelled week for every phase: say so.
    assert re.search(r"at \d{4}-\d{2}-\d{2} \d{2}:\d{2} of the modelled week\)", msg), msg
    # Battery backup has no gensets: no genset clause.
    assert "firm gensets" not in msg and "genset fleet" not in msg, msg
    assert "Battery strings carry outages only" in msg
    assert "Pick Hybrid or Gensets backup" in msg, msg


def test_single_year_refusal_names_the_largest_it_load_that_builds():
    body = {**BESS, "phases": [{"cod": "2030-01-01", "it_mw": 36.0}]}
    msg = _refusal("eh_datacenter", body)
    assert re.search(r"\(at \d{4}-\d{2}-\d{2} \d{2}:\d{2}\)", msg), msg
    assert "firm gensets" not in msg and "genset fleet" not in msg, msg
    assert "Battery strings carry outages only" in msg and "Hybrid" in msg
    m = re.search(r"IT load to at most (\d+) MW", msg)
    assert m, msg
    most = int(m.group(1))
    # The number is the largest whole MW that builds.
    T.dc_load_spec("eh_datacenter", {**body, "phases": [{"cod": "2030-01-01", "it_mw": float(most)}]})
    _refusal("eh_datacenter", {**body, "phases": [{"cod": "2030-01-01", "it_mw": most + 1.0}]})


def test_hybrid_is_a_way_out_at_the_default_it_load():
    body = {**BESS, "backup": {"mode": "hybrid"}, "phases": [{"cod": "2030-01-01", "it_mw": 36.0}]}
    assert T.dc_load_spec("eh_datacenter", body).backup.mode == "hybrid"
    staged = {k: v for k, v in body.items() if k != "phases"}
    assert T.dc_load_spec("eh_datacenter_staged", staged).backup.mode == "hybrid"


def test_binding_snapshot_is_iso_minutes():
    from services.adequacy.site_peak import peak_check

    spec = T.dc_load_spec("eh_datacenter_staged", {**BESS, "backup": {"mode": "genset"}})
    n = T.BUILDERS["eh_datacenter_staged"](load_spec=spec)
    rows = peak_check(n, T.island_config("eh_datacenter_staged", None, load_spec=spec))
    assert rows
    for r in rows:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", r["binding_snapshot"]), r

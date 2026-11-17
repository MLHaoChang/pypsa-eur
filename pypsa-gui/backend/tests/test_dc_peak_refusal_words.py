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
    m = re.search(r"IT load to at most (\d+(?:\.\d)?) MW", msg)
    assert m, msg
    most = float(m.group(1))
    # The number is the largest 0.1 MW step that builds (the form takes decimals).
    T.dc_load_spec("eh_datacenter", {**body, "phases": [{"cod": "2030-01-01", "it_mw": most}]})
    _refusal("eh_datacenter", {**body, "phases": [{"cod": "2030-01-01", "it_mw": round(most + 0.1, 1)}]})


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


# ── the words on their own (GK A D1–D4) ────────────────────────────────────

from types import SimpleNamespace as NS  # noqa: E402

from services.adequacy.site_peak import describe, snapshot_words  # noqa: E402


def _spec(mode, it=36.0):
    return NS(backup=NS(mode=mode), phases=[NS(it_mw=it)])


@pytest.mark.parametrize("mode", ["genset", "hybrid"])
def test_no_it_number_where_the_cap_may_move_with_it(mode):
    words = T._peak_ways_out(_spec(mode), None, single=True)
    assert "at most" not in words and "Battery" not in words
    assert words == " Raise the connection after you create the project."


def test_no_it_load_fits_is_said_not_zero():
    words = T._peak_ways_out(_spec("bess"), 0.0, single=True)
    assert "at most 0" not in words and "No IT load fits the energized import." in words
    assert words.endswith("Pick Hybrid backup, or raise the connection after you create the project.")


def test_the_search_stays_below_the_current_it_load(monkeypatch):
    # A check that always passes: the answer is the step below the current IT load, never it.
    monkeypatch.setattr(T, "_peak_over", lambda *a, **k: (None, []))
    spec = T.dc_load_spec("eh_datacenter", {**BESS, "phases": [{"cod": "2030-01-01", "it_mw": 20.0}]})
    over = [{"cap_mw": 47.2, "load_mw": 47.2}]
    assert T._largest_fitting_it("eh_datacenter", spec, None, over) == 19.9
    # None fits: 0.
    monkeypatch.setattr(T, "_peak_over", lambda *a, **k: (None, [{}]))
    assert T._largest_fitting_it("eh_datacenter", spec, None, over) == 0.0


def test_snapshot_words():
    import pandas as pd

    ts = pd.Timestamp("2030-07-07 13:00:00")
    assert snapshot_words(ts) == "2030-07-07 13:00"
    assert snapshot_words((2027, ts)) == "2030-07-07 13:00"
    assert snapshot_words(7) == "7" and snapshot_words("now") == "now"


def _row(*firm):
    return {"efficiency": 1.0, "energized_mw": 15.0, "cap_mw": 15.0,
            "firm": [{"source": s, "mw": mw, "rule": "r"} for s, mw in firm]}


def test_describe_drops_zero_sources_and_says_unknown():
    assert describe(_row(("gensets", 0.0), ("ride-through batteries", 0.0))) == \
        "the 15 MW energized import = 15 MW"
    assert describe(_row(("gensets", 0.0), ("ride-through batteries", 2.0))) == \
        "the 15 MW energized import plus 2 MW of firm ride-through batteries (r) = 15 MW"
    assert "plus an unknown amount of firm gensets (r)" in describe(_row(("gensets", float("nan"))))

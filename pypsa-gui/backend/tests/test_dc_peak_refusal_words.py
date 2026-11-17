"""DC grid-readiness walkthrough 01 and 02: the create refusal for a peak above
the energized import is plain words. It names no program internals (no
``Timestamp('…')`` tuple), leaves out a firm source that adds nothing, and for
Battery backup says why and names each way out with its number."""

import datetime
import re

import numpy as np

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
    assert re.search(r"at \d{4}-\d{2}-\d{2} \d{2}:\d{2} of the modelled week\)", msg), (
        msg
    )
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
    T.dc_load_spec(
        "eh_datacenter", {**body, "phases": [{"cod": "2030-01-01", "it_mw": most}]}
    )
    _refusal(
        "eh_datacenter",
        {**body, "phases": [{"cod": "2030-01-01", "it_mw": round(most + 0.1, 1)}]},
    )


def test_hybrid_is_a_way_out_at_the_default_it_load():
    body = {
        **BESS,
        "backup": {"mode": "hybrid"},
        "phases": [{"cod": "2030-01-01", "it_mw": 36.0}],
    }
    assert T.dc_load_spec("eh_datacenter", body).backup.mode == "hybrid"
    staged = {k: v for k, v in body.items() if k != "phases"}
    assert T.dc_load_spec("eh_datacenter_staged", staged).backup.mode == "hybrid"


def test_binding_snapshot_is_iso_minutes():
    from services.adequacy.site_peak import peak_check

    spec = T.dc_load_spec(
        "eh_datacenter_staged", {**BESS, "backup": {"mode": "genset"}}
    )
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
    words = T._peak_ways_out(_spec("bess"), 0.0, single=True, alts=("hybrid",))
    assert "at most 0" not in words and "No IT load fits the energized import." in words
    assert words.endswith(
        "Pick Hybrid backup, or raise the connection after you create the project."
    )


def test_the_search_stays_below_the_current_it_load(monkeypatch):
    # A check that always passes: the answer is the step below the current IT load, never it.
    monkeypatch.setattr(T, "_peak_over", lambda *a, **k: (None, []))
    spec = T.dc_load_spec(
        "eh_datacenter", {**BESS, "phases": [{"cod": "2030-01-01", "it_mw": 20.0}]}
    )
    assert T._largest_fitting_it("eh_datacenter", spec, None) == 19.9
    # None fits: 0.
    monkeypatch.setattr(T, "_peak_over", lambda *a, **k: (None, [{}]))
    assert T._largest_fitting_it("eh_datacenter", spec, None) == 0.0


def test_snapshot_words():
    import pandas as pd

    ts = pd.Timestamp("2030-07-07 13:00:00")
    assert snapshot_words(ts) == "2030-07-07 13:00"
    assert snapshot_words((2027, ts)) == "2030-07-07 13:00"
    assert snapshot_words(7) == "7" and snapshot_words("now") == "now"
    assert snapshot_words(np.datetime64("2030-07-07T13:00")) == "2030-07-07 13:00"
    assert snapshot_words(datetime.datetime(2030, 7, 7, 13)) == "2030-07-07 13:00"


def _row(*firm):
    return {
        "efficiency": 1.0,
        "energized_mw": 15.0,
        "cap_mw": 15.0,
        "firm": [{"source": s, "mw": mw, "rule": "r"} for s, mw in firm],
    }


def test_describe_drops_zero_sources_and_says_unknown():
    assert (
        describe(_row(("gensets", 0.0), ("ride-through batteries", 0.0)))
        == "the 15 MW energized import = 15 MW"
    )
    assert (
        describe(_row(("gensets", 0.0), ("ride-through batteries", 2.0)))
        == "the 15 MW energized import plus 2 MW of firm ride-through batteries (r) = 15 MW"
    )
    assert "plus an unknown amount of firm gensets (r)" in describe(
        _row(("gensets", float("nan")))
    )


@pytest.mark.parametrize("mode", ["genset", "hybrid"])
def test_a_refusal_with_gensets_searches_no_it_load(mode, monkeypatch):
    # GK A D1: the search runs for Battery backup only.
    row = {
        "period": "2030",
        "load_mw": 50.0,
        "binding_snapshot": "2030-07-07 14:00",
        "efficiency": 1.0,
        "energized_mw": 40.0,
        "cap_mw": 40.0,
        "firm": [{"source": "gensets", "mw": 0.0, "rule": ""}],
    }
    import pandas as pd

    spec = T.dc_load_spec(
        "eh_datacenter",
        {
            **BESS,
            "backup": {"mode": mode},
            "phases": [{"cod": "2030-01-01", "it_mw": 20.0}],
        },
    )
    builds = []
    monkeypatch.setattr(
        T,
        "_peak_over",
        lambda *a, **k: (
            builds.append(a[1].backup.mode)
            or (NS(snapshots=pd.DatetimeIndex([])), [row])
        ),
    )
    monkeypatch.setattr(
        T, "_largest_fitting_it", lambda *a, **k: pytest.fail("searched")
    )
    with pytest.raises(DcLoadError) as exc:
        T._check_dc_peak("eh_datacenter", spec)
    assert str(exc.value).endswith("Raise the connection after you create the project.")
    # Nor is another mode built to be named: only the entered one is checked.
    assert builds == [mode]


def test_hybrid_is_named_only_where_it_builds():
    """GK A ask 2: at IT 80, Hybrid is refused too (its firm cap is below the
    peak), so the Battery refusal does not offer it."""
    body = {**BESS, "phases": [{"cod": "2030-01-01", "it_mw": 80.0}]}
    msg = _refusal("eh_datacenter", body)
    assert "Hybrid" not in msg, msg
    _refusal("eh_datacenter", {**body, "backup": {"mode": "hybrid"}})
    assert re.search(
        r"Set IT load to at most \d+(?:\.\d)? MW, or raise the connection", msg
    ), msg


def test_the_search_brackets_from_the_current_it_load(monkeypatch):
    """GK A ask 1: the upper bracket is the current IT load (refused), never a
    linear estimate, so a check that fits above the estimate is still found."""
    spec = T.dc_load_spec(
        "eh_datacenter", {**BESS, "phases": [{"cod": "2030-01-01", "it_mw": 20.0}]}
    )
    # Fits up to 15.0 MW, above a linear estimate from a cap/load of 0.5 (10 MW).
    monkeypatch.setattr(
        T,
        "_peak_over",
        lambda tid, s, p: (None, [] if s.phases[0].it_mw <= 15.0 + 1e-9 else [{}]),
    )
    assert T._largest_fitting_it("eh_datacenter", spec, None) == 15.0


@pytest.mark.parametrize(
    "alts,want",
    [
        ((), "Change the build plan after you create the project."),
        (("hybrid",), "Pick Hybrid backup, or change the build plan"),
        (("genset",), "Pick Gensets backup, or change the build plan"),
        (
            ("hybrid", "genset"),
            "Pick Hybrid or Gensets backup, or change the build plan",
        ),
    ],
)
def test_the_staged_refusal_names_each_mode_that_builds(alts, want):
    words = T._peak_ways_out(_spec("bess"), None, single=False, alts=alts)
    assert want in words, words


@pytest.mark.parametrize("mode", ["genset", "hybrid"])
def test_only_a_battery_refusal_names_another_mode(mode):
    words = T._peak_ways_out(_spec(mode), None, single=True, alts=("hybrid", "genset"))
    assert "Pick" not in words and "backup" not in words, words


@pytest.mark.parametrize(
    "err", [RuntimeError("boom"), DcLoadError("hybrid words", "x")]
)
def test_a_probe_that_raises_is_not_named_and_is_logged(err, monkeypatch, caplog):
    """GK A ask: building another mode to name it may raise. That mode is not
    named, the error is logged, and the user still gets their own refusal."""
    fits = T.dc_load_spec(
        "eh_datacenter", {**BESS, "phases": [{"cod": "2030-01-01", "it_mw": 20.0}]}
    )
    # IT 36 with Battery backup is refused, and Hybrid would build there.
    phase = fits.phases[0].model_copy(update={"it_mw": 36.0})
    spec = fits.model_copy(update={"phases": [phase]})
    real = T._peak_over

    def peak_over(tid, s, p=None):
        if s.backup.mode == "hybrid":
            raise err
        return real(tid, s, p)

    monkeypatch.setattr(T, "_peak_over", peak_over)
    with caplog.at_level("WARNING", logger=T.__name__):
        with pytest.raises(DcLoadError) as exc:
            T._check_dc_peak("eh_datacenter", spec)
    msg = str(exc.value)
    assert exc.value.field == "dc_load.phases"
    assert "hybrid words" not in msg and "Hybrid" not in msg, msg
    assert "Battery strings carry outages only" in msg, msg
    assert any(
        "hybrid backup failed" in r.getMessage() and r.exc_info for r in caplog.records
    )


def test_the_search_fails_fast_when_the_bracket_stops_shrinking(monkeypatch):
    """GK A loop guard: a capped bisection raises instead of hanging."""
    spec = T.dc_load_spec(
        "eh_datacenter", {**BESS, "phases": [{"cod": "2030-01-01", "it_mw": 20.0}]}
    )
    monkeypatch.setattr(T, "_BISECT_CAP", 3)
    monkeypatch.setattr(T, "_peak_over", lambda tid, s, p: (None, []))
    with pytest.raises(RuntimeError, match="did not converge"):
        T._largest_fitting_it("eh_datacenter", spec, None)


def test_a_search_that_raises_drops_the_number_and_is_logged(monkeypatch, caplog):
    """GK A ruling: an exception inside the IT load search stops the search; the
    refusal stands without "at most N MW", and the error is logged."""
    fits = T.dc_load_spec(
        "eh_datacenter", {**BESS, "phases": [{"cod": "2030-01-01", "it_mw": 20.0}]}
    )
    spec = fits.model_copy(
        update={"phases": [fits.phases[0].model_copy(update={"it_mw": 36.0})]}
    )
    real = T._peak_over

    def peak_over(tid, s, p=None):
        # The entered spec (36 MW) and the Hybrid probe build; a probe below raises.
        if s.backup.mode == "bess" and s.phases[0].it_mw < 36.0:
            raise RuntimeError("boom")
        return real(tid, s, p)

    monkeypatch.setattr(T, "_peak_over", peak_over)
    with caplog.at_level("WARNING", logger=T.__name__):
        with pytest.raises(DcLoadError) as exc:
            T._check_dc_peak("eh_datacenter", spec)
    msg = str(exc.value)
    assert exc.value.field == "dc_load.phases"
    assert "at most" not in msg and "boom" not in msg, msg
    assert msg.endswith(
        "Pick Hybrid backup, or raise the connection after you create the project."
    ), msg
    assert any(
        "IT load search failed" in r.getMessage() and r.exc_info for r in caplog.records
    )

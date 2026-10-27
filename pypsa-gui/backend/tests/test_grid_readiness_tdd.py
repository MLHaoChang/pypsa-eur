"""The scorecard's current-distortion (TDD) row (DC grid readiness 12, part 1)."""
import json
import math

import pytest

from harness import grid_readiness_calculations as R
from services.adequacy import grid_readiness as G
from services.asset_schema import fields as F
from tests.test_grid_readiness import PROFILE, _harmonic_site, _rows

SPECTRUM = "5:3, 7:2"  # % of the unit's rated current at orders 5 and 7


def _row(tmp_path, n, profile=PROFILE, **kw):
    return _rows(G.grid_readiness(tmp_path, n, profile, **kw))["current_distortion"]


def _without_standard(tmp_path):
    from gridspine.templates.grid_codes import load_grid_code

    p = load_grid_code(PROFILE, raw=True)
    p.pop("current_tdd_standard")
    codes = tmp_path / "codes"
    codes.mkdir()
    (codes / "no_tdd.yaml").write_text(json.dumps(p))
    return codes


def test_tdd_is_the_rss_of_the_unit_currents_over_the_rated_import_by_hand(tmp_path):
    # rated 20 MVA, I_L = the 60 MW grid import, S_sc 1400 MVA at 110 kV: IEEE 519-2014 Table 3, Isc/IL 23.3.
    row = _row(tmp_path, _harmonic_site(spectrum=SPECTRUM))
    expected = math.hypot(3.0, 2.0) * 20.0 / 60.0
    assert row["project_value"] == pytest.approx(expected, abs=1e-4) == pytest.approx(1.2019, abs=1e-4)
    assert row["limit"] == 4.0 and row["verdict"] == "pass" and row["assumed"] is True
    assert row["unit"] == "% of I_L"
    assert row["tdd"]["table"] == "Table 3" and row["tdd"]["band"] == "20-50"
    assert row["tdd"]["isc_il"] == pytest.approx(1400.0 / 60.0) and row["tdd"]["i_l_mva"] == 60.0
    assert row["tdd"]["worst_order"] == 5 and row["tdd"]["failing_orders"] == []
    assert "I_L: the connection's rated import at the PoC" in row["note"]
    assert "S_sc: the fault level entered at the PoC" in row["note"]
    from gridspine.templates.grid_codes import load_grid_code
    assert row["source"].endswith(": " + load_grid_code(PROFILE, raw=True)["current_tdd_standard"]["clause"])
    # Like the harmonic row, the assistant does not restate a TDD figure as a scalar check.
    assert R.disposition(row) == ("not_scalar", None)


def test_a_demo_spectrum_points_go_to_field_at_that_unit_as_the_harmonic_row_does(tmp_path):
    rows = _rows(G.grid_readiness(tmp_path, _harmonic_site(), PROFILE))
    row = rows["current_distortion"]
    assert row["target"]["entity"] == "compressor" and row["target"]["field"] == "harmonic_spectrum"
    assert row["target"] == rows["harmonic_distortion"]["target"]
    assert "Demo default spectrum for compressor (no vendor spectrum entered)." in row["note"]
    vendor = _row(tmp_path, _harmonic_site(spectrum=SPECTRUM))
    assert vendor["target"]["entity"] is None and "Demo default" not in vendor["note"]


def test_over_the_limit_warns_and_names_the_failing_orders(tmp_path):
    row = _row(tmp_path, _harmonic_site(spectrum="5:30, 7:20"))
    assert row["project_value"] == pytest.approx(math.hypot(30.0, 20.0) / 3.0, abs=1e-4)
    assert row["verdict"] == "warn" and row["tdd"]["failing_orders"] == [5, 7]
    assert "Orders over their limit: 5, 7." in row["note"]
    assert R.disposition(row) == ("not_scalar", None)


@pytest.mark.parametrize("sk, band, limit", [(1200.0, "<= 20", 2.5), (1200.01, "20-50", 4.0)])
def test_a_ratio_of_exactly_20_takes_the_stricter_band(tmp_path, sk, band, limit):
    row = _row(tmp_path, _harmonic_site(spectrum=SPECTRUM, sk=sk))
    assert row["tdd"]["band"] == band and row["limit"] == limit
    # The note writes the band as people read it: "≤ 20", never the "<=" of the data.
    assert f"band {band.replace('<=', '≤')}." in row["note"] and "<=" not in row["note"]


def test_without_a_connection_rating_i_l_is_the_process_units_rated_load(tmp_path):
    n = _harmonic_site(spectrum=SPECTRUM)
    n.links.at["grid_import", "p_nom"] = 0.0
    row = _row(tmp_path, n)
    assert row["tdd"]["i_l_mva"] == 20.0 and row["project_value"] == pytest.approx(math.hypot(3.0, 2.0), abs=1e-4)
    assert "I_L: the process units' rated load only" in row["note"]
    # The I_L source moves the band: 1400 / 20 = 70 is 50-100 (limit 12.0), where the import's 23.3 was 20-50.
    with_import = _row(tmp_path, _harmonic_site(spectrum=SPECTRUM))
    assert (with_import["tdd"]["band"], with_import["limit"]) == ("20-50", 4.0)
    assert row["tdd"]["band"] == "50-100" and row["limit"] != with_import["limit"]


def _no_load_current():
    """No connection rating and no process unit rated: I_L is unknown on the process-unit fallback."""
    n = _harmonic_site(spectrum=SPECTRUM)
    n.links.at["grid_import", "p_nom"] = 0.0
    n.links.at["compressor", "rated_mva"] = math.nan
    return n


def test_no_load_current_on_the_process_unit_fallback_points_at_the_units_rating(tmp_path):
    row = _row(tmp_path, _no_load_current())
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["target"] == {**row["target"], "component": "Link", "entity": "compressor", "field": "rated_mva"}


def test_no_standard_in_the_grid_code_is_unknown(tmp_path):
    row = _row(tmp_path, _harmonic_site(spectrum=SPECTRUM), "no_tdd", profile_dirs=(_without_standard(tmp_path),))
    assert row["verdict"] == "unknown" and row["limit"] is None and row["project_value"] is None
    assert row["note"] == "The grid code names no current-distortion standard"


def test_no_process_unit_is_unknown(tmp_path):
    n = _harmonic_site(spectrum=SPECTRUM)
    n.links["eh_process"] = [False, False]
    n.links["harmonic_source"] = ["", ""]
    row = _row(tmp_path, n)
    assert row["verdict"] == "unknown" and row["note"].startswith("No process unit on this project")
    assert row["target"]["field"] == "eh_process"


def test_no_fault_level_is_unknown(tmp_path):
    row = _row(tmp_path, _harmonic_site(spectrum=SPECTRUM, sk=math.nan))
    assert row["verdict"] == "unknown" and row["note"] == "No short-circuit level at the point of connection"
    assert row["target"]["field"] == "eh_sk_mva"


def test_a_unit_without_a_harmonic_source_blocks_a_pass(tmp_path):
    row = _row(tmp_path, _harmonic_site(source=""))
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["target"] == {**row["target"], "entity": "compressor", "field": "harmonic_source"}
    assert "compressor has no Harmonic source or Rated apparent power" in row["note"]


def test_a_malformed_spectrum_is_unknown_not_a_failed_scorecard(tmp_path):
    row = _row(tmp_path, _harmonic_site(spectrum="5:three"))
    assert row["verdict"] == "unknown" and row["note"].startswith("compressor: ")
    assert row["target"]["entity"] == "compressor" and row["target"]["field"] == "harmonic_spectrum"


def test_every_target_is_a_registered_field(tmp_path):
    """Asset targets are Link fields of the asset schema; the fault level points where the harmonic row does
    (the Connection limits line, which is not an asset field)."""
    fields = {"Link": {f.key for f in F.LINK}, "Bus": {f.key for f in F.BUS}}
    n_proc = _harmonic_site(spectrum=SPECTRUM)
    n_proc.links["eh_process"] = [False, False]
    n_proc.links["harmonic_source"] = ["", ""]
    for n in (_harmonic_site(spectrum=SPECTRUM), _harmonic_site(source=""), _harmonic_site(spectrum="5:three"),
              _harmonic_site(spectrum=SPECTRUM, sk=math.nan), n_proc, _no_load_current()):
        rows = _rows(G.grid_readiness(tmp_path, n, PROFILE))
        t = rows["current_distortion"]["target"]
        if t["field"] == "eh_sk_mva":
            assert t == rows["harmonic_distortion"]["target"]
        else:
            assert t["field"] in fields[t["component"]], t

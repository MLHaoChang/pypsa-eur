"""
Background-harmonic import at the PoC and its IEC 61000-3-6 sum with the
plant's own distortion (GC-05).
"""

import pytest

from gridspine.ingest.background_harmonics import (
    combine,
    load_background,
    parse_background,
    save_background,
)
from gridspine.schema.contracts import ContractError

CSV = "order,u_h_pct_p95\n3,0.9\n5,2.0\n7,1.1\n11,0.6\n"


def _parse(text=CSV, **kw):
    args = {
        "poc": "grid",
        "source": "PQ survey, class A meter",
        "measured_on": "2026-09-30",
    } | kw
    return parse_background(text, **args)


def test_the_record_keeps_orders_source_and_date():
    rec = _parse()
    assert rec["orders_pct"] == {3: 0.9, 5: 2.0, 7: 1.1, 11: 0.6}
    assert (rec["poc"], rec["source"], rec["measured_on"]) == (
        "grid",
        "PQ survey, class A meter",
        "2026-09-30",
    )
    assert len(rec["sha256"]) == 64


def test_the_fifth_combines_with_alpha_1_4_to_the_hand_value():
    # U5 = (2.0^1.4 + 1.5^1.4)^(1/1.4): 2.0^1.4 = 2.63902, 1.5^1.4 = 1.76412,
    # sum 4.40313, ^(1/1.4) = 2.88290 %
    out = combine({5: 1.5}, {5: 2.0})
    assert out[5] == pytest.approx(2.88290, abs=1e-5)


def test_alpha_is_one_below_the_fifth_and_two_above_the_tenth():
    out = combine({3: 0.5, 11: 0.8}, {3: 0.9, 11: 0.6})
    assert out[3] == pytest.approx(1.4)  # arithmetic
    assert out[11] == pytest.approx(1.0)  # sqrt(0.64 + 0.36)
    assert combine({7: 1.0}, {}) == {7: 1.0}  # no background at 7: the plant alone


@pytest.mark.parametrize(
    "text,match",
    [
        ("order,u_h_pct_p95\n5,-0.1\n", "not a finite value >= 0"),
        ("order,u_h_pct_p95\n-5,1\n", "outside 2..50"),
        ("order,u_h_pct_p95\n5,\n", "order 5 has no value"),
        ("order,u_h_pct_p95\n,1.0\n", "has no order"),
        ("order,u_h_pct_p95\n5,1\n5,2\n", "given twice"),
        ("order,u_h_pct_p95\n51,1\n", "outside 2..50"),
        ("order,u_h_pct_p95\n1,1\n", "outside 2..50"),
        ("order,u_h_pct_p95\nfive,1\n", "not an integer"),
        ("order,u_h_pct_p95\n5,nan\n", "not a finite value"),
        ("order,u_h\n5,1\n", "missing column"),
        ("order,u_h_pct_p95\n", "no rows"),
    ],
)
def test_a_bad_order_or_value_is_refused(text, match):
    with pytest.raises(ContractError, match=match):
        _parse(text)


def test_provenance_is_required():
    with pytest.raises(ContractError, match="source"):
        _parse(source=" ")
    with pytest.raises(ContractError, match="ISO date"):
        _parse(measured_on="30/09/2026")
    with pytest.raises(ContractError, match="point of connection"):
        _parse(poc="")


def test_the_record_round_trips_through_the_run_dir(tmp_path):
    assert load_background(tmp_path) is None
    csv = tmp_path / "pq.csv"
    csv.write_text(CSV)
    rec = _parse(csv)
    assert rec["filename"] == "pq.csv"
    save_background(tmp_path, rec)
    assert load_background(tmp_path) == rec


def test_a_full_survey_given_as_text_parses():
    text = "order,u_h_pct_p95\n" + "".join(f"{h},0.{h % 10}\n" for h in range(2, 51))
    assert len(text) > 255  # longer than any file name
    assert len(_parse(text)["orders_pct"]) == 49


def test_a_damaged_stored_record_is_refused(tmp_path):
    from gridspine.ingest.background_harmonics import BACKGROUND_JSON

    (tmp_path / BACKGROUND_JSON).write_text('{"orders_pct": {"5": 1.0}}')
    with pytest.raises(ContractError, match="lacks"):
        load_background(tmp_path)
    (tmp_path / BACKGROUND_JSON).write_text("not json")
    with pytest.raises(ContractError, match="not a background-harmonics record"):
        load_background(tmp_path)

"""Grid-code profile fields a SAGC-type code needs (DC 10a).

Every new field is optional with no default: a blank profile stays valid,
and an unset limit is "no limit set" (``unknown``) in the report. The
values here are generic test values, not any code's or any network's.
"""
import copy
import math

import pytest

from gridspine.schema.contracts import ContractError
from gridspine.templates.grid_codes import (
    DYNAMIC_KINDS,
    PROFILE_KEYS,
    confirm_limit,
    load_grid_code,
    q_band_at,
    reported_requirements,
    unconfirmed,
    validate_profile,
)

BASE = load_grid_code("generic_assumed", raw=True)


def lim(**kw):
    return {"clause": "generic test clause", "source": "code", **kw}


def profile(**extra):
    return {**copy.deepcopy(BASE), **extra}


BAND = lim(points=[[0.0, -0.1, 0.1], [1.0, -0.3, 0.3]])


def test_a_profile_carrying_every_new_field_validates():
    p = profile(rvc_limit_infrequent_pct=lim(value=3.0), q_capability_pq=BAND,
                pf_min_lagging=lim(value=0.9), pf_min_leading=lim(value=0.95),
                dynamic_requirements=[{"id": "vrt", "requirement": "Ride through a voltage dip", "value": "0.15",
                                       "unit": "s", "kind": "vrt", "clause": "generic", "source": "code",
                                       "applies_to": "facility"}])
    out = validate_profile("p", p)
    assert out["q_capability_pq"]["points"] == BAND["points"]
    assert {"rvc_limit_infrequent_pct", "q_capability_pq", "pf_min_lagging", "pf_min_leading"} <= PROFILE_KEYS


def test_a_blank_profile_sets_none_of_them():
    out = validate_profile("p", profile())
    assert not {"rvc_limit_infrequent_pct", "q_capability_pq", "pf_min_lagging", "pf_min_leading"} & set(out)


@pytest.mark.parametrize("extra, match", [
    ({"rvc_limit_infrequent_pct": lim(value=1.0)}, "below rvc_limit_pct"),
    ({"rvc_limit_infrequent_pct": lim(value=-1.0)}, "positive"),
    ({"rvc_limit_infrequent_pct": {"value": 5.0}}, "clause"),
    ({"pf_min_lagging": lim(value=0.0)}, r"\(0, 1\]"),
    ({"pf_min_leading": lim(value=1.2)}, r"\(0, 1\]"),
    ({"pf_min_leading": lim(value=True)}, r"\(0, 1\]"),
    ({"q_capability_pq": lim(points=[[0.0, -0.1, 0.1]])}, "at least two"),
    ({"q_capability_pq": lim(points=[[0.0, -0.1, 0.1], [0.0, -0.2, 0.2]])}, "rise strictly"),
    ({"q_capability_pq": lim(points=[[0.5, -0.1, 0.1], [0.2, -0.2, 0.2]])}, "rise strictly"),
    ({"q_capability_pq": lim(points=[[0.0, -0.1, 0.1], [math.nan, -0.2, 0.2]])}, "finite"),
    ({"q_capability_pq": lim(points=[[0.0, -0.1, 0.1], [1.0, -0.2, math.inf]])}, "finite"),
    ({"q_capability_pq": lim(points=[[0.0, 0.2, 0.1], [1.0, -0.2, 0.2]])}, "above q_max_pu"),
    ({"q_capability_pq": {"points": [[0.0, -0.1, 0.1], [1.0, -0.2, 0.2]]}}, "clause"),
])
def test_a_malformed_new_field_is_refused(extra, match):
    with pytest.raises(ContractError, match=match):
        validate_profile("p", profile(**extra))


def test_the_infrequent_tier_may_equal_the_frequent_one():
    p = profile(rvc_limit_infrequent_pct=lim(value=BASE["rvc_limit_pct"]["value"]))
    validate_profile("p", p)


def test_the_q_band_is_linear_between_points_and_none_outside():
    assert q_band_at(BAND, 0.0) == (-0.1, 0.1)
    lo, hi = q_band_at(BAND, 0.5)
    assert (lo, hi) == (pytest.approx(-0.2), pytest.approx(0.2))
    assert q_band_at(BAND, 1.0) == (-0.3, 0.3)
    assert q_band_at(BAND, -0.01) is None and q_band_at(BAND, 1.01) is None


def test_new_limits_can_be_extracted_and_confirmed():
    doc = {"sha256": "0" * 64, "filename": "code.pdf", "title": "A code"}
    p = profile(document=doc, pf_min_lagging=lim(value=0.9, source="extracted", page=12, quote="0.9 lagging"))
    out = validate_profile("p", p)
    assert unconfirmed(out) == ["pf_min_lagging"]
    assert confirm_limit(out, "pf_min_lagging")["pf_min_lagging"]["source"] == "code"


@pytest.mark.parametrize("kind", DYNAMIC_KINDS)
def test_every_dynamic_kind_is_reported_as_needing_an_rms_study_never_judged(kind):
    row = {"id": kind, "requirement": "A dynamic requirement", "value": "1", "unit": "s", "kind": kind,
           "clause": "generic", "source": "code", "applies_to": "facility"}
    out = reported_requirements(validate_profile("p", profile(dynamic_requirements=[row])))
    assert [r["status"] for r in out] == ["reported"]
    assert out[0]["detail"] == "A dynamic requirement: 1 s (needs an RMS study)"


@pytest.mark.parametrize("bad, match", [
    ({"kind": "harmonics"}, "kind"),
    ({"unit": "  "}, "unit"),
])
def test_an_unknown_dynamic_kind_or_blank_unit_is_refused(bad, match):
    row = {"id": "x", "requirement": "r", "value": "1", "clause": "c", "source": "code", "applies_to": "load", **bad}
    with pytest.raises(ContractError, match=match):
        validate_profile("p", profile(dynamic_requirements=[row]))

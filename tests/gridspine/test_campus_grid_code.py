"""Grid-code profiles read from a document, and the project's own profiles
(plan C10, engine half).

A profile drafted from an uploaded grid code tags each limit ``extracted``:
the copilot read it from the document, and no person has confirmed it yet.
Such a limit names the page it came from and quotes the text verbatim, and
the profile names the document by hash. A person then confirms each limit,
which turns it into ``code``; the page and the quote stay.

A project keeps its profiles as one YAML file per profile in a directory.
They are listed and loaded next to the shipped ones, and may not take a
shipped profile's name.

A limit that is still ``extracted`` reaches every report row that uses it as
``extracted``, so an unconfirmed number is never presented as the code's.
"""
import copy

import pandas as pd
import pytest
import yaml

from gridspine.drivers.campus_study import (
    draft_from_project,
    grid_code_profiles,
    prepare_campus,
    rank_campus,
    size_campus,
)
from gridspine.schema.contracts import ContractError
from gridspine.static.campus_compliance import campus_compliance
from gridspine.static.campus_reactive import requirement_from
from gridspine.templates.grid_codes import (
    PROFILE_ID,
    SOURCES,
    band_for,
    confirm_limit,
    list_grid_codes,
    load_grid_code,
    uncovered_kv_ranges,
    unconfirmed,
    validate_profile,
)
from tests.gridspine.test_campus_compliance import inputs
from tests.gridspine.test_campus_study import project  # noqa: F401  (fixture)

SHA = "ab" * 32
DOC = {"sha256": SHA, "filename": "tso_code.pdf", "title": "TSO grid code, edition 3"}


def extracted(**over):
    """The generic profile with every limit extracted from DOC."""
    p = load_grid_code("generic_assumed", raw=True)
    for i, lim in enumerate([*p["voltage_bands"], p["q_range_demand"], p["rvc_limit_pct"], p["campus_voltage"]]):
        lim.update(source="extracted", page=i + 2, quote=f"quoted text {i}", clause=f"section 4.{i}")
    p["document"] = dict(DOC)
    p["title"] = "TSO code (draft)"
    p.update(over)
    return p


# --------------------------------------------------------------------------
# the extracted tag
# --------------------------------------------------------------------------

def test_extracted_is_a_tag_beside_code_and_assumed():
    assert SOURCES == {"code", "assumed", "extracted"}


def test_an_extracted_profile_validates_and_keeps_page_and_quote():
    p = validate_profile("tso", extracted())
    assert p["name"] == "tso"
    assert p["q_range_demand"]["page"] == 3 and p["q_range_demand"]["quote"] == "quoted text 1"
    assert p["document"] == DOC


@pytest.mark.parametrize("mutate, match", [
    (lambda p: p["q_range_demand"].pop("page"), "q_range_demand.*page"),
    (lambda p: p["q_range_demand"].update(page=0), "q_range_demand.*page"),
    (lambda p: p["q_range_demand"].update(page="3"), "q_range_demand.*page"),
    (lambda p: p["q_range_demand"].update(page=True), "q_range_demand.*page"),
    (lambda p: p["q_range_demand"].update(page=2.0), "q_range_demand.*page"),
    (lambda p: p["voltage_bands"][0].pop("quote"), r"voltage_bands\[0\].*quote"),
    (lambda p: p["rvc_limit_pct"].update(quote=""), "rvc_limit_pct.*quote"),
    (lambda p: p["rvc_limit_pct"].update(quote="   "), "rvc_limit_pct.*quote"),
    (lambda p: p["campus_voltage"].update(quote=7), "campus_voltage.*quote"),
])
def test_an_extracted_limit_without_its_page_or_quote_is_refused(mutate, match):
    p = extracted()
    mutate(p)
    with pytest.raises(ContractError, match=match):
        validate_profile("tso", p)


def test_a_code_limit_may_carry_a_page_and_quote_and_need_not():
    p = extracted()
    p["q_range_demand"]["source"] = "code"                  # confirmed: keeps both
    p["rvc_limit_pct"].update(source="code")
    p["rvc_limit_pct"].pop("page")
    p["rvc_limit_pct"].pop("quote")                         # a code limit typed by hand
    out = validate_profile("tso", p)
    assert out["q_range_demand"]["page"] == 3 and "page" not in out["rvc_limit_pct"]


@pytest.mark.parametrize("mutate, match", [
    (lambda p: p["q_range_demand"].update(source="code", page=-1), "q_range_demand.*page"),
    (lambda p: p["q_range_demand"].update(source="code", quote=""), "q_range_demand.*quote"),
    (lambda p: p["rvc_limit_pct"].update(source="assumed"), "rvc_limit_pct.*assumed.*page"),
])
def test_a_page_or_quote_is_checked_whatever_the_tag(mutate, match):
    p = extracted()
    mutate(p)
    with pytest.raises(ContractError, match=match):
        validate_profile("tso", p)


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------

def test_an_extracted_limit_needs_the_document():
    p = extracted()
    p.pop("document")
    with pytest.raises(ContractError, match="document"):
        validate_profile("tso", p)


def test_a_code_limit_with_a_page_needs_the_document_too():
    p = load_grid_code("generic_assumed", raw=True)
    p["q_range_demand"].update(source="code", page=4, quote="0,329")
    with pytest.raises(ContractError, match="document"):
        validate_profile("tso", p)
    p["document"] = dict(DOC)
    assert validate_profile("tso", p)["q_range_demand"]["page"] == 4


def test_a_profile_without_extracted_limits_or_pages_needs_no_document():
    assert "document" not in validate_profile("g", load_grid_code("generic_assumed", raw=True))


@pytest.mark.parametrize("mutate, match", [
    (lambda d: d.update(sha256="xyz"), "sha256"),
    (lambda d: d.update(sha256=SHA.upper()), "sha256"),
    (lambda d: d.pop("filename"), "filename"),
    (lambda d: d.update(filename=""), "filename"),
    (lambda d: d.pop("title"), "title"),
    (lambda d: d.update(title=3), "title"),
    (lambda d: d.update(path="/etc/passwd"), "unknown"),
])
def test_an_incomplete_document_is_refused(mutate, match):
    p = extracted()
    mutate(p["document"])
    with pytest.raises(ContractError, match=match):
        validate_profile("tso", p)


def test_a_document_that_is_not_a_mapping_is_refused():
    with pytest.raises(ContractError, match="document must be a mapping"):
        validate_profile("tso", extracted(document="tso_code.pdf"))


# --------------------------------------------------------------------------
# validate_profile is the loader's check
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mutate, match", [
    (lambda p: p["q_range_demand"].pop("clause"), "clause"),
    (lambda p: p["rvc_limit_pct"].update(source="guessed"), "source"),
    (lambda p: p["voltage_bands"][0].update(v_min=1.2), "v_min"),
    (lambda p: p.pop("rvc_limit_pct"), "rvc_limit_pct"),
    (lambda p: p["campus_voltage"].update(v_min=1.2), "campus_voltage"),
])
def test_validate_profile_refuses_what_the_loader_refuses(tmp_path, mutate, match):
    p = load_grid_code("eu_rfg_dcc_ce", raw=True) if "campus" not in match else load_grid_code("generic_assumed", raw=True)
    mutate(p)
    with pytest.raises(ContractError, match=match):
        validate_profile("p", copy.deepcopy(p))
    f = tmp_path / "codes.yaml"
    f.write_text(yaml.safe_dump({"profiles": {"p": p}}))
    with pytest.raises(ContractError, match=match):
        load_grid_code("p", path=f)


def test_validate_profile_does_not_touch_its_argument():
    p = extracted()
    before = copy.deepcopy(p)
    validate_profile("tso", p)
    assert p == before


def test_a_profile_that_is_not_a_mapping_is_refused():
    with pytest.raises(ContractError, match="mapping"):
        validate_profile("tso", ["title"])


# --------------------------------------------------------------------------
# project profiles in a directory
# --------------------------------------------------------------------------

def _write(d, name, profile):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(yaml.safe_dump(profile, sort_keys=False))


def test_project_profiles_are_listed_and_loaded_next_to_the_shipped_ones(tmp_path):
    _write(tmp_path, "tso_2026", extracted())
    codes = list_grid_codes(extra_dirs=[tmp_path])
    assert codes["tso_2026"] == "TSO code (draft)"
    assert {"eu_rfg_dcc_ce", "generic_assumed"} <= set(codes)
    p = load_grid_code("tso_2026", extra_dirs=[tmp_path])
    assert p["name"] == "tso_2026" and p["q_range_demand"]["source"] == "extracted"
    # the shipped ones still load with the directory given
    assert load_grid_code("eu_rfg_dcc_ce", extra_dirs=[tmp_path])["name"] == "eu_rfg_dcc_ce"
    # and without it the project profile is unknown
    with pytest.raises(ContractError, match="tso_2026"):
        load_grid_code("tso_2026")
    assert "tso_2026" not in list_grid_codes()


def test_a_missing_directory_lists_nothing_extra(tmp_path):
    assert list_grid_codes(extra_dirs=[tmp_path / "none"]) == list_grid_codes()


def test_a_project_profile_is_validated_on_load(tmp_path):
    p = extracted()
    p["q_range_demand"].pop("quote")
    _write(tmp_path, "broken", p)
    with pytest.raises(ContractError, match="broken.*quote"):
        load_grid_code("broken", extra_dirs=[tmp_path])


def test_a_project_file_that_is_not_a_profile_is_refused(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "junk.yaml").write_text("- just\n- a list\n")
    with pytest.raises(ContractError, match="junk"):
        list_grid_codes(extra_dirs=[tmp_path])
    with pytest.raises(ContractError, match="junk"):
        load_grid_code("junk", extra_dirs=[tmp_path])


@pytest.mark.parametrize("shipped", ["eu_rfg_dcc_ce", "generic_assumed"])
def test_a_project_profile_may_not_shadow_a_shipped_one(tmp_path, shipped):
    _write(tmp_path, shipped, extracted())
    with pytest.raises(ContractError, match=f"{shipped}.*shipped"):
        list_grid_codes(extra_dirs=[tmp_path])
    with pytest.raises(ContractError, match=f"{shipped}.*shipped"):
        load_grid_code(shipped, extra_dirs=[tmp_path])


def test_two_directories_may_not_both_define_a_profile(tmp_path):
    _write(tmp_path / "a", "tso", extracted())
    _write(tmp_path / "b", "tso", extracted())
    with pytest.raises(ContractError, match="tso.*more than once"):
        list_grid_codes(extra_dirs=[tmp_path / "a", tmp_path / "b"])
    with pytest.raises(ContractError, match="tso.*more than once"):
        load_grid_code("tso", extra_dirs=[tmp_path / "a", tmp_path / "b"])


def test_files_whose_names_are_not_profile_ids_are_ignored(tmp_path):
    for name in ("Upper", ".hidden-tmp", "with-dash", "x" * 65):
        _write(tmp_path, name, extracted())
    (tmp_path / "notes.txt").write_text("not yaml")
    assert list_grid_codes(extra_dirs=[tmp_path]) == list_grid_codes()


@pytest.mark.parametrize("name", ["../tso", "tso/../../etc", "/abs", "Tso", "tso-1", "", "a" * 65, "tso\n"])
def test_a_name_that_is_not_a_profile_id_never_reaches_the_disk(tmp_path, name):
    _write(tmp_path, "tso", extracted())
    assert not PROFILE_ID.fullmatch(name)
    with pytest.raises(ContractError, match="unknown grid-code profile"):
        load_grid_code(name, extra_dirs=[tmp_path])


@pytest.mark.parametrize("name", ["tso", "tso_2026", "a", "a" * 64, "0"])
def test_profile_ids_are_lower_case_digits_and_underscores(name):
    assert PROFILE_ID.fullmatch(name)


# --------------------------------------------------------------------------
# confirmation
# --------------------------------------------------------------------------

def test_every_extracted_limit_is_unconfirmed_until_a_person_confirms_it():
    p = validate_profile("tso", extracted())
    assert unconfirmed(p) == ["voltage_bands[0]", "q_range_demand", "rvc_limit_pct", "campus_voltage"]
    assert unconfirmed(load_grid_code("eu_rfg_dcc_ce")) == []


def test_confirming_a_limit_makes_it_code_and_keeps_its_page_and_quote():
    p = validate_profile("tso", extracted())
    out = confirm_limit(p, "q_range_demand")
    assert out["q_range_demand"]["source"] == "code"
    assert out["q_range_demand"]["page"] == 3 and out["q_range_demand"]["quote"] == "quoted text 1"
    assert out["q_range_demand"]["value"] == p["q_range_demand"]["value"]
    assert p["q_range_demand"]["source"] == "extracted"          # the input is untouched
    assert unconfirmed(out) == ["voltage_bands[0]", "rvc_limit_pct", "campus_voltage"]
    out = confirm_limit(out, "voltage_bands[0]")
    assert out["voltage_bands"][0]["source"] == "code"
    for path in ("rvc_limit_pct", "campus_voltage"):
        out = confirm_limit(out, path)
    assert unconfirmed(out) == []
    validate_profile("tso", out)


@pytest.mark.parametrize("path", ["voltage_bands[1]", "voltage_bands[-1]", "voltage_bands", "title", "document",
                                  "q_range_demand.value", "nothing", ""])
def test_confirming_a_limit_the_profile_does_not_have_is_refused(path):
    with pytest.raises(ContractError, match="limit"):
        confirm_limit(validate_profile("tso", extracted()), path)


def test_campus_voltage_cannot_be_confirmed_when_the_profile_has_none():
    p = extracted()
    p.pop("campus_voltage")
    with pytest.raises(ContractError, match="campus_voltage"):
        confirm_limit(validate_profile("tso", p), "campus_voltage")


@pytest.mark.parametrize("source", ["code", "assumed"])
def test_only_an_extracted_limit_can_be_confirmed(source):
    p = load_grid_code("generic_assumed")
    p["q_range_demand"]["source"] = source
    with pytest.raises(ContractError, match="extracted"):
        confirm_limit(p, "q_range_demand")


# --------------------------------------------------------------------------
# an unconfirmed limit reaches every report row that uses it
# --------------------------------------------------------------------------

def test_the_reactive_requirement_carries_the_extracted_tag():
    req = requirement_from(validate_profile("tso", extracted()), p_ref_mw=50.0)
    assert req.source == "extracted" and req.clause == "section 4.1"
    # a study power factor replaces the code's range, and is the study's assumption
    assert requirement_from(validate_profile("tso", extracted()), p_ref_mw=50.0, pf=0.95).source == "assumed"


def _mv(vm=0.97):
    return pd.DataFrame([
        {"period": 2030, "hour": 1, "case": "intact", "bus": "PCC", "vm_pu": 1.0},
        {"period": 2030, "hour": 1, "case": "intact", "bus": "MV1", "vm_pu": vm},
    ])


def test_compliance_rows_on_extracted_limits_say_extracted():
    p = validate_profile("tso", extracted())
    req = requirement_from(p, p_ref_mw=50.0)
    rows = campus_compliance(**inputs(
        bus=_mv(), profile=p,
        requirement={"q_limit_mvar": req.q_limit_mvar, "clause": req.clause, "source": req.source},
    )).set_index("check")
    assert rows.at["pcc_reactive", "source"] == "extracted"
    assert rows.at["pcc_voltage", "source"] == "extracted"
    assert rows.at["campus_voltage", "source"] == "extracted"
    # a confirmed band says code on the same row
    confirmed = confirm_limit(p, "voltage_bands[0]")
    rows = campus_compliance(**inputs(bus=_mv(), profile=confirmed)).set_index("check")
    assert rows.at["pcc_voltage", "source"] == "code"


def test_the_code_band_used_inside_the_campus_stays_extracted_when_it_is_unconfirmed():
    # without campus_voltage the code band is applied inside the campus as a
    # design limit, normally tagged assumed; an unconfirmed band must not be
    # laundered into "assumed"
    p = extracted()
    p.pop("campus_voltage")
    p = validate_profile("tso", p)
    row = campus_compliance(**inputs(bus=_mv(), profile=p)).set_index("check").loc["campus_voltage"]
    assert row["source"] == "extracted" and "design" in row["detail"]
    confirmed = confirm_limit(p, "voltage_bands[0]")
    row = campus_compliance(**inputs(bus=_mv(), profile=confirmed)).set_index("check").loc["campus_voltage"]
    assert row["source"] == "assumed"


# --------------------------------------------------------------------------
# the campus study takes project profiles
# --------------------------------------------------------------------------

def test_the_study_lists_project_profiles(tmp_path):
    _write(tmp_path, "tso", extracted())
    assert grid_code_profiles(extra_dirs=[tmp_path])["tso"] == "TSO code (draft)"
    assert "tso" not in grid_code_profiles()


def test_a_study_run_against_a_project_profile_reports_its_unconfirmed_limits(tmp_path, project):  # noqa: F811
    codes = tmp_path / "codes"
    _write(codes, "tso", extracted())
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    rank_campus(run, k=1)
    out = size_campus(run, profile="tso", profile_dirs=[codes])
    assert out["requirement"]["profile"] == "tso" and out["requirement"]["source"] == "extracted"
    rows = out["compliance"].set_index("check")
    assert rows.at["pcc_reactive", "source"] == "extracted"
    assert rows.at["pcc_voltage", "source"] == "extracted"
    stored = pd.read_csv(run / "campus_compliance.csv").set_index("check")
    assert stored.at["pcc_reactive", "source"] == "extracted"
    with pytest.raises(ContractError, match="tso"):
        size_campus(run, profile="tso")


def test_the_investment_step_takes_project_profiles_too(tmp_path, project):  # noqa: F811
    """C8 and C10 meet here: the investment's re-checked compliance is judged
    against the project's own (unconfirmed) code, and says so."""
    from gridspine.drivers.campus_study import invest_campus
    codes = tmp_path / "codes"
    _write(codes, "tso", extracted())
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(project).spec, project)
    rank_campus(run, k=1)
    rows = invest_campus(run, profile="tso", profile_dirs=[codes])["compliance"].set_index("check")
    assert rows.at["pcc_reactive", "source"] == "extracted"
    with pytest.raises(ContractError, match="tso"):
        invest_campus(run, profile="tso")


# --------------------------------------------------------------------------
# voltage coverage: which nominal voltages below the top band have no band
# --------------------------------------------------------------------------

def _bands(*spec):
    """A profile of ``(kv_min, kv_max[, inclusive])`` bands."""
    return {"voltage_bands": [
        {"kv_min": float(lo), "kv_max": float(hi), **({"kv_max_inclusive": True} if rest and rest[0] else {}),
         "v_min": 0.9, "v_max": 1.1, "clause": "c", "source": "assumed"}
        for lo, hi, *rest in spec]}


def test_every_shipped_profile_covers_every_voltage_up_to_its_top_band():
    for name in list_grid_codes():
        assert uncovered_kv_ranges(load_grid_code(name)) == [], name


def test_a_profile_that_starts_at_110_kv_leaves_0_to_110_uncovered():
    """The DCC states bands from 110 kV upward, so a profile drafted from it
    has nothing below."""
    dcc = _bands((110, 300), (300, 400, True))
    assert uncovered_kv_ranges(dcc) == [(0.0, 110.0, True)]


def test_a_gap_in_the_middle_is_reported():
    assert uncovered_kv_ranges(_bands((0, 20), (110, 300))) == [(20.0, 110.0, True)]


def test_several_gaps_are_reported_in_order_whatever_the_order_of_the_bands():
    p = _bands((300, 400), (50, 110), (0, 20))
    assert uncovered_kv_ranges(p) == [(20.0, 50.0, True), (110.0, 300.0, True)]


def test_nothing_is_uncovered_above_the_top_band():
    """Only 0 up to the highest band is asked for: a profile ending at 400 kV
    inclusive is complete, whether or not 500 kV exists."""
    assert uncovered_kv_ranges(_bands((0, 110), (110, 400, True))) == []
    assert uncovered_kv_ranges(_bands((0, 400))) == []


def test_an_inclusive_edge_covers_its_own_point_and_the_gap_starts_above_it():
    """A band ending at 110 kV inclusive covers 110 kV, so the next band's gap
    is open at 110: 110 kV itself is not uncovered."""
    assert uncovered_kv_ranges(_bands((0, 110, True), (200, 400))) == [(110.0, 200.0, False)]


def test_an_exclusive_edge_leaves_its_own_point_to_the_gap_or_the_next_band():
    """A band ending at 110 kV exclusive does not cover 110 kV. A band starting
    there covers it (no gap); one starting higher leaves 110 kV in the gap."""
    assert uncovered_kv_ranges(_bands((0, 110), (110, 400))) == []
    assert uncovered_kv_ranges(_bands((0, 110), (110.5, 400))) == [(110.0, 110.5, True)]


def test_an_inclusive_edge_met_by_the_next_band_is_not_a_gap():
    assert uncovered_kv_ranges(_bands((0, 110, True), (110, 400))) == []


def test_a_first_band_above_zero_leaves_zero_up_to_it_uncovered():
    assert uncovered_kv_ranges(_bands((0.4, 110))) == [(0.0, 0.4, True)]


def test_a_band_inside_another_does_not_hide_a_gap_after_it_or_make_one():
    p = _bands((0, 100), (10, 20), (150, 200))
    assert uncovered_kv_ranges(p) == [(100.0, 150.0, True)]


def test_uncovered_ranges_are_exactly_where_band_for_finds_no_band():
    """The helper and the engine's lookup agree at every probe voltage,
    including each edge."""
    profiles = [_bands((110, 300), (300, 400, True)), _bands((0, 20), (110, 300)),
                _bands((0, 110, True), (200, 400)), _bands((0, 110), (110.5, 400)),
                _bands((0, 110), (110, 400))]
    probes = [0.0, 0.2, 20.0, 50.0, 109.99, 110.0, 110.25, 110.5, 199.0, 200.0, 300.0, 399.0, 400.0]
    for p in profiles:
        top = max(b["kv_max"] for b in p["voltage_bands"])
        top_inclusive = any(b.get("kv_max_inclusive") for b in p["voltage_bands"] if b["kv_max"] == top)
        gaps = uncovered_kv_ranges(p)
        for kv in (k for k in probes if k < top or (k == top and top_inclusive)):
            in_gap = any((lo <= kv if inc else lo < kv) and kv < hi for lo, hi, inc in gaps)
            try:
                band_for(p, kv)
                has_band = True
            except ContractError:
                has_band = False
            assert has_band != in_gap, (p["voltage_bands"], kv)


def test_uncovered_kv_ranges_does_not_touch_its_argument_and_a_profile_without_bands_has_none():
    p = _bands((110, 300))
    before = copy.deepcopy(p)
    uncovered_kv_ranges(p)
    assert p == before
    assert uncovered_kv_ranges({"voltage_bands": []}) == []


def test_validate_profile_and_the_loader_still_accept_a_profile_with_a_gap(tmp_path):
    """Coverage is asked of a published profile (the backend), not at load: a
    partial test profile stays loadable."""
    p = load_grid_code("generic_assumed", raw=True)
    p["voltage_bands"][0].update(kv_min=110.0)
    assert uncovered_kv_ranges(validate_profile("partial", p)) == [(0.0, 110.0, True)]
    (tmp_path / "partial.yaml").write_text(yaml.safe_dump(p))
    assert load_grid_code("partial", extra_dirs=(tmp_path,))["name"] == "partial"

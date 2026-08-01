"""Registry + applicability invariants. Pure-Python: no network, no client."""
import pytest

from services.asset_results import applicability as ap
from services.asset_results import registry as reg


def test_metric_ids_are_unique_within_class_and_category():
    """
    Ids are scoped to a (class, category) pair, not global: `p0` means one
    thing on a Line and another on a Link, and six classes declare
    `mu_upper`. Everything downstream — the response's `metrics`/`columns`
    arrays, the frontend tick-set memory, the chat tool's `metrics` argument,
    the workbook's per-category sheets — resolves ids inside one class and one
    category, so this is the uniqueness that actually has to hold.
    """
    for cls in reg.ALL_CLASSES:
        for cat in reg.CATEGORY_IDS:
            ids = [m.id for m in reg.metrics_for(cls, cat)]
            assert len(ids) == len(set(ids)), f"duplicate ids in {cls}/{cat}"


def test_metric_for_disambiguates_a_shared_id_by_class():
    line_p0 = reg.metric_for("Line", "p0")
    link_p0 = reg.metric_for("Link", "p0")
    assert line_p0 is not None and link_p0 is not None
    assert line_p0 is not link_p0
    # Same id, different meaning — which is exactly why `metric_for` exists.
    assert line_p0.category == "loadflow"
    assert link_p0.category == "dispatch"
    assert reg.metric_for("Bus", "p0") is None


def test_every_metric_lands_in_a_known_category_and_classes():
    for m in reg.METRICS:
        assert m.category in reg.CATEGORY_IDS, f"{m.id}: bad category {m.category}"
        assert m.classes, f"{m.id}: declares no classes"
        for c in m.classes:
            assert c in reg.ALL_CLASSES, f"{m.id}: unknown class {c}"


def test_every_metric_is_computable_and_well_formed():
    for m in reg.METRICS:
        assert callable(m.compute), f"{m.id}: compute is not callable"
        assert m.kind in ("series", "scalar"), f"{m.id}: bad kind {m.kind}"
        assert m.origin in ("output", "input", "derived"), f"{m.id}: bad origin"
        if m.kind == "series":
            assert m.unit != "" or m.id == "status", f"{m.id}: series needs a unit"
        if m.origin == "derived":
            assert m.formula, f"{m.id}: derived metrics must carry a formula"


def test_summary_covers_every_component_class():
    for c in reg.ALL_CLASSES:
        assert reg.metrics_for(c, "summary"), f"{c} has no summary metrics"


def test_metric_by_id_round_trips_and_misses_cleanly():
    assert reg.metric_by_id("p").id == "p"
    assert reg.metric_by_id("no_such_metric") is None


# Which categories each class is expected to populate. The empty ones are
# structural, not "not built yet": a Line does not dispatch, a Bus does not
# store energy, a Load has no optimisable capacity. Pinning the whole matrix
# here means deleting a class's metrics by accident fails a test instead of
# quietly re-emptying a tab.
EXPECTED_EMPTY: dict[str, set[str]] = {
    "Bus": {"storage", "emissions"},
    "Generator": {"storage"},
    "Load": {"capacity", "storage", "emissions"},
    "Line": {"dispatch", "storage", "emissions"},
    "Transformer": {"dispatch", "storage", "emissions"},
    "Link": {"storage"},
    "StorageUnit": {"emissions"},
    "Store": {"emissions"},
}


@pytest.mark.parametrize("cls", list(EXPECTED_EMPTY))
def test_every_class_populates_exactly_the_categories_that_apply(cls):
    for cat in reg.CATEGORY_IDS:
        members = reg.metrics_for(cls, cat)
        if cat in EXPECTED_EMPTY[cls]:
            assert members == (), f"{cls}/{cat} should be structurally empty"
        else:
            assert members, f"{cls} lacks {cat} metrics — its tab would be empty"


@pytest.mark.parametrize("cls", list(EXPECTED_EMPTY))
def test_every_headline_id_resolves_to_a_scalar_metric_of_that_class(cls):
    """
    A headline is a pointer into the registry, so a typo or a renamed metric
    must fail here rather than silently dropping a row from the Summary tab.
    """
    ids = reg.headline_ids(cls)
    assert ids, f"{cls} has no headline KPIs"
    for mid in ids:
        m = reg.metric_for(cls, mid)
        assert m is not None, f"{cls} headline '{mid}' is not a metric of {cls}"
        assert m.kind == "scalar", f"{cls} headline '{mid}' is a series"


def test_resolve_metric_returns_na_for_a_class_the_metric_excludes():
    curtailment = reg.metric_by_id("curtailment")
    st = ap.resolve_metric(curtailment, "Line", {})
    assert st.status == "na"
    assert st.remedy is None, "na must never carry a remedy"
    assert "Line" in st.reason


def test_resolve_metric_surfaces_the_first_unmet_precondition():
    status_metric = reg.metric_by_id("status")
    blocked = ap.Status(
        "blocked", "unit commitment is not enabled on Gas 1",
        ap.Remedy("open_properties", "Enable committable"),
    )
    st = ap.resolve_metric(status_metric, "Generator", {reg.REQ_COMMITTABLE: blocked})
    assert st.status == "blocked"
    assert st.remedy.action == "open_properties"


def test_resolve_metric_is_ok_when_every_precondition_is_ok():
    p = reg.metric_by_id("p")
    precond = {r: ap.OK for r in (reg.REQ_DISPATCH, reg.REQ_AC_PF, reg.REQ_DUALS)}
    assert ap.resolve_metric(p, "Generator", precond).status == "ok"


def test_resolve_category_is_na_when_the_class_has_no_metrics_there():
    # Storage, not loadflow: a Generator genuinely has NO storage metric.
    # Generator/loadflow is the spec's partial case — see the next test.
    st = ap.resolve_category("storage", "Generator", {})
    assert st.status == "na"
    assert "store energy" in st.reason


def test_generator_loadflow_is_blocked_not_na_because_reactive_power_applies():
    """
    `q` exists on a Generator but only in the AC PF snapshot, so the
    category is blocked until that stage runs — never n/a.
    """
    blocked = ap.Status("blocked", "AC power flow has not been run",
                        ap.Remedy("run_ac_pf", "Run AC power flow"))
    st = ap.resolve_category("loadflow", "Generator", {reg.REQ_AC_PF: blocked})
    assert st.status == "blocked"
    assert st.remedy.action == "run_ac_pf"


def test_a_reason_every_member_shares_beats_the_generic_one():
    """
    When every member of a category is n/a for the SAME reason, that reason
    is what the user needs — not the generic category fallback.

    Live case: a Link whose carrier row is missing entirely. All three of its
    emissions metrics require REQ_CO2, which resolves n/a with "Link has no
    carrier". The generic fallback would say "Link does not emit CO₂", which
    is wrong — a gas link emits plenty; this one just has no carrier attached.
    """
    st = ap.resolve_category("emissions", "Link", {
        reg.REQ_CO2: ap.Status("na", "Link has no carrier"),
    })
    assert st.status == "na"
    assert st.reason == "Link has no carrier"
    assert st.remedy is None
    # The generic fallback is the thing this must beat.
    assert "does not emit" in ap.category_na_reason("emissions", "Link")


def test_resolve_category_is_ok_when_any_member_metric_is_ok():
    precond = {reg.REQ_DISPATCH: ap.OK}
    assert ap.resolve_category("dispatch", "Generator", precond).status == "ok"


def test_resolve_category_is_blocked_when_no_member_is_ok():
    blocked = ap.Status("blocked", "network has not been solved",
                        ap.Remedy("run_simulation", "Run simulation"))
    st = ap.resolve_category("dispatch", "Generator", {reg.REQ_DISPATCH: blocked})
    assert st.status == "blocked"
    assert st.remedy.action == "run_simulation"


@pytest.mark.parametrize("cat,cls,needle", [
    # Every pair here is one a real request actually reaches — see
    # EXPECTED_EMPTY. A reason for a category the class DOES populate would
    # be dead text no user can ever see.
    ("storage", "Generator", "store energy"),
    ("storage", "Bus", "store energy"),
    ("dispatch", "Line", "dispatch"),
    ("capacity", "Load", "capacity"),
    ("emissions", "Load", "CO"),
    ("emissions", "Transformer", "CO"),
])
def test_category_na_reasons_are_specific(cat, cls, needle):
    assert needle in ap.category_na_reason(cat, cls)


@pytest.mark.parametrize("cls", list(EXPECTED_EMPTY))
def test_every_structurally_empty_category_gets_a_reason(cls):
    """No tab may grey out without telling the user why."""
    for cat in EXPECTED_EMPTY[cls]:
        st = ap.resolve_category(cat, cls, {})
        assert st.status == "na"
        assert st.reason, f"{cls}/{cat} is n/a with no reason"
        assert st.remedy is None, "na must never carry a remedy"

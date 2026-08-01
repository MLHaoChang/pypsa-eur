"""Registry + applicability invariants. Pure-Python: no network, no client."""
import pytest

from services.asset_results import applicability as ap
from services.asset_results import registry as reg


def test_every_metric_id_is_unique():
    ids = [m.id for m in reg.METRICS]
    assert len(ids) == len(set(ids)), "duplicate metric ids"


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


def test_generator_has_every_category_except_structurally_absent_storage():
    """
    Generator's Phase-1 promise: real metrics in every category except
    `storage`, which is structurally n/a — a generator has no state of
    charge. `storage` is asserted separately by the resolve_category test.
    """
    for cat in reg.CATEGORY_IDS:
        members = reg.metrics_for("Generator", cat)
        if cat == "storage":
            assert members == (), "Generator must have NO storage metrics"
            continue
        assert members, f"Generator lacks {cat}"
        assert any(reg.REQ_NOT_YET not in m.requires for m in members), \
            f"Generator's {cat} is placeholders only — phase 1 needs real metrics"


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
    A phase-2 placeholder must say 'not yet available', not 'Dispatch does
    not apply to Load' — Loads DO dispatch, it is simply not wired up yet.
    """
    st = ap.resolve_category("dispatch", "Load", {
        reg.REQ_NOT_YET: ap.Status(
            "na", "not yet available — arrives in a later phase of this feature"),
    })
    assert st.status == "na"
    assert "not yet available" in st.reason


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
    ("storage", "Generator", "store energy"),
    ("dispatch", "Bus", "dispatch"),
    ("capacity", "Bus", "capacity"),
    ("emissions", "Load", "CO"),
])
def test_category_na_reasons_are_specific(cat, cls, needle):
    assert needle in ap.category_na_reason(cat, cls)

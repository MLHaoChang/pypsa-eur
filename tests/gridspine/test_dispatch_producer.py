import pytest

from gridspine.ingest.pandapower_source import load_case39
from gridspine.producers.pypsa_nodal import run_uc, to_dispatch_table, to_pypsa
from gridspine.schema.dispatch import validate_dispatch


@pytest.fixture(scope="module")
def solved():
    n = to_pypsa(load_case39(), snapshots=24)
    return run_uc(n)


def test_dispatch_table_validates(solved):
    table = to_dispatch_table(solved)
    validate_dispatch(table)  # raises on violation
    assert len(table) == 10 * 24


def test_energy_balance_per_hour(solved):
    table = to_dispatch_table(solved)
    gen_h0 = table[table["hour"] == 0]["p_mw"].sum()
    load_h0 = float(solved.loads_t.p_set.iloc[0].sum())
    assert abs(gen_h0 - load_h0) / load_h0 < 0.01


def test_status_is_binary_commitment_not_prorata(solved):
    table = to_dispatch_table(solved)
    # In the load valley at least one committable unit must be OFF -- this
    # binary on/off is the metric the pipeline exists to preserve (min-
    # inertia hours need real UC, not scaled-down everything-online).
    #
    # Restricted to COMMITTABLE units on purpose. The non-committable slack
    # import is priced above every thermal unit and so sits at zero through
    # the whole valley; an unrestricted assertion passes on the slack alone
    # and stays green even when the committed status is forced to 1 for every
    # thermal unit, i.e. it does not test unit commitment at all.
    committable = set(solved.generators.index[solved.generators["committable"]])
    valley = table[(table["hour"] == 3) & table["unit_id"].isin(committable)]
    assert not valley.empty
    assert (valley["status"] == 0).any()


def test_non_committable_status_is_inferred_from_output(solved):
    # PyPSA carries an all-zero `status` column for the non-committable slack
    # unit even in the hours it genuinely imports. Reading status from that
    # column would emit status 0 next to p_mw > 0, which validate_dispatch
    # rejects outright -- the `committable` guard in to_dispatch_table is what
    # keeps the producer's output admissible under the contract.
    table = to_dispatch_table(solved)
    slack = table[table["unit_id"] == "SLK_BUS_31"]
    producing = slack[slack["p_mw"] > 1e-4]
    assert not producing.empty, "slack never dispatched: this path is untested"
    assert (producing["status"] == 1).all()

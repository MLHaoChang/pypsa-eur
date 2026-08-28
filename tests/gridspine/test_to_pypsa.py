from gridspine.ingest.pandapower_source import load_case39
from gridspine.producers.pypsa_nodal import LOAD_SHAPE, to_pypsa


def test_identity_mapping_bus_names():
    net = load_case39()
    n = to_pypsa(net)
    assert set(n.buses.index) == set(net.bus["name"])


def test_all_units_present_and_committable_flags():
    net = load_case39()
    n = to_pypsa(net)
    assert len(n.generators) == 10
    assert int(n.generators["committable"].sum()) == 9  # ext_grid unit is not
    slack_units = [u for u in n.generators.index if u.startswith("SLK_")]
    assert len(slack_units) == 1
    assert not n.generators.loc[slack_units[0], "committable"]


def test_snapshots_and_load_shape():
    net = load_case39()
    n = to_pypsa(net, snapshots=24)
    assert len(n.snapshots) == 24
    assert len(LOAD_SHAPE) == 24
    total_p = float(net.load["p_mw"].sum())
    peak = float(n.loads_t.p_set.sum(axis=1).max())
    assert abs(peak - total_p * max(LOAD_SHAPE)) < 1.0


def test_branch_counts():
    net = load_case39()
    n = to_pypsa(net)
    assert len(n.lines) == len(net.line)
    assert len(n.transformers) == len(net.trafo)

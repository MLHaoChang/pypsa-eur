import pandapower as pp
import pytest

from gridspine.ingest.pandapower_source import load_case39, registry_from_net
from gridspine.schema.contracts import ContractError


def test_case39_has_canonical_names():
    net = load_case39()
    assert len(net.bus) == 39
    assert list(net.bus["name"])[:2] == ["BUS_01", "BUS_02"]
    assert net.bus["name"].is_unique
    assert (len(net.gen) + len(net.ext_grid)) == 10


def test_registry_covers_all_units():
    net = load_case39()
    reg = registry_from_net(net)
    assert len(reg) == 10
    assert (reg["kind"] == "ext_grid").sum() == 1
    assert set(reg["bus"]).issubset(set(net.bus["name"]))


def test_case39_load_flow_converges():
    net = load_case39()
    pp.runpp(net)
    assert net.converged


def test_load_case39_actually_validates(monkeypatch):
    """load_case39 must WIRE the validator, not merely import it. Shrinking the
    cap makes every canonical name illegal, so a load that reaches the end
    without raising proves the validate_canonical call is missing."""
    import gridspine.schema.network as netmod

    monkeypatch.setattr(netmod, "MAX_NAME_LEN", 5)
    with pytest.raises(ContractError):
        load_case39()

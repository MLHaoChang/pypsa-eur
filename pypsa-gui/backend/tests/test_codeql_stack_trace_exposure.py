"""
CodeQL ``py/stack-trace-exposure`` on PR #60 — three flows that could put an
exception's own text (a server file path, a numpy/pandas internal) into an
HTTP response.

Each test plants the bad input the alert is about and asserts the response
carries a plain message and none of the planted internal text, while the
real exception still reaches the server log. See
docs/superpowers/findings/2026-09-29-codeql-stack-trace-exposure-pr60.md.
"""
from __future__ import annotations

import json
import logging
import pathlib

import pytest

from services.adequacy import stress as ST

FAKE_PATH = "/srv/secret-host/projects/acme/adequacy_stress_scenarios.json"
FAKE_PACK_PATH = "/srv/secret-host/backend/data/eh_class_c/leaky_pack.json"


def _raise_oserror_for(monkeypatch, filename: str, fake_path: str):
    """``Path.read_text`` raises an OSError naming ``fake_path`` for one file."""
    real = pathlib.Path.read_text

    def read_text(self, *a, **kw):
        if self.name == filename:
            raise PermissionError(13, "Permission denied", fake_path)
        return real(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)


def _no_internals(text: str, *needles: str) -> None:
    for needle in ("/srv/secret-host", "Permission denied", "Errno",
                   "Expecting", "Traceback", *needles):
        assert needle not in text, f"{needle!r} leaked into {text!r}"


# ── alert 1: the stress-scenario registry ───────────────────────────────────


def test_unreadable_registry_names_no_path(tmp_path, monkeypatch, caplog):
    (tmp_path / ST.SIDECAR_NAME).write_text("{}")
    _raise_oserror_for(monkeypatch, ST.SIDECAR_NAME, FAKE_PATH)
    with caplog.at_level(logging.WARNING):
        scenarios, error = ST.load_scenarios_checked(tmp_path)
    assert scenarios == []
    assert error == "stress-scenario registry unreadable: could not be read"
    _no_internals(error)
    assert FAKE_PATH in caplog.text          # the real cause is logged


def test_corrupt_registry_gives_line_and_column_only(tmp_path, caplog):
    (tmp_path / ST.SIDECAR_NAME).write_text('{\n  "a": 1,\n  oops\n}')
    with caplog.at_level(logging.WARNING):
        _scenarios, error = ST.load_scenarios_checked(tmp_path)
    assert error == ("stress-scenario registry unreadable: "
                     "not valid JSON (line 3, column 3)")
    _no_internals(error)
    assert "Expecting" in caplog.text


def test_registry_route_does_not_leak_the_path(client, install_network,
                                               tmp_projects_dir, monkeypatch):
    from tests.test_adequacy_http import _network
    install_network(_network(), name="PP3")
    client.post("/api/projects/PP3", params={"force": True, "rebind": True})
    r = client.put("/api/projects/PP3/stress_scenarios", json={"scenarios": []})
    assert r.status_code == 200, r.text
    _raise_oserror_for(monkeypatch, ST.SIDECAR_NAME, FAKE_PATH)
    r = client.get("/api/projects/PP3/stress_scenarios")
    assert r.status_code == 200
    assert "unreadable" in r.json()["error"]
    _no_internals(r.text)


# ── alert 2: the shipped profile-pack listing ───────────────────────────────


def test_corrupt_pack_is_listed_without_parser_internals(tmp_path, monkeypatch,
                                                         caplog):
    (tmp_path / "bad_pack.json").write_text("{not json")
    monkeypatch.setattr(ST, "PROFILE_PACK_DIR", tmp_path)
    with caplog.at_level(logging.WARNING):
        packs = {p["id"]: p for p in ST.list_profile_packs()}
    assert packs["bad_pack"]["error"] == (
        "profile_pack 'bad_pack' unreadable: not valid JSON (line 1, column 2)")
    _no_internals(packs["bad_pack"]["error"])
    assert "Expecting" in caplog.text


def test_unreadable_pack_names_no_path(tmp_path, monkeypatch, caplog):
    (tmp_path / "leaky_pack.json").write_text("{}")
    monkeypatch.setattr(ST, "PROFILE_PACK_DIR", tmp_path)
    _raise_oserror_for(monkeypatch, "leaky_pack.json", FAKE_PACK_PATH)
    with caplog.at_level(logging.WARNING):
        packs = {p["id"]: p for p in ST.list_profile_packs()}
    assert packs["leaky_pack"]["error"] == (
        "profile_pack 'leaky_pack' unreadable: could not be read")
    assert FAKE_PACK_PATH in caplog.text


def test_pack_route_does_not_leak_the_path(client, install_network,
                                           tmp_projects_dir, tmp_path,
                                           monkeypatch):
    from tests.test_adequacy_http import _network
    install_network(_network(), name="PP3")
    client.post("/api/projects/PP3", params={"force": True, "rebind": True})
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "leaky_pack.json").write_text("{}")
    (packs / "bad_pack.json").write_text("{not json")
    monkeypatch.setattr(ST, "PROFILE_PACK_DIR", packs)
    _raise_oserror_for(monkeypatch, "leaky_pack.json", FAKE_PACK_PATH)
    r = client.get("/api/projects/PP3/stress_profile_packs")
    assert r.status_code == 200, r.text
    errors = {p["id"]: p.get("error") for p in r.json()["packs"]}
    assert "unreadable" in errors["leaky_pack"]
    assert "unreadable" in errors["bad_pack"]
    _no_internals(r.text)


# ── alert 3: /results/eh_readiness ──────────────────────────────────────────

NUMPY_TEXT = ("operands could not be broadcast together with shapes (3,) (4,) "
              "at /opt/venv/lib/python3.12/site-packages/numpy/core/_methods.py")


def _hub(install_network):
    from tests.test_energy_hub_frontier_fmea import _feeder_hub
    install_network(_feeder_hub())


def test_readiness_hides_an_internal_value_error(client, install_network,
                                                 monkeypatch, caplog):
    _hub(install_network)
    import services.adequacy.eh_readiness as R

    def boom(*a, **kw):
        raise ValueError(NUMPY_TEXT)

    monkeypatch.setattr(R, "eh_readiness", boom)
    with caplog.at_level(logging.ERROR):
        r = client.get("/api/results/eh_readiness",
                       params={"archetype": "off_grid"})
    assert r.status_code == 422
    assert r.json()["detail"] == (
        "readiness could not be computed for this network")
    _no_internals(r.text, "broadcast", "site-packages", "numpy")
    assert "broadcast" in caplog.text          # logged server-side


@pytest.mark.parametrize("make_exc", [
    lambda: __import__("services.adequacy.archetypes",
                       fromlist=["x"]).ArchetypePackError("pack refuses: X"),
    lambda: __import__("services.adequacy.archetypes",
                       fromlist=["x"]).HubBoundaryError("pack refuses: X"),
    lambda: __import__("services.adequacy.dtc",
                       fromlist=["x"]).DtcConfigError("pack refuses: X"),
    lambda: __import__("services.adequacy.levers",
                       fromlist=["x"]).LeverScenarioError("pack refuses: X"),
    lambda: __import__("services.adequacy.redundancy",
                       fromlist=["x"]).RedundancyScenarioError("pack refuses: X"),
])
def test_readiness_surfaces_the_repos_own_refusals(client, install_network,
                                                   monkeypatch, make_exc):
    _hub(install_network)
    import services.adequacy.eh_readiness as R

    def refuse(*a, **kw):
        raise make_exc()

    monkeypatch.setattr(R, "eh_readiness", refuse)
    r = client.get("/api/results/eh_readiness", params={"archetype": "off_grid"})
    assert r.status_code == 422
    assert r.json()["detail"] == "pack refuses: X"


def test_readiness_still_names_a_bad_stage(client, install_network):
    _hub(install_network)
    r = client.get("/api/results/eh_readiness", params={
        "archetype": "off_grid", "stages": "bogus"})
    assert r.status_code == 422
    assert "unknown EH pipeline stage(s) ['bogus']" in r.json()["detail"]

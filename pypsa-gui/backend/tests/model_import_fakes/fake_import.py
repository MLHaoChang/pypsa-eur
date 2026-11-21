"""A fake model importer: the file's first word says what it does (tests only)."""
from __future__ import annotations

import time

import pypsa

from gridspine.ingest.import_result import Assumed, ImportFormatError, ImportReport, ImportResult, Unmapped


def read_fake(paths, options):
    word = paths[0].read_text().split()[0] if paths[0].read_text().split() else ""
    if word == "refuse":
        raise ImportFormatError(f"{paths[0].name} is not a fake model")
    if word == "boom":
        raise RuntimeError("the reader broke")
    if word == "sleep":
        time.sleep(120)
    if word == "eat":
        hog = bytearray(8 * 1024 * 1024 * 1024)  # past the child's address-space limit
        del hog
    n = pypsa.Network(name=str(options.get("model") or "Fake grid"))
    n.add("Bus", ["North", "South"], v_nom=110.0)
    n.add("Line", "North-South", bus0="North", bus1="South", x=0.1, s_nom=100.0)
    report = ImportReport(
        source_format="fake",
        source_files=[p.name for p in paths],
        counts={"Buses": 2, "Lines": 1},
        unmapped=[Unmapped("Relay R1", "protection relays are not modelled")],
        assumed=[Assumed("Line North-South", "Thermal rating", 100.0, "the file gives none")],
    )
    sidecars = {"psse_case.json": {"buses": 2}}
    if word == "meta":
        sidecars["metadata.json"] = {"bus_count": 99}
    if word == "importmeta":
        sidecars["metadata.json"] = {"import": {"format": "fake", "names": {"North": "N1"}}}
    if word == "ownmeta":
        sidecars["metadata.json"] = {"scenario_type": "planning"}
    if word == "stray":
        sidecars["notes.json"] = {"x": 1}
    return ImportResult(network=n, report=report, sidecars=sidecars)

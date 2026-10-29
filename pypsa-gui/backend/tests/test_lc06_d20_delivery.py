"""LC-06 S11, LC-D20: the bytes the export tools deliver, parsed independently.

The seam is the copilot's export tools (``export_network_nc``, ``export_csv_bundle``,
``export_excel``, ``export_matpower``, ``build_delivery``) and the authenticated
download each one's delivery names. An HTTP 200 and a URL prove nothing; the bytes
are fetched and read with ``pypsa``, ``zipfile``, ``pandas`` and plain text.

Hand oracle, the baseline project: one bus B1 (110 kV), G1 20 MW at 10 EUR/MWh,
L1 5 MW, two hourly snapshots.
- the delivery's ``sha256`` and ``size`` are those of the downloaded bytes, and its
  ``file_id`` is the head of that hash;
- ``network.nc`` opened with ``pypsa`` has B1, G1 (p_nom 20, marginal cost 10), L1
  (p_set 5) and two snapshots;
- the CSV bundle's ``generators.csv``, ``loads.csv`` and ``snapshots.csv`` and the
  workbook's ``generators`` sheet carry the same rows;
- MATPOWER: bus 1 has Pd 5 and baseKV 110, the generator row has Pmax 20, its
  cost row has c1 10;
- a bundle of two deliveries is a zip of ``<file_id>_<name>`` members and a manifest,
  each member byte for byte the delivery it names.
"""
# ruff: noqa: F811 -- shared public harness fixtures
import hashlib
import io
import re
import zipfile

import pandas as pd
import pypsa
import pytest

from services import chat_tools
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


def delivered(client, result):
    """The bytes behind a delivery, after checking the delivery describes them."""
    delivery = chat_tools.get_file_delivery(result["file_id"])
    response = client.get(delivery["download_url"])
    assert response.status_code == 200, response.text
    body = response.content
    assert delivery["size"] == len(body)
    assert delivery["sha256"] == hashlib.sha256(body).hexdigest()
    assert delivery["file_id"] == delivery["sha256"][:16]
    assert response.headers["content-type"].startswith(delivery["mime"])
    return body


def test_the_network_file_is_the_hand_network(baseline, client, tmp_path):
    path = tmp_path / "network.nc"
    path.write_bytes(delivered(client, chat_tools.export_network_nc()))
    n = pypsa.Network(str(path))
    assert list(n.buses.index) == ["B1"] and float(n.buses.at["B1", "v_nom"]) == 110.0
    assert float(n.generators.at["G1", "p_nom"]) == 20.0
    assert float(n.generators.at["G1", "marginal_cost"]) == 10.0
    assert float(n.loads.at["L1", "p_set"]) == 5.0
    assert len(n.snapshots) == 2


def test_the_csv_bundle_and_the_workbook_carry_the_same_rows(baseline, client):
    bundle = zipfile.ZipFile(io.BytesIO(delivered(client, chat_tools.export_csv_bundle())))
    generators = pd.read_csv(io.BytesIO(bundle.read("generators.csv"))).set_index("name")
    loads = pd.read_csv(io.BytesIO(bundle.read("loads.csv"))).set_index("name")
    assert (generators.at["G1", "p_nom"], generators.at["G1", "marginal_cost"]) == (20.0, 10.0)
    assert loads.at["L1", "p_set"] == 5.0
    assert len(pd.read_csv(io.BytesIO(bundle.read("snapshots.csv")))) == 2

    sheets = pd.read_excel(io.BytesIO(delivered(client, chat_tools.export_excel())), sheet_name=None)
    book = sheets["generators"].set_index("name")
    assert (book.at["G1", "p_nom"], book.at["G1", "marginal_cost"]) == (20.0, 10.0)
    assert sheets["loads"].set_index("name").at["L1", "p_set"] == 5.0
    assert len(sheets["snapshots"]) == 2


def test_the_matpower_case_holds_the_hand_numbers(baseline, client):
    text = delivered(client, chat_tools.export_matpower()).decode()

    def rows(block):
        body = re.search(rf"mpc\.{block} = \[\n(.*?)\];", text, re.S).group(1)
        return [[float(v) for v in line.strip().rstrip(";").split()] for line in body.splitlines() if line.strip()]

    (bus,), (gen,), (cost,) = rows("bus"), rows("gen"), rows("gencost")
    assert (bus[0], bus[2], bus[9]) == (1.0, 5.0, 110.0)    # bus_i, Pd, baseKV
    assert gen[0] == 1.0 and gen[8] == 20.0                 # bus, Pmax
    assert cost[4] == 10.0                                  # c1
    assert rows("branch") == []


def test_a_bundle_of_two_deliveries_holds_each_file_byte_for_byte(baseline, client):
    first = chat_tools.export_network_nc()
    second = chat_tools.export_matpower()
    bundle = chat_tools.build_delivery(file_ids=[first["file_id"], second["file_id"]])
    archive = zipfile.ZipFile(io.BytesIO(delivered(client, bundle)))
    names = {first["file_id"] + "_network.nc", second["file_id"] + "_network.m"}
    assert set(archive.namelist()) == names | {"manifest.json"}
    assert archive.read(first["file_id"] + "_network.nc") == delivered(client, first)
    assert archive.read(second["file_id"] + "_network.m") == delivered(client, second)

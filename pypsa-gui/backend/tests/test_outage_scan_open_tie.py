# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""A normally-open tie (p_max_pu 0) is no unit missing outage data (walkthrough 12)."""

from __future__ import annotations

import pypsa

from services.adequacy.eh_readiness import outage_scan


def _net(p_max_pu: float) -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Bus", "mainland")
    n.add("Link", "tie", bus0="mainland", bus1="site", p_nom=100, p_max_pu=p_max_pu)
    return n


def test_a_normally_open_import_tie_is_not_missing_outage_data():
    _, missing = outage_scan(_net(0.0), ["tie"])
    assert missing == []


def test_a_closed_import_tie_without_outage_data_is_still_missing():
    _, missing = outage_scan(_net(1.0), ["tie"])
    assert [m["name"] for m in missing] == ["tie"]

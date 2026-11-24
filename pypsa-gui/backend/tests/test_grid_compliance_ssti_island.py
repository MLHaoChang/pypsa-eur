# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""GC-08b (GK A2 U4): the SSTI UIF screen's island rows leave every GC-08 reader as it was. For an
islanding campus, the scorecard's UIF row and the pack's judged UIF rows equal what they read from the same
run with the island rows taken out (the table master wrote); the pack prints the island rows "not judged"."""

import pandas as pd
from gridspine.drivers.campus_study import SSTI_UIF_CSV
from gridspine.static import ssti_uif as su

from services.grid_compliance import ssti_source as S
from tests.test_grid_compliance_ssti_source import _pack, _project
from tests.test_grid_readiness_ssti import _card, _grid_run_with_open_tie


def test_island_rows_leave_the_card_and_the_pack_as_master(tmp_path, monkeypatch):
    run, n = _grid_run_with_open_tie(tmp_path, 2000.0)
    table = pd.read_csv(run / SSTI_UIF_CSV)
    island = table[table["role"] == su.ROLE_ISLAND]
    assert set(island["case"]) == set(su.ISLAND_CASES) and len(island) > 0  # the campus islands
    project = _project(monkeypatch, tmp_path, n=n)
    card = _card(tmp_path, n=n)[0]["ssti_uif"]
    _, pack = _pack(project)
    # the run as master wrote it: the grid cases alone (the lines dropped as text, so no number is re-read)
    lines = (run / SSTI_UIF_CSV).read_text().splitlines(keepends=True)
    kept = [line for line in lines if f",{su.ROLE_ISLAND}," not in line]
    assert len(lines) - len(kept) == len(island)
    (run / SSTI_UIF_CSV).write_text("".join(kept))
    assert _card(tmp_path, n=n)[0]["ssti_uif"] == card
    _, master = _pack(project)
    rows = pack["ssti_uif"].rows
    grid, printed = rows[: len(master["ssti_uif"].rows)], rows[len(master["ssti_uif"].rows):]
    assert grid == master["ssti_uif"].rows
    assert pack["ssti_uif"].caption == master["ssti_uif"].caption
    assert len(printed) == len(island)
    assert {r[1] for r in printed} == set(su.ISLAND_CASES)
    assert {r[5] for r in printed} == {S.NOT_JUDGED}

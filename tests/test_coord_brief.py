# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""brief.py: a package whose likely files an open PR changes is flagged."""

import importlib.util
import sys
from pathlib import Path

COORD = Path(__file__).parents[1] / "scripts" / "coord"
sys.path.insert(0, str(COORD))
SPEC = importlib.util.spec_from_file_location("brief", COORD / "brief.py")
brief = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(brief)

PRS = {"957": {"pypsa-gui/backend/services/adequacy/campaign.py", "doc/x.md"}}


def test_overlap_by_file_and_directory():
    assert brief.overlaps(PRS, ["pypsa-gui/backend/services/adequacy/campaign.py"])
    assert brief.overlaps(PRS, ["pypsa-gui/backend/services/adequacy/"])
    assert brief.overlaps(PRS, ["pypsa-gui/backend/services/adequacy"])


def test_no_overlap_on_name_prefix_or_other_dir():
    assert not brief.overlaps(PRS, ["pypsa-gui/backend/services/adeq"])
    assert not brief.overlaps(PRS, ["pypsa-gui/frontend/src/"])


def test_main_exits_3_on_overlap(tmp_path, capsys):
    prs = tmp_path / "prs.txt"
    prs.write_text("957 pypsa-gui/backend/services/adequacy/campaign.py\n")
    out = tmp_path / "brief.md"
    args = ["--task", "t", "--open-prs", str(prs), "--out", str(out)]
    assert brief.main(args + ["--files", "pypsa-gui/backend/services/adequacy/"]) == 3
    assert "OVERLAP:" in capsys.readouterr().out
    assert brief.main(args + ["--files", "pypsa-gui/frontend/"]) == 0

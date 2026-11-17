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


def test_soft_overlap_does_not_block(tmp_path, capsys):
    prs = tmp_path / "prs.txt"
    prs.write_text(
        "5 pypsa-gui/backend/harness/README.md\n"
        "6 pypsa-gui/backend/harness/catalogue.py\n"
        "7 doc/release-notes.d/x.md\n"
    )
    out = tmp_path / "brief.md"
    args = [
        "--coordinator",
        "session_x",
        "--task",
        "t",
        "--open-prs",
        str(prs),
        "--out",
        str(out),
    ]
    assert (
        brief.main(
            args + ["--files", "pypsa-gui/backend/harness/,doc/release-notes.d/"]
        )
        == 0
    )
    text = capsys.readouterr().out
    assert "soft:" in text and "OVERLAP:" not in text
    prs.write_text(
        "5 pypsa-gui/backend/harness/README.md\n8 pypsa-gui/backend/harness/x.py\n"
    )
    assert brief.main(args + ["--files", "pypsa-gui/backend/harness/"]) == 3
    text = capsys.readouterr().out
    assert "soft:" in text and "OVERLAP:" in text


def test_stay_out_is_compact():
    prs = {
        "1": {f"pypsa-gui/backend/services/m{i}.py" for i in range(30)} | {"doc/a.md"}
    }
    text = brief.stay_out(prs, ["Build · A — one", "Build · B — two"], "")
    assert text.count("\n") == 1
    assert text.splitlines()[0].startswith("- open PR #1: `pypsa-gui/backend/")
    assert text.splitlines()[1] == "- live sessions: Build · A — one, Build · B — two"
    assert len(text) < 200


def test_unknown_stream_warns_and_continues(tmp_path, capsys):
    out = tmp_path / "brief.md"
    code = brief.main(
        [
            "--coordinator",
            "session_x",
            "--task",
            "t",
            "--stream",
            "nope",
            "--out",
            str(out),
        ]
    )
    assert code == 0 and out.exists()
    assert "no '## Stream nope' block" in capsys.readouterr().err


def test_spawn_prints_create_session_arguments(tmp_path, capsys):
    out = tmp_path / "brief.md"
    brief.main(
        [
            "--coordinator",
            "session_x",
            "--task",
            "t",
            "--model",
            "claude-sonnet-5-5",
            "--title",
            "Build · X — y",
            "--out",
            str(out),
        ]
    )
    capsys.readouterr()
    spec = importlib.util.spec_from_file_location("spawn", COORD / "spawn.py")
    spawn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spawn)
    assert spawn.main([str(out)]) == 0
    text = capsys.readouterr().out
    assert "model: claude-sonnet-5-5" in text
    assert 'tags: ["team:build"]' in text
    assert "source_url: https://github.com/MLHaoChang/pypsa-eur" in text
    brief.main(["--coordinator", "session_x", "--task", "t", "--out", str(out)])
    try:
        spawn.main([str(out)])
    except SystemExit as e:
        assert "model" in str(e)
    else:
        raise AssertionError("spawn must refuse a brief with no model")

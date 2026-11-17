"""
Parameter guide files (spec expert-catalogue §3.1, §6.2, §10.1; WIKI-1).

Exactly one `guide/<id>.md` per entry, asset type, group and function, and no
orphan; each holds the two hand-written sections and nothing a renderer could
run; no model name outside the guide's generated "In model files" line (which
is not in the file at all); the frozen app ships the folder.
"""

from __future__ import annotations

import re

import pytest

from services.asset_schema import catalogue as C

FILES = {p.name[:-3]: p for p in C.GUIDE_DIR.glob("*.md")}
IDS = C.guide_ids()


def test_one_file_per_guide_id():
    missing = sorted(set(IDS) - set(FILES))
    assert not missing, f"guide ids with no file: {missing}"


def test_no_orphan_files():
    orphans = sorted(set(FILES) - set(IDS))
    assert not orphans, f"guide files no catalogue id names: {orphans}"
    assert {p.name for p in C.GUIDE_DIR.iterdir()} == {f"{i}.md" for i in FILES}


def test_guide_ids_are_unique():
    assert len(IDS) == len(set(IDS))


@pytest.mark.parametrize("gid", IDS)
def test_guide_body_shape(gid):
    body = FILES[gid].read_text("utf-8")
    assert re.fullmatch(
        r"## What it means\n\n.+\n\n## Example\n\n.+\n", body, flags=re.S
    ), gid
    assert C.guide_body(gid) == body


def test_no_raw_html_and_no_model_names_in_any_body():
    raw = {e.model_name for e in C.entries() if re.search(r"[_0-9]", e.model_name)}
    for gid, path in FILES.items():
        body = path.read_text("utf-8")
        assert not re.search(r"<[a-zA-Z/!]", body), f"{gid}: raw HTML"
        assert "`" not in body, f"{gid}: code span"
        hits = [
            n for n in raw if re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", body)
        ]
        assert not hits, f"{gid}: model names {hits}"


def test_frozen_app_ships_the_guide_folder():
    from tests.test_packaging_requirements import _check_bundle, _spec_backend_datas

    assert (
        _spec_backend_datas().get("services/asset_schema/guide")
        == "services/asset_schema/guide"
    )
    assert "group.general.md" in _check_bundle().EXPECTED

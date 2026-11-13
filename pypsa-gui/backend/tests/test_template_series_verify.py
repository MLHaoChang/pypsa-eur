# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 14 (I8, GK B (56) C2): `template_series.verify` checks every file of a
template's `price_series/` against the sha256 in its meta.json. The generator writes the
hashes; a meta.json without them never reads as verified; the human create path names the
refusal as a typed 422, never a 500."""

from __future__ import annotations

import filecmp
import hashlib
import json
import pathlib

import pytest
from fastapi import HTTPException

from project_templates import eh_templates as T
from project_templates import eh_ipp
from routers import projects
from services.library import template_series as TS

TID = "eh_ipp_hybrid"
COMMITTED = pathlib.Path(__file__).resolve().parents[1] / "project_templates" / TID


def _dir(tmp_path, files=None, entries=None):
    """A `price_series/` dir whose meta hashes what is written, then bent by the caller."""
    d = tmp_path / TS.DIR
    d.mkdir(parents=True)
    files = (
        {"a.csv": b"timestamp,value\n2030-01-01T00:00:00Z,1.0\n"}
        if files is None
        else files
    )
    for name, body in files.items():
        (d / name).write_bytes(body)
    if entries is None:
        entries = [
            {"name": n, "file": n, "sha256": hashlib.sha256(b).hexdigest()}
            for n, b in files.items()
        ]
    (d / TS.META).write_text(json.dumps({"series": entries}))
    return tmp_path


def _code(tpl):
    with pytest.raises(TS.TemplateSeriesError) as err:
        TS.verify(tpl)
    return err.value.code


def test_a_series_dir_whose_files_match_their_hashes_verifies(tmp_path):
    TS.verify(_dir(tmp_path))


def test_a_dir_with_no_meta_json_refuses(tmp_path):
    d = _dir(tmp_path)
    (d / TS.DIR / TS.META).unlink()
    assert _code(d) == "template_series_meta_missing"


def test_an_entry_with_no_hash_refuses(tmp_path):
    entries = [{"name": "a.csv", "file": "a.csv"}]
    assert _code(_dir(tmp_path, entries=entries)) == "template_series_hash_missing"


def test_an_entry_with_an_empty_hash_refuses(tmp_path):
    entries = [{"name": "a.csv", "file": "a.csv", "sha256": ""}]
    assert _code(_dir(tmp_path, entries=entries)) == "template_series_hash_missing"


def test_a_named_file_that_is_missing_refuses(tmp_path):
    d = _dir(tmp_path)
    (d / TS.DIR / "a.csv").unlink()
    assert _code(d) == "template_series_file_missing"


def test_a_file_the_meta_does_not_name_refuses(tmp_path):
    d = _dir(tmp_path)
    (d / TS.DIR / "extra.csv").write_text("timestamp,value\n")
    assert _code(d) == "template_series_file_extra"


def test_a_file_whose_bytes_changed_refuses(tmp_path):
    d = _dir(tmp_path)
    (d / TS.DIR / "a.csv").write_bytes(b"timestamp,value\n2030-01-01T00:00:00Z,2.0\n")
    assert _code(d) == "template_series_hash_mismatch"


def test_a_meta_that_does_not_parse_is_a_typed_refusal_not_a_crash(tmp_path):
    d = _dir(tmp_path)
    (d / TS.DIR / TS.META).write_text("{not json")
    assert _code(d) == "template_series_meta_invalid"


def test_an_entry_naming_a_path_not_a_file_is_refused(tmp_path):
    entries = [{"name": "a", "file": "../a.csv", "sha256": "00"}]
    assert _code(_dir(tmp_path, entries=entries)) == "template_series_meta_invalid"


def test_the_committed_template_verifies():
    TS.verify(COMMITTED)


def test_the_generator_writes_a_hash_for_every_series_file_it_writes(tmp_path):
    eh_ipp.write_price_series(tmp_path)
    TS.verify(tmp_path)
    meta = json.loads((tmp_path / TS.DIR / TS.META).read_text())["series"]
    assert meta and all(e["sha256"] for e in meta)


def test_the_committed_price_series_is_what_the_generator_writes(tmp_path):
    T.write_sidecars(tmp_path, TID)
    files = sorted(p.name for p in (COMMITTED / TS.DIR).iterdir())
    assert files == sorted(p.name for p in (tmp_path / TS.DIR).iterdir())
    _, mismatch, errors = filecmp.cmpfiles(
        tmp_path / TS.DIR, COMMITTED / TS.DIR, files, shallow=False
    )
    assert not mismatch and not errors


def test_the_human_create_path_names_a_series_with_no_hash_as_a_422(
    tmp_path, monkeypatch
):
    tpl = tmp_path / "tpl"
    _dir(tpl, entries=[{"name": "a.csv", "file": "a.csv"}])
    monkeypatch.setattr(projects, "_PROJECT_TEMPLATES_DIR", tmp_path)
    with pytest.raises(HTTPException) as err:
        projects._mint_template_series(None, None, "tpl")
    assert err.value.status_code == 422
    assert err.value.detail["error_kind"] == "template_series_hash_missing"


def test_the_human_create_path_names_a_changed_file_as_a_422(tmp_path, monkeypatch):
    tpl = tmp_path / "tpl"
    _dir(tpl)
    (tpl / TS.DIR / "a.csv").write_bytes(b"changed")
    monkeypatch.setattr(projects, "_PROJECT_TEMPLATES_DIR", tmp_path)
    with pytest.raises(HTTPException) as err:
        projects._mint_template_series(None, None, "tpl")
    assert err.value.detail["error_kind"] == "template_series_hash_mismatch"


def test_an_entry_with_no_file_name_is_a_typed_refusal_not_a_crash(tmp_path):
    entries = [{"name": "a", "sha256": "00"}]
    assert _code(_dir(tmp_path, entries=entries)) == "template_series_meta_invalid"


def test_a_hash_that_is_not_a_string_is_a_missing_hash(tmp_path):
    entries = [{"name": "a.csv", "file": "a.csv", "sha256": 123}]
    assert _code(_dir(tmp_path, entries=entries)) == "template_series_hash_missing"


def test_a_folder_inside_the_series_dir_is_not_a_series_file(tmp_path):
    d = _dir(tmp_path)
    (d / TS.DIR / "notes").mkdir()
    TS.verify(d)

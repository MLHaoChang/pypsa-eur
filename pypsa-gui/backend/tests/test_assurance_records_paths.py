"""`assurance_records._path` keeps every record inside the assurance root."""

import pytest


def test_an_assurance_record_path_outside_the_assurance_root_is_refused(tmp_path, monkeypatch):
    import uuid

    import app_paths
    from fastapi import HTTPException
    from services import assurance_records
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path / "app")
    owner, record = str(uuid.uuid4()), str(uuid.uuid4())
    root = tmp_path / "app" / "assistant_assurance"
    root.mkdir(parents=True)
    assert assurance_records._path(owner, record) == root.resolve() / owner / (record + ".json")
    for bad_owner, bad_record in (("..", record), (owner, "../" + record), (owner, "x")):
        with pytest.raises(HTTPException):
            assurance_records._path(bad_owner, bad_record)
    (tmp_path / "outside").mkdir()
    (root / owner).symlink_to(tmp_path / "outside")
    with pytest.raises(HTTPException):
        assurance_records._path(owner, record)

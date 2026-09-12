import json

from gridspine.schema.errors import StageError


def test_write_produces_the_named_artifact(tmp_path):
    p = StageError(stage="ingest", element_ids=["B1"], cause="boom").write(tmp_path)
    assert p == tmp_path / "error_ingest.json"
    assert json.loads(p.read_text())["cause"] == "boom"


def test_traversal_in_stage_cannot_escape_outdir(tmp_path):
    """`stage` is a plain public field, so the filename is attacker-shaped as
    soon as a non-driver caller constructs one. Sanitize the component rather
    than trusting the four driver literals."""
    p = StageError(stage="../../evil", element_ids=[], cause="x").write(tmp_path)
    assert p.parent == tmp_path
    assert "/" not in p.name and "\\" not in p.name and ".." not in p.name
    assert p.is_file()
    assert list(tmp_path.iterdir()) == [p]

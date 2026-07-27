"""
chat.jsonl lives with its project (spec workstream D).

`get_persist_path` built `PROJECTS_DIR / ctx.loaded_project / chat.jsonl` from
the flat DISPLAY NAME, while project data lives at
`projects_root/<org_uuid>/<project_uuid>/`. Those are different directories, so
a project's chat history sat somewhere other than the project — which is also
why `chat.jsonl` could not be added to the export bundle.
"""
from services import chat_service


class _Ctx:
    """Minimal stand-in for ProjectContext — only the three fields used here."""

    def __init__(self, storage_dir, loaded_project):
        self.storage_dir = storage_dir
        self.loaded_project = loaded_project
        self.chat_state = type("S", (), {"persist_path": None})()


def test_persist_path_uses_storage_dir(tmp_path):
    storage = tmp_path / "org-uuid" / "project-uuid"
    storage.mkdir(parents=True)
    ctx = _Ctx(str(storage), "My Project")
    assert chat_service.get_persist_path(ctx) == storage / chat_service.CHAT_FILENAME


def test_persist_path_is_none_when_truly_unbound():
    """UNBOUND (New Project): returns None before either branch is reached."""
    assert chat_service.get_persist_path(_Ctx(None, None)) is None


def test_persist_path_falls_back_to_the_flat_root_without_storage_dir(tmp_path, monkeypatch):
    """Bound by name but with no storage_dir — the pre-tenancy shape."""
    from routers import projects as projects_router

    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_path / "flat")
    got = chat_service.get_persist_path(_Ctx(None, "My Project"))
    assert got == tmp_path / "flat" / "My Project" / chat_service.CHAT_FILENAME

# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Collate per-PR release-note fragments into ``doc/release_notes.md``."""

import pytest

from scripts.collate_release_notes import MARKER, collate, render

NOTES = f"""# Release Notes

{MARKER}
<!-- ================= -->

* Fix(old): Existing entry.

# PyPSA-Eur v1.0.0
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def tree(tmp_path):
    notes = write(tmp_path / "release_notes.md", NOTES)
    return notes, tmp_path / "release-notes.d"


def test_fragments_go_under_the_marker_in_sorted_order(tree):
    notes, fragments = tree
    write(fragments / "b-second.md", "* Feat(b): Second.\n")
    write(fragments / "a-first.md", "* Feat(a): First.\n\n* Feat(a): Also first.\n")

    collate(notes, fragments)

    assert (
        notes.read_text()
        == f"""# Release Notes

{MARKER}
<!-- ================= -->

* Feat(a): First.

* Feat(a): Also first.

* Feat(b): Second.

* Fix(old): Existing entry.

# PyPSA-Eur v1.0.0
"""
    )
    assert not (fragments / "a-first.md").exists()
    assert not (fragments / "b-second.md").exists()


def test_missing_marker_fails_and_keeps_fragments(tree):
    notes, fragments = tree
    notes.write_text("# Release Notes\n\n* Fix(old): Existing entry.\n")
    fragment = write(fragments / "a.md", "* Feat(a): First.\n")

    with pytest.raises(ValueError, match="Upcoming Release"):
        collate(notes, fragments)

    assert notes.read_text() == "# Release Notes\n\n* Fix(old): Existing entry.\n"
    assert fragment.exists()


def test_no_fragments_changes_nothing(tree):
    notes, fragments = tree
    fragments.mkdir()

    assert collate(notes, fragments) == []
    assert notes.read_text() == NOTES


def test_readme_and_non_markdown_files_are_ignored(tree):
    notes, fragments = tree
    readme = write(fragments / "README.md", "# How to write fragments\n")
    keep = write(fragments / ".gitkeep", "")
    other = write(fragments / "draft.txt", "* Feat(x): Not a fragment.\n")

    assert collate(notes, fragments) == []
    assert notes.read_text() == NOTES
    assert readme.exists() and keep.exists() and other.exists()


def test_rerun_after_interrupted_delete_does_not_duplicate(tree):
    notes, fragments = tree
    write(fragments / "a.md", "* Feat(a): First.\n")
    collate(notes, fragments)
    once = notes.read_text()

    # A crash between writing the notes and deleting the fragment leaves it behind.
    write(fragments / "a.md", "* Feat(a): First.\n")
    collate(notes, fragments)

    assert notes.read_text() == once
    assert not (fragments / "a.md").exists()


def test_render_does_not_touch_disk():
    assert render(NOTES, ["* Feat(a): First."]).count("* Feat(a): First.") == 1
    assert render(NOTES, []) == NOTES


def test_docs_build_shows_pending_fragments():
    import importlib.util
    from types import SimpleNamespace

    from scripts.collate_release_notes import FRAGMENTS, NOTES, fragment_files

    spec = importlib.util.spec_from_file_location("hooks", NOTES.parent / "hooks.py")
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)

    def page(uri):
        return SimpleNamespace(file=SimpleNamespace(src_uri=uri))

    pending = [f.read_text().strip() for f in fragment_files(FRAGMENTS)]
    assert pending, "this change ships its own fragment"
    rendered = hooks.on_page_markdown(
        NOTES.read_text(), page("release_notes.md"), {}, None
    )
    head, _, _ = rendered.partition("## PyPSA-Eur v")
    assert all(text in head for text in pending)
    assert hooks.on_page_markdown(NOTES.read_text(), page("index.md"), {}, None) == (
        NOTES.read_text()
    )

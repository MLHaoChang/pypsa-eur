# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Fold the release-note fragments in ``doc/release-notes.d/`` into
``doc/release_notes.md``.

Every pull request adds its own fragment file instead of editing the shared
notes file, so two pull requests never conflict on the same line. At release
time this script inserts the fragments, sorted by file name, directly under the
``<!-- Upcoming Release -->`` marker and deletes them. Existing entries are left
untouched, and a fragment whose text is already in the notes is not inserted
twice, so the script can be re-run safely.

Usage: ``pixi run collate-release-notes``
"""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "doc" / "release_notes.md"
FRAGMENTS = ROOT / "doc" / "release-notes.d"
MARKER = "<!-- Upcoming Release -->"


def fragment_files(directory):
    """Return the fragment files in ``directory``, sorted by name."""
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix == ".md" and p.name != "README.md"
    )


def render(notes, fragments):
    """Return ``notes`` with ``fragments`` inserted under the upcoming-release marker."""
    lines = notes.splitlines(keepends=True)
    try:
        at = next(i for i, line in enumerate(lines) if line.strip() == MARKER) + 1
    except StopIteration:
        raise ValueError(f"{MARKER!r} not found in the release notes") from None
    # Skip the `<!-- ===== -->` underline that follows the marker.
    while at < len(lines) and lines[at].strip().startswith("<!-- ="):
        at += 1
    new = [f.strip() for f in fragments if f.strip() and f.strip() not in notes]
    if not new:
        return notes
    rest = "".join(lines[at:]).lstrip("\n")
    return "".join(lines[:at]) + "\n" + "\n\n".join(new) + "\n\n" + rest


def collate(notes_path=NOTES, fragments_dir=FRAGMENTS):
    """Insert every fragment into ``notes_path``, delete the fragments, return them."""
    files = fragment_files(Path(fragments_dir))
    if not files:
        return []
    notes_path = Path(notes_path)
    notes = notes_path.read_text()
    updated = render(notes, [f.read_text() for f in files])
    if updated != notes:
        notes_path.write_text(updated)
    for f in files:
        f.unlink()
    return files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--notes", type=Path, default=NOTES)
    parser.add_argument("--fragments", type=Path, default=FRAGMENTS)
    args = parser.parse_args()
    for f in collate(args.notes, args.fragments):
        print(f"collated {f.name}")

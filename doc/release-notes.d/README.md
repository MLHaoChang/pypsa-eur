<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Release-note fragments

Do not edit `doc/release_notes.md` for a change. Add one new file here instead.
New files never conflict, so pull requests that land close together no longer
collide on the top line of the release notes.

## Adding a note

- Name the file `<short-slug>.md`, for example your branch name with `/`
  replaced by `-`: `claude/fix-solver` becomes `claude-fix-solver.md`.
- Write the bullet(s) exactly as they would appear in `doc/release_notes.md`:

  ```markdown
  * Feat(pypsa-gui): Short description of the change.
  ```

  Separate several bullets with a blank line.
- No license header is needed; `REUSE.toml` covers this directory.

The docs build already shows pending fragments at the top of the rendered
release-notes page (`doc/hooks.py`).

## At release time

The release workflow (`.github/workflows/release.yaml`) does not build the
notes, so collate them by hand before tagging:

```bash
pixi run collate-release-notes
git add -A doc/release_notes.md doc/release-notes.d
git commit -m "Collate release notes"
```

The script inserts every fragment, sorted by file name, directly under
`<!-- Upcoming Release -->` and deletes the fragment files. This README and
non-`.md` files are left alone. Existing entries are untouched, and re-running
it is safe.

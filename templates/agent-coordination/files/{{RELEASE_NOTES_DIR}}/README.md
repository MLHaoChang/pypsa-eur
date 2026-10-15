# Release-note fragments

Add one new file here for each change, instead of editing the release notes
file. New files never conflict, so pull requests that land close together
don't collide on the top line of the release notes.

## Adding a note

- Name the file `<short-slug>.md`, for example the branch name with `/`
  replaced by `-`: `claude/fix-solver` becomes `claude-fix-solver.md`.
- Write the bullet(s) exactly as they will appear in the release notes:

  ```markdown
  * Feat(area): Short description of the change.
  ```

  Separate several bullets with a blank line.

## At release time

Collate the fragments into the release notes, sorted by file name, delete the
fragment files, and commit both changes in one commit before tagging.

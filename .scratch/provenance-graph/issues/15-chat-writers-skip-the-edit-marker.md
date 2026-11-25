# Chat writers that edit a value without the kept-run marker

Status: needs-triage
Model tier: opus.
Size: M.
Blocked by: 13 (PR #1960).
Gated: GK.
Likely files:
- `pypsa-gui/backend/services/dispatch_status.py` (`note_edit_kept`)
- the chat tools that write the network (`set_snapshot_weightings`, a rename of a class's only member, others)
Type: bug

Issue 13 marks a kept run with `assistant_edit_kept_run` where `_update_component` (no rename) and `apply_bulk_update` leave the dispatch in place. GK B2's review found chat writers that change a dispatch input without calling `note_edit_kept`, so their stale run is served as fresh: `set_snapshot_weightings`, a chat rename of a class's only member, and possibly others.

Done when: every chat writer that changes a dispatch input and keeps the run marks it, or clears the run as a bus edit does, with one test per writer; a sweep lists the writers checked.

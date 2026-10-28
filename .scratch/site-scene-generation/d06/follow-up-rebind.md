# D06 follow-up: explicit re-bind of unbound placements (for SC-02)

Status: needs-triage

From GK B's D06 verdict (option 1, strict). A uid is never re-attached by name, so an entry can read unbound while it is still on disk:

- **The loss window.** A placement bound after the last save shows as unbound after any of:
  - a scenario branch;
  - a snapshot or bundle export;
  - a Save-As, for the source project;
  - a discarded session.

  The uid was minted only in the unsaved network. Autosave every 5 minutes bounds the window.
- **Stale entries.** An entry whose component was deleted, replaced by an import or clustered also reads unbound.

These entries stay in `sites.json` and are listed in `unbound_keys` by GET `/sites` and by the scene GET.

SC-02 should offer an explicit, user-confirmed re-bind: "attach this placement to <component>", which writes a fresh binding through PUT. It should never re-bind automatically by name. Test that a re-bind needs the user's choice, and that a declined re-bind leaves the entry unbound.

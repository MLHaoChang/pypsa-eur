# The Guided ledger view does not show an out-of-date cost

Status: ready-for-agent (owner decision TCU07-TRIAGE-1: open later, queued behind current work)
Type: gap
Found in: TCU-05 S2 (issue 05), 2026-11-08.

Issue 05 marks a cost priced through a scale input (a PV generator's DC/AC ratio, a Link's efficiency) "out of date"
when that input changes, on the asset card, its read-out and the Economics costs table. The Guided ledger view
(`pypsa-gui/backend/services/study/asset_view.py`) builds its own rows and does not carry `stale` from the provenance
routes, so a Guided user reading the ledger sees a fresh-looking cost.

## Comments

- Likely files: `services/study/asset_view.py` and the ledger row it feeds; reuse `staleNote` in
  `frontend/src/components/ProvenanceChip.tsx` for the wording.
- Blocked by: none (issue 05 merged).

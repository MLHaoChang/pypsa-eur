# "Why this number" and the Results assumptions group

Status: ready-for-agent
Type: task
Spec: `.scratch/guided-results/spec.md` §1.4, §2 (Results group)
Blocked by: 01

Gated: GK B (62), UX (23). GK B conditions G2 and G3 in `01-headline-card.md` apply.

## Comments

### Inputs for the plan (2026-11-17)

- UX (23) condition 3: Expert never shows less than Guided (#1193). The plan says how Expert gets "Why this number":
  the same disclosure on Expert's HeadlineStrip, or a logged slice.
- UX (23) condition 4: the walk passes the plain-words test (no ledger keys, route names, engine ids; RAW-NAMES-1);
  "Assumed" is the word for unconfirmed defaults. Prose uses the tiles' notation (`kpiMoney`), not `money()`.
- Keyed walkthrough audit (J6, `origin/claude/ux-walkthrough-2026-11:.scratch/ux-walkthrough-2026-11/README.md`):
  for the same project Expert Results reads "not run yet" while Guided reads "Certified". Expert's Summary has no
  study verdict or expected-shortfall figure (`SummaryTab.tsx:262` only links to Adequacy). The parity answer for
  condition 3 should cover the verdict and the reliability figure too, not only "why".

### Owner decision WHY-FULL-1 (2026-11-17, via the coordinator)

Option 2, the full walk first: add a provenance-graph target for the Guided study's figures (backend). Send the plan to
GK B (63) before any code, and build it TDD. Then the disclosure on top: each input's source and whether it is current,
the run and its settings, and the unconfirmed Assumed defaults with Edit. The same component goes on Expert's Results
headline. Slice 03 may run in parallel where it does not overlap.

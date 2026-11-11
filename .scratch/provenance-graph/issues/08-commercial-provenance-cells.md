# `commercial.provenance` cells on the graph

Status: in progress
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B.
Blocked by: 02.

## What to build

`input` nodes for the `commercial.provenance` cells (`services/document_values_commercial.py:583` `provenance`,
`:634` `_live`), keyed `<target>:<entity>/<path>`, with `stamped_with` edges from their approval stamps. Costs that
read them stop listing them as `uncompared` (GK B ruling (b) in issue 01).

## Acceptance

- A cell whose item, period, tier or contract no longer exists reads stale, never fresh.
- Conditions P1, P3, P5 and P8 of the plan GO apply.
- One PR within PR-SIZE-1.

## Comments

### GK B (45), 2026-11-11: plan GO with conditions C1–C9

Rulings:

- The walk follows a `stamped_with` edge only when its `recorded` is a `CellStamp`, through one helper that
  `staleness` and `why` both use. Asset stamps stay unwalked (P3).
- No cost node on master reads a commercial cell, so nothing lists one as `uncompared` today. The `derived_from`
  edges from cost nodes to `input:commercial/<key>` come in slice 05. No `why_this_number` target here.
- The three follow-ups from the 03 landing are filed as issues 09, 10 and 11, not folded in.

Conditions (each a property and a test):

- **C1 Walk rule.** An asset stamp edge (dict `recorded`) stays unwalked and a `CellStamp` edge is walked, in
  `staleness` and in `why`, each with its own case. A `CellStamp` edge with no registered comparator reads
  uncompared, never fresh.
- **C2 What the stamp compares.** Each entry field the slot can supply is compared; the others are named as
  uncompared. A cell whose value is equal but whose currency or unit moved is not fresh.
- **C3 Refusal set.** The comparator catches exactly `CELL_GONE`; any other exception is never read as fresh. One
  killing case per exception type, and one for the tuple widened to `Exception`.
- **C4 `_same`.** Cases for int vs float, NaN, None and a numeric string; the `_same` → `==` mutant is killed.
- **C5 Oracle.** The frozen copy of master's `document_source` is compared where master is right. Where master's
  behaviour is a defect, the test asserts the new behaviour and the PR body says so.
- **C6 Ids.** `cell_id` is injective and total. A key that does not parse still gets a node whose verdict is Broken
  "stamp record malformed", never a crash and never fresh.
- **C7 Sources.** An entry with no source or an unknown source still gets its edge and compares normally; it does
  not merge into another source's node.
- **C8 No write, no cache.** Building the graph and asking a verdict writes nothing to the commercial config and
  reads the stored config once.
- **C9** `_live` and apply's compare-and-set stay as they are; apply still prunes as before.

Mutants run each on its single targeted test; survivors go in the PR body.

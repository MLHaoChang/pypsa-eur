<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Design plan

Status: done (#1305)
Type: design
Model: Opus. Reviewer: GK B (harness and app backend). The plan goes to GK B before any code.

## What to decide

- The data model: a memory item's kind, scope, concept keys, body, evidence links, status (proposed, confirmed, retired), use count and last use.
- The capture paths and their cards.
- The recall budget and filter, and where the block sits in prompt assembly.
- The Memory page.
- The org-promotion rule.
- How the dream job (04) plugs into the durable task queue.
- The overlap with the provenance-graph issue and with DASHBOARD-1 chart specs.

The plan names slices 02–04 as one PR each (PR-SIZE-1). Read `spec.md` first.

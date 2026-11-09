<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant memory: lessons learned across projects

Owner decisions MEMORY-1 and MEMORY-DREAM-1 (2026-11-08). The owner's goal: the in-app (GridSpine harness) assistant learns from past work, so the user "does not have to say each time how specific the graph should be drawn", and saved parameters "should be considered as a suggestion when they reuse it". Built for the local user now, and separable per organization later.

## What "learning" means here

The assistant retrieves curated, user-approved notes. There is no model retraining. Memory is untrusted data in the prompt (`harness/prompts/untrusted_data_clause.md`) and never bypasses validation.

## Three kinds of memory

1. **Preferences:** chart specs and style, units, currency, report sections, default sensitivities.
2. **Reusable parameters:** values saved before, offered next to the field with their source ("Used before: 7% · project A ›"). They are never applied silently (COPILOT-1).
3. **Run lessons:** short notes tagged by project type, asset or step, recalled when the same situation comes back.

## Capture (MEMORY-1: explicit + proposed)

- A "Save as default" button, and "remember this" in chat.
- The assistant proposes a memory when the user repeats a correction or a run ends. The user confirms each one on a card. Nothing is learned silently.

## Scope

Each item belongs to a user, an organization (the existing `Organization`/`OrgMembership` tables in `pypsa-gui/backend/db/models.py`) or a project type. An org admin can promote a user item to the org.

## Recall

- A capped memory block in prompt assembly, next to `_skills_block`, filtered by the active project type and the panel's concepts.
- Field-level suggestion chips in the UI.

## Governance

A Memory page lists each item with its source project or run. The user can edit or retire any item.

## Dream consolidation (MEMORY-DREAM-1)

Slice 3 ports the pattern of Anthropic's "Dreaming with a dynamic workflow" cookbook into a harness durable task:
- It runs nightly, after N sessions, or on demand, with a budget cap.
- Analyze: one cheap-model analyzer per past chat or run, in batches of about 20.
- Organize: one organizer merges duplicates and links items to their sources.
- It writes proposals only, which the user confirms.

It is provider-agnostic and keeps data local. It does not use the Managed Agents Dreams API, which is a research preview, Anthropic-only and not ZDR-eligible.

## Links

- Memory items become nodes of the provenance graph (owner ask, 8 Nov), so "why this number" can cite a saved default.
- Saved chart specs are DASHBOARD-1's added charts.

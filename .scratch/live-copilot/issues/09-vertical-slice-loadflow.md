<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-09 (VS-1) — "show me the load flow results of the latest calculation"

Status: ready-for-agent once the owner accepts the vertical-slice order (Q1)
Type: task
Baseline: master `d006f4beaa302b97432c65757762ae180222731a`
Dependencies: LC-01B merged; issue 13's Tier A harness (built in this slice)
Review: [vision review](../vision-review-2026-10-19.md); journeys OJ-01 in
[owner journeys](../owner-journeys.md)

## Goal

The owner's first example works end to end in an ordinary (non-strict) turn,
typed, in Expert. It is the thinnest path through LC-01/03/C7, not a new layer.

## Scope

1. **Latest.** "Latest calculation" means the newest completed run of the
   active project (`list_project_runs`, newest first,
   `harness/catalogue.py:3529`). If it equals the current solved results, use
   the current load flow. If it does not (inputs changed since), say so and
   offer to re-run; never describe current results as that run. The answer
   names the run ID and finish time.
2. **Read, then show.** `get_loadflow_summary` first, then
   `ui_open_panel(results, loadflow)` with the same source and window. The
   answer cites only the summary's facts and its proxy caveat.
3. **Know it is shown.** Issue the existing v2 source-bound ACK for this target
   in ordinary turns too, not only strict ones (`harness/assurance.py:894`,
   `frontend/src/components/ChatPanel.tsx:2385`). Without a matching ACK the
   answer says "opened" and not "showing". No new ACK vocabulary.
4. **Prompt.** Add the rule to `harness/prompts/assistant_stance.md`
   "chaining": read before you show; name the run; one short paragraph of key
   points (peak loading, branches over limit, what drives it), then offer the
   next section.
5. **Local shortcut.** With a model available, the dock forwards this sentence
   to the harness; the local tour is the no-key fallback (Q4).

## Not in scope

Historical load-flow capture (LC-03), other tabs (LC-11), checked publication
changes, voice.

## Done when

- OJ-01 passes in Tier A (backend and browser) with the fixture's oracle values.
- The pilot gate, inventory and control-matrix checks pass unchanged.
- Tier B result for OJ-01 recorded if the owner approved Q2.

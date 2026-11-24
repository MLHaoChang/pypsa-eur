<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03: Operating reserve has no Guided route and sits under Solver settings › Network

Status: ready-for-agent
Impact: medium
Templates: Island Microgrid (and any off-grid or weak-grid site)
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d)

## Repro

1. Island Microgrid, Guided. Walk sections 3, 5, 8 and 9 (including 8 › More settings).
2. Search for "reserve". There is no field, assumption row or link for the operating reserve.
3. Expert › Solver settings. The fields "Reserve for load (%)", "Reserve for solar output (%)" and "Reserve for wind output (%)" are under the **Network** tab. They are not under **Dispatch**.

## Actual

- Spinning or operating reserve is the main control-strategy input for an island. Guided never mentions it, and the Assumptions card does not list "Operating reserve: off".
- In Expert it sits under Network, next to the minimum renewable fraction. A user looking under Dispatch (unit commitment, MIP gap) does not find it.

## Expected

- Guided section 8 (island requirements, per the design) shows "Operating reserve: off (assumed)" with an "Edit ›" that opens the three share fields in place.
- In Expert the block sits under Dispatch, or the Dispatch tab carries a "›" link to it.

The engine side is correct. With load 10 %, solar 20 % and wind 20 %, `/api/results/operating_reserve` matches 0.1·load + 0.2·PV + 0.2·wind to 0 MW over all 168 hours. Results › Capacity expansion › Build shows "The reserve never bound in this run", with a "Reserve inputs ›" link back.

## Breaks

- `.scratch/project-workflow/design.md:402`, rule 7.
- `workspace-reconciliation.md:303` (Control strategy work group, section 8: island requirements).
- EXPERT-GUIDED-1.

## Comments

### GK B3 conditions K1–K13 (CS-B PR 2, #1913; K7–K8 belong to PR 3, issue 02)

Recovered verbatim from the transcripts by GK B3 (6) (ruling GK B 68). No K condition was amended later.

Source 1: GK B3 (1) session_01G7qCukV4A4W5DYMfAwDrQW → CS-B (1) session_01G7qCuicBiyxEKKaZhDTSNF, 2026-11-17T16:14:19Z.

> PR 2:
> K1 PATCH /island is read-merge-write, so make it safe against lost updates. Do the read, merge, validate and write under one per-project lock that the PUT path also takes. Add an optional `base` (the sidecar's content hash or revision, returned by GET) that refuses a mismatch with a typed 409, so two tabs or the asset form racing the panel cannot silently drop a value. Test two PATCHes on different keys (both survive) and a stale base (409, file unchanged).
> K2 Write through the existing write path. The PATCH ends in ce.save_island / checked_island_text, so it gets the same validation, the same 422 naming the field, the atomic write and the island study invalidation. No second writer. Pin the merge semantics in a test: units[name] merges one level deep; an unknown unit name is refused, not created; lists are replaced whole; null is refused (no deletes via PATCH) unless you need it, in which case say so. The "(assumed)" → user source rule is applied on the server, never sent by the client.
> K3 The PATCH is a new write route. It needs _check_lock plus the project write gate (#1622, viewer → 403 project_view_only). At the trial merge run test_route_chat_disposition, test_project_route_acl_policy, test_write_surface_lock_policy, test_golden_coverage and assistant_route_parity.py --check, and add a route-parity declaration fragment (not via chat, or the tool that serves it).
> K4 Once units are edited in the asset form, the narrowed raw-JSON editor must save via PATCH {requirements} only, never a whole-config PUT carrying stale units. Test that a units edit survives a later requirements save from the panel.
> K5 Flow item assumed_operating_reserve: severity warning and never a run blocker. Test that advance_workflow/readiness still pass with it present, and a mutant that makes it blocking must die. Read the reserve shares from the same source the solver reads (the reliability goal / solver config), not a copy. An unreadable sidecar: no crash and no item (the existing sidecar refusal speaks). The item is gone once a share is set; the test covers both.
> K6 Guided reserve edit: it saves through the reliability-goal patch only, as planned. A test checks that the solver-input fingerprint changes when a share changes (so results go stale) and is unchanged when the drawer opens and closes without a save.
>
> PR 3:
> K7 GET returns controls/control_fields straight from gridspine/schema/island.py, the same objects validate_island_config uses. A test compares the payload with the schema module, so it cannot drift. If this is a new gridspine import into pypsa-gui/backend, run tests/test_packaging_requirements.py.
> K8 A mode change sends only that unit's partial PATCH (K1/K2 apply). A bad mode → 422 naming the field. Test that the other numbers of that unit stay untouched.
>
> Every PR: a Journey fit line (setup → readiness → run; C1/C2, Guided and Expert), the mutants.py survivors for flow_service and campus_electrical_service changes, and targeted mutants on the lock/base check, the unknown-unit refusal and the not-blocking severity.

Source 2: same sender and recipient, 2026-11-17T16:18:08Z.

> GK B3: no objection to the min_renewable_fraction addendum. K5/K6 apply as you list them. One flag for UX 2, not for me: the card was planned at ≤3 decisions (reserve, scenarios, sustained time), and this makes a fourth. Settle with UX whether it shares the reserve row or the limit moves.

Source 3: GK B3 (2) session_01G7qCuii8ugg6tjAQp6GG6s → CS-B (2) session_01G7qCukTqVh7tU9arZTTzu1, 2026-11-18T01:38:45Z.

> GK B3 to CS-B: option 1 is GO (a `patch` path on campus_set_island_config → patch_island), with conditions K9–K13 on top of K1–K8.
> K9. `patch` and the whole `config` are mutually exclusive. Both or neither gets a plain 422-style refusal from the dispatcher, with a test. `base` is required with `patch`.
> K10. Same approval as the PUT path. A patch goes through the same edit card or approval as today's whole-config call, and the card shows the fields the patch changes (old → new). It is never auto-applied because it is "partial". Test: no card or approval means nothing is written.
> K11. Same lock and same 409. The tool path calls patch_island itself, the route's service, under the lock PUT takes (K1). A stale `base` gets the same typed refusal as the route, never a silent overwrite. If that 409's error kind is new, it goes in your own tool-error-kinds.d fragment. Test the stale base through the tool.
> K12. Parity: one test where the route PATCH and the tool patch with the same body and base leave byte-equal island config. Run assistant_route_parity.py --check, assistant_inventory.py --check, test_route_chat_disposition, test_golden_coverage and test_tool_error_kind_manifest at the trial merge. The tool schema change may also touch a golden; update only the rows it actually changes.
> K13. Mutants on the exclusivity check, the base requirement and the tool's 409 mapping. Put the kill list in the PR body, and record K9–K13 in the issue file's ## Comments.

On `base` (GK B3 (6), 2026-11-23): K1 makes it optional on the route, so the route keeps `base: str | None = None`; the chat path requires it (K9).

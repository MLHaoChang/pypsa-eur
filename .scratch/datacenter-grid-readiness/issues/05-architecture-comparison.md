# Power-architecture comparison: AC / MV UPS / 800 VDC / SST

Status: done
Model: Opus
Type: task
Blocked by: 03
Gated: yes (finance)
Model tier: opus
Size: M

## What to build

- Architecture as a DC template parameter. Each option carries a conversion-loss chain (feeds the effective PUE), capex per MW, and which grid services it can offer (feeds 02).
- A compare view across architectures: energy, cost, grid-readiness verdicts.
- Data rows are "assumed" with sources, in a defaults-pack version (never edit a shipped pack).

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: DC template parameters, defaults-pack version (new version, never edit a shipped pack), compare view in the frontend. Blockers: 03. Reviewers: GK A and GK B (finance); UX for the compare view.

### 2026-11-06: plan verdicts (session_01G7qCuivz5KEGYG3aBcEzgz)

UX (11): GO with changes. Expert shows the "Cheapest over the horizon: X · most grid services: Y" line too; headers in words ("Efficiency to rack", "NPV vs this project (M EUR, <money year>)", "Peak within import limit"); a tooltip with the full name on each service chip; the footnote reads "A screening estimate, with no solve. Outage proof is in Backup.", with the pack version on its own "Source:" line; Guided shows the price used, marked assumed; after a spec change the table reads "out of date", and the section is complete only while the result matches the spec; Compare runs in place; the create-form select's hints are visible text; shots in both modes, one Expert shot at about 1250 px.

GK B (28), finance: GO with K1–K9. Q-B1 (a), Q-B2 (a).
- K1: 2026-11-07 becomes the unpinned default, and the PR lists the call sites. Every 2026-11-06 row is byte-equal in 2026-11-07, and 2026-11-06's hash is unchanged. The new hash is pinned, and so are the tariff currency pins.
- K2: the dc-arch rows are assumed, ranged, money_year 2025, real, output_electric per MW of IT, without grid connection. A test covers the EUR/kW→EUR/MW conversion.
- K3: every row has one boundary (MV bus to rack input, in-rack conversion inside), with no overlap with IT, building or DC-03 batteries.
- K4: the NPV delta is reported at central values and at the range ends. An interval that spans zero reads "not distinguishable"; this is tested.
- K5: the price is the tariff energy component, weighted by the loss hours or the time-mean (named which). Demand, capacity and connection charges are not_valued. The tariff and finance currencies must match. No price → 422. A request price is assumed, and the price is held constant in real terms. Tests: TOU vs mean, no tariff, currency mismatch.
- K6: DC-03's NPV machinery is reused, not forked. The project's own leg is computed. Hand-checked tests cover lifetime < horizon and lifetime > horizon.
- K7: default "ac" is byte-identical. Any LP input from the architecture feeds the fingerprints only when it is not the default; otherwise the PR says the architecture is metadata only.
- K8: the compare route writes nothing (tested). The route policy tests run at the trial merge. Each refusal gets its own error kind in a tool-error-kinds.d fragment.
- K9: each new guard has a test and a mutant that the test kills.

GK A (14) handed off at 300k before verdict; plan re-sent to GK A (15).

GK A (15), engine: GO with C1–C9. Q-A1 (a): PUE is stated for the own architecture; Facility_v = IT_v·(PUE − 1/η_own + 1/η_v), IT_v = IT_own·η_rack(own)/η_rack(v). Q-A2 (a), in this PR (#1195 no longer touches grid_readiness.py).
- C1: unset (assumed "ac") is byte-equal to master in the sidecars, fingerprint, investment-case digest, ledger (the ups_distribution_loss_share line kept verbatim) and scorecard. Chain stages appear in the ledger only when the architecture is set explicitly. Scorecard equality is tested.
- C2: the metering boundary (IT at the rack input, Green Grid PUE category 3) is stated in the ledger and the compare.
- C3: PUE(T) − 1/η_chain(own) < 1 at any snapshot gives a typed 422 with its own kind and fragment. No clipping; tested.
- C4: assumed second-order effects are stated: the cooling of the shifted loss heat is not modelled, and stage η is flat over part load.
- C5: MWh and the energy-weighted PUE use snapshot weightings (named), with a non-unit weight test. The peak is per snapshot, unweighted.
- C6: SERVICES is imported from gridspine. An architecture-sourced service reads assumed and never passes alone. GFM and inertia on SST, and FFR on 800 VDC, count only with declared DC-bus storage (otherwise conditional, never pass). The RMS caveat appears once per surface, positive test.
- C7: as K1, plus older pins and REDUNDANCY_PACK_VERSION are unaffected. The stage-part efficiency rows load through the unchanged loader, and an unknown part is still rejected.
- C8: as K8, plus file mtimes are unchanged. An unsaved or missing spec is a typed 422.
- C9: mutants RED: the in-rack line dropped, own and variant swapped, the weighting dropped, the C3 guard removed, a conditional service passed without storage, and an unset "ac" taking the chain-ledger path.

Amendments, GK A (15): C3 refuses when PUE(T) − 1/η_own < 0; it is checked at create only when the architecture is set. Tests: AC 1.05 is refused, 1.3 passes, and the boundary passes. C7: stage rows have no part and are not_applicable, and a stage-named part is still refused. Services: SST's FFR and FCR are also conditional; DC-bus storage means backup bess or hybrid (assumed). Still to add: a test that a genset-only backup leaves every conditional service unpassed. UX (11): (a) GO. Issue 22 carries the create-form select; the create-form shot is dropped, and the PR body names issue 22.

### 2026-11-06: build finished (session DC-05 (2))

- GK A (15)'s added test is in: a genset-only backup leaves every conditional service unpassed (`test_a_genset_only_backup_leaves_every_conditional_service_unpassed`; the mutant "DC-bus storage always declared" is RED).
- The wider run found three real failures, now fixed: the `dc_architecture` not-via-chat keywords were not single words; its row was missing from the declared-rows test; the six kinds were built with an f-string, so the error-kind manifest guard could not see them (now `error_kind` literals in `_KINDS`, and an undeclared kind is a KeyError).
- Found in the shots: the template ships no import tariff, so Guided hit the no-price refusal with no field to answer it. Guided now shows the price field in place after that refusal only (two vitests, both mutants RED). The table cells got padding, the service chips no longer wrap, and the price line drops the doubled "(assumed)" beside the assumed tag.
- Shots: `.scratch/datacenter-grid-readiness/shots/dc05-*` from `pypsa-gui/frontend/scripts/dc-architecture-shots.mjs` (real app; the result states answer from the committed fixture).- mp-code-review (standards and spec), fixed: (1) the own leg kept moving with its own chain's efficiency ends; under Q-A1 it now keeps the saved load at the stated PUE on every cost end, and only the variants move. (2) On a multi-period network the yearly energy and its cost summed every period (three times on the staged template); they now count the final period, the full build-out that the capex is on, and the response names it (`energy_basis`). (3) The tariff currency was checked against the pack's finance currency; it is now the project's finance-case currency, falling back to the pack's. (4) C1 now has a scorecard test: an unset architecture equals the master path, and SST carries the caveat. (5) The price line names its weighting. (6) User copy: no ticket id and no repr in refusals or in the ledger note. Each of (1)–(3) has a mutant that turns RED.
- Left for the reviewers: K4 ranges only the overnight capex and the stage efficiencies; the lifetime and O&M rows have ranges but are read at their central values. C3 also refuses at compare time on an unset project whose PUE is below the AC chain (create only checks a set architecture).


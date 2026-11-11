<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — run_coupling_loop / margin loop cannot sample outages carried on Links (firm Link backup, grid-import Link)

Status: done (#1341)
Type: task
Gated: yes (GK A)
Model tier: opus
Raised by: [study-harness 11](../../study-harness/issues/11-profile-h2-hub.md) (GK A (19) ruling SH11-L4, 9 Nov)

## What

The adequacy loops sample a fleet built from **electrical Generators** only (`snapshot_inputs`). A network whose outage data sits on Links has nothing to sample, and every loop refuses before it starts with the same message:

> nothing to sample: no electrical generator carries resolvable occurrence data (unavailability + MTTR), so the sampled fleet is empty

The refusal is shared by three call sites:

- `pypsa-gui/backend/services/adequacy/coupling_loop_runner.py:250` (`run_coupling_loop`)
- `pypsa-gui/backend/services/adequacy/margin_loop_runner.py:223` (`run_margin_loop`)
- `pypsa-gui/backend/services/adequacy/eh_stages.py:1150` (the EH study's loop stage)

Both H2-hub templates (`eh_h2_hub`, `eh_h2_hub_staged`) carry their outage data only on Links: `grid_import`, `electrolyser` and the firm backup `fuel_cell`. Their harness profiles' LOLE rule (`eh.adequacy.lole`, `run_coupling_loop`) therefore can never be evaluated, and an H2-hub run stops at the result gate with a typed NoEvidence (SH11-L1).

The refusal itself is correct: an empty fleet would report the whole horizon as loss of load. The gap is that a firm Link (a fuel cell feeding the electrical bus) and a grid-import Link are supply the loop should sample.

For reference only: the EH study's two-area Monte Carlo (`mc_certify`) already samples Link-borne outages; F9 (study-harness 07, owner Q3 = A) keeps its LOLE out of the gate.

## Acceptance

- The coupling and margin loops sample the outages of Links that supply an electrical bus (at least a firm backup Link and a grid-import Link), each with its own occurrence data; the refusal stays for a network with no sampled supply at all.
- The three refusal sites share one rule for what is sampleable (no drift between them).
- On both H2-hub templates `run_coupling_loop` runs to a verdict; the H2-hub profiles' LOLE rule then evaluates, and study-harness 11's acceptance 1 can be closed.
- A test per Link role (firm backup, grid import), plus the existing Generator-only fixtures byte-unchanged.

## Comments

**GK A (20) plan verdict, 10 Nov: GO WITH CONDITIONS** (recorded verbatim):

- **AL-1 No unlimited credit for an energy-limited unit.** This is the main change. A firm-backup Link fed from a bus whose supply is a Store (the H2 hub's fuel cell on the H2 store) must not be sampled as an unlimited two-state unit with only a note. LOLE is a block rule, and full p_nom in every hour overstates adequacy whenever the store is empty. Either model it the way `snapshot_inputs` models storage (an energy-limited `StorageSpec` discharge side with the store's e_nom, the same dispatch and initial-state rule as other storage, outage-sampled), or don't credit it at all (0 MW, with a note). Full credit is only for a Link whose bus0 has an unlimited fuel supply (a fuel Generator with no energy cap). Tests: a fuel cell on an empty or small store gives LOLE > 0 where the unlimited model gives 0. Pin which path each shipped hub takes.
- **AL-2 Grid import treated as firm is disclosed.** An import Link with no outage data becomes a firm block (mc_certify's model). Then the LOLE verdict rests on that assumption, so the loop result and the gate note name it ("grid import taken as never failing: no outage data for <link>"). A test checks the words on eh_h2_hub. If you or GK B think it should read not_evaluated instead, bring it to me before building; don't decide it alone.
- **AL-3 Per period.** Import and firm Links use p_nom_opt for extendables and `get_active_assets(period)` / ActivityContext per period, never the raw p_nom. Test on eh_h2_hub_staged with non-unit weightings. LOLE still goes through `loop_goal(horizon_years(n))` on the hub-side demand, using the same demand basis (`demand_scaled_in_place`).
- **AL-4 CRN and membership.** Generators first, Links appended in a fixed order (sorted names). With keep_zero_capacity every candidate Link stays in at 0 MW across iterates, import Links included. Test: generator substreams are identical with and without Links, and Link membership is the same across two iterates that build differently.
- **AL-5 Byte-equal.** A network with no qualifying Link returns snapshot_inputs' result unchanged. Pin this on every shipped template without a hub Link (eh_site and the DC templates at least), not only on a fixture.
- **AL-6 The split's fallback.** Test the "Links don't separate the sides" path: it falls back to the whole network with no import Link sampled. Rates come from `resolve_outage_params(n, "links")` only. A bad rate raises OutageRateError, tested at each of the four loop sites as a typed refusal (#1318 F1: the refusal names only what it establishes).
- **AL-7 One predicate.** coupling, margin, mc_loop and freeze_fixed_plan import the same predicate and message, and a test asserts that by identity. The NOTHING_TO_SAMPLE constant is unchanged. Solve counts are unchanged (snapshot only), and a test shows it.
- **Q1: (a)**, provided AL-1 holds, so every site samples the same fleet. The mc_certify numbers that move on the H2 and ammonia hubs get listed in the release note, with a before/after pin per template. F9 still keeps that LOLE out of the gate.

**GK A (20), 10 Nov: the split, answer (a) with conditions** (recorded verbatim):

> Netting a 1000 MW grid generator as must-take on a copper plate is a double count, and one fleet rule shared with mc_certify is the fix. (b) and (c) keep the bug on seven templates.
> - **AL-5 restated:** byte-equal for every network with no trusted import Link (the non-EH templates and the Generator-only fixtures), pinned.
> - **AL-8 Before/after per template.** For each of the nine EH templates, the PR body and the release note give the loop LOLE before and after, and the harness LOLE verdict (pass/block) before and after on the shipped network. If any shipped template's verdict flips pass→block or block→pass, say so in the PR body and message me. A verdict flip on a shipped template is what users see, so I'll take that one to the owner before OK.
> - **AL-9 The import cap is the binding term now.** Test a template-like fixture where the import Link's delivered cap (p_nom×p_max_pu×eff, per period) is below peak hub demand: LOLE > 0, where the old copper plate gives 0. AL-2's disclosure ("grid import taken as never failing: no outage data") applies on every template that takes the firm-block path.
> - **AL-1 path accepted:** a store-fed fuel cell is not credited (0 MW, with a note), because a StorageSpec would double-count the store that also feeds the offtake. Full credit only under your stated rule (a non-slack fuel Generator with no e_sum_max, and no Store, StorageUnit or Load on bus0). Test each exclusion of that rule separately (#1257 F1a), not only the all-clear fixture.

**GK A (20), 10 Nov: the 4th site (mc_loop), answer (a) with one condition** (recorded verbatim):

> mc_loop keeps snapshot_inputs' fleet in this PR, so its refusal must be decided on the fleet it actually samples. On an H2 hub, the shared predicate fed the Link-aware fleet would say "something to sample" while mc_loop samples nothing, and it would run empty or give a misleading reason. Make the shared predicate and message take the MCInputs (or a fleet) as their argument. mc_loop passes its snapshot_inputs result and gets the generator-only tail. Test it on eh_h2_hub: mc_loop refuses with NOTHING_TO_SAMPLE and its message doesn't claim a Link was checked (#1318 F1). AL-7's identity test then asserts the same function object, called with each site's own fleet. Name the follow-up issue (MC study, ELCC and portfolio onto sampled_fleet, and the grid double count there) in the PR body and the release note's known limits.

Follow-ups: [02](02-gate-names-the-fleet-notes.md) (the gate names the fleet notes; waits for #1333), [03](03-mc-study-elcc-portfolio-on-sampled-fleet.md) (the MC study, ELCC and the portfolio).

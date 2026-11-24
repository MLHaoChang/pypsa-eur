<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 No market data: historical prices, PPA defaults per contract form and market bundles are unbuilt (S1, S2, K8)

Status: ready-for-agent
Owner: SCU06-PLAN-1.
Blocked by: none for S1/S2; K8 needs its own gated plan (with DC-06, one reserve model)
Design: `.scratch/project-workflow/integration-sensitivities.md` §1, §2 and §6; commercial-finance.md §7 row K8 (Q8 = A).
Source: gap review 2026-11-21, design map.
Model: opus. Review: GK A/B + UX.

## Problem

Prices come only from the pinned seed series (`ipp-renewables-bess/08`). The design promises:
- historical day-ahead prices from ENTSO-E, with the user's own token and the bidding zone taken from the site pin (S1);
- PPA defaults per contract form, such as pay-as-produced and baseload (S2);
- market bundles per country with reserve products (K8, `MarketPack`).

None is filed.

## User-visible impact

An IPP or BESS business case runs on one seed price year. The user cannot pick their zone's real price history.

## Question for the owner

1. **(Recommended) S1 first**: the ENTSO-E fetch with the user's token (stored like the API key), zone from the pin, cached per year, and a plain refusal with no token. S2 next. K8 stays a plan.
2. S1 + S2 + a K8 plan as one stack.
3. Not now: the seed prices are enough.

## Acceptance criteria (S1)

- [ ] With a token set, section 7 offers "Use <zone> day-ahead prices for <year>". The series lands as a time series with its source and fetch date. Without a token, the reason is shown.
- [ ] The fetch is mocked in CI, and one live probe is documented. A wrong token gives a plain error.
- [ ] End to end in the real app: on the IPP template in Germany, fetch DE-LU 2024 (or the mocked fixture in CI), run, and see the price series named in Results › Costs & prices and in the report's assumptions.

## Likely files

new `pypsa-gui/backend/services/market_prices.py`, the time-series routes, section 7 price card, app secrets.

## Comments

- 2026-11-21: owner chose option 2, "S1 + S2 + a K8 plan as one stack" (SCU06-PLAN-1, in `docs/agents/owner-decisions.md`). S1 in progress on `claude/scu06-s1-entsoe-prices`; the section-7 offer waits for SCU03 K1.
- 2026-11-21: K8 plan verdicts.
  - **K8a (GK B4 (2)): GO with conditions.**
    1. Build nothing SCU06-K8-1..4 decides until the answers are recorded; the PR cites each answer.
    2. If AncillaryProduct is deleted, saved configs or packs holding it still load (stripped with a named flag, or a plain refusal, never a 500). Configs without it re-serialise byte-equal to master. A pin test uses a fixture with the old field.
    3. Apply is an ordinary edit: the same validators as hand entry (#738, #1466 D1), plus the write gate, lock and undo. It moves `_network_input_fingerprint` and the IC digest exactly as hand entry would; the byte-equal test extends to both.
    4. Empty refs fail closed: a template never clears or overwrites an existing export-price pick. A reserve product with no price is refused at solve and settlement with a named reason, never priced as 0; both tested.
    5. "Update available" never overwrites user edits: compare per field against what the last apply wrote (per cell, LC-43a T1), show conflicts, and list every value it would change on a card (#1379 F2).
    6. `region` is typed to S1's `BIDDING_ZONES` keys and validated at load; each part of the zone/currency mismatch refusal has its own test (#1257 F1a).
    7. Mutants on the hash, the pin, the region type, the empty-ref guard and the per-field compare, each with its killing test.
    - GK B4 (4) on the K8a build deviations (2026-11-22, verbatim): GO. The PR shows each point.
      1. Own pin sidecar `market_pack.json`: accepted.
         a) It survives save, Save-As, Save-a-Copy into another directory, and export → import → save (#1709). Test each.
         b) An unreadable sidecar (not JSON, not a dict, wrong-typed fields, [] or null) reads as "pin unreadable": no update_available claim, and apply never overwrites on it. It is never read as "no pin" (#1384 N2b, #1390 F2).
         c) Undo or hand-revert after an apply: the per-cell compare reads the reverted cell as a user edit and keeps it. Test this.
      2. Apply through `update_solver_config`: accepted, including "no undo, exactly as hand entry". Say so in the PR body.
         a) The `pack_hash` check and the per-cell conflict compare run under the same lock as the write. Test it with a pack or cell change made at lock entry, shown red with the check moved above the lock (#1378 K1b).
         b) Apply never writes a conflicting cell. It returns the cells it kept, and its "done" count holds only the cells it changed (#1175 E2).
         c) Apply bumps the network revision like any solver-config write (#1001).
         d) Until the UI card PR lands, the apply route is not chat-callable (SCU06-CHAT-1: ui-pointer). Run route_chat_disposition, project_route_acl_policy, write_surface_lock_policy and golden_coverage at the trial merge.
      3. Nullable `capacity_price`: accepted, as described, with the master pin.
         a) Condition 4's other half still applies: a pack apply never clears or overwrites an existing export-price pick. Test it.
         b) Condition 2: a saved config or pack that still holds AncillaryProduct loads, either stripped with a named flag or refused plainly, never a 500. A config without it re-serialises byte-equal to master. Pin this with the old-field fixture.
      4. `region: BiddingZone` Literal plus the equality test: accepted.
         a) An unknown region in a pack or saved config is a typed refusal with its code, never a pydantic 422/500 passing through.
         b) A project with no stated currency is refused or named. It never passes the currency check.
         c) Condition 7's mutants (hash, pin, region, empty-ref guard, per-field compare) each have a killing test listed in the body.
  - **K8b (GK A): GO, conditional on SCU06-K8-1 = (a).**
    - B1: the stored "no backup obligation" attestation is a named second floor source in `derive_floors`; the docstring says a floor of 0 comes only from it. An unreadable floor or a missing attestation stays not eligible (R3), with a test for each.
    - B2: the attestation is refused on any DC-03 string or spare and on any storage the island sidecar lists as ride-through; each refusal tested.
    - B3: products with `storages=None` widen to newly eligible storage. With no attestation, floors, `inputs_digest`, the solve fingerprint and the IC digest equal master's (test); with one, `inputs_digest` changes (test). The user text states the widening.
    - B4: the exact-value LP test (one BESS, p_nom 10, symmetric product) adds a case where the energy rows bind first (max_hours 1, sustain_h 2, so r ≤ 5), with the objective pinned to a hand-computed literal; the no-double-count test as planned.
    - B5: the planned mutants (drop r from the up row; unreadable read as 0; settle from the objective), plus dropping the d_k term from row 3 and admitting storage with no attestation. DC-03 assess byte-equal and DC strings unchanged.
- 2026-11-22: owner answers, relayed by the coordinator ("all recommended"):
  - SCU06-S2-HOME-1 = (a): the PPA/CfD defaults are shared read-only common Library items (S2a as built).
  - SCU06-CHAT-1 = (a): chat points to the screen; the project market-price routes are `ui-pointer` with decision SCU06-CHAT-1 (S1c).
  - SCU06-K8-1 = (a): storage with an attested 0 backup floor may earn reserves (K8b, GK A conditions B1–B5 apply).
  - SCU06-K8-2 = (a): AncillaryProduct is deleted (K8a condition 2 applies).
  - SCU06-K8-3 = (a): reserve prices come from user upload only.
  - SCU06-K8-4 = (a): flexible loads are deferred.
  - The ENTSO-E token (`ENTSOE_API_TOKEN`) is still to be added by the owner; E2E-DONE-1 stays blocked on it.
- 2026-11-24: **K8 status: built.**
  - K8b (attested-zero-backup reserves): #1923 merged at e6070342.
  - K8a backend (market packs, pin, apply): #1932 merged at e74e6c21.
  - K8a UI (the Market pack card, the "Confirm no backup obligation" list, and the stale-apply refetch): #1938, which carries this note. GK B2 and UX (28) gave OK at 8224aa23; master merged in since. E2E-DONE-1 passed in the real app (apply NO-1, price the products, tick bess, solve, reserve revenue in Results → Storage cycling).
  - Follow-ups filed: 06b (pin runs ahead of a discarded apply), 06c (results name products by id), 06d (no revision on the commercial PUT). Flexible loads stay deferred (SCU06-K8-4).

### 2026-11-21: S2a gatekeeper conditions (GK B4 (2))

S2a ships the PPA/CfD defaults as five `common` Library contract items (`eu-ppa-pay-as-produced`, `eu-ppa-baseload`, `eu-ppa-as-consumed-btm`, `eu-ppa-sleeved`, `eu-cfd`). No swap or shaped off-site item: there is no model type for them yet (K7 gated plan).

1. Home of the defaults: the owner answered SCU06-S2-HOME-1 = (a), shared read-only common Library items (relayed 2026-11-22), which is what S2a builds.
2. Versioning: a shipped item is never edited in place; a later change ships as a new ref version with the v1 bytes still pinned. `PINNED` covers all five, and a one-byte change fails the hash check.
3. Placeholders fail closed on the server: seller, buyer and asset_ids (a CfD's generator_owner, counterparty and asset_ids) hold a fill-in marker that can never be a valid component name. Saving refuses a contract that still holds one, with a plain message, and settlement (what billing and finance read) refuses it too. Tests: save refused with each of the three left, accepted once all three are replaced; dropping the check turns them red.
4. Every numeric value has a cited range (source, URL, year) and lies inside it; a test parses the notes. A copied contract keeps `library_ref`, so `source="illustrative"` stays traceable.
5. Settlement with exact values: the CfD settled with the reference above and below the strike (opposite signs, exact amounts); baseload at baseload_mw against a profile that under- and over-produces; sleeved, with the fee landing as a cost.
6. `changes_dispatch` is false or absent on all five, pinned by a test.
7. Mutation runs (`scripts/coord/mutants.py`) on `common.py` and the placeholder-check module, survivors explained in the PR.

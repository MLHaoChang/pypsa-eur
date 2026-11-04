<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Input gate loop L1: headless agent turns and the change ledger

Status: done (#1072, 07cc07a9b)
Type: task
Blocked by: 02, 03
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH-1, SH-ENVELOPE-1, AI-558, TOOLSEL-1
Design: [design.md](../design.md) §6.1, §6.3, §6.4, §6.5

## What to build

1. Agent turns: `loop.run_turn` on the runner session, headless, with the stage instruction as per-turn content (`harness/workflows/profiles/<profile>/<stage>.md`, package files only) and a per-stage tool allowlist.
2. Stage 2 agent fill and stage 3 loop L1: trigger = failed `block` input rules; ≤3 rounds (profile may lower); `no_progress` when the failing set and the network fingerprint repeat; exhaustion → `needs_user`.
3. What the agent may change: `tunable` attrs of the failed rules and named data gaps inside the envelope; never deletes, objective, horizon, gate rules, grid-code limits.
4. Change ledger rows (design §6.4) from verified edit receipts only; a write without a receipt fails the round.
5. Budget accounting in `run.budget.used` (rounds, tokens from budget.py) by harness code.

## Acceptance criteria

- [ ] eh_site fixture with a missing load time series: L1 attaches it within one round, the ledger has one row with its receipt, the gate passes.
- [ ] A seeded defect the agent cannot fix within its tunables ends `no_progress` or after 3 rounds, run `needs_user`, ledger intact.
- [ ] An attempted edit outside the tunables or the envelope is carded (not run) and counted as a refusal.
- [ ] The headless turn offers only the allowlisted tools (assert the offered tool list).
- [ ] Round and token use in `run.budget.used` match budget.py, not the model's text.
- [ ] `attach_tariff`, `set_weather_choice` and series attaches are admitted only when listed in the envelope with a target; unlisted, each gets a card (both cases tested).

## Likely files

`pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/harness/loop.py` (headless entry only), `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_site/` (new), `pypsa-gui/backend/tests/test_harness_run_loops.py` (new)

## Comments

- 3 Nov (SH04-OPEN-1), session session_01G7qCumQaK5z1YKhiBJ34Du on `claude/sh04-input-gate-loop`. Built in `harness/run_loops.py`, the runner's `_agent_round`, `_advance_run` and an envelope `gaps` list in `run_envelope.py`. The eh_site input gate gains `in.loads.series`. The data-gap admission rule (fill only, never replace) went to GK B as a plan question. Stage 2's agent fill has no trigger fact yet, so it is not built here: L1 covers the gaps the input gate names.
- 3 Nov, GK B (session_01G7qCumTFJ816iDVecdps7F) answered the data-gap plan question at 19:04 CET: GO for option (A), the envelope `gaps` list, on 7 conditions it checks in the code PR:
  1. **Resolve at grant, never widen.** `at_grant` stores the concrete instance uids that are gaps at the grant. At use, the target must be in that set and still empty, checked atomically with the write. A component created or renamed after the grant never matches. Tests: a new Load matching the glob is refused; a value the user set between the grant and the fill is refused as `not_a_gap`.
  2. **No bounds never means any value the model picks (AI-567b).** Every fill's value comes from a source fixed at the grant: a profile default listed in the profile; a user upload or an SH-21 intake row; or, for `apply_generated_profile`, a generator run whose inputs (peak, shape or profile id, year) are user-confirmed template params or profile defaults, never chosen by L1. The admitted row is digest-bound to its source. A fill that does not trace to a granted source gets a card. Test: a scripted provider passing its own series or number is refused.
  3. **Demand.** `Load.p_set` is a gap entry only for an electrical site load whose generator inputs meet condition 2. The never-tunable demand classes (H2 and NH3 offtake, process demand) are never gap entries, and the grant refuses them. Test both.
  4. **Prepare binding.** The `generate_load_profile` pending item is admitted only when this run's round created it, bound by pending id and task id. Any other pending item gets a card.
  5. **Tariff and weather.** `attach_tariff`: the tariff must be one already held, listed at the grant by its hash. `set_weather_choice`: only from the profile's listed choices. L1 never creates or uploads the source itself.
  6. **Narrowing.** Only profile ⊇ grant ⊇ use. Admission runs the same validators as the single-edit card path (#738) and leaves the same `_edit_receipt`.
  7. **Fail closed.** A gap entry that cannot be checked counts as not a gap and gets a card.

  GK B also checks that `session.stage_tools` only narrows the tool set.
- 3 Nov, session session_01G7qCujkDhmnhwtCpNxLfT6 took over #1072. How each condition is met:
  1. `at_grant` holds each gap load by name, with its resolved source and its identity (bus and static `p_set`). PyPSA has no uid, so a load renamed onto a granted name, or made again under it, fails the identity check (`new_instance`). Admission re-reads "still a gap" under the step's locks (`admit`, after the decision guard). The edit card's `require_current` re-preview refuses a value that moved before the handler.
  2. Each gap names a `source` in the profile, resolved at the grant. `load_generator` fixes the archetype and year in the profile, and the energy is the load's own static `p_set` × 8760 MWh: the user's own model value, so the demand's energy never changes (§6.0). `weather_choices` is the profile's list. `library_tariff` lists the held tariffs by hash at the grant. `generate_load_profile` is admitted only when its archetype, year and params equal the granted record exactly. The audit row and the ledger row carry the source digest. GK B to confirm: the static `p_set` as the generator input, in place of a template param, since eh_site runs carry no template params.
  3. Only electric loads (`site_peak.load_split`) are gap instances. A glob skips an offtake or product-bus load. A gap that names one refuses the grant (`invalid_envelope`).
  4. An admitted `generate_load_profile` result is bound in `envelope.prepared` (job id, task id, round, loads). `apply_generated_profile` is admitted only for that job in the same round, for one of its loads (`unbound_prepare`). It also refuses a `point_id` other than its own load, and any `time_basis` but UTC.
  5. A tariff is admitted when its Library hash is in the grant's list; `replace_inline` is refused. Weather is admitted only for a listed (source, year).
  6. `narrow` checks the sources too (same generator inputs, a subset of the weather choices). Admitted calls take the ordinary edit-card path (`require_current`, `_edit_receipt`).
  7. Any exception in the gap check reads `not_a_gap`, so the call gets a card.

  Also in this PR: an L1 edit outside the failed rules' `tunable` lists is refused as `not_tunable`, and a round that errors still charges its tokens. Follow-up: the user notice on exhaustion is [issue 25](25-l1-exhaustion-notice.md) (needs a channel).
- 3 Nov, GK B NOT OK @7f37c8ca, then owner decision LOAD-SHAPE-1: a constant `p_set` is the user's demand, not a gap. Reshaping it with the same annual energy raises the peak (§6.0). An unattended run may not reshape it; a shaped profile needs a card, or an SH-21 intake value the user confirmed (peak or annual energy). So, with GK B's agreement on option (A):
  - eh_site ships no Load gap entry and no `in.loads.series` rule. A constant-load eh_site run passes its input gate as before, and L1 does not start (regression test).
  - The `load_generator` mechanism stays: grant resolution, exact-copy check, prepare binding and identity. Its energy comes from `run_envelope.LOAD_ENERGY_SOURCES`, a code registry that ships empty. A profile or grant naming an unregistered source (`static_p_set` included) is refused (test).
  - The L1 tests drive a test-only rule and energy source through an autouse fixture; nothing ships them. So acceptance criterion 1 ("L1 attaches a missing series") holds only with a registered source, which SH-21 or a later owner decision supplies.
  - Also: a tariff fill needs exactly one tariff granted for the setting (the card names it); "still a gap" under the locks has its own test; each row is checked against its own grant record; the exhaustion notice is now issue 25.

## Folded from #970

Owner decision HARNESS-970-1 (3 Nov): #970's reliability items are folded into the study-harness plan. This issue's agent loop is the first consumer of three of them; each is its own issue, so nothing here changes the build in progress.

- **HH-02, requirements on assistant edits** → issue 23: the agent's edits are checked against the user's confirmed requirements at dispatch, and a violating edit is carded and counted as a refusal. Build after this issue.
- **HH-03, a durable reservation before each model send** → issue 24: the agent turn's model call is the first send it covers. It starts after this issue and after #1059 merges; this issue keeps counting rounds and tokens from `harness/budget.py` as written above.
- **HH-01A, atomic envelope edit** → issue 22: the envelope's unattended edits (including this loop's tunables) compare and write inside one project lock. Blocked by LC-40b, not by this issue.
- **Dropped:** HH-04 (typed domain references; `services/reference_resolution.py` already covers it) and HH-09B (delegation to worker agents; SH-FOLD-1 and AI-567 keep one agent loop).

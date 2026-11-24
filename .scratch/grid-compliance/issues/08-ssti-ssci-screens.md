<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-08 SSTI unit-interaction-factor screen and sub-synchronous impedance scan

Status: done

Done in #1573 (f0e56b15); #1578 (261f15fd); #1849 (0febafa6); #1879 (4b67db01). Engine, campus stage, scorecard rows and report-pack source.
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A, gridspine)
Model tier: opus
Size: M
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17); picks up SAGC-1 G8 ("later")
TRGC: 19, 20 (screening; binding studies stay in PSCAD)
Likely files: new `gridspine/static/ssti_uif.py`, new `gridspine/static/subsync_scan.py`, `gridspine/static/harmonic_scan.py` (read-only reuse of `_branches`; promote it to public, no behaviour change), `gridspine/drivers/campus_study.py` (shared; own step), `pypsa-gui/backend/services/campus_electrical_service.py` (shared; own section), new `tests/gridspine/test_ssti_uif.py`, `tests/gridspine/test_subsync_scan.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

TRGC 19 and 20 decide whether full EMT SSTI / SSR / SSCI studies are needed. A screen tells the
user early which nearby machines or series-compensated lines warrant them.

## What to build

1. **UIF screen** (EPRI method): UIF_i = (S_plant / S_i) · (1 − SC_i / SC_tot)² for each nearby
   synchronous machine i, with SC from the existing fault levels with and without the plant.
   Flag UIF above the profile threshold (default 0.1, labelled "assumed").
2. **Sub-synchronous scan**: R(f) and X(f) seen from the PoC for f in 5–(f0 − 5) Hz, with series
   capacitors modelled; report frequencies where X crosses zero with R < 0 margin risk, and series
   resonances against machine torsional modes where given.
3. Rows labelled "screening; binding answer is the EMT study".

## Done when

- UIF: hand calculation on a two-machine fixture to 1e-6; a machine with SC_i = SC_tot gives 0.
- Scan: a radial line with series capacitor of compensation k shows electrical resonance at
  f0·√k within one frequency step.
- Threshold edge test kills "≥ vs >"; no profile threshold → "assumed" label present.
- Existing harmonic-scan tests pass unchanged.

Release note: one new file under `doc/release-notes.d/` (see its README).

## Comments

**2026-11-17, GK A2 plan GO** (two PRs: engine, then campus step + service wiring).

- SC_i is the fault level at the evaluation bus with machine i out, SC_tot with every machine in (EPRI). The "with and without the plant" wording under What to build is wrong; read it as with and without machine i.
- UIF is evaluated at each converter connection bus with its own S_plant (BESS, PV, wind, fuel cell, UPS and the rectifier-fed load, counted conservatively), not only at the PCC, where the grid's infeed understates it. The PCC row is information only. The verdict is the worst over buses and over the max and min fault-level cases, with c stated per case.
- SC_i above SC_tot within a stated tolerance clamps (noted); beyond it refuses. Every input is checked finite.
- The scanned network is passive, so R at a crossing is information only; SSCI rows read "not evaluated: converter damping not modelled". Verdict states: flag, clear, check (no torsional modes), not evaluated (no series capacitor in the model: "grid-side series compensation needs the TSP's data"). The torsional margin uses the complement f0 − f_er.
- The campus has no series-capacitor entry (that is `ingest/`, GC-01): the follow-up goes to the coordinator.
- PR2 adds the two frontend lines a new run stage needs (CampusRunStage, STAGE_LABEL) and no UI section.

**2026-11-17, GK A2 plan GO for PR3 slice A** (scorecard rows `ssti_uif`, `subsync_series_resonance`, `ssci` in `services/adequacy/grid_readiness.py`). Slice B, the `SOURCES["GC-08"]` provider in `grid_compliance/sources.py`, is a separate PR after #1577 merges. Conditions:

- R1: the limit and its source come from the run's meta, not today's profile. A profile threshold that changed since the run reads unknown: "the UIF threshold changed since the run: rerun the grid study".
- R2: fail closed. An unknown engine verdict, or a blank, non-finite or non-numeric worst UIF, or an empty UIF table with no error json, each reads unknown with the reason. One test per case.
- R3: each mapped verdict pins its reason text (no genset, no converter, no torsional modes, TSP data, error text, no run, pre-stage run with the VS meta but no SSTI meta).
- R4: off-grid keeps `ssti_uif`; confirm what the stage does on an islanded campus and test it.
- R5: the end-to-end oracle is hand-computed UIF from two gensets of different ratings and SC shares, with no import of the ssti code.
- R6: add the ids to every registry keyed by scorecard id, or say why not; ids unique (test).
- R7: mutants.py in a subagent, plus hand-mutating each verdict-map entry and the off-grid exclusion set.
- R8: Journey-fit paragraph in the PR body, and a release-note fragment.
- R9: an assumed threshold's "clear" reads pass, with the note "against the assumed screening threshold"; every row is assumed=True.
- Torsional-mode input becomes a separate GC-01c, not part of GC-01b (sent to the coordinator).

**2026-11-21, GK A2 (6) plan GO for slice B** (`SOURCES["GC-08"]` in `grid_compliance/sources.py`), recorded verbatim as GK A2 resent it on 2026-11-22:

> GK A2 (6): GC-08 slice B plan is a GO, on conditions S1–S7. Record them verbatim in issue 08's ## Comments.
>
> Question: (a). The provider raises on an error json or an unreadable meta, so sources.read gives "unavailable". That is sources.py's documented contract, the scorecard row carries the reason, and a one-row "present" table would report a failed package as delivered. Raise one named exception class (not a bare Exception) whose message is plain text.
>
> S1. One reader. The pack tables come through the same validated read the scorecard rows use (the fail-closed guards from #1849), never a second parser of the run files. Where the scorecard row reads unknown (damaged table, contradicting verdict, profile moved since the run, unreadable verdict), the table's caption states that row's reason. The Verdict column must not show a judged verdict the scorecard refused. Test: a damaged UIF table and a moved profile give a caption that matches the scorecard note, and no judged verdict.
>
> S2. One off-grid rule. R4b uses the scorecard's own predicate through the small public helper. A test mutates the helper's result and shows both the scorecard and the pack follow it. Cover these cases:
> - off-grid with an open tie: caption "islanded case not evaluated…", each converter-bus verdict "not evaluated (islanded)", never the grid-counted UIF given as the island answer;
> - grid-connected with an open tie: judged, unmarked;
> - off-grid by label only (tie closed): judged.
> Off-grid leaves the subsync table out, as the scorecard leaves out its PCC rows.
>
> S3. Literal expectations. Column lists and rows are compared whole against hand-written lists, including one blank cell and one non-finite cell ("—"). Never build the expectation from the module's own constants or formatter (rulings 10 and #1786 M6a).
>
> S4. Plain values. No repr in any cell or caption. A non-string note cell reads as an unreadable note, as the scorecard does. source_path is relative to the project, never absolute.
>
> S5. One real stage. The two-genset hub runs through sources.read and gives "present", with the UIF worst row equal to the scorecard's project_value. Pre-stage gives pending.
>
> S6. End to end. Before review, generate the GC-08 pack entry in the real app on a grid project and on an off-grid project with an open tie. Send me the two captions and the rendered tables (screenshot via shots.sh, .small only). I will repeat it.
>
> S7. File .scratch/grid-compliance/issues/08c-subsync-reason-order.md as you listed, in this PR. Also add a release-note fragment, and the Journey-fit paragraph in the body as for slice A.
>
> Size ~400 lines is fine. Send the PR when it is ready.

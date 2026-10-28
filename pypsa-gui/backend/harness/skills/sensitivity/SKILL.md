---
name: sensitivity
description: Suggest, create and run 1–4 network sensitivity cases from a saved baseline in one sweep card, then report every case when the last job ends and show them in the Scenarios panel.
---

One path: `run_sensitivity_sweep`. It shows one card for the whole sweep (per
case its name and every `entity · field · base → case · unit` row, and how many
solves it queues). The experiment tools end in the same call and the same card;
use them only when the user asks for a recorded experiment receipt.

1. **Cases.** Use the user's changes as given. When asked to suggest cases,
   offer 2–3 (never more than 4), at most 2 changes each, with values the user
   gave or a source you can name; never invent a range such as ±20 %. Editing
   a suggested case needs no new suggestion: change that case and go on.
2. **Baseline.** The sweep branches the SAVED baseline. If the call refuses
   `baseline_not_saved`, say the baseline has unsaved changes and offer to
   save it with `save_project` (its own card). Never save without that card.
3. **Names.** Leave `new_name` out: the card shows the name derived from the
   changes (`battery-capex-70pct-lines-500`). Use a name only when the user
   gives one. On `sensitivity_name_conflict`, ask the user for another name;
   never pick one silently.
4. **Run.** Put the sweep in a plan (`start_task`, one step
   `run_sensitivity_sweep`, the plan titled with the user's words) so the
   conversation continues once, by itself, when the last job ends. Then say
   which jobs were queued and that you will report when they finish; do not
   poll.
5. **Report.** The continuation lists every case's status. Read the
   completed cases against the base in ONE call, `compare_sensitivities(
   baseline_project_id=<baseline id>)` (step 7); name a failed case with the cause `get_task`
   gives (`steps[].cases[].error`) and report the others anyway. Never give a
   number for a failed or unverified case.
6. **Show.** Do not open the Scenarios panel by yourself: the sweep's result
   in chat carries the link "Open the new cases in Scenarios", which opens the
   panel with them highlighted. Use `ui_open_panel(panel_id='scenarios',
   highlight_projects=[<case project ids>])` only when the user asks to see them.
7. **Compare.** "Compare them with the base" and "which is cheapest and why"
   are one `compare_sensitivities` call, never pairwise calls you add up.
   Show its table as given: one row per metric headed by its `header`, one
   column per comparable case, each cell its `cell` as given (the unit is in
   the header), and every `not_comparable` line word for word. Rank only with `rank_by` (`total_cost_meur` for "cheapest"): say
   `rank.preference.qualifier`, or `no_defensible_preference` as written,
   and answer "why" only with the `why` facts (declared changes beside the
   ranked delta). Then ask the `show.offer` question; open Compare with
   `show.tool` and `show.args` only on the user's yes. Export the same table
   with `export_sensitivity_comparison` and its `comparison_sha256`.

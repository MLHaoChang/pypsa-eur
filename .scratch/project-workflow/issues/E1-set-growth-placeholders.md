# E1 follow-up. Setting a growth placeholder

Status: done (#733)
Type: task
Blocked by: E1a merged (`claude/e1a-planning-years-staged-dc`)
Gated: yes, gatekeeper (adequacy and the investment case read the result). Send the plan first.
Model tier: opus
Size: M

Read [E1](E1-examples-any-planning-years.md) and the E1a PR (#540) first.

## Goal

A staged example built for entered planning years marks a growth value it has no figure for as a placeholder: `growth_inputs` rows with `set: false` in `eh_template.json`, an assumed ledger row, `energization_peak:<period>` unknown in the scorecard, `site_peak.peak_check(unset=)` unknown, and a `growth_placeholder:<kind>_<year>` flag on the investment case and the chat tool's solved PPA price.

Nothing sets one today. Only the template build writes `growth_inputs` (`eh_templates._dc_staged_meta`, and the staged hubs' metas from E1b), and a flow acknowledgement stores an ack, not a value. So editing `grid_import.eh_energization_mw` for 2038 and acknowledging leaves `energization_peak:2038` unknown and the case flagged for good. The notes say so and name this ticket (gatekeeper review of #540, 2026-10-21 19:59 CEST, option (a)).

## Done when

- An edit of the field a placeholder row names (`component` / `entity` / `field` in its ledger row; per period for a time series such as `eh_energization_mw` or `p_set`) flips that row's `set` to true, with `source: "entered"`, in the project's `eh_template.json`.
- Its period then reads a verdict in the peak check and the scorecard, and its flag leaves the investment case and the PPA flags.
- An edit back to the placeholder value keeps it set: the user confirmed it. An acknowledgement alone does not set it.
- Tests per kind (`it_mw`, `energized_mw`, then `h2_offtake_mw` and `load_scale` from E1b).

## Notes

- The ledger notes and the scorecard note name this ticket (`eh_templates.PLACEHOLDER_FOLLOW_UP`). Reword them when it lands.
- `load_scale` covers several loads per period: decide whether one load's edit sets the period, or each load needs its own.

# Chat refinement of a generated series

Status: needs-triage
Type: task
Blocked by: 01 (the first family to refine); copilot LC-04 (the old→new card and the chat-edit path); the approved project workflow (placement)
Gated: yes (apply path), plus the UX reviewer for the UI
Model tier: opus

Read [the spec](../spec.md) first.

## Goal, in the owner's words

> "The user can parameterize the time series in the front end and visualize it, for example for exemplary weeks or a selected time period. But then the person should be able to say, yeah, I think the peak demand is slightly too much… it reaches 80%, but maybe we should reach 75%. But then on the hours between 5 to 10, instead of the current very low demand, we actually expect 30% more demand… give this type of suggestions to a bot that then can do the automation part and provide new suggestions."

## What to build

1. **Preview.** Every generated or uploaded series shows a chart with a period picker: exemplary weeks (peak week, typical week, a chosen week) or any date range, plus the summary numbers: peak, peak as % of nameplate/connection, annual energy, load factor.
2. **Refinement operations.** A small closed set of deterministic edits, applied by the backend, never by the model writing numbers:
   - `scale_peak_to(fraction | MW)`;
   - `scale_window(hours, days?, months?, factor)`, e.g. hours 05–10 × 1.3;
   - `set_floor` / `set_cap`;
   - `shift(hours)`;
   - `smooth(window)`;
   - `rescale_energy(MWh)`.
   Each op is applied to the parameters where the generator exposes them (e.g. a morning ramp), otherwise as a post-transform on the series.
3. **Chat path.** The copilot turns a sentence into ops ("peak 75 % instead of 80 %, and 30 % more between 05:00 and 10:00" → `scale_peak_to(0.75)` + `scale_window(5–10, 1.3)`) and shows the **old→new card** (owner decision Q3, LC-04). The card overlays old and new on the same chart and window, with a delta table (peak, energy, load factor, hours affected). Nothing is applied until the user accepts. It may also propose follow-ups ("energy rose 4 %. Keep the annual total?").
4. **Same ops without a key.** Without an API key, the same ops are available as controls under the chart. Chat is a faster way to the same edits, not the only one.
5. **Provenance and undo.** The series records its generator, parameters and the ordered op list. Provenance reads "synthetic, user-refined", and every op shows in the change log and can be undone. Re-running the generator replays the ops.
6. **Applies to every family.** The same ops work for loads, availability (e.g. "move the maintenance window two weeks later") and other series, where the op makes sense for the target field.

## Done when

On the data-centre, industrial and residential archetypes, the owner's example sentence produces the two ops, the old→new card shows the right deltas, accepting applies them, undo reverts them, and the same result is reachable through the controls without a key.

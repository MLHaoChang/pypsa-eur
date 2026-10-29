<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-27 — the copilot's island report never shows the frequency events

Status: done

Found by LC-06 (5) while building S8a (LC-D16). `campus_island_report` returns
`island_report(project)` through `_b2b_fit` (`services/chat_tools.py`). On a hub
with a campus Run, the payload holds 21 tables. The fit trims the largest list,
`tables`, down to the first two (`requirements`, `units`), and `truncated`
says `{"tables": {"total": 21, "shown": 2}}`. So the copilot never sees
`freq_events`, `freq_e1_hourly`, `freq_ups` or any of the other frequency and
static tables. It cannot quote RoCoF, nadir, Δf_qss or a collapse for E1–E6.

Reproduce: the `make_hub` fixture in
`pypsa-gui/backend/tests/test_lc06_d16_frequency_events.py`, then
`chat_tools.campus_island_report("Freq Hub")`.

LC-06 S8a reads the events through `GET /api/campus-electrical/{name}/island/report`,
which returns the whole payload.

Options for whoever takes this:
- A `table` argument (a table id) on `campus_island_report`, and a description
  that lists the ids. The catalogue, the route parity and the tool-list tests change.
- Trim rows inside each table rather than whole tables, so every table keeps
  its first rows. No schema change, but a 21-table payload may still not fit.

## Comments

GK B plan GO, first option, conditions (all met in `test_lc27_island_report_table_arg.py`): one `TABLE_IDS` literal feeds the schema enum, the description and the refusal, and a real full report's ids are a subset of it; rows over the cap say shown of total and point at the route; an unknown, empty, wrong-case or non-string id is `invalid_tool_args` (no new kind); `table` is checked after the campus resolution, so a missing campus is refused identically with or without it; the no-argument answer keeps its two tables and adds `table_ids`.

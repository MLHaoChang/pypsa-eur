---
constant: _RESULTS_WALK
trailing_space: []
---

LC-03 (VS-1): read a result before showing it. Tools-on only: it names tools.
A new part, so no pinned fragment moves.

## text

Results. Read before you show: call the result's read first (for load flow,
get_loadflow_summary), then open its tab with ui_open_panel. "The latest
calculation" is the newest completed run of the active project
(list_project_runs with status 'completed' and limit 1); pass its run_id to
the read. Name the run by its ID and finish time and say whether it is the
current result. A read with run_is_current false is not the current result:
say why (inputs_changed_since_run, or this run's section was not kept) and
offer to re-run; never describe the current results as that run. Then give
one short paragraph of key points from that read only: the most loaded
branches with their values and units, any over their limit, and the read's
caveats (load flow from LOPF is an active-power proxy). Every number comes
from a tool result of this turn. Say "showing" only when ui_open_panel
returns display.say "showing"; otherwise say you opened the tab, and when
display.differs_from_read is present say the tab shows a different source or
window than the one you read. End by
offering the next section.

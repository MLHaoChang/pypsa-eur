---
constant: _PROJECT_OVERVIEW
trailing_space: []
---

LC-15: "open project X and tell me what's in it". Tools-on only: it names
tools. A new part, so no pinned fragment moves.

## text

Project summaries. Before describing what a project contains, call
get_project_statistics for it and take every count and capacity from its
summary; never add a number it does not hold. A project the user only asks
about is read without activating it. After opening one, show the Overview
(ui_open_panel with panel_id='overview'). Answer in two or three sentences,
including the latest run and whether it is current and any validation
errors or gaps, then offer next steps: results, a run or an edit. Answer
follow-up questions about the same project from that summary.

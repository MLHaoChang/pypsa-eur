# PR #792 (LC-02 plan card) UX shots

Built app at PR head 4c6a48dd, with a real login and a real project (oj-grid, via `composer-fixture.py --journey oj21`). The chat stream is fulfilled with `tool_result` frames whose `_task` views are written by the backend's own `assistant_tasks._view` (`views.py`; `shots.mjs` drives the browser). No model and no paid inference.

Each state is shot in Expert and Guided: `<mode>-<state>-close.png` (the card) and, for `next` and `review`, `<mode>-<state>-dock.png` (the whole page, card in the dock).

- `next`: ready; the next step is bold and marked `aria-current`; Applied lists the first step's receipt.
- `waiting`: waiting_job. Expert reads "waiting for job job-7f3c21"; Guided reads "waiting for a background job". The existing LC-12 waiting line sits below.
- `brief`: "Size the battery for the evening peak · phase 2 of 3: AC power flow & grid" (the `CARD_GROUPS` label).
- `trimmed`: 12 steps, of which the backend's 3800-character cap keeps 7; "Some steps are not listed."; Applied with 11 receipts. Applied is a list: as one `;`-joined line, 11 receipts read as one paragraph.
- `review`: needs_review, with a failed step and a needs-review step, and the step error below.

`guided-companion.png`: the dock is closed and the same turn is sent from the companion. The companion shows the turn's outcome line ("Answer not checked · Show answer"), not the plan card; the card stays in the dock transcript, as before this PR. Two notices in that shot come from the fixture's fake chat session, not from the change: "Current access is unavailable" and "Chat session is unavailable".

`report.json` holds each card's text.

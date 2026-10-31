<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-39 — The HTTP adequacy readers serve pre-edit numbers with no freshness marker

Status: needs-triage
Type: bug

Found by LC-37. The manifest row is `LC-D13.stale_source_http_readers` (`_pending`).

LC-37 put `edited_since_study` on the chat readers (`get_adequacy_results` for `frontier`, `mc`
and `margin_loop`, and `campaign_status`). The routes behind them, `GET /api/results/frontier`,
`/mc` and `/margin_loop` in `routers/results.py`, still serve the record without it, so the
panels show these numbers unflagged after an edit. LC-37 left `routers/results.py` alone
because RW-04 owned it at the time.

Each record already carries `network_revision` (captured when the study started), so the fix is
the EH route's line: `out["edited_since_study"] = study_state.edited_since(st)`, then a banner
in each panel.

## Acceptance

- [ ] `GET /api/results/{frontier,mc,margin_loop}` carry `edited_since_study`, False after a
      run, True after an edit, cleared by a re-run (one HTTP test per route).
- [ ] Each panel shows the stale state.
- [ ] `LC-D13.stale_source_http_readers` becomes `_covered`.

## Comments

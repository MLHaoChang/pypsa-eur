<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-29 — the HTTP GFM apply checks no card when no digest is sent

Status: done

Found by LC-06 (7) while building S9 (LC-D17); GK A (4) asked for the ticket.
`POST /api/campus-electrical/{name}/island/gfm-settings/apply`
(`routers/campus_electrical.py`, `apply_gfm_settings`) checks the card only
when the body carries a `digest`. With no body it applies whatever the
recommendation is now, with no check that the user saw those values.

The recommendation's own staleness still refuses a network, campus-file or
library edit since the run (409). `tests/test_lc06_d17_recommend_apply.py`
`test_the_http_apply_without_a_digest_still_refuses_a_stale_network` pins that
guard while this ticket is open.

Options for whoever takes this:
- Require the digest on the route (a 422 without it). The Results tab's apply
  button must then send the card's digest; check the frontend caller first.
- Keep it optional, and record that the route's callers are trusted to have shown the card.

## Comments

- GK B (10) plan GO, 29 Oct, with conditions: `digest` is required (422 without
  it); every caller of the route checked (only the frontend's
  `campusApi.applyGfmSettings`, which always sends the card's digest);
  `require_current` before any write, the lock checked first.
- Done in PR `claude/lc-28-29-gfm-apply-digest`.

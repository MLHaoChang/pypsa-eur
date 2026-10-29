<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-28 — the GFM apply card's digest does not hash the network

Status: done

Found by LC-06 (7) while building S9 (LC-D17); GK A (4) asked for the ticket.
The card for `campus_apply_gfm_settings` (`harness/edit_card.py`, its adapter
and `_digest`) hashes the card's rows, says, sets and binding. Its rows come
from `services/campus_island_tuning.py` `card_rows`/`_plan_apply`, which read
the recommendation, the campus file and the sidecar, never the network. A
network edit after the card was shown leaves the digest as it was. The
fingerprint helper beside it in `edit_card.py` is not used by this apply.

Today a network edit is still refused, but by another path: `get_settings`
reads the recommendation as `stale` (`campus_electrical_service._stale`
compares the network's digest with the campus run's manifest), and
`_plan_apply` refuses with 409, mapped to `edit_preview_stale` on the card
path (`_card_refusal`). `tests/test_lc06_d17_recommend_apply.py`
`test_a_network_edit_after_the_card_refuses_the_apply` pins that. The reason the
user then reads is "no recommendation for the last campus run; recommend GFM
settings again", although a recommendation exists and only went stale.

Options for whoever takes this:
- Put the network fingerprint in the card's binding, so the digest moves with
  the network and the refusal no longer leans on the run manifest.
- Leave it, and record that the run manifest is the network guard for this card.

## Comments

- GK B (10) plan GO, 29 Oct, with conditions: bind `network_file` (the named
  project's saved `network.nc` digest) and `network_live` (`_fingerprint()`, only
  when the named project is the active context), both read through `_campus`;
  tests for a saved edit with the run manifest made to agree (binding alone
  refuses), an unsaved edit of the active project, and an edit to another active
  project that must not refuse, each mutation-checked; the stale reason replaces
  "no recommendation" (not_run unchanged); refusals assert file bytes and mtime.
- Done in PR `claude/lc-28-29-gfm-apply-digest`: `harness/edit_card.py`
  `_gfm_binding`, `campus_island_tuning._plan_apply`.

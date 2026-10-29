<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-28 — the GFM apply card's digest does not hash the network

Status: ready-for-agent

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

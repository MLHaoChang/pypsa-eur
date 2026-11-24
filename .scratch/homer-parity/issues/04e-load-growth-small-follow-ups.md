# 04e — Load growth: small follow-ups from the 04a review

Status: needs-triage

Follow-ups noted in #1604 (homer-parity 04a). None blocks.

1. **Raw names in the chat card (RAW-NAMES-1).** The `update_solver_config` card's load-growth rows and their `says` sentence name the carrier by its key ("Load growth electrical", "Load multiplier electrical 2035: 1.104 → 1.159"), while Model Horizon shows plain carrier names. Use the same plain name in the card and its `says`. This needs a UX check.
2. **One list of the growth keys.** `simulation._LOAD_GROWTH_KEYS`, `load_growth._stored_after` and `edit_card._LOAD_GROWTH_INPUTS` each list the growth fields by hand. They differ on purpose (PUT trigger keys, derivation inputs, card rows), but they could derive from one source in `load_growth.py` so a new field cannot miss one. GK B area.
3. **Reports.** No report or export names the load scalers or the growth assumption. Add the rate, start year and derived multipliers where the study assumptions are listed. Consider it with 04b.

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-01 — coordinates: generator first, refuse when missing

Status: ready-for-agent
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: the profile stack landed (RP-Q1)
Requirements: R1 in [the spec](../spec.md)

Fix the silent (0, 0) default in `points_from_generators` (`routers/profiles.py:106`, PR #398).
It is the one resolver behind the HTTP routes and the chat tools' `from_generators`.

- PyPSA generators have no coordinates of their own: a generator sits at its bus, so the
  bus `x`/`y` are its coordinates. Use the one existing rule, `network_geometry._bus_coord`.
- Missing: NaN in either value, out of range, or both exactly 0. Refuse with a 422
  `{"error_kind": "missing_coordinates", "message", "generators": [...]}`. The message names
  both remedies.
- Explicit `points` for the same id still win; they are the override.

Acceptance (tests first, `tests/test_profiles_router.py` and `tests/test_chat_tools_profiles.py`):
- A bus with x=y=0 → 422 `missing_coordinates` naming the generator. Today this generates at 0/0 (RED).
- A bus with NaN y → the same refusal.
- A bus out of range (y = 91) → the same refusal.
- An explicit point with a generator's name overrides it, but an unknown name is still `unknown_generator`.
- Apply warns "no coordinates now" when the bus lost its coordinates after generation.
- The chat tool `generate_renewable_profile(from_generators=[...])` gives the same typed refusal, and the words relay both remedies.
- One request with two generators, one missing → the refusal lists only the missing one, and nothing is queued.

## Comments

2026-10-19: implemented on `claude/rp-01-coordinates` (stacked on #398's head). Review dropped
the generator-x/y branch: nothing in PyPSA or the app sets generator coordinates.

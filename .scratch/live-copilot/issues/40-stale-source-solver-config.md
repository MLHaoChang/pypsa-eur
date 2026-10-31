<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-40 — A solver-config change does not mark an adequacy study stale

Status: done
Type: bug

Found by LC-37 (GK A review). The manifest row is `LC-D13.stale_source_solver_config`
(`_pending`).

`edited_since_study` (`services/study_state.edited_since`) compares the project's network edit
counter. That counter moves on a network edit (the chat seam for tools that write
`/api/network/` or `/api/io/`, and the HTTP middleware on the same prefixes). It does not move
on `update_solver_config` (`PUT /api/simulation/solver_config`). So a frontier read after a VoLL
change, or a margin loop read after a `reserve_margin` change, reports `edited_since_study:
False` while its numbers came from the old config. The EH study has the same limit.

## Acceptance

- [x] Decide whether a solver-config change counts as an edit for the study readers (a
      separate config revision, or the same counter), and say so in `edited_since`'s docstring.
- [x] A chat-tool test: a frontier, then `update_solver_config(voll=...)`; the reader says the
      study is stale, and a re-run clears it with the hand-moved knee.
- [x] `LC-D13.stale_source_solver_config` becomes `_covered`.

## Comments

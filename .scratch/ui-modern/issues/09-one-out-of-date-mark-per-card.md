<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# One "Out of date" mark per card

Status: needs-triage
Type: feature
Reviewer: UX.

From the screenshot audit (`audit.md` finding 10, third bullet). Split out of 04.

A dispatch run in Expert turns the Guided verdict card "Out of date" with a banner and a chip on each tile (shot `dc-guided-light-s10.jpg`). The state is correct, but three marks on one card is loud for one fact. Show it once per card (the banner, with its re-run link), and keep per-tile chips only where a tile's freshness differs from the card's.

The verdict card is in the files of ui-modern 03 and 05, so this starts after their open PRs merge.

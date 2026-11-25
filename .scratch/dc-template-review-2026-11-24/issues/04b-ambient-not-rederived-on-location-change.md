<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04b The data-centre ambient is not re-derived when the site location changes

Status: needs-triage
Blocked by: DC 04 merged
Source: GK A review of DC 04 (PR #1969).
Model: sonnet. Review: GK A.

## Problem

DC 04 picks the ambient temperature (climate-zone preset) from the site latitude given at create and stores it in the data-centre spec (`ambient_source`, `ambient_entered`). The facility load is built into the network at create too. Moving the site anchor afterwards (Guided "Open here", Latitude/Longitude) leaves the stored ambient, the baked load and the load card's basis as they were.

## Acceptance criteria

- [ ] Either a changed anchor latitude re-derives the ambient fields the user did not enter and rebuilds the facility load (with an undo), or the card says the ambient was fixed at create and offers to re-derive.
- [ ] A test: create at a temperate latitude, move the anchor to a hot one, and the peak PUE and peak facility MW follow (or the card names the stale basis).

## Comments

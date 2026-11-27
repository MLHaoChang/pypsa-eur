<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The navigation ACK binds the tab's default view, so other views can only say "opened"

Status: needs-triage
Type: task

## Problem

- **Default view only.** `navigation.issue` reads each displayed-data tab's default view (every index null). A tab shown at a non-default period, window or source therefore gets 409 on its ACK, and the reply says "opened" rather than "showing". Fix: bind the navigation target's own params at issue time.
- **Bounded summaries.** The chat reads for Energy flow and Load shape are bounded summaries, not the route payload, so their facts can't be matched to the displayed hash.
- **Wrong read for Storage.** Storage's chat read is state of charge, not `storage_dispatch`; add `storage_dispatch` to the views' reads.
- **Overview.** Overview's read is vintage.

All of these can only say "opened".

## Found in

Assistant-assurance 06 S3b (#2045, #2046), recorded in the AA 06 (4) hand-off.

## Acceptance

- A Results navigation at a non-default period, window or source issues an ACK bound to those params and can say "showing".
- Each displayed-data tab whose chat read matches the shown data says "showing". The rest keep "opened", each with a test that names why.

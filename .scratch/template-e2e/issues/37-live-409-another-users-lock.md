<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 37 — no live test for another user's lock (409)

Status: needs-triage

Open item from the template-e2e 02 stack hand-off.

## Problem

02b-1's lock refusal for a different user is only covered by unit tests, not against the running API.

## Wanted

A live test where user B edits while user A holds the lock and gets 409 with the holder named.

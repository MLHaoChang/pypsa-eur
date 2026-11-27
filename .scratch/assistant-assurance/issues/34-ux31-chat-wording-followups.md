<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Chat wording follow-ups from UX 31's review of #2032

Status: needs-triage
Type: task

## Problem

UX 31's review of assistant-assurance 06 S4 (#2032) left these raw or awkward strings in the chat. Each breaks RAW-NAMES-1 or reads as machine output:

- "1 facts": a count with no singular form.
- The Expert ConfirmationCard shows raw JSON arguments.
- "energy: answered".
- The evidence cost basis joins its parts with a "; " chain.
- The task card shows "Next: wait_for_job (pending)" and "ready".

## Acceptance

- Each string reads as plain words: correct plurals, no tool names, and no raw JSON or status codes. Each has a test pinning the new wording.
- The UX reviewer rules on any string whose new wording is unclear.

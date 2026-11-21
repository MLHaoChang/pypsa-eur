# The levers scroll request is not cleared when the levers fetch fails

Status: ready-for-agent

Raised on #1752 (GK B3 (3) nit c, accepted as a follow-up).

`EhReferenceDesignPanel` (around the `LEVERS_SECTION` effect) clears
`resultsSectionRequest` only after it scrolls to the levers block. If the
levers query fails or returns no options, the request stays set, so a later
remount with levers jumps to that block unasked. The risk is low because the
"Design options ›" link only shows when levers exist.

Ask: clear the request when the levers query settles without a levers block
(error or empty), with a test for each.

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Still true: the levers scroll request is cleared only after the scroll. Acceptance as in the Ask: cleared when the levers query errors or returns no options, one test for each. Gated: GK B3. Blocked by: none.

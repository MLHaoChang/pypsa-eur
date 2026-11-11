# Issues panel: the Findings section clips its own content at 1440×900

Status: ready-for-agent

Found while re-shooting grid-to-process issue 15 (#1142); filed at the UX reviewer's (9) request, separate from that PR.

## What happens

On the Issues page (Expert, and Guided via Ctrl K → "Issues"), at a 1440×900 viewport (also 1440×1000), the **Findings** `PageSection` shows only part of its body: about 163 px of 497 px of content. The rest is cut off by the section's own `overflow-hidden`, and the hidden part does not scroll into view by hand. In the observed case, a `heat_level_above_reach` error message was cut mid-sentence, below the snapshot-weights and network-islanded warnings. At 1440×1500 the section shows in full.

## Where to look

- `pypsa-gui/frontend/src/pages/IssuesPanel.tsx`: the `<PageSection title="Findings" …>` inside the page body.
- `pypsa-gui/frontend/src/components/PageKit.tsx`, `PageSection`: the root has `rounded-[10px] … overflow-hidden`. The section is probably shrunk as a flex child (a flex/min-height interaction) rather than growing and letting `PageBody` scroll. Not diagnosed.

## Acceptance

- At 1440×900 every finding in the section is readable, either by the section growing and the page scrolling, or by the section body scrolling. No content is clipped beyond reach.
- A test or a shot at 1440×900 shows it, in both Guided and Expert.
- Other `PageSection` users keep their layout (check one or two other pages).

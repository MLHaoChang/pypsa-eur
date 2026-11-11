<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Always-on chrome: the floating chat button and the repeating Guided banner

Status: needs-triage
Type: feature
Reviewer: UX.

From the screenshot audit (`audit.md` finding 8). Split out of 04, whose slices own the canvas and solver progress, not the assistant chrome.

- The floating "Chat here" button covers the bottom-right of every screen: charts, tables and the network canvas (shot `dc-expert-dark-ops-operation-1.jpg`). It should not sit over content the user reads or clicks.
- In Guided, the "Every step below works by hand… Set up the assistant" banner (`pages/hubDesign/shared/AssistantSetupCard.tsx`) shows at the top of all 10 sections and returns on each visit after it is closed. Closing it once should hold for the project, or it should show in one place only.

Files this touches (`components/AssistantDock.tsx`, `CompanionLauncher.tsx`, `AssistantSetupCard.tsx`) are also in the open live-copilot and ui-modern 05 PRs, so it starts after those merge.

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guide tour: opt-in only, numbering and copy out of step with the stepper

Status: needs-triage
Type: finding (keyed walkthrough audit, UX-NEXT-1)
Journey steps: J1 Pick template, J2 Set up (onboarding)
Severity: medium
Send to: next-step flow (onboarding)

## Judgement of `components/GuidedTour.tsx`

The component works: 7 popovers, Back, Next, Skip tour, a highlight ring, a "not yet viewed" dot. It is never offered by itself. It starts only from a small dotted "Guide" button under the stepper (`data-testid=hub-guide-button`); a new user who does not spot it never sees it.

Problems:
- Titled "Design a hub in five steps", with "1/7" … "7/7" popovers, while the stepper has 10 numbered sections. Popover 2 says "Pick a starting point" and popover 5 "Set your goal", but the tour never moves to those sections: it stays on the open one and the popover covers the control it describes (`shots/mg-tour-3.small.png`).
- Says "the assistant can do any step for you" while no key is set and the banner above says every step works by hand.
- The same hub-design tour is the only one in Guided; the Expert side has none for the workbench.

## Done when

First open of a project offers the tour once (or an equivalent first-run checklist), its steps match the 10 sections and jump to them, and its wording follows whether the assistant is on.

## Journey fit

J1 to J2: the tour is the hand-off from picking a template to filling section 1.

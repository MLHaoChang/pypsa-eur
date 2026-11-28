<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Tell a viewer's chat its role up front

Status: needs-triage
Type: task
Source: UX 35 review of #2151 (issue 05)

## Problem

A viewer's assistant is never offered write tools, so a model asked to edit gets `tool_not_offered` instead of a plain reason. The model should know the user's role so it can say: "You can view this project. Ask its owner for edit access." The owner's email must not go to the model provider (GK B3, #2151).

## Acceptance criteria

- [ ] The viewer's chat context carries the role (no owner email).
- [ ] Asked to edit, the assistant relays the viewer sentence, unnamed.
- [ ] A test pins both.

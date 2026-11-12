<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 44 — offer eh_site@3 to start_task

Status: needs-triage
Type: task
Gated: yes (GK B)
Parent: [33-eh-site-fetch-site-context.md](33-eh-site-fetch-site-context.md) (GK B (50) reachability note on #1417)

Issue 33 (#1417) adds `eh_site@3`, which fetches the site's map context in the inputs stage.
Nothing can start it yet. `start_task`'s `run.profile` enum in `harness/catalogue.py` is
`["eh_site@2"]`, so `guards.check('start_task', …, 'eh_site@3')` returns
`required_guard_failed`. No REST or frontend path starts it either. Until this lands, the step
is reachable only from tests.

Switch the offered version to `eh_site@3`, or offer both versions. Then make the model's intake
ask for the site (`site.lat` and `site.lon`, in degrees) as typed text.

Acceptance:
- [ ] `start_task` accepts `eh_site@3`, and a new chat run on it reaches the inputs-stage fetch.
- [ ] Runs already granted on `eh_site@2` keep `@2` (L8).
- [ ] The tool inventory and control matrix checks (`assistant_inventory --check`,
      `assistant_control_matrix --check`) and the golden/manifest tests are updated and pass.
- [ ] The decision about the outbound fetch for chat-started runs is recorded. Today no setting
      gates `fetch_site_context`'s egress; the endpoints come from the operator's environment
      (issue 33 S4).

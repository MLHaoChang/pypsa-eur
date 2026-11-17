# PR #1691 UX review shots (claude/dc-walk-03-05 @ 4ac3d163)

App run from the PR head (API :8000, Vite :5173), Playwright Chromium, 1440 px wide, deviceScaleFactor 2, dark and light theme.

- `01-guided-scorecard-ac-*`: Data Center Energy Hub created from New project › From template, Backup mode Gensets, Power architecture AC (LV UPS) set explicitly. Guided › 3 Connection & grid › Site › Grid readiness, expanded, Source opened on both rows. The two rows sit in different groups (Assets & sizing, Connection & grid), so each is cropped separately and stacked: Frequency support first, Short-circuit ratio second.
- `02-guided-scorecard-unset-*`: the same two rows on a project created with `POST /api/projects/from_template/eh_datacenter?name=DC architecture unset` and no body (architecture unset). The wizard always sends an architecture (default AC (LV UPS)), so it cannot make this project.
- `03-expert-compare-ac-*`: the AC project, Expert › Scenarios & optimisation › Grid study › Power architecture, Energy price 80, Compare. The Frequency support and Voltage control offered cells show the source text under the chip.
- `04-expert-compare-mvups-*`: the same compare on an MV UPS project (Gensets). It was created through the API with the wizard's own `dc_load` body, because the wizard has no name field and refuses a second "Data Center Energy Hub".

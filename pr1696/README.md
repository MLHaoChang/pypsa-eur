# PR #1696 UX review shots (claude/dc-walk-backup-assess @ 996897469df294a56bb2858104084602fe6af6ab)

App run from the PR head (API :8742, Vite :5742), Playwright Chromium, 1440 px wide, deviceScaleFactor 2, dark and light theme. Each shot is cropped to the element under review.

- `01-form-bess-hint-*`: New project › From template › Data Center Energy Hub setup form, Backup mode set to Batteries, showing the new hint under Backup mode. `expert` is the New project wizard's Templates tab; `guided` is the Guided Start card (opened through the hub store, as `scripts/dc-load-form-shots.mjs` does).
- `02-form-staged-bess-*`: the same on Data Center Energy Hub (staged growth) with Batteries picked: "Create refuses Batteries on this example at the default PUE: pick Hybrid." follows the hint. Wizard (`expert`) and Start card (`guided`).
- `03-backup-built-bess-*`: a project created through the wizard with Batteries at IT load 30, then Grid study › Backup section, Assess pressed (a real assessment, about 5 s each). The result opens with "This project was created with Backup mode set to Batteries, so it is assessed as if created with Gensets. …". Guided and Expert; Expert also shows the Cost lines disclosure, which the component hides in Guided.
- `04-backup-assess-wait-expert-*`: Expert › Grid study › Backup section on the same project while a mode change (Batteries → Hybrid) is saving. Mock: `page.route` holds the PUT `/dc-backup` request open (never answered), so Assess stays disabled with "Saving the Backup mode. Assess is ready once it is saved." beside it. Guided renders the same `DcBackupSection` component (its `guided` flag only hides Cost lines), so only Expert is shot.

The project was deleted after the shots.

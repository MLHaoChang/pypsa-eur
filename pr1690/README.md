# PR #1690 UX review shots (claude/dc-walk-scorecard-live @ 1adb7b8d)

App run from the PR head (API :8741, Vite :5741 with its proxy pointed at the API), Playwright Chromium, 1440 × 900 viewport, deviceScaleFactor 2, dark and light theme. Project: Data Center Energy Hub created with `POST /api/projects/from_template/eh_datacenter?name=DC walk 04 shots`, no unsaved edits, no run going. Full-viewport shots, scrolled so the scorecard header sits mid-screen with its surroundings.

- `01-guided-live-*`: Guided › 3 Connection & grid › Site › Grid readiness, expanded. Real response, `network: "live"`; no status line.
- `02-guided-out-of-date-*`: the same place with the grid-readiness response mocked to `network: "out_of_date"`. The amber line "A run is using the project, so this reads the last saved project. Save the project to update." (role=status) shows under "Advisory: nothing here blocks Run."
- `03-expert-out-of-date-*`: Expert › 9 Readiness and run › Issues, same mock. The line sits in the scorecard header under the summary.

Mock: Playwright `page.route` on `GET /api/campus-electrical/<project>/grid-readiness` fetches the real response (`route.fetch()`, which said `"live"`), sets `network` to `"out_of_date"` and fulfils with the rest unchanged. No run was actually holding the network, so the header still says Idle.

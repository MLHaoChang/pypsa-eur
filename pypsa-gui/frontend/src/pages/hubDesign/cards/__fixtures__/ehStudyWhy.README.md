<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# ehStudyWhy.json

Captured from the real `GET /api/results/eh_study/why/{figure}` route (guided-results 02a) at master
254eb121, through the backend's `TestClient`. A throwaway pytest reused the `project` fixture of
`pypsa-gui/backend/tests/test_provenance_graph_eh_study.py`, whose study record carries one assumed row
(`Generator size`). It saved each response's status and body:

- `current`: all three figures on that record;
- `out_of_date`: after `_goal(project, mc_draws=1000)`;
- `not_compared`: after removing the record's `assumptions` key;
- `running`: with the record's status set to `running` (409 `study_not_run`);
- `not_run`: with no study record (404 `study_not_run`);
- `other_project`: with another `project_id` (409 `project_not_open`).

The test file was deleted after the capture. Recapture the same way when the route's wire shape changes.

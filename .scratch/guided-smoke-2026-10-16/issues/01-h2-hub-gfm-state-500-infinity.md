<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# H2 hub: GET island/gfm-settings answers 500 (inf in the recommendation)

Status: done (#329, ef48c4cd)
Type: bug
Found by: Guided smoke re-run, 2026-10-16, master dc4e4b37

## Error

`GET /api/campus-electrical/{name}/island/gfm-settings` on `eh_h2_hub_staged`
after the campus run + "Recommend GFM settings":

```
HTTP 500
ValueError: Out of range float values are not JSON compliant: inf
  starlette/responses.py:198 (json.dumps in JSONResponse.render)
```

The uvicorn log also shows `Exception in ASGI application`; the client sees
`Connection reset by peer` on the next call in the same session.

## Where

- Written: `gridspine/drivers/campus_study.py:606`. `json.dumps(out, ..., default=_json_default)`
  writes `Infinity` into `campus_gfm_settings.json`. `_json_default`
  (`:610-612`, which maps non-finite floats to None) is only called for objects
  json cannot serialise, never for floats, so it never fires.
- The infs come from `gridspine/static/gfm_tuning.py:412-413`
  (`worst_rocof_hz_per_s`, `worst_nadir_hz` = `math.inf` on collapse) and `:299`
  (`capacity[*].delta` = inf, "frequency collapse: no grid-forming headroom").
- Served: `services/campus_island_tuning.py:105` (`_state` returns the file as read)
  via `routers/campus_electrical.py:347-352` (`get_gfm_settings`).

## Repro

1. Sign in; `POST /api/projects/from_template/eh_h2_hub_staged?name=x`.
2. Solve it (`POST /api/simulation/queue {project_id}`), wait for `completed`.
3. `POST /api/campus-electrical/x/draft {"overwrite": false}`.
4. `POST /api/campus-electrical/x/run` with the Guided defaults
   (`k=3, margin=0.2, invest=true`). Same with `k=1`. ~60-100 s.
5. `POST /api/campus-electrical/x/island/gfm-settings {"allow_i_max": false}` (202), wait for the run job.
6. `GET /api/campus-electrical/x/island/gfm-settings` -> 500.

## Also

The recommendation itself is `not_achievable` here (SITE_BATTERY, 372
simulations at k=3): "frequency collapse: no grid-forming headroom" in the
2027 h0 seamless E2 case. The H2 sidecar was added in #325 with island rows off,
so the Guided "Control" tile cannot show a state for this template at all
(GFM tile = 500).

## Comments

- 2026-10-16 (session_01TP6mmWWALLE8q4y73ypwoq): fixed on `claude/guided-control-gfm-json`. gridspine writes the recommendation as strict JSON (non-finite → null, `allow_nan=False`); `_state` cleans an older file on read; the panel and the report name a collapse row by its note. The H2 verdict stays `not_achievable`, which is honest: the island rows are off (owner, 2026-10-16) and SITE_BATTERY, the only grid-forming unit, has no headroom in 50 of 80 cases (E2 38.8 MW, and E3 trips SITE_BATTERY itself). Whether the H2 hub should reach a recommendation is an owner question (the island rows' cost: NPV +14.6 M → −2.1 M EUR, the ledger's measure).


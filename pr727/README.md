# PR #727 evidence: real app

Shots of the real React app (Vite dev server) against the native FastAPI backend, signed in as the admin account. No network mocks, no store injection except the public navigation actions (`openFlowTargetInPlace`, `showGridReadiness`, `setSlidePanel('campusElectrical')`).

Data: `build.py` builds the four `_flicker_site` variants (pass step 10 MVA, warn step 20, no step, no S_k) with `tests/test_grid_readiness.py`, imports each as project `flk-*` through `/api/io/import/netcdf` and saves it. `draft.py` adds the grid-code draft. `capture.mjs` takes the shots (1: Link card, 2: grid-code rows, 3: scorecard row in 4 states, 4: Expert "Go to field").

Guided shows no "Go to field" button on the row (it prints "Editable in Expert: ..."), so shot 4 is Expert only.

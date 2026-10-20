# pypsa-gui — agent navigation

React + FastAPI web GUI inside the PyPSA-Eur repo. Keep this file to
pointers; details live in the files pointed to.

## Layout

- `backend/routers/` — FastAPI route modules (thin; guards + validation).
- `backend/services/` — business logic. `services/profile_gen/` is the
  renewable profile generator (engine → job → apply).
- `backend/harness/` — the chat harness. NOTE: top-level under `backend/`,
  NOT under `services/`. `harness/catalogue.py` holds the tool catalogue;
  entries are Anthropic-shaped: `{"name", "description", "input_schema":
  {"type": "object", "properties", "required"}}`.
- `backend/harness/workflows/` — guided workflows, one Markdown file each
  (YAML front matter + `## Step: <id>` bodies), validated at load by
  `registry.py`; the frontend fetches the menu from `/chat/workflows`.
- `backend/tests/` — pytest; see `TESTING.md` for the working rules.
- `frontend/src/` — React + TS; `src/api/` mirrors the backend routers.

## Rules that bite

- Chat-tool wrappers in `services/chat_tools.py` call route handlers
  in-process via `_route`; project-scoped ones need the acting identity
  (`set_acting_user`) and, when they touch the network, a bound request
  context — in tests use the `bound_ctx` fixture (see `TESTING.md`).
- Workflow bodies may only backtick tool names that exist in the catalogue
  (or harness-internal tools); `tests/test_harness_layout.py` enforces it.
- Every backend refusal is a typed exception mapped to a 422 with
  `{"error_kind", "message"}` — extend that pattern, never bare strings.

# Network CRUD-factory lift — Implementation Plan

> **For agentic workers:** execute task-by-task. Steps use checkbox syntax.

**Goal:** Move the generic CRUD factory from `routers/network.py` into
`services/network_crud.py` behind identity re-exports.

**Architecture:** Pure move of 12 helpers; router stays the import surface.
Facade `_STAYS` → `_MOVED`. Profiles/time-axis stays assertions retargeted.

**Tech Stack:** FastAPI, PyPSAService, existing network facade tripwires.

## Global Constraints

- Behaviour-preserving only; no semantics changes.
- Never import `routers.*` from `services/network_crud.py`.
- Re-export public factory names from `routers.network` (chat_tools /
  project_network / tests).

---

### Task 1: Add `services/network_crud.py`

- [ ] Cut factory body (serialize → delete) into the new module with its own
      imports (`math`, `pd`, `Any`, `HTTPException`, `PyPSAService`,
      `df_to_json`, `_BLANK_SPELLINGS`, `attribute_catalog`, `ensure_carrier`,
      `change_log_service`, `vintage_service`, `_user_ts_*` from user_timeseries).
- [ ] Leave `_filter_transient_names` alias on the router.

### Task 2: Thin the router

- [ ] Replace factory defs with identity imports from `services.network_crud`.
- [ ] Drop imports that become unused (ruff F401).

### Task 3: Tripwires

- [ ] Facade: move 7 `_STAYS` → `_MOVED` (`services.network_crud`); empty or
      shrink `_STAYS`.
- [ ] Profiles / time-axis: require names still on `routers.network`, allow
      `__module__ == "services.network_crud"`.
- [ ] Skill row + design/plan docs.

### Task 4: Proof

- [ ] `pytest` facade + profiles + time-axis + lost_load custom attrs +
      adequacy merge + chat_tools GC/update.
- [ ] `ruff check --select F821` on services/routers.
- [ ] Commit, push, draft PR, merge when CI green.

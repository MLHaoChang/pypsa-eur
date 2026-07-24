# Chat-Tool Surface Audit — Authoritative Health Report

> **RESOLUTION STATUS (2026-06-08): all 23 confirmed defects FIXED + QA-gated.**
> Fixed in four QA-gated batches: (1) 5 binary-export tools → `agent_export` artifacts;
> (2) `solve_queue_abort` int-coercion; (3) 4 output-contract drifts (log-history,
> dispatch_status, undo_last/status); (4) 6 arg-shape (template/snapshot/run_simulation/
> list_vintage_bounds/upload_timeseries) + 8 broken/structural (bulk_update, cluster_network,
> set_vintage_bounds, set_multi_period_snapshots, solve_queue_enqueue, both project_network
> DI tools, sample_representative_weeks). A new AST regression test
> (`tests/test_chat_tools_imports.py`) now guards the whole delegation surface (routers/
> services/models imports); `KNOWN_BROKEN` is empty. The findings below are retained as the
> historical audit record.

## Summary

- **Total tools audited:** ~100 thin wrappers in `backend/services/chat_tools.py` (schema in `chat_tools_schema.py`, handlers in `routers/*.py` / `services/*.py`).
- **Healthy:** ~71 tools — wrappers import the correct handler, forward arguments in the expected shape, and the declared output matches what the handler returns.
- **Confirmed defects:** 23 (all independently verified; refuted findings dropped).
- **Severity:** 21 high · 2 medium · 0 low.
- **Safety tiers:** No misclassifications found in the audited range.

**Healthy surface:** The mutating-CRUD core (`create_component` / `update_component` / `delete_component` / `cascade_delete_bus`), all `import_*` tools (`import_network_nc`, `import_csv_bundle`, `import_excel`, `import_matpower`, `import_project_bundle`), project management (`list_projects`, `load_project`, `activate_project`, `save_project*`, `rename_project`, `delete_project`), snapshot lifecycle reads, the `get_results` dispatcher (correct `source=` handling), `ui_*` event tools, and audit/chat-history tools are all sound and correctly wired.

---

## Confirmed Defects (grouped by verdict, high → low severity)

### output_mismatch (11)

| Tool | Sev | Expected vs Actual | Fix | Evidence |
|---|---|---|---|---|
| `export_network_nc` | high | Expected JSON-serializable binary; returns raw `StreamingResponse` stringified to `<StreamingResponse ...>` by `json.dumps(default=str)`. | Read bytes, return `{'bytes_b64': base64(...)}` like `import_*`. | `chat_tools.py:1071-1073`; `routers/io.py:36-50`; `chat_service.py:1559-1571` |
| `export_csv_bundle` | high | Same: ZIP `StreamingResponse` stringified, ZIP bytes lost. | Read BytesIO buffer, return base64. | `chat_tools.py:1076-1078`; `routers/io.py:54-69` |
| `export_excel` | high | Same: XLSX `StreamingResponse` stringified. | Extract bytes, return base64. | `chat_tools.py:1081-1083`; `routers/io.py:72-103` |
| `export_matpower` | high | Same: MATPOWER text `StreamingResponse` stringified (comment claims body is drained; it isn't). | Drain response body, return the text. | `chat_tools.py:1086-1088`; `routers/io.py:106-161` |
| `download_project_bundle` | high | `application/zip` `StreamingResponse` stringified; binary can't tunnel through a chat result. | Read buffer → base64, or return an HTTP download pointer. | `chat_tools.py:951-953`; `projects.py:2146-2188` |
| `get_simulation_log_history` | high | Declares `-> list[str]`; handler returns `{"lines": [...], "running": bool}`. | `return _h()['lines']` (or document the dict). | `chat_tools.py:283-285`; `routers/simulation.py:815-828` |
| `dispatch_status` | high | Schema promises `{state, mismatched_classes}`; service returns a bare Literal string. | Return `{'state': ..., 'mismatched_classes': [...]}` from the service. | `chat_tools.py:737-741`; `services/dispatch_status.py:68-91` |
| `undo_last` | high | Schema promises `{undone, action_id}`; handler returns `{undone, remaining}`. | Rename field, or fix schema to `{undone, remaining}`. | `chat_tools_schema.py:920-924`; `routers/network.py:1978` |
| `undo_status` | high | Schema promises `{has_undo, stack_depth}`; handler returns `{depth, memory_bytes, max_bytes, max_steps}`. | Fix schema to the real shape, or add `has_undo`/`stack_depth`. | `chat_tools_schema.py:925-929`; `routers/network.py:1917-1933` |

> Note: the four `export_*` tools + `download_project_bundle` share **one root cause** — the chat dispatcher (`chat_service.py:1559-1571`) cannot serialize a Starlette `StreamingResponse`. A single shared helper that drains/encodes any `StreamingResponse` before return would fix all five at once.

### arg_shape_mismatch (6)

| Tool | Sev | Expected vs Actual | Fix | Evidence |
|---|---|---|---|---|
| `upload_timeseries` | high | Handler wants `file: UploadFile` (required) + optional `period`; wrapper passes `body=dict` → `TypeError`, required `file` missing. | Wrap CSV in BytesIO `UploadFile`, pass `file=` via `_sync()`. | `chat_tools.py:636-644`; `routers/network.py:2982-2987` |
| `create_project_from_template` | high | Handler `name: str \| None`; wrapper passes `{'name': new_name}` dict → `(name or '').strip()` `AttributeError`. | `return _h(template_id, new_name)`. | `chat_tools.py:988-991`; `projects.py:748,775` |
| `create_project_snapshot` | high | Handler wants `req: CreateSnapshotRequest` (`req.label`); wrapper passes a dict → `AttributeError`. | Build `CreateSnapshotRequest(label=..., message=...)`. | `chat_tools.py:1007-1010`; `snapshots.py:369,378` |
| `solve_queue_abort` | high | Schema/wrapper pass `job_id` string; handler + jobs dict are int-keyed → silent 404, queue never aborts. | Coerce: `return _h(int(job_id))`. | `chat_tools.py:789-791`; `routers/solve_queue.py:59`; `services/solve_queue.py:106` |
| `run_simulation` | high | Schema declares `force` to bypass an empty-network check; wrapper drops it, handler takes no params, no such check exists. | Implement+forward `force`, or remove it from the schema. | `chat_tools.py:747-754`; `routers/simulation.py:465`; `chat_tools_schema.py:543-550` |
| `list_vintage_bounds` | medium | Schema declares filter params `component_class`/`name`; wrapper ignores them, handler returns all bounds unfiltered. | Remove params or add handler-side filtering. | `chat_tools_schema.py:159-167`; `chat_tools.py:224-226`; `routers/vintage.py:50-62` |

### broken_import (4)

These are **additional** to the 6 known-broken (see note below).

| Tool | Sev | Expected vs Actual | Fix | Evidence |
|---|---|---|---|---|
| `set_vintage_bounds` | high | Imports `put_vintage_bounds` (real: `update_vintage_bounds`) → ImportError; also passes dict where `VintageBoundsUpdate` model expected. | Import `update_vintage_bounds`; pass `VintageBoundsUpdate(bounds=period_bounds)`. | `chat_tools.py:618-620`; `routers/vintage.py:85,39-46,108` |
| `set_multi_period_snapshots` | high | Imports non-existent `MultiPeriodSnapshotConfig` → ModuleNotFoundError; schema `operational_from/to` vs handler `start/end`. | Drop the model; build `{start,end,freq,periods}` dict; align schema names. | `chat_tools.py:589-599`; `routers/network.py:1181-1210` |
| `bulk_update_components` | high | Imports removed class `BulkUpdateRequest` → ImportError; handler now takes a plain dict. | Build `{'component_class','names','updates'}` dict; drop the import. | `chat_tools.py:501-505`; `routers/network.py:1752` |
| `cluster_network` | high | Imports `ClusteringRequest` (real: `ClusterRequest`) → ImportError; schema also missing required `mode`, uses `algo` not `algorithm`. | Import `ClusterRequest`; add `mode`, rename `algo`→`algorithm`. | `chat_tools.py:539`; `routers/clustering.py:56-65,280` |

### behavior_bug (1)

| Tool | Sev | Expected vs Actual | Fix | Evidence |
|---|---|---|---|---|
| `list_project_network_component` | high | Imports non-existent `get_project_network_component` → ImportError; even fixed, handler `get_project_component(component_class, ctx=ProjectDep)` needs FastAPI DI, which the raw call bypasses → `ctx` unresolved. | Import `get_project_component`; resolve `ProjectContext` explicitly or route via HTTP. | `chat_tools.py:967-974`; `project_network.py:55-57`; `deps.py:76` |

### schema_wrapper_mismatch (1)

| Tool | Sev | Expected vs Actual | Fix | Evidence |
|---|---|---|---|---|
| `sample_representative_weeks` | medium | Schema declares `weighting_strategy` enum; wrapper drops it, handler hardcodes annual-hours calc → silent divergence for `equal`/`user_provided`. | Add field to `SampleWeeksConfig`, forward it, branch handler calc. | `chat_tools.py:579-583`; `chat_tools_schema.py:374-385`; `routers/network.py:1289-1373` |

---

## Known-Broken Imports (single grouped note — already regression-tracked)

Six wrappers fail on import-name drift and are already covered by `test_chat_tools_imports.py` (KNOWN_BROKEN). They are **not** re-litigated per tool here:

- `routers.network` / `BulkUpdateRequest` (surfaces as `bulk_update_components`)
- `routers.clustering` / `ClusteringRequest` (surfaces as `cluster_network`)
- `routers.vintage` / `put_vintage_bounds` (surfaces as `set_vintage_bounds`)
- `routers.solve_queue` / `enqueue` (surfaces as `solve_queue_enqueue`)
- `routers.project_network` / `get_project_network_meta` (surfaces as `get_project_network_meta`)
- `routers.project_network` / `get_project_network_component` (surfaces as `list_project_network_component`)

These overlap with several high-severity entries above because the same wrappers also carry a *second* independent defect (arg-shape drift or DI bypass) that survives even after the import name is corrected — those second-order fixes are captured in the tables above and should be applied together.

---

## Verifier Reconciliation Notes

- All 23 included defects have `confirmed=true`; refuted findings were dropped.
- Where the verifier's verdict/severity differed from the auditor's, the **verifier's** value was used (e.g. `list_vintage_bounds` retained medium; the `export_*`/`download_*` cluster confirmed high `output_mismatch`).
- De-duplicated repeated auditor entries (e.g. `get_solver_config`, `delete_project`, `get_results` appeared twice in the raw findings as `ok`).
- `solve_queue_enqueue` is listed both in the known-broken note (import drift, regression-tracked) and is one of the 6; its second defect (raw dict vs `EnqueueRequest`) is the load-bearing follow-up fix.
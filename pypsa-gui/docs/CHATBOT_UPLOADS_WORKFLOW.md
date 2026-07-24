# Chatbot file uploads — workflow plan

Roadmap to let users drop files into the chat panel (PDFs, images, Excel, Word) and have the assistant work with them — e.g. ingest a demand-profile spreadsheet, or reconstruct a network from a hand-drawn topology photo. The assistant can also generate downloadable Excel / CSV / PNG exports back to the user.

**Scope locks**
- **Storage**: per-project, `projects/<name>/uploads/<file_id>/`. Lineage follows the same rules as `network.nc` (Save-As MOVE, Save-a-Copy COPY, snapshot include).
- **Multimodal strategy**: hybrid.
  - **Images** (PNG/JPG/WebP) and **PDFs** → forwarded to Anthropic via the Messages API `image` / `document` content blocks. No server-side OCR.
  - **Excel** (.xlsx/.xls/.csv) and **Word** (.docx) → parsed server-side (openpyxl, python-docx, pandas). Chatbot accesses via tool calls.
- **Tools**: 6 consume (`list_uploads`, `read_upload_meta`, `read_excel_sheet`, `apply_demand_from_excel`, `reconstruct_network_from_image`, `delete_upload`) + 4 export (`export_to_excel`, `export_to_csv`, `export_preview_png`, `export_chat_summary`).

## Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | File size cap | **25 MB / file** |
| 2 | Image dimension cap | **Trust Anthropic auto-resize** (no pre-downscale) |
| 3 | PDF page cap | **100 pages** (warn + truncate beyond) |
| 4 | Excel column shape | **Single time/value pair** (wide-table mode is a follow-up) |
| 5 | Picture-to-network coords | **Image pixel → canvas grid** by default; geographic projection is a follow-up |
| 6 | Bundle inclusion | **YES** — `bundle.zip` carries `uploads/` |
| 7 | clear_chat_history vs uploads | **Independent** — separate `clear_uploads` tool, default keep |

**NEW export requirement (locked)**: chatbot must be able to create files for user download/validation. Export tools live in Phase B; UI in Phase D distinguishes uploaded vs exported chips with a download button.

---

## Phase 0 — Prerequisites (~0.25 day)

- `pixi.toml [dependencies]`: add `python-magic >=0.4.27`, `python-docx >=0.8.11`, `pypdf >=4.0`. `openpyxl >=3.1.5` already present.
- `pypsa-gui/backend/requirements.txt`: append the same four (currently lists only web-stack deps).
- Smoke import: `python -c "import magic; import docx; import openpyxl; import pypdf"` passes in the activated env.
- Document the new deps in `pypsa-gui/CHATBOT.md`.
- `scripts/migrate_uploads.py` (one-shot, idempotent, `--dry-run` / `--apply`): scans `projects/*/{attachments,files,media}/` and migrates to `projects/*/uploads/<file_id>/` with a synthesised `meta.json`. No-op for the in-tree pypsa-gui deployment; future-proofing for forks.

---

## Phase A — Storage + upload API (~1 day)

### Backend

- `routers/uploads.py` (new) mounted at `/api/projects/{name}/uploads`:
  - `POST /` multipart upload.
  - `GET /` list (id, filename, mime, size, kind).
  - `GET /{file_id}/blob` raw bytes (Content-Disposition: inline).
  - `GET /{file_id}/meta` meta.json.
  - `DELETE /{file_id}` idempotent (always 200, body shape below).
  - `GET /{file_id}/signature` HMAC token for Phase C.
- `services/upload_service.py` (new): `add_upload`, `list_uploads`, `get_upload_path`, `delete_upload`, `_write_agent_export`, `_apply_with_proper_lock_hierarchy`.
- `services/filename_service.py` (new): `safe_upload_filename(name, ext="")` — rejects `..`, slashes, control chars, Windows reserved names (CON/PRN/NUL/AUX/COM1-9/LPT1-9); strips leading dots; NFKD-transliterates non-ASCII; caps at 200 chars; falls back to `export_<ts>.<ext>` on empty.
- `models/upload_schemas.py` (new): `UploadMeta` Pydantic model with `extra="ignore"` (forward-compat).

### Upload validation sequence (pre-lock)

0. Project-existence check via `Depends(get_loaded_project)` → 404 `project_not_found`.
1. Read up to 25 MB; over → 413 `file_too_large`. Zero → 400 `empty_file`.
2. **Magic-byte MIME sniff** via `python-magic` on raw bytes; declared MIME mismatch → 400 `mime_type_mismatch`; not in allowlist → 400 `unsupported_mime`.
3. `file_id = sha256(bytes)[:16]`, then enter the locked critical section.

### Lock policy

Three locks, hierarchical and never overlapping:

| Lock | Scope | Order |
|---|---|---|
| `ctx.chat_state.lock` | per-session, held for ENTIRE turn (tool dispatch + JSONL append) | outermost |
| `upload_service._project_upload_lock(name)` | per-project RLock | middle |
| `PyPSAService.get_lock()` | process-global | innermost |

Upload + PyPSA mutation MUST route through `_apply_with_proper_lock_hierarchy(upload_phase, psa_phase)` — the helper releases the upload lock before acquiring the PyPSA lock. **Per-thread sentinel** is always active (no env-var gate): `PyPSAService.get_lock()` raises `RuntimeError("lock hierarchy violation")` if the upload lock is held. A pre-commit lint rule (`scripts/check_lock_hierarchy.py`) flags manual nested `with` blocks in `chat_tools.DISPATCHERS` handlers.

### Atomic critical section (under upload lock)

```
0. uploaded_at = time.time()  # computed BEFORE lock acquisition
1. quota_check (walk uploads/, sum sizes; if exceeded → 507)
2. file_id = sha256(bytes)[:16]
3. stat uploads/<file_id>/meta.json
4. if meta.json exists → verify existing.sha256 == sha256(bytes);
   mismatch → 500 file_integrity_error; ok → return existing (dedup hit)
5. if dir exists but meta.json missing/unparseable → race-lost predecessor;
   restore uploaded_at from uploads/<file_id>/uploaded_at.sidecar if present
6. mkdir uploads/<file_id>/ (idempotent)
7. write meta.json (blob_ready: false) via os.open(O_CREAT|O_EXCL) on .tmp,
   os.replace to dst   [Windows-safe atomic]
8. write blob via blob.tmp + os.replace
9. flip meta.json.blob_ready = true via os.replace
```

`list_uploads()` skips `blob_ready: false` entries; `prune_orphans` reaps after 60s mtime grace. Dedup is **per-project only** — no cross-project hardlinks/symlinks (each project is self-contained).

### Bundle inclusion (7 transitions)

Add `_BUNDLE_DIRS = ('uploads',)` next to `_BUNDLE_FILES` in `routers/projects.py`. Helper:

```python
def _copy_bundle_dirs(src: Path, dst: Path) -> None:
    for dname in _BUNDLE_DIRS:
        src_d, dst_d = src / dname, dst / dname
        if not src_d.exists(): continue
        if dst_d.exists(): _force_rmtree(dst_d)
        shutil.copytree(src_d, dst_d)
```

Invocation table (verify function names via grep before implementing):

| # | Transition | Call site | Action |
|---|---|---|---|
| 1 | Save-As MOVE | `routers/projects.py::save_project` MOVE branch | `_copy_bundle_dirs(src, dst)` after per-file loop |
| 2 | Save-a-Copy | `routers/projects.py::save_project_as` | same |
| 3 | scenario_copy | `routers/projects.py::copy_scenario` (CROSS-project copy semantics) | same |
| 4 | rename | `routers/projects.py::rename_project` | dir `mv` already carries subdir; smoke-test only |
| 5 | snapshot create | `routers/snapshots.py::_create_snapshot_internal` | `_copy_bundle_dirs(project_dir, snapshot_dir)` |
| 6 | snapshot restore | `routers/snapshots.py::restore_snapshot` | `_force_rmtree(live_uploads_dir)` + `_copy_bundle_dirs(snapshot_dir, project_dir)` |
| 7 | routine re-save (same project) | `services/project_service.py::save_context` | NO copy — same dir |

`import_bundle` also extracts `uploads/*` entries from the zip. `delete_project(P)` acquires the upload lock BEFORE `shutil.rmtree(project_dir)` to serialise against concurrent `list_uploads(P)`. `_copy_bundle_dirs` silently skips dirs that don't exist (legacy project compat).

### meta.json schema versioning

- v1 (Phase A): `{schema_version: 1, file_id, filename, mime, size, sha256, kind, uploaded_at, blob_ready, version}`.
- v2 (Phase C, PDF support): adds `page_count?: int`, `truncated_to_100_pages?: bool`.
- Policy: v1 readers accept v2 files (`extra="ignore"`); v2 readers default missing fields; `schema_version > CURRENT` → SKIP + single WARN log per project per startup.

### API contract — errors + idempotency

Every endpoint returns `{detail, error_kind, hint?}`:

| HTTP | `error_kind` | Trigger |
|---|---|---|
| 400 | `invalid_filename` | Path traversal, control chars, Windows reserved names |
| 400 | `mime_type_mismatch` | Declared MIME ≠ sniff |
| 400 | `empty_file` | Zero bytes |
| 400 | `unsupported_mime` | Outside allowlist |
| 400 | `time_column_parse_error` / `value_column_parse_error` | apply_demand parse failure |
| 400 | `snapshot_count_mismatch` / `snapshot_range_mismatch` | apply_demand alignment failure |
| 400 | `multimodal_rejected` | Anthropic mid-stream rejection |
| 400 | `too_many_multimodal_blocks` | >20 blocks |
| 400 | `image_analysis_timeout` | reconstruct_network_from_image >30s |
| 404 | `upload_not_found` | file_id missing (canonical across all endpoints) |
| 409 | `signature_expired` | HMAC token expired or version mismatch |
| 409 | `upload_in_use_conflict` | File mtime changed during tool execution |
| 413 | `file_too_large` / `image_too_large` | Per-file 25 MB / per-image 10 MB |
| 413 | `pdf_page_cap_exceeded` | >100 pages (warn variant) |
| 415 | `mime_not_allowlisted_for_multimodal` | Tried to multimodal-attach Excel/Word |
| 500 | `internal_write_failed` / `file_integrity_error` | Write or dedup integrity failure |
| 507 | `upload_quota_exceeded` | Project quota hit |

- `POST /` is idempotent on bytes (same SHA256 → 200, existing meta, first writer wins for `uploaded_at`).
- `DELETE /` always 200. `DeleteUploadResponse` Pydantic model with validator: success body `{deleted: true, file_id}`; failure `{deleted: false, file_id, reason: "not_found" | "in_use"}` — validator enforces `reason` REQUIRED on failure, OMITTED on success.
- Tests assert `response.status_code` AND `response.json()["error_kind"]` only (message text NEVER asserted). Helper `assert_error(response, code, kind)` lives in `conftest.py`.

### Frontend

- `api/uploads.ts` (new): `uploadFile`, `listUploads`, `deleteUpload`, `getUploadMeta`, `getUploadBlobUrl`.
- `chatStore.ts`: `uploads: UploadMeta[]` slice with `setUploads`, `addUpload`, `removeUpload`.

### Tests (`tests/test_chat_uploads.py`)

- Add → list returns entry; same bytes twice → same file_id (dedup).
- Concurrent same-bytes from two threads → ONE entry, both threads see the same `uploaded_at` (first-writer wins, via `O_CREAT|O_EXCL` on meta.json.tmp).
- Race-lost partial state (dir exists, no meta.json) → upload recovers via sidecar.
- 25 MB cap → 413 `file_too_large`; zero-byte → 400 `empty_file`; path traversal → 400 `invalid_filename`.
- MIME validation: declared/sniffed mismatch → 400; EXE renamed `.pdf` → 400; valid xlsx w/o declared MIME → accepted.
- Bundle survival across all 7 transitions (parametrised; spy on `_copy_bundle_dirs` asserts call count: 0 for routine re-save, 1 for Save-As / scenario_copy / etc.).
- Legacy project (no uploads/) loads + Save-As without spurious dir; `list_uploads` returns `[]`.
- DELETE missing → 200 `{deleted: false, file_id, reason: "not_found"}`; DELETE existing → 200 `{deleted: true, file_id}` (no `reason` field). Hand-constructing the model without `reason` raises `ValidationError`.
- Project DELETE removes uploads/ tree.
- Quota soft-boundary: 99 MB existing + 2 MB upload → success; next upload → 507. Concurrent pressure at the boundary serialises via the per-project lock.
- Orphan blob (no meta.json) → invisible to `list_uploads`; `prune_orphans` reaps after 60s.
- `schema_version=99` → SKIP + WARN; missing field → treated as v1; v1↔v2 round-trip via `extra="ignore"`.
- `prune_orphans` task lifecycle: activate/deactivate/reactivate idempotent under `_prune_tasks_lock`; backend shutdown cancels all.
- `delete_project(P)` concurrent with `list_uploads(P)`: list returns valid list OR 404, NEVER `FileNotFoundError`.
- Project-existence pre-check: POST to `__nonexistent__` → 404 before lock.

---

## Phase B — Chat tools that consume + produce uploads (~1 day)

All new tools register in `chat_tools_schema.TOOLS` AND `chat_tools.DISPATCHERS`. Post-Phase-B count is 44 (34 existing + 10 new). `test_chat_tools_dispatch.py` asserts `len(TOOLS) == len(DISPATCHERS)`.

### Tier tagging

Each new tool carries `tier='read' | 'write' | 'destructive'` in its description string. Confirmation card rules:
- `read` — no card.
- `write` — 5-min TTL.
- `destructive` — 5-min TTL + typed confirmation ONLY for `delete_upload` on `kind="agent_export"` files (user uploads use the 5s Undo toast).

### Tools (consume)

1. `list_uploads()` — read. Returns `[{file_id, filename, mime, kind, size_kb}]`.
2. `read_upload_meta(file_id)` — read. Full meta + per-kind preview (sheets / pages / dimensions).
3. `read_excel_sheet(file_id, sheet_name?, max_rows=200)` — read. Returns `{columns, rows, total_rows, total_cols}`.
4. `apply_demand_from_excel(file_id, sheet_name, time_col, value_col, load_name, replace?)` — destructive. Two-pass:
   - **Pass 1 (unlocked)**: read Excel, coerce time/value, align to `n.snapshots`. Errors → `time_column_parse_error` / `value_column_parse_error` / `snapshot_count_mismatch` / `snapshot_range_mismatch`. NO mutation.
   - **Pass 2 (locked)**: via `_apply_with_proper_lock_hierarchy`; upload lock briefly to read bytes, then PyPSA lock for `_user_ts` write.
   - All-or-nothing; participates in turn-level undo.
   - **File-in-use guard**: stat blob mtime at Pass 1 entry, re-stat at Pass 2; mismatch or missing → 409 `upload_in_use_conflict`.
5. `delete_upload(file_id)` — destructive. Idempotent.
6. `reconstruct_network_from_image(file_id)` — destructive. Sub-call wraps Anthropic SDK in `asyncio.wait_for(..., timeout=30.0)` → `image_analysis_timeout` on stall. Coordinate transform: `gx = (px - px0) * scale_x`, `gy = (py0 - py) * scale_y` (Y-flip); user can override origin/scale args. Wrapped in undo snapshot.

### Tools (produce — agent exports)

All export tools go through `_write_agent_export(project, sanitised_filename, bytes_payload, mime)`. Helper asserts `name == safe_upload_filename(name)` (defence-in-depth runtime catch), enforces 25 MB cap, acquires upload lock, runs the atomic write sequence, server-stamps `kind="agent_export"`.

7. `export_to_excel(sheets, filename)` — write. `openpyxl.Workbook` materialise.
8. `export_to_csv(rows, columns, filename)` — write. `pandas.DataFrame.to_csv`.
9. `export_preview_png(filename, png_bytes_b64)` — write. Decode b64, sniff PNG magic, write.
10. `export_chat_summary(format, since_turn?)` — write. Renders recent conversation as `chat_summary_<ts>.<ext>`.

### Upload immutability contract

Uploads are IMMUTABLE after initial write — no `update_upload` API. Tools wanting an "updated version" use the `export_*` helpers to create a NEW file_id. **No symlinks or hardlinks** (Windows requires admin privileges and breaks on cross-project semantics; each blob is self-contained).

### Tests (`tests/test_chat_upload_tools.py` + `tests/test_filename_service.py`)

- `read_excel_sheet` returns shape + 200 rows.
- `apply_demand_from_excel` writes `_user_ts` and round-trips via save/load.
- `apply_demand_from_excel` parse / count / range mismatches → correct `error_kind`; partial state rollback verified.
- `apply_demand_from_excel` participates in turn-level undo.
- `apply_demand_from_excel` file deleted mid Pass 1 → 409 `upload_in_use_conflict`.
- `reconstruct_network_from_image` with fake LLM (2 buses + 1 line) creates components + audit log entry; 320×240 mock image produces buses inside `0..320, -240..0`.
- `reconstruct_network_from_image` SDK stall (60s mock) → `image_analysis_timeout` within ~31s; network unchanged.
- `delete_upload` removes from disk + list; second call → `{deleted: false}`.
- `safe_upload_filename` unit tests: `..`, `CON`, `\x00`, `.htaccess`, `résumé.pdf`, `🎉party🎉.pdf`, 500-char input, empty → all behave per spec.
- `export_to_excel(filename='../../etc/passwd.xlsx')` → 400 `invalid_filename`; `CON.xlsx` → 400; >25 MB serialised → 413.
- Tool that bypasses `safe_upload_filename` and calls `_write_agent_export` directly → assertion fires.
- Agent export appears in `list_uploads()` with `kind="agent_export"`; survives all 7 bundle transitions.
- `test_chat_tools_dispatch.py::test_lock_hierarchy_per_tool` parametrised over every dispatcher: synthetic concurrent context, asserts no sentinel violation.

---

## Phase C — Multimodal pass-through to Anthropic (~0.5 day)

### Wire-in

- `StreamRequest` gains `attachment_file_ids: Optional[list[str]] = None` (and `attachment_signatures: Optional[list[str]] = None`). `chat_service.run_turn(ctx, message, attachment_file_ids=None)`.
- **Block-count prevalidation**: `len(attachment_file_ids) <= 20` checked BEFORE any block is built (no I/O for over-limit).
- **Block ordering matches input order exactly** — no server-side reordering. Build rules:
  - image/png|jpeg|webp → `{"type": "image", "source": {"type": "base64", ...}}`
  - application/pdf → `{"type": "document", "source": {"type": "base64", ...}}`
  - xlsx/docx → 415 `mime_not_allowlisted_for_multimodal`
  - Append user text block LAST.
- Stat each file before reading; `FileNotFoundError` → 404 `upload_not_found` for that file_id.

### Caps

- Per-image base64: > 5 MB toast (frontend), > 10 MB → 413 `image_too_large`.
- Per-message: 20 blocks max → 400 `too_many_multimodal_blocks`.
- PDF: page count read via `pypdf.PdfReader` on upload; > 100 pages → meta.json `truncated_to_100_pages=true`; multimodal block built from first 100 pages (in-memory truncation, original blob preserved).

### Token-cost accounting

- Extend `session.accrue_usage` to accept `cache_read_input_tokens`, `cache_creation_input_tokens`; accrue into `session.usage_acc` and the per-turn `chat.jsonl` record.
- New per-turn fields:
  - `multimodal_input_tokens: int` — delta `input_tokens_current - input_tokens_previous`. STORED RAW (incl. negative for cache-hit cases) for audit. UI clamps to `max(0, delta)` and labels "(estimated)".
  - `multimodal_attachment_bytes: int` — sum of attachment sizes from meta.json. Reliable, monotonic, always shown in tooltip.
- Optional `PYPSA_GUI_SUPPRESS_TOKEN_ESTIMATE=1` hides the token estimate, shows only bytes.
- CHATBOT.md footnote: "Token estimates are not a basis for cost disputes; Anthropic's invoice is authoritative."
- Backward compat on session load: missing fields default to 0; no migration script.

### Mid-stream rejection + token rollback

- `usage_delta` accumulated in LOCAL `pending_usage` during streaming; applied to `session.usage_acc` ONLY on successful completion. Exception (incl. `multimodal_rejected`) discards `pending_usage`.
- chat.jsonl turn record gets `partial: true` + `partial_input_tokens` for audit.
- Anthropic `INVALID_REQUEST_ERROR` mentioning image/media/aspect/format/pages: log full SDK response (request ID + detail) at ERROR; return 400 `multimodal_rejected` with detail copied verbatim. No auto-retry, no auto-downscale (silent transformation surprises users).

### Request-signed file_id (Phase C stub, enforced in multi-user mode)

- `GET /uploads/{file_id}/signature` returns `{file_id, signature, expires_at, sha256, version}` with 5-min TTL.
- `signature = hmac.new(SECRET, f"{session_id}|{project}|{file_id}|{sha256}|{version}|{expires_at}", sha256).hexdigest()`.
- `version` field in meta.json increments on every successful re-upload of a previously-deleted file_id — catches the "same bytes deleted + re-uploaded" race where file_id alone would silently pass.
- Single-user posture: backend verifies if present, skips silently if absent. Multi-user (future): missing/invalid → 409 `signature_expired`.
- `SECRET` is a module-level random 32-byte value; server restart invalidates in-flight signatures.

### Tests (`tests/test_chat_multimodal.py`, uses `FakeAnthropicClient`)

- Image + text → SDK receives `[image, text]`; PDF + text → `[document, text]`.
- Reordered `[pdf, image, image]` → exact SDK order `[document, image, image, text]`.
- xlsx multimodal-attach → 415.
- > 10 MB image → 413 before SDK; > 20 blocks → 400.
- Non-existent file_id → 404 `upload_not_found`; concurrent delete between chip update and send → 404 for that file_id only.
- 101-page PDF → meta v2 with `truncated_to_100_pages=true`; multimodal block has exactly 100 pages.
- `usage_acc` accrues `cache_read_input_tokens` + `cache_creation_input_tokens`; turn record carries both.
- Negative-delta cache-hit turn: chat.jsonl stores raw negative; tooltip clamps to 0 and shows byte metric.
- SDK mid-stream rejection → tool-result frame with `multimodal_rejected`; `usage_acc` UNCHANGED from pre-turn baseline; chat.jsonl turn has `partial: true`.
- Legacy chat.jsonl (missing token fields) loads without error, treated as 0.
- Expired signature → 409 `signature_expired`; missing signature in single-user mode → silent accept.

---

## Phase D — Chat panel UI (~1 day)

### 1. Upload zone (drag-drop)

- Dashed border (3-4px) + `bg-accent/20` tint + 1.02× scale on `dragover`; 100ms dwell guard against fade flicker; cursor `copy`.
- File type whitelist on `accept` AND `DataTransfer.types`.
- **Pre-upload size check**: `file.size > 25 MB` → modal "demand.xlsx (48 MB) is too large. Maximum is 25 MB." with `[OK]` and `[Learn how]` (OS-specific compression guide). NO "Upload anyway" path. 15-25 MB → orange warning badge on chip.
- **Multi-file progress toast** with per-file rows, file-type icons (📊/📄/📸/📝), overall percentage at top, per-row cancel. Failed file gets `[Retry]` button inline with specific `error_kind`. Successful files retain order; failure does not reorder. Quota example: A=40 MB ✓, B=40 MB ✓, C=30 MB ✗ `upload_quota_exceeded` → C row shows `[Retry]` and `[Manage uploads]`.

### 2. Paste from clipboard

- Button **"📎 Paste image/PDF"** adjacent to paperclip (clearer than "Paste from clipboard").
- Inspects `navigator.clipboard.read()`:
  - Image/PDF only → upload as `pasted-<ts>.<ext>`.
  - Text only → toast "Use Ctrl+V to paste into message".
  - Both → toast with `[Text] [Image]` buttons.
- Ctrl+V preserves text paste (browser default). **Ctrl+Shift+V** secondary shortcut for image/PDF paste.
- `onPaste` fallback on textarea handles direct screenshot-paste from Snipping Tool / macOS screenshot.

### 3. File-picker button

- Paperclip icon; `accept=".xlsx,.xls,.csv,.pdf,.png,.jpg,.jpeg,.webp,.docx"`. Tooltip + aria-label: "Upload files (Excel, PDF, images; max 25 MB each) — keyboard shortcut Ctrl+U".
- **Keyboard shortcut Ctrl+U / ⌘+U** triggers file picker.
- **Empty-state block** (zero messages + zero uploads): "Drop files here, click 📎 to upload, or press Ctrl+U" with three buttons `[📊 Upload Excel] [📄 Upload PDF] [📸 Paste image]` filtered to respective MIMEs. Plus a "Quick workflows" section showing `📊 Apply demand profile from Excel` → `[Start this workflow]` (opens picker filtered to xlsx/csv, pre-fills chat textarea, opens chip quick-actions). Fades on first upload/message; recoverable via `?` help icon.

### 4. Upload chip strip

Strip header: `Uploads (3)   [Clear all]` (Clear all always visible for count ≥ 1; click → 5s Undo toast).

Each chip:
- Filename + size + file-type icon + delete (x).
- **Sticky across turns by default**. Each chip carries an **"Attach to next message" checkbox defaulting ON for ALL chips** (incl. previously-sent).
- **Unchecked chip rendering**: 50% opacity + "not attached" badge (visually distinct from deleted). Tooltip: "This file is in your project but won't be attached to your next message."
- **Order badges visible by default when >1 file checked-ON** (hidden for single-file). Drag horizontally OR up/down buttons on each chip. Reorder animation: 200ms slide + bounce on neighbours + flash on moved chip. Send-button tooltip echoes order: "Sending 3 files: [1] demand.xlsx, [2] forecast.pdf, [3] network.png".
- Post-upload primer toast on ≥2 files: "Files will be read in order [1] demand.xlsx, [2] forecast.pdf. Drag or click ⬆️⬇️ to reorder." 5s auto-dismiss.
- **Touch devices** (`matchMedia("(pointer: coarse)")`): horizontal drag DISABLED (scroll conflict); up/down buttons enlarged to 44×44px; grab-handle hidden; long-press 300ms enters reorder mode.
- **Uploaded vs exported visual distinction**:
  - User-uploaded: `bg-muted`, click = preview, delete (trash) icon. Badge "Uploaded".
  - Agent-exported: `bg-accent/15`, click = preview-then-download modal, download (↓) icon. Badge "Generated".
- **Quick-actions micro-menu** `[⋯]` on hover, type-aware:
  - xlsx/csv: `[Preview] [Apply as demand profile] [Delete]` — "Apply as demand profile" prefills `Apply this Excel as the demand profile for load <select>`.
  - pdf/image: `[Preview] [Delete]`.
  - Agent-export xlsx/csv: `[Preview] [Download] [Delete]`.
- **Preview modal per type**:
  - Images ≤10 MB → full-res; > 10 MB → 400×400 thumbnail + `[Open original]`.
  - PDFs → first page + `[Download full PDF]`.
  - Excel/CSV → first sheet 20 rows via `read_excel_sheet(max_rows=20)` + sheet picker + `[Download full file]`.
  - Markdown → first 1000 chars + `[Download full]`.
- **Delete confirmation**: trash → inline 5s Undo toast for user uploads. Typed-confirmation modal RESERVED for `kind="agent_export"` only (agent exports represent hours of LLM compute).
- **History rendering**: chip shows "attached to this turn" label/paperclip if it was attached. Deleted files render as muted "Attachment no longer available" chip; click removes from view only (doesn't mutate chat.jsonl).

### 5. Export-ready announcement

- **Persistent numbered badge** on chat panel header ("Exports: 2"); decrements on preview/download click.
- **5s gentle pulse** on the new chip (subtle yellow glow → fade).
- **Floating discovery pill** "↓ N exports ready" bottom-right of chat viewport; dismissable `×`; auto-dismiss 15s OR on drawer open / badge click. Catches users on long agent responses where the header badge is easy to miss.
- `aria-live="polite"` announces "New export ready: <filename>".
- Side drawer ("Recent exports") accessible via badge / pill / sidebar arrow — triple-redundant discovery.

### 6. Send wiring + history rehydration

- `onSend` builds `attachment_file_ids` from checked-ON chips in display order.
- Sticky chips survive the turn.
- `chat_history` endpoint returns turns with `attachment_file_ids`; panel renders read-only chip strip ABOVE each replayed user message. Missing file_id → muted chip with tooltip.

### 7. Accessibility

- Delete/download buttons: `aria-label="Delete {filename}"` / `"Download {filename}"`.
- Chip container: `aria-live="polite"` for add announcements; each chip `tabindex="0"`; Enter = preview.
- Send button: `aria-describedby="send-attachments-desc"` listing checked-ON files; on send fires `aria-live="assertive"` "Sending message with 2 attachments: …".
- Paste button aria-label: "Paste image or PDF from clipboard — Ctrl+Shift+V or Command+Shift+V".
- CHATBOT.md "Keyboard shortcuts" table: Ctrl+U (picker), Ctrl+Shift+V (image paste), Ctrl+V (text paste).

### 8. Error UX with remediation

`ErrorBanner` gains cases keyed by `error_kind`. One-click action button ONLY when remediation is genuinely one-click; otherwise explanation + docs link.

| `error_kind` | Action button |
|---|---|
| `file_too_large` | `[Try another file]` re-opens picker |
| `mime_type_mismatch` | `[Retry upload]` re-opens picker |
| `unsupported_mime` | `[Open docs]` |
| `empty_file` | `[Try another file]` |
| `snapshot_count_mismatch` | `[Resample to N rows]` (client/server resample helper) + collapsible inline preview |
| `snapshot_range_mismatch` | NO button — date shifting unsafe (leap days); inline preview shows ranges side-by-side |
| `time_column_parse_error` | NO button — offline fix |
| `upload_quota_exceeded` | `[Manage uploads]` opens modal (sortable table, multi-select, filter tabs: Oldest / Largest / User-uploaded / Agent-exported) |
| `multimodal_rejected` | `[Retry without file]` + `[Report to maintainers]` (pre-fills GitHub issue) |
| `signature_expired` | `[Refresh attachments]` re-issues HMAC tokens for all chips. NO auto-refresh (avoids thundering herd) |

### Tests (`e2e_chat_uploads.sh` + frontend specs)

- Upload Excel → list; `apply_demand_from_excel` end-to-end.
- Image upload → multimodal pass-through reaches SDK; PDF same.
- Delete → list empty; file survives Save-As.
- Reload page → chip strip rehydrates with prior turn attachments.
- Agent export → accent-coloured chip, download button, 5s pulse, header badge increments, floating pill appears.
- Drag-drop 5 files → stacked progress toast with per-file rows, file-type icons, per-row cancel.
- Pre-size check: 48 MB → modal `[OK]` + `[Learn how]`, no upload.
- 15-25 MB → orange warning badge.
- Reorder → bounce animation, badges re-number, send tooltip reflects order.
- Sticky chip checkbox: uncheck previously-sent → not in next send.
- Snapshot mismatch → inline preview, `[Resample to N rows]` works, no panel-jump.
- Screen reader (jest-axe): send button `aria-describedby` reflects checked attachments.
- Ctrl+U opens picker; Ctrl+Shift+V triggers image paste UX.

---

## Cross-cutting concerns

### Multi-user posture

Backend is single-origin localhost; `loaded_project` is process-global. All uploads endpoints inherit single-user posture. Future multi-user must add per-project ACL at the router layer BEFORE the upload lock — left as docstring in `routers/uploads.py`. Lock catalogue:

| Lock | Scope | Multi-user behaviour |
|---|---|---|
| `_project_upload_lock(name)` | per-project | SHARED across sessions; per-user ACL gates at router |
| `PyPSAService.get_lock()` | per-process | UNCHANGED; one user mutates at a time |
| `ctx.chat_state.lock` | per-session | Independent per session |
| `_state_lock` | per-process | UNCHANGED |

Phase C signature's `session_id` is signing-only, not lock participation.

### CSRF posture

Localhost-only deployment with permissive CORS. Public-facing deployments MUST add CSRF (double-submit cookie or X-CSRF-Token header) at the reverse proxy. `main.py` startup checks CORS origins; non-localhost → loud WARNING in log + one-time GUI banner.

### Quota enforcement

- Per project: **100 MB total** OR **50 files**, configurable via `PYPSA_GUI_UPLOAD_QUOTA_MB` / `PYPSA_GUI_UPLOAD_QUOTA_FILES`.
- Algorithm walks `uploads/`, counts BOTH valid uploads (from meta.json) AND orphan blobs (stat fallback) toward both caps. Orphan-counting test: write 95 MB orphan, attempt 10 MB legitimate upload → 507 (without the fix, 105 MB silently lands).
- 507 `upload_quota_exceeded` carries usage vs cap. Logged to `change_log_service`.

### Zip-bomb defence

- `.xlsx` / `.docx`: `zipfile.ZipFile(...).infolist()` cumulative `file_size` must be < 100 MB (4× cap).
- Agent exports: serialised-bytes check BEFORE write.
- PDFs: page-count read BEFORE multimodal block; truncate at 100.

### Crash-safety + orphan cleanup

- `prune_orphans(project_name)` runs on project load + every 5 min; removes dirs with no meta.json AND mtime >60s old.
- `_prune_tasks: dict[str, asyncio.Task]` protected by `_prune_tasks_lock` (threading.Lock). `activate_project(name)` atomic check-and-spawn; `deactivate_project(name)` atomic pop + cancel-outside-lock.
- FastAPI shutdown cancels all tasks; each task's `CancelledError` performs a final sweep.

### OneDrive sync

`uploads/` lives in the OneDrive-synced project dir. Pre-Phase-A smoke test: 5 × 2 MB uploads + concurrent solve; if sync delay > 30s or file lock blocks the solve, banner advises users to exclude `pypsa-gui/projects/*/uploads/` via OneDrive Selective sync. Data-recovery section in CHATBOT.md points to OneDrive Recycle Bin and Sync problems center.

### Logging contract

| Event | Level | Channels |
|---|---|---|
| Successful upload | INFO | change_log + logging |
| Successful delete | INFO | change_log + logging |
| Delete not_found | DEBUG | logging only |
| Quota hit (507) | WARN | change_log + logging |
| Quota approaching (>80%) | INFO | logging only |
| Schema/malformed skip | WARN | logging only (1 per project per startup) |
| `prune_orphans` sweep | INFO if N>0, DEBUG if 0 | change_log if N>0 |
| Write failure | ERROR | change_log + logging + stacktrace |
| Lock hierarchy violation | ERROR | logging + raise `RuntimeError` |

`PYPSA_GUI_STRUCTURED_LOGS=1` formats as JSON. Metrics endpoint `GET /api/uploads/metrics`: `upload_count_total`, `upload_quota_bytes_used`, `upload_orphan_dirs_total`, `upload_dedup_hits_total`, `upload_concurrent_same_bytes_races_total`.

### Documentation

- `pypsa-gui/CHATBOT.md` "File uploads" section: drag-drop, paste, supported formats, 25 MB cap, example workflows, sticky chip semantics, OneDrive guidance + data-recovery, keyboard shortcuts table, "Cost estimates" footnote.
- `pypsa-gui/CHANGELOG.md`: "Chatbot file uploads + agent exports: drop spreadsheets, images, and PDFs into chat; the assistant can read and apply them, and generate downloadable Excel/CSV/PNG exports."

---

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Surprise token bill from large PDF | Hard 25 MB cap; PDF truncated at 100 pages |
| Image vision hallucinates buses | `reconstruct_network_from_image` inside undo snapshot |
| Path traversal via filename | sha256 file_id on disk; `safe_upload_filename` rejects traversal |
| MIME spoofing | `python-magic` byte sniff; mismatch → 400 |
| Concurrent upload race | Per-project lock; full critical section atomic; `O_CREAT\|O_EXCL` on meta.json.tmp |
| Bundle inclusion forgotten | `_BUNDLE_DIRS` constant + `_copy_bundle_dirs` helper + 7-transition spy tests |
| Disk fills from unbounded uploads | 100 MB / 50-file quota; orphan-counting in algorithm |
| Zip-bomb via crafted xlsx | Central-directory size guard ≤ 100 MB |
| Mid-stream multimodal SDK rejection | `multimodal_rejected` frame, no auto-retry, retry-without-file button |
| Orphan blob from crash | meta-first write order with `blob_ready`; `prune_orphans` after 60s grace |
| Cache tokens uncounted | Accrue `cache_read_input_tokens` + `cache_creation_input_tokens` into `usage_acc` and chat.jsonl |
| Token estimate misleads | Disclaimer + byte-based fallback + suppress feature flag |
| `os.rename` fails on Windows | `os.replace` everywhere (atomic on both platforms) |
| Same-bytes re-upload after delete bypasses signature | `version` counter in meta.json + signature |
| Lock hierarchy violation deadlocks solver | Per-thread sentinel always active (production); `_apply_with_proper_lock_hierarchy` helper; pre-commit lint rule |
| `chat_state.lock` race on tool dispatch | Lock held for ENTIRE turn (tool dispatch + JSONL append) |
| File deleted while tool reads it | mtime stat-and-verify; 409 `upload_in_use_conflict` |
| Partial multimodal failure inflates usage_acc | try/finally with `pending_usage` local; partial-turn marker in chat.jsonl |
| Project delete races concurrent list | `delete_project` acquires upload lock before rmtree |
| Quota cleanup tedious | `[Manage uploads]` modal with sortable table + filter tabs |
| Oversize file leaves user without remediation | `[Learn how]` button with OS-specific compression guide |
| Touch reorder conflicts with scroll | Horizontal drag disabled on touch; long-press reorder mode |
| Signature expiry stranded user | `[Refresh attachments]` button (manual; no auto-refresh) |

---

## Implementation order

Each phase ships independently with its own QA gate (3 parallel read-only agents per the standing rule).

0. **Phase 0** (deps + migration script) — `pixi.toml` + `requirements.txt` land first.
1. **Phase A** (storage + REST + bundle inclusion + lock policy + schema versioning) — ships as MVP; power users can drive uploads via the API even before chat tools land.
2. **Phase B** (chat tools — consume + export) — enables demand-profile workflow without multimodal, plus downloadable agent outputs.
3. **Phase C** (multimodal pass-through) — enables image-based workflows.
4. **Phase D** (chat panel UI) — exposes everything to non-technical users.

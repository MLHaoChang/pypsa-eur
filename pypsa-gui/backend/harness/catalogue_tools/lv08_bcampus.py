"""
LV08 B-campus (live-voice issue 08 slice B): campus and gridspine routes, schemas and routes.

Moved out of `harness/catalogue.py` (RETRO-FRAG-1); spliced back at its old place, so it declares no ORDER.
Dispatchers: `services/chat_dispatchers/lv08_bcampus.py`.
"""

from __future__ import annotations

from harness.catalogue_base import _t
from services.campus_island_report import TABLE_IDS as _ISLAND_TABLE_IDS

TOOLS = [
    # ── LV08 B-campus (live-voice issue 08 slice B): campus and gridspine routes ──
    # Each calls its GUI route's own handler in-process (TOOL_ROUTES below).
    _t(
        "campus_save_campus",
        "Save a hub project's campus file (the YAML campus_get_study returns as campus_yaml) as the Campus electrical panel's editor does: it is validated by building the campus, so a file that does not build is 422 naming the problem and nothing is saved. Read the current file first (campus_get_study: campus_yaml and campus_sha256), change only what the user asked and keep every value's measured|datasheet|assumed tag; pass that campus_sha256 as base_sha256 (omit it only when there is no file yet). The user sees a card of what changes before it is saved. 409 file_changed_since_read when the file changed since you read it (read it again); 409 project_locked under another user's edit lock; 413 over the size cap. Returns {campus_sha256, campus_yaml}. Safety: write.",
        {
            "project_id": {"type": "string"},
            "yaml": {"type": "string"},
            "base_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
        ["project_id", "yaml"],
    ),
    _t(
        "campus_milp_status",
        "The joint optimisation (MILP) background job of a hub project's campus study, as the panel polls it: {job: {state running|done|cancelled|failed, iteration, max_iter, best_cost and c8_cost (EUR per year), message, error}, or null when no job ran since the backend started}. campus_get_milp reads the result. Safety: read.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _t(
        "campus_start_milp",
        "Start the joint optimisation (MILP) of a hub project's campus study, as the panel's Joint optimisation button does (the user confirms a card of the settings first), with campus_run_study's settings (omitted ones take the panel's defaults). It needs a finished campus run and takes minutes: follow it with campus_milp_status and read the result with campus_get_milp; campus_cancel_milp stops it. 409 while one runs or under another user's edit lock; 422 for a bad setting. Safety: execution_long_running.",
        {
            "project_id": {"type": "string"},
            "k": {"type": "integer", "minimum": 1, "maximum": 50},
            "pf": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            "profile": {"type": "string", "minLength": 1, "maxLength": 64},
            "margin": {"type": "number", "minimum": 0, "maximum": 2},
            "n_minus_1": {"type": "boolean"},
            "invest": {"type": "boolean"},
            "pcc_switchgear_by_operator": {"type": "boolean"},
            "pcc_tap_control": {"type": "string", "enum": ["campus", "grid_operator"]},
        },
        ["project_id"],
    ),
    _t(
        "campus_cancel_milp",
        "Ask a hub project's running joint optimisation (MILP) to stop; it keeps the best choice found so far. Returns {state, cancelling}; 404 when no job ran since the backend started. Safety: write.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _t(
        "campus_set_grid_limit",
        "Set the user's own grid-readiness limit for one scorecard item a grid code does not fix (item connection_ramp|oscillation|fault_level), as the scorecard's limit editor does: source code|datasheet|measured|assumed (only assumed leaves the limit to check), value in the item's unit (fault_level takes a source only), note (<= 500 chars) says where it comes from. Returns the stored entry {value, source, note, set_by, set_at, default}; campus_grid_readiness then shows it. 422 naming the field for a bad item, source or value; 409 project_locked under another user's edit lock. Safety: write.",
        {
            "project_id": {"type": "string"},
            "item": {
                "type": "string",
                "enum": ["connection_ramp", "oscillation", "fault_level"],
            },
            "source": {
                "type": "string",
                "enum": ["code", "datasheet", "measured", "assumed"],
            },
            "value": {"type": "number"},
            "note": {"type": "string", "maxLength": 500},
        },
        ["project_id", "item", "source"],
    ),
    _t(
        "campus_reset_grid_limit",
        "Reset one grid-readiness limit (connection_ramp|oscillation|fault_level) to the template's value or the generic placeholder, dropping the user's own entry. Returns {item, cleared} (false when there was none). Safety: write.",
        {
            "project_id": {"type": "string"},
            "item": {
                "type": "string",
                "enum": ["connection_ramp", "oscillation", "fault_level"],
            },
        },
        ["project_id", "item"],
    ),
    _t(
        "campus_island_report",
        "The island report of a hub project, as the Island tab shows it: its tables (each with the model behind it), the screening caveat, and the rows that rest on assumed values. Say the caveat and the assumed rows with the numbers. Long lists are trimmed and `truncated` names each one's total: without `table` only the first two tables are shown, and `table_ids` lists every table the report holds. Pass `table` (an id) to read one table with all its rows, for example `freq_events` for the frequency events. The ids are `"
        + "`, `".join(_ISLAND_TABLE_IDS)
        + "`. A table whose rows still exceed the cap says rows shown of total in `truncated`. Safety: read.",
        {
            "project_id": {"type": "string"},
            "table": {"type": "string", "enum": list(_ISLAND_TABLE_IDS)},
        },
        ["project_id"],
    ),
    _t(
        "campus_cancel_island_cuts",
        "Stop a hub project's running island-cuts apply job after the trial in hand. Returns {job}: the job record, or null when none ran. Safety: write.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _t(
        "campus_remove_island_cut",
        "Take one applied frequency cut back (period, hour and kind from campus_island_cuts' applied rows); the next solve no longer has it. 404 when no such cut is applied; 409 project_locked under another user's edit lock. Returns the cuts as campus_island_cuts does. Safety: destructive.",
        {
            "project_id": {"type": "string"},
            "period": {"type": "integer"},
            "hour": {"type": "integer", "minimum": 0},
            "kind": {
                "type": "string",
                "enum": ["inertia", "headroom_up", "headroom_down", "droop_gain"],
            },
        },
        ["project_id", "period", "hour", "kind"],
    ),
    _t(
        "campus_clear_island_cuts",
        "Take EVERY applied frequency cut of a hub project back, and the trials with them. Ask the user first: the trials are lost. Returns the cuts as campus_island_cuts does. Safety: destructive.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _t(
        "campus_gfm_settings_card",
        "Preview applying a hub project's recommended GFM settings (campus_recommend_gfm_settings) without writing: the same card campus_apply_gfm_settings shows, old to new per unit and the consequences. Safety: read.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _t(
        "campus_reset_library",
        "Delete a hub project's own copy of the campus asset library, so the shipped default is used again (the user's prices, quotes and added items are lost). Ask the user first. Returns the library as campus_get_library does. Safety: destructive.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _t(
        "gridspine_upload_external_dispatch",
        "Set a planning → dynamics study's dispatch source to the client's OWN dispatch and demand tables, from files the user UPLOADED to the active project (file ids from list_uploads): dispatch_file_id (CSV, or one Excel workbook with dispatch and loads sheets) and loads_file_id (CSV, when the dispatch file does not carry the loads). Validated on arrival: 422 names what the file needs fixed; 413 over the size cap. The project must be the active one (409 upload_project_mismatch otherwise). Safety: write.",
        {
            "project_id": {"type": "string"},
            "dispatch_file_id": {"type": "string"},
            "loads_file_id": {"type": "string"},
        },
        ["project_id", "dispatch_file_id"],
    ),
    _t(
        "gridspine_upload_readback",
        "Compare the engineer's PowerFactory export for hour `hour`'s handoff bundle with the study, from CSVs the user UPLOADED to the active project (file ids from list_uploads): bus_file_id (required) and branches_file_id (optional). Returns the comparison as gridspine_get_readback reads it. 404 when that hour has no bundle; 422 for a malformed CSV; 413 over the shared size cap. The project must be the active one (409 upload_project_mismatch otherwise). Safety: write.",
        {
            "project_id": {"type": "string"},
            "hour": {"type": "integer", "minimum": 0},
            "bus_file_id": {"type": "string"},
            "branches_file_id": {"type": "string"},
        },
        ["project_id", "hour", "bus_file_id"],
    ),
    # Grid codes (owner decision GRIDCODE-CHAT-1): confirm, publish and the deletes show a card first.
    _t(
        "campus_upload_grid_code_document",
        "Add a grid code PDF the user UPLOADED to the active project (file_id from list_uploads) to a hub project's campus grid codes, as the Grid codes tab's upload does; page_ranges (e.g. '1-40,120-180') selects the pages extraction reads. It is kept by its hash: the same file again returns the first upload's metadata. Returns {id, sha256, filename, size, uploaded_at, pages, page_ranges?}; campus_extract_grid_code drafts a profile from it. 415 when the file is not a PDF; 413 over the size cap; 422 for bad page ranges. The project must be the active one (409 upload_project_mismatch otherwise). Safety: write.",
        {
            "project_id": {"type": "string"},
            "file_id": {"type": "string"},
            "page_ranges": {"type": "string", "maxLength": 200},
        },
        ["project_id", "file_id"],
    ),
    _t(
        "campus_delete_grid_code_document",
        "Delete an uploaded grid-code document (document_id from campus_list_grid_codes). The user confirms a card naming the file and the drafts that cite it; their quotes can no longer be checked afterwards. Safety: destructive.",
        {"project_id": {"type": "string"}, "document_id": {"type": "string"}},
        ["project_id", "document_id"],
    ),
    _t(
        "campus_new_grid_code_draft",
        "Start a BLANK grid-code draft to fill in by hand (no document, no API key): the generic profile's structure with every limit tagged assumed. profile_id names it; overwrite replaces an existing draft of that id (the card says so). 409 when the draft exists without overwrite. Returns the draft as campus_get_grid_code_draft does. Safety: write.",
        {
            "project_id": {"type": "string"},
            "profile_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "title": {"type": "string", "maxLength": 200},
            "overwrite": {"type": "boolean"},
        },
        ["project_id", "profile_id"],
    ),
    _t(
        "campus_get_grid_code_draft",
        "Read one grid-code DRAFT of a hub project: {id, sha256 (pass it as base_sha256 to campus_save_grid_code_draft), yaml, profile, review (per limit: quote_found and found_on_page), unconfirmed (limit paths still extracted), document}. A limit tagged extracted was read from a document and no person has checked it: say so. 404 for an unknown draft. Safety: read.",
        {
            "project_id": {"type": "string"},
            "profile_id": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        ["project_id", "profile_id"],
    ),
    _t(
        "campus_save_grid_code_draft",
        "Replace a grid-code draft's YAML with an edit, as the Grid codes tab's editor does: validated by the profile loader, and its quotes are looked up in the document again. Read it first (campus_get_grid_code_draft), change only what the user asked, and pass its sha256 as base_sha256. The user sees a card of what changes first. A limit you change by hand is no longer extracted: tag it as the user says (code, assumed). 409 file_changed_since_read when the draft changed since you read it; 422 when the YAML does not validate (nothing is saved); 404 for an unknown draft. Safety: write.",
        {
            "project_id": {"type": "string"},
            "profile_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "yaml": {"type": "string"},
            "base_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
        ["project_id", "profile_id", "yaml", "base_sha256"],
    ),
    _t(
        "campus_delete_grid_code_draft",
        "Delete a grid-code draft and its review. The user confirms a card naming the draft first; it cannot be undone. 404 for an unknown draft. Safety: destructive.",
        {
            "project_id": {"type": "string"},
            "profile_id": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        ["project_id", "profile_id"],
    ),
    _t(
        "campus_confirm_grid_code_limit",
        "Confirm ONE extracted limit of a grid-code draft: it becomes code, keeping its page and quote. Only when the user says they checked that limit against its quoted page: the card shows them the limit, the verbatim quote, its page and clause, and whether the quote was found on that page, and they confirm there. limit is a path from the draft's unconfirmed list (voltage_bands[i], q_range_demand, rvc_limit_pct, campus_voltage, ...). 422 for a limit that is not extracted or does not exist. Returns the draft as campus_get_grid_code_draft does. Safety: destructive.",
        {
            "project_id": {"type": "string"},
            "profile_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "limit": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        ["project_id", "profile_id", "limit"],
    ),
    _t(
        "campus_publish_grid_code",
        "Publish a grid-code draft so a campus study can use it as its profile (campus_run_study's profile), replacing a published profile of the same id. The user confirms a card first: the title, what it replaces and each unconfirmed limit. 409 while a limit is unconfirmed unless allow_unconfirmed (then those limits stay extracted and every report row on them says so: only when the user asks for it); 422 while the voltage bands leave a voltage uncovered. Returns {id, unconfirmed, profiles}. Safety: destructive.",
        {
            "project_id": {"type": "string"},
            "profile_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "allow_unconfirmed": {"type": "boolean"},
        },
        ["project_id", "profile_id"],
    ),
    _t(
        "campus_get_published_grid_code",
        "Read one PUBLISHED grid-code profile of a hub project: {id, sha256, yaml, profile, unconfirmed, document}. unconfirmed limits were published still extracted: say so before relying on them. 404 for an unknown profile. Safety: read.",
        {
            "project_id": {"type": "string"},
            "profile_id": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        ["project_id", "profile_id"],
    ),
    _t(
        "campus_delete_published_grid_code",
        "Delete a published grid-code profile: studies can no longer use it (the draft, if any, stays). The user confirms a card naming it first. 404 for an unknown profile. Safety: destructive.",
        {
            "project_id": {"type": "string"},
            "profile_id": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        ["project_id", "profile_id"],
    ),
]

ROUTES = {
    # ── LV08 B-campus (live-voice issue 08 slice B) ─────────────────────────
    "campus_save_campus": [("PUT", "/api/campus-electrical/{name}/campus")],
    "campus_milp_status": [("GET", "/api/campus-electrical/{name}/milp")],
    "campus_start_milp": [("POST", "/api/campus-electrical/{name}/milp")],
    "campus_cancel_milp": [("POST", "/api/campus-electrical/{name}/milp/cancel")],
    "campus_set_grid_limit": [
        ("PUT", "/api/campus-electrical/{name}/grid-readiness/limits/{item}")
    ],
    "campus_reset_grid_limit": [
        ("DELETE", "/api/campus-electrical/{name}/grid-readiness/limits/{item}")
    ],
    "campus_island_report": [("GET", "/api/campus-electrical/{name}/island/report")],
    "campus_cancel_island_cuts": [
        ("POST", "/api/campus-electrical/{name}/island/cuts/apply/cancel")
    ],
    "campus_remove_island_cut": [
        ("DELETE", "/api/campus-electrical/{name}/island/cuts/{period}/{hour}/{kind}")
    ],
    "campus_clear_island_cuts": [
        ("DELETE", "/api/campus-electrical/{name}/island/cuts")
    ],
    "campus_gfm_settings_card": [
        ("GET", "/api/campus-electrical/{name}/island/gfm-settings/card")
    ],
    "campus_reset_library": [("POST", "/api/campus-electrical/{name}/library/reset")],
    "gridspine_upload_external_dispatch": [
        ("POST", "/api/gridspine/{name}/dispatch-source/external")
    ],
    "gridspine_upload_readback": [("POST", "/api/gridspine/{name}/readback/{hour}")],
    "campus_upload_grid_code_document": [
        ("POST", "/api/campus-electrical/{name}/grid-codes/documents")
    ],
    "campus_delete_grid_code_document": [
        ("DELETE", "/api/campus-electrical/{name}/grid-codes/documents/{document_id}")
    ],
    "campus_new_grid_code_draft": [
        ("POST", "/api/campus-electrical/{name}/grid-codes/drafts")
    ],
    "campus_get_grid_code_draft": [
        ("GET", "/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}")
    ],
    "campus_save_grid_code_draft": [
        ("PUT", "/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}")
    ],
    "campus_delete_grid_code_draft": [
        ("DELETE", "/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}")
    ],
    "campus_confirm_grid_code_limit": [
        ("POST", "/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}/confirm")
    ],
    "campus_publish_grid_code": [
        ("POST", "/api/campus-electrical/{name}/grid-codes/drafts/{profile_id}/publish")
    ],
    "campus_get_published_grid_code": [
        ("GET", "/api/campus-electrical/{name}/grid-codes/published/{profile_id}")
    ],
    "campus_delete_published_grid_code": [
        ("DELETE", "/api/campus-electrical/{name}/grid-codes/published/{profile_id}")
    ],
}

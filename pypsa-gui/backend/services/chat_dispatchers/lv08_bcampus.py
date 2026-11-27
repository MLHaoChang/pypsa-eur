"""
LV08 B-campus (live-voice issue 08 slice B): the campus and gridspine route
dispatchers.

Moved out of `services/chat_tools.py` (RETRO-FRAG-1), function bodies
unchanged. Schemas and routes: `harness/catalogue_tools/lv08_bcampus.py`.

Each tool calls its GUI route's own handler in-process (the route in
`catalogue.TOOL_ROUTES`), so the route's lock check, body validation and
refusals are the tool's. The project is NAMED (`project_id`, as the other
campus tools), not taken from the session.

The chat_tools helpers stay in chat_tools and are reached through the
proxies below, at call time: chat_tools imports this module part-way through
its own import, and tests that patch `chat_tools.<helper>` still apply.
"""

from __future__ import annotations

from fastapi import HTTPException


def _ct():
    from services import chat_tools

    return chat_tools


def _acting(*args, **kwargs):
    return _ct()._acting(*args, **kwargs)


def _b2b_fit(*args, **kwargs):
    return _ct()._b2b_fit(*args, **kwargs)


def _campus_route(*args, **kwargs):
    return _ct()._campus_route(*args, **kwargs)


def _closing_uploads(*args, **kwargs):
    return _ct()._closing_uploads(*args, **kwargs)


def _cut(*args, **kwargs):
    return _ct()._cut(*args, **kwargs)


def _gridspine_project(*args, **kwargs):
    return _ct()._gridspine_project(*args, **kwargs)


def _gridspine_upload(*args, **kwargs):
    return _ct()._gridspine_upload(*args, **kwargs)


def _with_sha(*args, **kwargs):
    return _ct()._with_sha(*args, **kwargs)


def campus_save_campus(
    project_id: str, yaml: str, base_sha256: str | None = None
) -> dict:
    """`PUT …/campus`: the user's campus file, validated by building it. Refused
    when the file changed since the model read it (`base_sha256`)."""
    from harness import campus_cards
    from routers.campus_electrical import CampusText, save_campus as _h

    with _acting() as (db, user):
        campus_cards.require_base(
            campus_cards.campus_text(_gridspine_project(db, user, project_id)),
            base_sha256,
            "campus file",
        )
    out = _campus_route(_h, project_id, CampusText(yaml=yaml))
    return _b2b_fit(
        {"campus_sha256": campus_cards.sha256_text(out.get("campus_yaml")), **out}
    )


def campus_milp_status(project_id: str) -> dict:
    """`GET …/milp`: the joint optimisation job's record."""
    from routers.campus_electrical import milp_status as _h

    return _b2b_fit({"job": _campus_route(_h, project_id)})


def campus_start_milp(
    project_id: str,
    k: int | None = None,
    pf: float | None = None,
    profile: str | None = None,
    margin: float | None = None,
    n_minus_1: bool | None = None,
    invest: bool | None = None,
    pcc_switchgear_by_operator: bool | None = None,
    pcc_tap_control: str | None = None,
) -> dict:
    """`POST …/milp`: start the joint optimisation with these run settings
    (omitted ones take the route's defaults)."""
    from routers.campus_electrical import RunSettings, start_milp as _h

    given = {
        key: v
        for key, v in (
            ("k", k),
            ("pf", pf),
            ("profile", profile),
            ("margin", margin),
            ("n_minus_1", n_minus_1),
            ("invest", invest),
            ("pcc_switchgear_by_operator", pcc_switchgear_by_operator),
            ("pcc_tap_control", pcc_tap_control),
        )
        if v is not None
    }
    return _campus_route(_h, project_id, RunSettings(**given))


def campus_cancel_milp(project_id: str) -> dict:
    """`POST …/milp/cancel`: the job stops and keeps its best point."""
    from routers.campus_electrical import cancel_milp as _h

    return _campus_route(_h, project_id)


def campus_set_grid_limit(
    project_id: str, item: str, source: str, value: float | None = None, note: str = ""
) -> dict:
    """`PUT …/grid-readiness/limits/{item}`: the user's own limit."""
    from routers.campus_electrical import LimitBody, put_grid_readiness_limit as _h

    return _campus_route(
        _h, project_id, item, LimitBody(value=value, source=source, note=note)
    )


def campus_reset_grid_limit(project_id: str, item: str) -> dict:
    """`DELETE …/grid-readiness/limits/{item}`: back to the template's value."""
    from routers.campus_electrical import delete_grid_readiness_limit as _h

    return _campus_route(_h, project_id, item)


def campus_island_report(project_id: str, table: str | None = None) -> dict:
    """`GET …/island/report`, trimmed under the result cap. The whole payload
    keeps its first two tables; `table` (an id) returns that one table with
    its rows, trimmed only when the rows alone exceed the cap."""
    from routers.campus_electrical import island_report as _h
    from services.campus_island_report import TABLE_IDS

    payload = _campus_route(_h, project_id)  # resolves the campus first
    tables = payload.get("tables")
    tables = tables if isinstance(tables, list) else []
    available = [t["id"] for t in tables]
    if table is None:
        return {**_b2b_fit(payload), "table_ids": available}
    if not isinstance(table, str) or table not in TABLE_IDS:
        raise HTTPException(
            status_code=422,
            detail={
                "error_kind": "invalid_tool_args",
                "message": f"table must be one of {list(TABLE_IDS)}, got {_cut(table, 40)!r}",
            },
        )
    one = next((t for t in tables if t["id"] == table), None)
    out = _b2b_fit(
        {
            "caveat": payload.get("caveat"),
            "missing": payload.get("missing"),
            "table": one,
        }
    )
    out["table_ids"] = available
    if one is None:
        out["not_in_report"] = (
            f"{table} is not in this report: see `missing` and `table_ids`."
        )
    if "truncated" in out:
        out["truncated_note"] = (
            "Rows beyond `shown` are cut; GET "
            "/api/campus-electrical/{name}/island/report returns the full table."
        )
    return out


def campus_cancel_island_cuts(project_id: str) -> dict:
    """`POST …/island/cuts/apply/cancel`: stop after the trial in hand."""
    from routers.campus_electrical import cancel_island_cuts as _h

    return {"job": _campus_route(_h, project_id)}


def campus_remove_island_cut(
    project_id: str, period: int, hour: int, kind: str
) -> dict:
    """`DELETE …/island/cuts/{period}/{hour}/{kind}`: take one applied cut back."""
    from routers.campus_electrical import remove_island_cut as _h

    return _b2b_fit(_campus_route(_h, project_id, int(period), int(hour), kind))


def campus_clear_island_cuts(project_id: str) -> dict:
    """`DELETE …/island/cuts`: every cut back, and the trials with them."""
    from routers.campus_electrical import clear_island_cuts as _h

    return _b2b_fit(_campus_route(_h, project_id))


def campus_gfm_settings_card(project_id: str) -> dict:
    """`GET …/island/gfm-settings/card`: the apply card, written nowhere."""
    from routers.campus_electrical import gfm_settings_card as _h

    return _b2b_fit(_campus_route(_h, project_id))


def campus_reset_library(project_id: str) -> dict:
    """`POST …/library/reset`: drop the project's copy of the asset library."""
    from routers.campus_electrical import reset_library as _h

    return _b2b_fit(_campus_route(_h, project_id))


def gridspine_upload_external_dispatch(
    project_id: str, dispatch_file_id: str, loads_file_id: str | None = None
) -> dict:
    """`POST …/dispatch-source/external` with UPLOADED tables (never model bytes)."""
    from routers.gridspine import upload_external_dispatch as _h

    dispatch = _gridspine_upload(project_id, dispatch_file_id)
    loads = _gridspine_upload(project_id, loads_file_id) if loads_file_id else None
    with _closing_uploads(dispatch, loads):
        return _b2b_fit(_campus_route(_h, project_id, dispatch=dispatch, loads=loads))


def gridspine_upload_readback(
    project_id: str, hour: int, bus_file_id: str, branches_file_id: str | None = None
) -> dict:
    """`POST …/readback/{hour}` with the engineer's UPLOADED PowerFactory CSVs."""
    from routers.gridspine import upload_readback as _h

    bus = _gridspine_upload(project_id, bus_file_id)
    branches = (
        _gridspine_upload(project_id, branches_file_id) if branches_file_id else None
    )
    with _closing_uploads(bus, branches):
        return _b2b_fit(
            _campus_route(_h, project_id, int(hour), bus=bus, branches=branches)
        )


def campus_upload_grid_code_document(
    project_id: str, file_id: str, page_ranges: str | None = None
) -> dict:
    """`POST …/grid-codes/documents` with the active project's UPLOADED PDF; the route's size cap runs."""
    from routers.campus_electrical import upload_grid_code_document as _h

    pdf = _gridspine_upload(project_id, file_id)
    with _closing_uploads(pdf):
        return _b2b_fit(
            _campus_route(_h, project_id, file=pdf, page_ranges=page_ranges)
        )


def campus_delete_grid_code_document(project_id: str, document_id: str) -> dict:
    from routers.campus_electrical import delete_grid_code_document as _h

    return _campus_route(_h, project_id, document_id)


def campus_new_grid_code_draft(
    project_id: str, profile_id: str, title: str | None = None, overwrite: bool = False
) -> dict:
    from routers.campus_electrical import NewDraft, new_grid_code_draft as _h

    return _with_sha(
        _campus_route(
            _h,
            project_id,
            NewDraft(profile_id=profile_id, title=title, overwrite=bool(overwrite)),
        )
    )


def campus_get_grid_code_draft(project_id: str, profile_id: str) -> dict:
    from routers.campus_electrical import get_grid_code_draft as _h

    return _with_sha(_campus_route(_h, project_id, profile_id))


def campus_save_grid_code_draft(
    project_id: str, profile_id: str, yaml: str, base_sha256: str | None = None
) -> dict:
    """`PUT …/grid-codes/drafts/{id}`; refused when the draft changed since it was read."""
    from harness import campus_cards
    from routers.campus_electrical import ProfileText, save_grid_code_draft as _h

    with _acting() as (db, user):
        current = campus_cards._draft_or_none(
            _gridspine_project(db, user, project_id), profile_id
        )
    campus_cards.require_base(
        current and current["yaml"], base_sha256, f"draft {profile_id!r}"
    )
    return _with_sha(_campus_route(_h, project_id, profile_id, ProfileText(yaml=yaml)))


def campus_delete_grid_code_draft(project_id: str, profile_id: str) -> dict:
    from routers.campus_electrical import delete_grid_code_draft as _h

    return _campus_route(_h, project_id, profile_id)


def campus_confirm_grid_code_limit(
    project_id: str, profile_id: str, limit: str
) -> dict:
    from routers.campus_electrical import ConfirmLimit, confirm_grid_code_limit as _h

    return _with_sha(
        _campus_route(_h, project_id, profile_id, ConfirmLimit(limit=limit))
    )


def campus_publish_grid_code(
    project_id: str, profile_id: str, allow_unconfirmed: bool = False
) -> dict:
    from routers.campus_electrical import Publish, publish_grid_code as _h

    return _b2b_fit(
        _campus_route(
            _h,
            project_id,
            profile_id,
            Publish(allow_unconfirmed=bool(allow_unconfirmed)),
        )
    )


def campus_get_published_grid_code(project_id: str, profile_id: str) -> dict:
    from routers.campus_electrical import get_published_grid_code as _h

    return _with_sha(_campus_route(_h, project_id, profile_id))


def campus_delete_published_grid_code(project_id: str, profile_id: str) -> dict:
    from routers.campus_electrical import delete_published_grid_code as _h

    return _campus_route(_h, project_id, profile_id)


DISPATCHERS = {
    "campus_save_campus": campus_save_campus,
    "campus_milp_status": campus_milp_status,
    "campus_start_milp": campus_start_milp,
    "campus_cancel_milp": campus_cancel_milp,
    "campus_set_grid_limit": campus_set_grid_limit,
    "campus_reset_grid_limit": campus_reset_grid_limit,
    "campus_island_report": campus_island_report,
    "campus_cancel_island_cuts": campus_cancel_island_cuts,
    "campus_remove_island_cut": campus_remove_island_cut,
    "campus_clear_island_cuts": campus_clear_island_cuts,
    "campus_gfm_settings_card": campus_gfm_settings_card,
    "campus_reset_library": campus_reset_library,
    "gridspine_upload_external_dispatch": gridspine_upload_external_dispatch,
    "gridspine_upload_readback": gridspine_upload_readback,
    "campus_upload_grid_code_document": campus_upload_grid_code_document,
    "campus_delete_grid_code_document": campus_delete_grid_code_document,
    "campus_new_grid_code_draft": campus_new_grid_code_draft,
    "campus_get_grid_code_draft": campus_get_grid_code_draft,
    "campus_save_grid_code_draft": campus_save_grid_code_draft,
    "campus_delete_grid_code_draft": campus_delete_grid_code_draft,
    "campus_confirm_grid_code_limit": campus_confirm_grid_code_limit,
    "campus_publish_grid_code": campus_publish_grid_code,
    "campus_get_published_grid_code": campus_get_published_grid_code,
    "campus_delete_published_grid_code": campus_delete_published_grid_code,
}

"""
Campus electrical core: the campus study, island, GFM, library, investment,
MILP and grid-code dispatchers.

Moved out of `services/chat_tools.py` (RETRO-FRAG-1), function bodies
unchanged. Schemas and routes: `harness/catalogue_tools/campus_electrical.py`.

The chat_tools helpers stay in chat_tools and are reached through the
proxies below, at call time: chat_tools imports this module part-way through
its own import, and tests that patch `chat_tools.<helper>` still apply.
"""

from __future__ import annotations


def _ct():
    from services import chat_tools

    return chat_tools


def _acting(*args, **kwargs):
    return _ct()._acting(*args, **kwargs)


def _campus_check_lock(*args, **kwargs):
    return _ct()._campus_check_lock(*args, **kwargs)


def _gridspine_project(*args, **kwargs):
    return _ct()._gridspine_project(*args, **kwargs)


def _gridspine_project_for_write(*args, **kwargs):
    return _ct()._gridspine_project_for_write(*args, **kwargs)


def island_tool_args(*args, **kwargs):
    return _ct().island_tool_args(*args, **kwargs)


def campus_get_study(project_id: str) -> dict:
    from harness.campus_cards import sha256_text
    from services.campus_electrical_service import get_state as _h

    with _acting() as (db, user):
        state = _h(_gridspine_project(db, user, project_id))
    # The digest campus_save_campus's base_sha256 names (LV08 B-campus).
    return {**state, "campus_sha256": sha256_text(state.get("campus_yaml"))}


def campus_draft_campus(project_id: str, overwrite: bool = False) -> dict:
    from services.campus_electrical_service import draft as _h

    with _acting() as (db, user):
        return _h(_gridspine_project_for_write(db, user, project_id), bool(overwrite))


def campus_run_study(
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
    from services.campus_electrical_service import enqueue_run as _h

    settings = {
        key: v
        for key, v in (
            ("k", k),
            ("profile", profile),
            ("margin", margin),
            ("n_minus_1", n_minus_1),
            ("invest", invest),
            ("pcc_switchgear_by_operator", pcc_switchgear_by_operator),
            ("pcc_tap_control", pcc_tap_control),
        )
        if v is not None
    }
    settings["pf"] = pf
    with _acting() as (db, user):
        return _h(_gridspine_project_for_write(db, user, project_id), settings, user)


def campus_island_config(project_id: str) -> dict:
    from services.campus_electrical_service import get_island as _h

    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def campus_grid_readiness(project_id: str, profile: str | None = None) -> dict:
    """The grid-readiness scorecard (DC grid readiness 02); one source with
    ``GET /api/campus-electrical/{name}/grid-readiness``."""
    from services.adequacy.grid_readiness import grid_readiness_for as _h

    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), profile)


def campus_set_island_config(
    project_id: str,
    config: dict | None = None,
    patch: dict | None = None,
    base: str | None = None,
) -> dict:
    from services.campus_electrical_service import patch_island, save_island

    island_tool_args(config, patch, base)
    with _acting() as (db, user):
        project = _gridspine_project(db, user, project_id)
        _campus_check_lock(db, user, project)
        if patch is not None:
            return patch_island(project, patch, base)
        return save_island(project, config)


def campus_run_island_study(
    project_id: str, scenarios: list[str] | None = None
) -> dict:
    from services.campus_electrical_service import run_island_study as _h

    with _acting() as (db, user):
        project = _gridspine_project(db, user, project_id)
        _campus_check_lock(db, user, project)
        return _h(project, list(scenarios) if scenarios else None)


def campus_island_cuts(project_id: str) -> dict:
    from services import campus_island_cuts as _cic

    with _acting() as (db, user):
        project = _gridspine_project(db, user, project_id)
        return {**_cic.get_cuts(project), "job": _cic.apply_status(project)}


def campus_apply_island_cuts(project_id: str) -> dict:
    from services import campus_island_cuts as _cic

    with _acting() as (db, user):
        project = _gridspine_project(db, user, project_id)
        _campus_check_lock(db, user, project)
        return _cic.start_apply_job(project, user_id=user.id)


def campus_island_gfm_settings(project_id: str) -> dict:
    from services import campus_electrical_service as _ce
    from services import campus_island_tuning as _cit

    with _acting() as (db, user):
        project = _gridspine_project(db, user, project_id)
        job = _ce.run_job_state(project)
        return {
            **_cit.get_settings(project),
            "job": job if job and job.get("stage") == _cit.STAGE else None,
        }


def campus_recommend_gfm_settings(
    project_id: str, allow_i_max: bool | None = None
) -> dict:
    from services import campus_island_tuning as _cit

    with _acting() as (db, user):
        project = _gridspine_project(db, user, project_id)
        _campus_check_lock(db, user, project)
        return _cit.enqueue_settings(project, user, allow_i_max=bool(allow_i_max))


def campus_apply_gfm_settings(project_id: str) -> dict:
    from harness import edit_card as _ec
    from services import campus_island_tuning as _cit

    # The card the user approved must still be this recommendation's (the digest binds its
    # time and values), checked again here, right before the write.
    approved = _ec.approved_digest()
    if approved is not None:
        _ec.require_current(
            "campus_apply_gfm_settings",
            {"project_id": project_id},
            {"digest": approved},
        )
    with _acting() as (db, user):
        project = _gridspine_project(db, user, project_id)
        _campus_check_lock(db, user, project)
        return _cit.apply_settings(project)


def campus_get_library(project_id: str) -> dict:
    from services.campus_electrical_service import get_library as _h

    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def campus_set_library(project_id: str, yaml: str) -> dict:
    from services.campus_electrical_service import save_library as _h

    with _acting() as (db, user):
        return _h(_gridspine_project_for_write(db, user, project_id), yaml)


def campus_get_investment(project_id: str) -> dict:
    from services.campus_electrical_service import get_investment as _h

    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def campus_get_milp(project_id: str) -> dict:
    # Read only, on purpose: the joint optimisation runs for minutes and is
    # started and cancelled in the panel (plan C12).
    from services.campus_electrical_service import get_milp as _h

    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def campus_list_grid_codes(project_id: str) -> dict:
    from services.campus_grid_code_service import list_grid_codes as _h

    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def campus_extract_grid_code(
    project_id: str,
    document_id: str,
    profile_id: str | None = None,
    overwrite: bool = False,
) -> dict:
    # No publish or confirm tool exists, on purpose: the copilot drafts, a
    # person confirms each limit and publishes, in the panel (plan C10).
    from services.campus_grid_code_service import extract as _h

    with _acting() as (db, user):
        return _h(
            _gridspine_project_for_write(db, user, project_id),
            document_id,
            profile_id,
            bool(overwrite),
        )


DISPATCHERS = {
    "campus_get_study": campus_get_study,
    "campus_draft_campus": campus_draft_campus,
    "campus_run_study": campus_run_study,
    "campus_get_library": campus_get_library,
    "campus_set_library": campus_set_library,
    "campus_get_investment": campus_get_investment,
    "campus_get_milp": campus_get_milp,
    "campus_island_config": campus_island_config,
    "campus_grid_readiness": campus_grid_readiness,
    "campus_set_island_config": campus_set_island_config,
    "campus_run_island_study": campus_run_island_study,
    "campus_island_cuts": campus_island_cuts,
    "campus_apply_island_cuts": campus_apply_island_cuts,
    "campus_island_gfm_settings": campus_island_gfm_settings,
    "campus_recommend_gfm_settings": campus_recommend_gfm_settings,
    "campus_apply_gfm_settings": campus_apply_gfm_settings,
    "campus_list_grid_codes": campus_list_grid_codes,
    "campus_extract_grid_code": campus_extract_grid_code,
}

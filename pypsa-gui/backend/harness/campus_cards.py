"""
Edit cards for the campus electrical and grid-code chat tools (live-voice issue
08 slice B-campus; owner decision GRIDCODE-CHAT-1: panel-only actions get chat
tools behind a confirmation card).

Registered on `harness.edit_card` at import (`edit_card` imports this module
last). Each card binds the sha256 of the file it shows, so `require_current`
refuses `edit_preview_stale` when the file moves between card and confirm. A
whole-file write also takes `base_sha256`, the digest of the text the model
read, and refuses `file_changed_since_read` when the file changed before the
card: a model-emitted file never silently reverts a concurrent edit.
"""
from __future__ import annotations

import hashlib
import json

import yaml
from fastapi import HTTPException

from harness import edit_card as ec

CLASS = "Settings"


def sha256_text(text: str | None) -> str | None:
    """The digest a whole-file write's `base_sha256` names (None: no file yet)."""
    return None if text is None else hashlib.sha256(text.encode()).hexdigest()


def require_base(current: str | None, base_sha256: str | None, what: str) -> None:
    """Refuse a whole-file write whose base is not the file as it is now."""
    now = sha256_text(current)
    if base_sha256 != now:
        raise HTTPException(409, {
            "error_kind": "file_changed_since_read",
            "message": f"the {what} changed since it was read (base_sha256 does not match the file now"
                       f"{'' if now is None else f', sha256 {now}'}); read it again and redo the change on "
                       "the current text"})


def _short(value):
    text = json.dumps(value, default=str, allow_nan=True)
    return value if len(text) <= 200 else text[:200] + "…"


def _row(name: str, field: str, label: str, old, new, *, old_known: bool = True) -> dict:
    return {"component_class": CLASS, "name": name, "field": field, "label": label, "old": _short(old),
            "old_known": old_known, "new": _short(new), "unit": "", "role": "typed"}


def _note(name: str, i: int, text: str) -> dict:
    return ec._note(CLASS, name, f"note_{i}", text)


def _back(args: dict, rows: list[dict]) -> list:
    return [None] * len(rows)


def _load(text: str, what: str) -> dict:
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        ec._refuse("invalid_edit_value", f"the {what} is not valid YAML: {exc}")
    return spec if isinstance(spec, dict) else {}


def _yaml_rows(name: str, old_text: str | None, new_text: str, what: str) -> list[dict]:
    """One old → new row per top-level key the write changes."""
    old = _load(old_text, what) if old_text is not None else {}
    new = _load(new_text, what)
    rows = [_row(name, key, key, old.get(key), new.get(key), old_known=old_text is not None)
            for key in sorted(set(old) | set(new), key=str) if old.get(key) != new.get(key)]
    return rows or [_note(name, 0, f"The {what} is unchanged.")]


# ── the campus file ─────────────────────────────────────────────────────────


def campus_text(project) -> str | None:
    from services import campus_electrical_service as ce

    f = ce.campus_dir(project) / ce.CAMPUS_FILE
    return f.read_text() if f.is_file() else None


def _save_campus_preview(args: dict) -> list[dict]:
    def run(project):
        current = campus_text(project)
        require_base(current, args.get("base_sha256"), "campus file")
        return _yaml_rows("Campus file", current, str(args.get("yaml", "")), "campus file")

    return ec._campus(args, run)


def _save_campus_binding(args: dict) -> dict:
    return {"sha256": ec._campus(args, lambda project: sha256_text(campus_text(project)))}


# ── the joint optimisation ──────────────────────────────────────────────────


def _milp_preview(args: dict) -> list[dict]:
    from routers.campus_electrical import RunSettings

    given = {k: v for k, v in (args or {}).items() if k != "project_id" and v is not None}
    try:
        settings = RunSettings(**given).model_dump()
    except ValueError as exc:
        ec._refuse("invalid_edit_value", str(exc))
    rows = [_row("Joint optimisation", key, key, None, value, old_known=False)
            for key, value in settings.items()]
    rows.append(_note("Joint optimisation", 0, "Runs for minutes in the background from the last campus run; "
                                               "cancelling keeps the best choice found so far."))
    return rows


# ── grid codes ──────────────────────────────────────────────────────────────


def _gc(args: dict, run):
    return ec._campus(args, run)


def _draft_or_none(project, profile_id: str):
    from services import campus_grid_code_service as gc

    try:
        return gc.get_draft(project, profile_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _published_or_none(project, profile_id: str):
    from services import campus_grid_code_service as gc

    try:
        return gc.get_published(project, profile_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _profile_note(name: str, i: int, shaped: dict) -> dict:
    profile = shaped.get("profile") or {}
    return _note(name, i, f"{profile.get('title', shaped['id'])!s}: {len(shaped.get('unconfirmed') or [])} "
                          "unconfirmed limit(s).")


def _save_draft_preview(args: dict) -> list[dict]:
    def run(project):
        pid = str(args.get("profile_id", ""))
        current = _draft_or_none(project, pid)
        require_base(current and current["yaml"], args.get("base_sha256"), f"draft {pid!r}")
        if current is None:
            ec._refuse("component_not_found", f"no draft grid-code profile {pid!r}", 404)
        return _yaml_rows(f"Draft {pid}", current["yaml"], str(args.get("yaml", "")), "draft")

    return _gc(args, run)


def _draft_binding(args: dict) -> dict:
    def run(project):
        d = _draft_or_none(project, str(args.get("profile_id", "")))
        return {"draft_sha256": sha256_text(d and d["yaml"])}

    return _gc(args, run)


def _new_draft_preview(args: dict) -> list[dict]:
    """The arguments, and what an overwrite replaces (extract and the blank draft)."""
    rows = [r for r in ec._arguments_preview({k: v for k, v in args.items() if k != "project_id"})]

    def run(project):
        from services import campus_grid_code_service as gc

        pid = args.get("profile_id")
        if pid is None and args.get("document_id"):
            return rows  # the extract derives the id from the document; it refuses an existing one
        existing = _draft_or_none(project, gc._profile_id(str(pid)))
        if existing is None:
            return rows
        if not args.get("overwrite"):
            return rows + [_note(f"Draft {pid}", 0, "A draft of this id exists; without overwrite the call is "
                                                    "refused (409).")]
        return rows + [_profile_note(f"Draft {pid}", 0, existing),
                       _note(f"Draft {pid}", 1, "overwrite replaces this draft and its review.")]

    return _gc(args, run)


def _new_draft_binding(args: dict) -> dict:
    if args.get("profile_id") is None:
        return {}
    return _draft_binding(args)


def _confirm_preview(args: dict) -> list[dict]:
    def run(project):
        from services import campus_grid_code_service as gc

        pid, path = str(args.get("profile_id", "")), str(args.get("limit", ""))
        draft = gc.get_draft(project, pid)
        limit = dict(gc._limits(draft["profile"])).get(path)
        if not isinstance(limit, dict):
            ec._refuse("component_not_found", f"draft {pid!r} has no limit {path!r}", 404)
        if limit.get("source") != "extracted":
            ec._refuse("edit_refused", f"{path} is {limit.get('source')!r}, not extracted; only an extracted "
                                       "limit is confirmed")
        name = f"Draft {pid} {path}"
        rows = [_row(name, "source", "source", "extracted", "code")]
        rows += [_row(name, key, key, value, value) for key, value in limit.items()
                 if key not in ("source", "quote", "page", "clause")]
        check = ((draft.get("review") or {}).get("limits") or {}).get(path) or {}
        found = check.get("quote_found")
        rows += [
            _note(name, 0, f"Quote (page {limit.get('page', '?')}, clause {limit.get('clause', '?')}): "
                           f"“{limit.get('quote', '')}”"),
            _note(name, 1, {True: "The quote was found on its page.",
                            False: "Quote not found on its page"
                                   + (f" (it is on page {check['found_on_page']})"
                                      if check.get("found_on_page") else "") + ": check it before confirming.",
                            None: "The quote could not be checked (no document)."}[found]),
            _note(name, 2, "Confirming says a person checked this limit against the quoted page."),
        ]
        return rows

    return _gc(args, run)


def _publish_preview(args: dict) -> list[dict]:
    def run(project):
        from services import campus_grid_code_service as gc

        pid = str(args.get("profile_id", ""))
        draft = gc.get_draft(project, pid)
        old = _published_or_none(project, pid)
        name = f"Grid code {pid}"
        rows = [_row(name, "published", "published",
                     None if old is None else (old["profile"] or {}).get("title", pid),
                     (draft["profile"] or {}).get("title", pid))]
        rows.append(_note(name, 0, "Replaces the published profile of this id." if old is not None
                                   else "A new published profile; studies can then use it."))
        open_limits = draft.get("unconfirmed") or []
        for i, path in enumerate(open_limits, start=1):
            rows.append(_note(name, i, f"{path} is unconfirmed: "
                                       + ("it stays extracted; every report row on it says so."
                                          if args.get("allow_unconfirmed")
                                          else "publishing is refused (409) until it is confirmed.")))
        return rows

    return _gc(args, run)


def _publish_binding(args: dict) -> dict:
    def run(project):
        pid = str(args.get("profile_id", ""))
        d, p = _draft_or_none(project, pid), _published_or_none(project, pid)
        return {"draft_sha256": sha256_text(d and d["yaml"]), "published_sha256": sha256_text(p and p["yaml"])}

    return _gc(args, run)


def _delete_profile(kind: str):
    def preview(args: dict) -> list[dict]:
        def run(project):
            from services import campus_grid_code_service as gc

            pid = str(args.get("profile_id", ""))
            shaped = (gc.get_draft if kind == "draft" else gc.get_published)(project, pid)
            name = f"{kind.capitalize()} {pid}"
            return [_row(name, kind, kind, (shaped["profile"] or {}).get("title", pid), None),
                    _profile_note(name, 0, shaped),
                    _note(name, 1, f"The {kind} profile is deleted; this cannot be undone.")]

        return _gc(args, run)

    def binding(args: dict) -> dict:
        def run(project):
            pid = str(args.get("profile_id", ""))
            shaped = (_draft_or_none if kind == "draft" else _published_or_none)(project, pid)
            return {"sha256": sha256_text(shaped and shaped["yaml"])}

        return _gc(args, run)

    return preview, binding


def _delete_document_preview(args: dict) -> list[dict]:
    def run(project):
        from services import campus_grid_code_service as gc

        did = str(args.get("document_id", ""))
        listing = gc.list_grid_codes(project)
        doc = next((d for d in listing.get("documents") or [] if d.get("id") == did), None)
        if doc is None:
            ec._refuse("component_not_found", f"no grid-code document {did!r}", 404)
        name = f"Document {doc.get('filename') or did}"
        citing = [d["id"] for d in listing.get("drafts") or [] if d.get("document") == did]
        rows = [_row(name, "document", "document", f"{doc.get('filename')} ({doc.get('size')} bytes, "
                                                    f"{doc.get('pages')} pages)", None)]
        rows.append(_note(name, 0, ("Drafts that cite it: " + ", ".join(citing) + "; their quotes can no longer "
                                    "be checked.") if citing else "No draft cites it."))
        return rows

    return _gc(args, run)


def _register() -> None:
    reg, A = ec.register, ec.Adapter
    reg("campus_save_campus", A("settings_edit", _save_campus_preview, _back, binding=_save_campus_binding))
    reg("campus_start_milp", A("settings_edit", _milp_preview, _back))
    reg("campus_save_grid_code_draft", A("settings_edit", _save_draft_preview, _back, binding=_draft_binding))
    reg("campus_new_grid_code_draft", A("settings_edit", _new_draft_preview, _back, binding=_new_draft_binding))
    reg("campus_extract_grid_code", A("settings_edit", _new_draft_preview, _back, binding=_new_draft_binding))
    reg("campus_confirm_grid_code_limit", A("settings_edit", _confirm_preview, _back, binding=_draft_binding))
    reg("campus_publish_grid_code", A("settings_edit", _publish_preview, _back, binding=_publish_binding))
    for tool, kind in (("campus_delete_grid_code_draft", "draft"),
                       ("campus_delete_published_grid_code", "published")):
        preview, binding = _delete_profile(kind)
        reg(tool, A("settings_edit", preview, _back, binding=binding))
    reg("campus_delete_grid_code_document", A("settings_edit", _delete_document_preview, _back))


_register()

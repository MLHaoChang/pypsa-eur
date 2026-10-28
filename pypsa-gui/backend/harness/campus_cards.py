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

Each adapter's readback observes the action's effect in the files the service
wrote (the stored text's sha256, the limit's source, the profile gone, the job
record), so a receipt is `verified` only when that effect is there.
"""
from __future__ import annotations

import hashlib
import json
import re

import yaml
from fastapi import HTTPException

from harness import edit_card as ec

CLASS = "Settings"

# The Grid codes panel's words for a limit (`limitLabel` in
# frontend/src/pages/CampusGridCodeSection.tsx; tests/test_lv08_bcampus_tools.py keeps them equal).
LIMIT_LABELS = {
    "q_range_demand": "Reactive range at the PCC",
    "rvc_limit_pct": "Rapid voltage change",
    "campus_voltage": "Voltage inside the campus",
    "rvc_limit_infrequent_pct": "Rapid voltage change, infrequent",
    "q_capability_pq": "Reactive power band against active power at the PCC",
    "pf_min_lagging": "Minimum power factor, lagging",
    "pf_min_leading": "Minimum power factor, leading",
    "thd_limit_pct": "Total harmonic distortion at the PCC",
    "harmonic_limits_pct": "Harmonic voltage by order at the PCC",
    "harmonic_resonance_clearance_h": "Harmonic resonance clearance from characteristic and injected orders",
    "pst_limit": "Flicker severity Pst at the PCC",
    "plt_limit": "Flicker severity Plt at the PCC",
    "unbalance_limit_pct": "Voltage unbalance at the PCC",
    "current_tdd_standard": "Current distortion (TDD) limits at the PCC",
    "q_capability_unit": "Reactive capability per generating unit (pu of the unit's rated P; positive = the unit "
                         "exports reactive power)",
    "voltage_deadband_pct": "Voltage-control deadband per unit (reported, not checked)",
    "angle_limit_deg": "Voltage-angle difference across a branch",
}


def limit_label(path: str) -> str:
    m = re.fullmatch(r"voltage_bands\[(\d+)\]", path)
    return f"Voltage band {int(m.group(1)) + 1}" if m else LIMIT_LABELS.get(path, path)


def _title(shaped: dict | None, pid: str) -> str:
    return str(((shaped or {}).get("profile") or {}).get("title") or pid)


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


def _load(text: str | None) -> dict:
    """A stored file's mapping ({} for none, or for text that is not one)."""
    try:
        spec = yaml.safe_load(text) if text is not None else None
    except yaml.YAMLError:
        spec = None
    return spec if isinstance(spec, dict) else {}


def _yaml_rows(name: str, old_text: str | None, new: dict, new_text: str, what: str) -> list[dict]:
    """One old → new row per top-level key the write changes, then the stored
    text's sha256: the receipt reads the file back against both."""
    old = _load(old_text)
    sha = sha256_text(new_text)  # kept on each row for the receipt, never shown
    rows = [{**_row(name, key, key, old.get(key), new.get(key), old_known=old_text is not None), "stored_sha256": sha}
            for key in sorted(set(old) | set(new), key=str) if old.get(key) != new.get(key)]
    return rows or [_note(name, 0, f"The {what} is unchanged.")]


_OTHER_FILE = "a different file"
_EXISTS, _DELETED = "exists", "deleted"  # a delete card's one row, and what its receipt reads


def _yaml_says(what: str):
    def says(args: dict, rows: list[dict]) -> str:
        keys = [r["field"] for r in rows if r["role"] == "typed"]
        name = rows[0]["name"] if rows else what
        return f"Saves {name}: " + (f"changes {', '.join(keys)}" if keys else "nothing changes")
    return says


def _yaml_back(text: str | None, rows: list[dict]) -> list:
    """Each row's stored value, read only from the very text the card bound."""
    stored, sha = _load(text), sha256_text(text)
    return [None if row["role"] == "note" else _OTHER_FILE if row.get("stored_sha256") != sha
            else _short(stored.get(row["field"])) for row in rows]


# ── the campus file ─────────────────────────────────────────────────────────


def campus_text(project) -> str | None:
    from services import campus_electrical_service as ce

    f = ce.campus_dir(project) / ce.CAMPUS_FILE
    return f.read_text() if f.is_file() else None


def _save_campus_preview(args: dict) -> list[dict]:
    def run(project):
        from services import campus_electrical_service as ce

        current = campus_text(project)
        require_base(current, args.get("base_sha256"), "campus file")
        text = str(args.get("yaml", ""))
        spec = ce.check_campus_text(text)  # save_campus's own refusal, before any card
        return _yaml_rows("the campus file", current, spec if isinstance(spec, dict) else {}, text, "campus file")

    return ec._campus(args, run)


def _save_campus_back(args: dict, rows: list[dict]) -> list:
    return ec._campus(args, lambda project: _yaml_back(campus_text(project), rows))


def _save_campus_binding(args: dict) -> dict:
    return {"sha256": ec._campus(args, lambda project: sha256_text(campus_text(project)))}


# ── the joint optimisation ──────────────────────────────────────────────────


# The run panel's words for each setting (CampusElectricalPanel.tsx).
MILP_LABELS = {
    "k": "Hours per criterion", "pf": "Connection-agreement power factor", "profile": "Grid code",
    "margin": "Design margin", "n_minus_1": "Transformer N-1",
    "invest": "Buy assets from the library (least cost, AC-checked)",
    "pcc_switchgear_by_operator": "PCC switchgear owned by the grid operator (not costed)",
    "pcc_tap_control": "PCC transformer taps held by",
}


def _milp_words(key: str, value) -> str:
    """A setting's value as the run panel shows it."""
    if key == "margin" and isinstance(value, (int, float)):
        return f"{round(value * 100, 6):g}%"
    if key == "pf" and value is None:
        return "the grid code's widest range"
    if key == "pcc_tap_control":
        return {"campus": "the campus", "grid_operator": "the grid operator"}.get(value, str(value))
    return ("yes" if value else "no") if isinstance(value, bool) else str(value)


def _milp_settings_text(settings: dict) -> str:
    return "; ".join(f"{MILP_LABELS.get(k, k)}: {_milp_words(k, v)}" for k, v in settings.items())


_NOT_STARTED = "not started"


def _milp_preview(args: dict) -> list[dict]:
    """One change, the job: its settings ride on the row for the receipt and show as a note."""
    from routers.campus_electrical import RunSettings

    given = {k: v for k, v in (args or {}).items() if k != "project_id" and v is not None}
    try:
        settings = RunSettings(**given).model_dump()
    except ValueError as exc:
        ec._refuse("invalid_edit_value", str(exc))

    def prior(project):
        from services import campus_electrical_service as ce

        return ce.milp_job_status(project)

    before = ec._campus(args, prior) or {}
    name = "Joint optimisation"
    job = _row(name, "job", "job", before.get("state") or _NOT_STARTED, "running")
    # Which record is the earlier job's: the receipt needs a NEW record, not the old one still running.
    return [{**job, "says_name": "the joint optimisation job", "prior_started_at": before.get("started_at"),
             "settings": settings},
            _note(name, 0, f"Settings: {_milp_settings_text(settings)}."),
            _note(name, 1, "Runs for minutes in the background from the last campus run; cancelling keeps the "
                           "best choice found so far.")]


def _milp_back(args: dict, rows: list[dict]) -> list:
    """The job record the start made (its state), read only when the run
    directory's basis file records the settings the card showed."""
    def run(project):
        from services import campus_electrical_service as ce

        record = ce.milp_job_status(project) or {}
        basis = ce.campus_dir(project) / ce.MILP_SUBDIR / ce.MILP_BASIS_FILE
        try:
            recorded = json.loads(basis.read_text()).get("settings") or {}
        except (OSError, ValueError):
            recorded = {}
        out = []
        for row in rows:
            if row["role"] == "note":
                out.append(None)
                continue
            new = record.get("started_at") is not None and record.get("started_at") != row["prior_started_at"]
            same = all(ec._same(recorded.get(k, _NOT_STARTED), v) for k, v in row["settings"].items())
            out.append(record.get("state") if new and same else None)
        return out

    return ec._campus(args, run)


def _milp_says(args: dict, rows: list[dict]) -> str:
    return f"Starts the campus joint optimisation in the background ({_milp_settings_text(rows[0]['settings'])})"


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
        require_base(current and current["yaml"], args.get("base_sha256"), f"draft “{pid}”")
        if current is None:
            ec._refuse("component_not_found", f"There is no draft grid-code profile “{pid}”.", 404)
        from services import campus_grid_code_service as gc

        stored = gc._dump(gc.check_draft_text(pid, str(args.get("yaml", ""))))  # save_draft's own refusal
        return _yaml_rows(f"the draft “{_title(current, pid)}”", current["yaml"], _load(stored), stored, "draft")

    return _gc(args, run)


def _draft_text(project, pid: str) -> str | None:
    d = _draft_or_none(project, pid)
    return d and d["yaml"]


def _save_draft_back(args: dict, rows: list[dict]) -> list:
    return _gc(args, lambda project: _yaml_back(_draft_text(project, str(args.get("profile_id", ""))), rows))


def _draft_binding(args: dict) -> dict:
    def run(project):
        d = _draft_or_none(project, str(args.get("profile_id", "")))
        return {"draft_sha256": sha256_text(d and d["yaml"])}

    return _gc(args, run)


def _new_pid(args: dict) -> str:
    """The draft id the call writes: an extract without one derives it from the document."""
    from services import campus_grid_code_service as gc

    pid = args.get("profile_id")
    if pid is None and args.get("document_id"):
        return gc.default_profile_id(gc._doc_id(str(args["document_id"])))
    return gc._profile_id(str(pid))


def _stamp(project, pid: str) -> str | None:
    """The draft file's identity: an atomic write (`gc._write_atomic`) gives it a new
    inode and mtime even when the bytes are the same, so a rewrite is observable."""
    from services import campus_grid_code_service as gc

    f = gc._drafts(project) / f"{pid}.yaml"
    try:
        st = f.stat()
    except OSError:
        return None
    return f"{st.st_ino}:{st.st_mtime_ns}"


_UNWRITTEN = "not written"


def _new_draft_preview(args: dict) -> list[dict]:
    """What the draft becomes, and what an overwrite replaces (extract and the blank draft)."""
    def run(project):
        from services import campus_grid_code_service as gc

        pid, extract = _new_pid(args), "document_id" in args
        existing = _draft_or_none(project, pid)
        if extract:
            doc = gc._doc_meta(project, gc._doc_id(str(args["document_id"])))
            name = f"Draft from {doc.get('filename') or 'the document'}"
            old = ((existing or {}).get("profile") or {}).get("document") or {}
            rows = [{**_row(name, "document", "document", old.get("sha256"), gc._doc_id(str(args["document_id"]))),
                     "says_name": f"{name}: source document"}]
            rows.append(_note(name, 9, gc.extraction_disclosure()))
        else:
            old_title = ((existing or {}).get("profile") or {}).get("title")
            title = (args.get("title") or "").strip()[:200] or "Project grid code (to be filled in by hand)"
            name = f"Draft “{title}”"
            rows = [{**_row(name, "title", "title", old_title, title), "says_name": "the draft's title"}]
        rows[0]["prior_stamp"] = _stamp(project, pid)  # the receipt needs the draft rewritten
        if existing is None:
            return rows
        if not args.get("overwrite"):
            return rows + [_note(name, 0, "A draft of this id exists; without overwrite nothing is replaced and the call is refused.")]
        return rows + [_profile_note(name, 1, existing),
                       _note(name, 2, "overwrite replaces this draft and its review.")]

    return _gc(args, run)


def _new_draft_back(args: dict, rows: list[dict]) -> list:
    def run(project):
        pid = _new_pid(args)
        written = _stamp(project, pid) not in (None, rows[0]["prior_stamp"])
        profile = ((_draft_or_none(project, pid) or {}).get("profile")) or {}
        return [None if row["role"] == "note" else _UNWRITTEN if not written
                else (profile.get("document") or {}).get("sha256") if row["field"] == "document"
                else profile.get("title") for row in rows]

    return _gc(args, run)


def _new_draft_says(args: dict, rows: list[dict]) -> str:
    first = rows[0]
    if first["field"] == "document":
        return f"Drafts a grid-code profile from {first['name'].removeprefix('Draft from ')} with the extraction provider"
    replaced = f"Replaces the draft “{first['old']}” with" if first["old"] is not None and args.get("overwrite") else "Creates"
    return f"{replaced} a blank draft “{first['new']}”, every limit assumed until filled in by hand"


def _new_draft_binding(args: dict) -> dict:
    return _gc(args, lambda project: {"draft_sha256": sha256_text(_draft_text(project, _new_pid(args)))})


def _confirm_preview(args: dict) -> list[dict]:
    def run(project):
        from services import campus_grid_code_service as gc

        pid, path = str(args.get("profile_id", "")), str(args.get("limit", ""))
        draft = gc.get_draft(project, pid)
        limit = dict(gc._limits(draft["profile"])).get(path)
        label, title = limit_label(path), _title(draft, pid)
        if not isinstance(limit, dict):
            ec._refuse("component_not_found", f"Draft “{title}” has no limit “{label}”.", 404)
        if limit.get("source") != "extracted":
            ec._refuse("edit_refused", f"{label} is tagged {limit.get('source') or 'with no source'}, not "
                                       "extracted; only an extracted limit is confirmed.")
        name = f"Draft “{title}”: {label}"
        rows = [_row(name, "source", "source", "extracted", "code")]
        values = ", ".join(f"{key} {value}" for key, value in limit.items()
                           if key not in ("source", "quote", "page", "clause"))
        rows.append(_note(name, 3, f"The limit stays as extracted: {values}."))
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


def _confirm_back(args: dict, rows: list[dict]) -> list:
    """The limit as the draft now stores it: its source reads `code`."""
    def run(project):
        from services import campus_grid_code_service as gc

        d = _draft_or_none(project, str(args.get("profile_id", "")))
        limit = dict(gc._limits(d["profile"])).get(str(args.get("limit", ""))) if d else None
        limit = limit if isinstance(limit, dict) else {}
        return [None if row["role"] == "note" else limit.get(row["field"]) for row in rows]

    return _gc(args, run)


def _confirm_says(args: dict, rows: list[dict]) -> str:
    title = _gc(args, lambda project: _title(_draft_or_none(project, str(args.get("profile_id", ""))),
                                             str(args.get("profile_id", ""))))
    return (f"Marks {limit_label(str(args.get('limit', '')))} in draft “{title}” as confirmed against its quoted "
            "page (source extracted → code)")


def _publish_preview(args: dict) -> list[dict]:
    def run(project):
        from services import campus_grid_code_service as gc

        pid = str(args.get("profile_id", ""))
        draft = gc.get_draft(project, pid)
        old = _published_or_none(project, pid)
        name = f"Grid code “{_title(draft, pid)}”"
        # The published file is the draft's validated text: the receipt compares its sha (never shown).
        rows = [{**_row(name, "published", "published", None if old is None else _title(old, pid), _title(draft, pid)),
                 "draft_sha256": sha256_text(draft["yaml"])}]
        rows.append(_note(name, 0, "Replaces the published profile of this id." if old is not None
                                   else "A new published profile; studies can then use it."))
        open_limits = draft.get("unconfirmed") or []
        for i, path in enumerate(open_limits, start=1):
            rows.append(_note(name, i, f"{limit_label(path)} is unconfirmed: "
                                       + ("it stays extracted; every report row on it says so."
                                          if args.get("allow_unconfirmed")
                                          else "publishing is refused until it is confirmed.")))
        return rows

    return _gc(args, run)


def _publish_back(args: dict, rows: list[dict]) -> list:
    def run(project):
        p = _published_or_none(project, str(args.get("profile_id", "")))
        return [None if row["role"] == "note" or p is None
                else _OTHER_FILE if sha256_text(p["yaml"]) != row["draft_sha256"]
                else _title(p, p["id"]) for row in rows]

    return _gc(args, run)


def _publish_says(args: dict, rows: list[dict]) -> str:
    old, new = rows[0]["old"], rows[0]["new"]
    return (f"Publishes the draft “{new}” as a grid code"
            + (f", replacing the published “{old}”" if old is not None else "; studies can then use it"))


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
            name = f"{kind.capitalize()} “{_title(shaped, pid)}”"
            return [_row(name, kind, kind, _EXISTS, _DELETED),
                    _profile_note(name, 0, shaped),
                    _note(name, 1, f"The {kind} profile is deleted; this cannot be undone.")]

        return _gc(args, run)

    def binding(args: dict) -> dict:
        def run(project):
            pid = str(args.get("profile_id", ""))
            shaped = (_draft_or_none if kind == "draft" else _published_or_none)(project, pid)
            return {"sha256": sha256_text(shaped and shaped["yaml"])}

        return _gc(args, run)

    def back(args: dict, rows: list[dict]) -> list:
        """The profile's title while it is still there; None (the card's new) once it is gone."""
        def run(project):
            shaped = (_draft_or_none if kind == "draft" else _published_or_none)(project, str(args.get("profile_id", "")))
            return [None if row["role"] == "note" else _DELETED if shaped is None else _EXISTS for row in rows]

        return _gc(args, run)

    def says(args: dict, rows: list[dict]) -> str:
        return f"Deletes the {kind} grid-code profile {rows[0]['name'].split(' ', 1)[1]}; this cannot be undone"

    return preview, binding, back, says


def _delete_document_preview(args: dict) -> list[dict]:
    def run(project):
        from services import campus_grid_code_service as gc

        did = str(args.get("document_id", ""))
        listing = gc.list_grid_codes(project)
        doc = next((d for d in listing.get("documents") or [] if d.get("id") == did), None)
        if doc is None:
            ec._refuse("component_not_found", f"There is no grid-code document “{did}”.", 404)
        name = f"Document {doc.get('filename') or did}"
        citing = [f"“{d.get('title') or d['id']}”" for d in listing.get("drafts") or [] if d.get("document") == did]
        rows = [_row(name, "document", "document", _EXISTS, _DELETED),
                _note(name, 1, f"{doc.get('filename')}: {doc.get('size')} bytes, {doc.get('pages')} "
                                  f"page{'' if doc.get('pages') == 1 else 's'}.")]
        rows.append(_note(name, 0, ("Drafts that cite it: " + ", ".join(citing) + "; their quotes can no longer "
                                    "be checked.") if citing else "No draft cites it."))
        return rows

    return _gc(args, run)


def _delete_document_binding(args: dict) -> dict:
    """What the card shows beside the row: the drafts that cite the document."""
    def run(project):
        from services import campus_grid_code_service as gc

        did = str(args.get("document_id", ""))
        return {"citing": sorted(d["id"] for d in gc.list_grid_codes(project).get("drafts") or []
                                 if d.get("document") == did)}

    return _gc(args, run)


def _delete_document_back(args: dict, rows: list[dict]) -> list:
    def run(project):
        from services import campus_grid_code_service as gc

        did = str(args.get("document_id", ""))
        doc = next((d for d in gc.list_grid_codes(project).get("documents") or [] if d.get("id") == did), None)
        return [None if row["role"] == "note" else _DELETED if doc is None else _EXISTS for row in rows]

    return _gc(args, run)


def _delete_document_says(args: dict, rows: list[dict]) -> str:
    return f"Deletes the grid-code document {rows[0]['name'].removeprefix('Document ')}; this cannot be undone"


def _register() -> None:
    reg, A = ec.register, ec.Adapter
    reg("campus_save_campus", A("settings_edit", _save_campus_preview, _save_campus_back,
                                binding=_save_campus_binding, says=_yaml_says("the campus file")))
    reg("campus_start_milp", A("settings_edit", _milp_preview, _milp_back, says=_milp_says))
    reg("campus_save_grid_code_draft", A("settings_edit", _save_draft_preview, _save_draft_back,
                                         binding=_draft_binding, says=_yaml_says("the draft")))
    reg("campus_new_grid_code_draft", A("settings_edit", _new_draft_preview, _new_draft_back,
                                        binding=_new_draft_binding, says=_new_draft_says))
    reg("campus_extract_grid_code", A("settings_edit", _new_draft_preview, _new_draft_back,
                                      binding=_new_draft_binding, says=_new_draft_says))
    reg("campus_confirm_grid_code_limit", A("settings_edit", _confirm_preview, _confirm_back,
                                            binding=_draft_binding, says=_confirm_says))
    reg("campus_publish_grid_code", A("settings_edit", _publish_preview, _publish_back,
                                      binding=_publish_binding, says=_publish_says))
    for tool, kind in (("campus_delete_grid_code_draft", "draft"),
                       ("campus_delete_published_grid_code", "published")):
        preview, binding, back, says = _delete_profile(kind)
        reg(tool, A("settings_edit", preview, back, binding=binding, says=says))
    reg("campus_delete_grid_code_document", A("settings_edit", _delete_document_preview, _delete_document_back,
                                              binding=_delete_document_binding, says=_delete_document_says))


_register()

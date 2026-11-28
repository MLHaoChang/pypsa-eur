"""Change a data-centre project's load inputs after creation (te 04, TEMPLATE-GAPS-1 Q3 (a)).

PUE at design, critical share, backup mode and power architecture are
creation-body fields (``dc_load``). This is the one writer that changes them
later: the merged spec goes through the create body's own checks
(``tpl.dc_load_spec``), the facility profile is rewritten on the network by
the same function creation uses (``apply_dc_profile_to_network``), and the
project's marker (spec, envelope, ledger) follows. Either both change or
neither does.

Backup mode and architecture move nothing in the network (the saved network
keeps the backup strings it was built with; ``dc_backup.assess`` reads the
stored mode), so they change the marker only.
"""
from __future__ import annotations

#: The patch keys; anything else (the IT MW, the phases) stays a creation input.
PATCH_KEYS = ("pue_at_design", "critical_share", "backup", "architecture")
_SERIES_KEYS = ("pue_at_design", "critical_share")
_LOADS = ["it_load", "cooling"]


class DcLoadUpdateError(ValueError):
    """A refused update: ``kind`` is the error kind, ``field`` the input at fault."""

    def __init__(self, message: str, field: str | None = None, kind: str = "dc_load_invalid"):
        super().__init__(message)
        self.field = field
        self.kind = kind


def update_dc_load(project_dir, tpl, patch: dict, network=None, *, overwrite_series: bool = False) -> dict:
    """Apply ``patch`` to the stored spec and, when PUE or critical share
    moved, to ``network``'s it_load and cooling series. Returns
    ``{"spec": <stored spec>, "series_changed": bool}``.

    ``network`` is the live network to edit (None: the project is not open,
    which refuses a PUE or critical-share change). A series that no longer
    matches what the stored spec produces was edited by the user: it is
    refused (``dc_series_edited``) unless ``overwrite_series``."""
    from services.adequacy.dc_backup import DcBackupError, _meta, _write_meta, project_spec
    from services.dc_load_profile import DcLoadError
    from services.template_marker import write_lock

    unknown = sorted(set(patch) - set(PATCH_KEYS))
    if unknown or not patch:
        raise DcLoadUpdateError(
            f"dc_load: only {', '.join(PATCH_KEYS)} can change after creation"
            + (f" (not {', '.join(unknown)})" if unknown else ""), unknown[0] if unknown else None)
    try:
        tid, spec, planning = project_spec(project_dir, tpl)
    except DcBackupError as exc:
        raise DcLoadUpdateError(str(exc), exc.field) from None
    raw = spec.model_dump(mode="json")
    for key, value in patch.items():
        if key == "backup":
            if not isinstance(value, dict):
                raise DcLoadUpdateError("dc_load.backup: must be an object", "dc_load.backup")
            raw["backup"] = {**raw.get("backup", {}), **value}
        else:
            raw[key] = value
    try:
        checked = tpl.dc_load_spec(tid, raw, planning)
    except DcLoadError as exc:
        raise DcLoadUpdateError(str(exc), exc.field) from None

    series_changed = any(
        getattr(checked, k) != getattr(spec, k) for k in _SERIES_KEYS if k in patch)
    saved = None
    if series_changed:
        if network is None or "it_load" not in network.loads_t.p_set.columns \
                or "cooling" not in network.loads_t.p_set.columns:
            raise DcLoadUpdateError(
                "dc_load: open the project to change PUE or critical share", None, "project_not_open")
        saved = network.loads_t.p_set[_LOADS].copy()
        if not overwrite_series and not _matches_spec(network, tpl, tid, spec, planning, saved):
            raise DcLoadUpdateError(
                "dc_load: the IT load or cooling series was edited since creation; "
                "changing PUE or critical share would replace it", None, "dc_series_edited")
        try:
            tpl.apply_dc_profile_to_network(network, tid, checked, planning)
        except Exception:
            network.loads_t.p_set[_LOADS] = saved
            raise
    try:
        with write_lock(project_dir):
            meta = _meta(project_dir)
            old = meta["dc_load"]
            block = tpl.dc_load_meta(tid, checked)
            # The saved network keeps the backup strings it was created with.
            if "network_backup_mode" in old:
                block["network_backup_mode"] = old["network_backup_mode"]
            else:
                block.pop("network_backup_mode", None)
            meta["dc_load"] = {**old, **block}
            _write_meta(project_dir, meta)
    except Exception:
        if saved is not None:
            network.loads_t.p_set[_LOADS] = saved
        raise
    return {"spec": meta["dc_load"]["spec"], "series_changed": series_changed}


def _matches_spec(network, tpl, tid, spec, planning, current) -> bool:
    """Whether the two series are what ``spec`` produces (nobody edited them)."""
    try:
        tpl.apply_dc_profile_to_network(network, tid, spec, planning)
        return bool(network.loads_t.p_set[_LOADS].equals(current))
    finally:
        network.loads_t.p_set[_LOADS] = current

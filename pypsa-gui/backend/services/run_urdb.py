"""Study harness 35 (GK B (71) plan GO, Q1-Q3 (a), C1-C10): eh_site@6's URDB tariff.

The intake cites an uploaded URDB JSON (`run_intake`: a `write` citation of
`import_urdb_tariff`, never a value). The grant pins the upload's sha256, every
`import_urdb_tariff` argument and the Library hash the converted tariff will
have (`plan`: the import route's own conversion and `items.prepare_item`, pure,
nothing written), in the envelope's `urdb_upload` gap (`run_envelope`). The
inputs stage runs the import and the attach as the harness's own steps with the
granted args (`run_envelope.urdb_steps`); each is admitted only when the upload
and the Library still give the granted hash, before anything is written (C3).

Stamp rules (GLE-05, Q3 (a)): a tariff whose currency differs from the
project's stated one (`binding.tariff_currency_mismatch`, unchanged) or whose
validity misses a modelled year (`stamp`) is refused at the grant and again at
use; preflight's `commercial.tariff_out_of_validity` stays a warning.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Mapping

from fastapi import HTTPException

TOOL = "import_urdb_tariff"
KIND = "urdb_upload"  # the envelope gap source kind
PARAM = "tariff.urdb"  # the intake row's param
CURRENCY = "currency_mismatch"  # `binding.CURRENCY_MISMATCH`
YEAR = "tariff_year_mismatch"
CHANGED = "urdb_upload_changed"
NAME_TAKEN = "urdb_name_taken"
# The args the grant pins (C2): the tool's own, the model supplies none.
ARGS = (
    "file_id",
    "name",
    "cyclic_year",
    "accept_partial",
    "valid_from",
    "item_index",
    "tariff_id",
    "jurisdiction",
)


class PlanError(ValueError):
    """The upload gives no tariff the run may import (`kind`, message)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def name_for(sha256: str) -> str:
    """The Library name the import stores under: one per upload's bytes, so a
    second run of the same file reuses the item (`put_item` adds no version
    when the hash matches, C4)."""
    return f"urdb-{sha256[:12]}"


def pinned_args(
    file_id: str, sha256: str, item_index=None, accept_partial=False
) -> dict:
    """Every `import_urdb_tariff` argument, fixed (C2): accept_partial only from
    the intake row, default false; the rest the tool's defaults."""
    return {
        "file_id": file_id,
        "name": name_for(sha256),
        "cyclic_year": False,
        "accept_partial": bool(accept_partial),
        "valid_from": None,
        "item_index": item_index,
        "tariff_id": None,
        "jurisdiction": None,
    }


def plan(blob: bytes, args: dict) -> dict:
    """What the import route would store for `args` on these upload bytes:
    `{tariff, hash, upload_sha256}`. Pure (the route's conversion and
    `prepare_item`'s checks and canonical hash); raises PlanError."""
    from routers.library import _item_name
    from services import chat_tools
    from services.library import items as I
    from services.library import urdb as U

    try:
        data = json.loads(blob.decode("utf-8-sig"))
        rate = chat_tools._urdb_rate(data, args.get("item_index"))
        name = _item_name(args["name"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanError(
            "urdb_upload_unreadable",
            f"the upload is not a JSON file: {type(exc).__name__}",
        ) from exc
    except HTTPException as exc:
        detail = (
            exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        )
        raise PlanError(
            detail.get("error_kind") or "urdb_upload_unreadable",
            str(detail.get("message"))[:300],
        ) from exc
    if not isinstance(rate, dict):
        raise PlanError(
            "urdb_upload_unreadable", "the upload holds no URDB rate object"
        )
    try:
        tariff, refusals, notes = U.urdb_to_tariff(
            rate,
            name=name,
            cyclic_year=args["cyclic_year"],
            accept_partial=args["accept_partial"],
            tariff_id=args["tariff_id"],
            jurisdiction=args["jurisdiction"],
            valid_from=args["valid_from"],
        )
        meta = {
            "source": "urdb",
            "provider": "OpenEI URDB",
            "notes": notes,
            "description": str(rate.get("label") or "")[:500] or None,
        }
        digest = I.prepare_item(
            "tariff", name, tariff.model_dump(mode="json"), meta
        ).digest
    except U.UrdbRefused as exc:
        raise PlanError("urdb_refused", str(exc)[:300]) from exc
    except I.TariffCurrencyRequired as exc:
        raise PlanError("tariff_currency_required", str(exc)[:300]) from exc
    except ValueError as exc:
        raise PlanError("urdb_invalid", str(exc)[:300]) from exc
    return {
        "tariff": tariff,
        "hash": digest,
        "upload_sha256": hashlib.sha256(blob).hexdigest(),
    }


def _finance(ctx):
    cfg = (getattr(ctx, "solver_state", None) or {}).get("solver_config")
    finance = (
        cfg.get("finance")
        if isinstance(cfg, Mapping)
        else getattr(cfg, "finance", None)
    )
    return finance.model_dump() if hasattr(finance, "model_dump") else finance


def tariff_held(ctx) -> bool:
    """Whether the project already prices imports (a Library ref or an inline
    tariff): the gap is filled, so the import would only leave an unused
    Library item (the attach is refused `not_a_gap` anyway)."""
    cfg = (getattr(ctx, "solver_state", None) or {}).get("solver_config")
    commercial = cfg.get("commercial") if isinstance(cfg, Mapping) else getattr(cfg, "commercial", None)
    if hasattr(commercial, "model_dump"):
        commercial = commercial.model_dump()
    if not isinstance(commercial, Mapping):
        return False
    return commercial.get("import_tariff_ref") not in (None, "", {}) or commercial.get("import_tariff") is not None


def modelled_years(network) -> tuple[list[int], tuple | None]:
    """The investment periods, or the first and last modelled dates."""
    import pandas as pd

    snapshots = network.snapshots
    if isinstance(snapshots, pd.MultiIndex):
        return [int(p) for p in snapshots.get_level_values(0).unique()], None
    if not len(snapshots):
        return [], None
    return [], (
        pd.Timestamp(snapshots.min()).date(),
        pd.Timestamp(snapshots.max()).date(),
    )


def stamp(ctx, tariff) -> tuple[str, str] | None:
    """`(kind, message)` when the stamp rules refuse `tariff` on this project
    (Q3 (a)), else None. Currency: the unchanged #499 rule. Year: every
    investment period, or the modelled dates, inside valid_from..valid_to
    (an open valid_to bounds nothing). Nothing is converted."""
    from services.commercial import binding

    clash = binding.tariff_currency_mismatch(
        tariff, binding.project_currency(_finance(ctx))
    )
    if clash:
        return CURRENCY, clash
    start, end = tariff.valid_from, tariff.valid_to
    periods, span = modelled_years(ctx.network)
    off = [p for p in periods if p < start.year or (end is not None and p > end.year)]
    if span is not None and (span[0] < start or (end is not None and span[1] > end)):
        where = f"The modelled dates {span[0]}..{span[1]}"
    else:
        where = f"Investment period(s) {', '.join(map(str, off))}" if off else None
    if where:
        return YEAR, (
            f"{where} fall outside tariff \u201c{tariff.id}\u201d's validity {start}..{end or 'open'}; "
            "a URDB tariff is never stretched to another year."
        )
    return None


def upload_bytes(ctx, file_id: str) -> bytes:
    """The upload's bytes in the run's own project directory (the context's
    ACL-checked `storage_dir`, as the upload routes resolve it): an id that is
    not this project's is `upload_not_found`, C6."""
    from services import upload_service

    storage = getattr(ctx, "storage_dir", None)  # a str on a bound context (pypsa_service)
    return upload_service.get_upload_bytes(
        ctx.loaded_project, file_id, project_dir=pathlib.Path(storage) if storage else None
    )


def library_hash(name: str) -> str | None:
    """The hash of the Library's latest tariff `name` in the acting user's
    organisation, or None when there is none."""
    from routers.library import ItemKind, get_item
    from services import chat_tools

    try:
        return chat_tools._library_call(
            get_item, ItemKind.tariff, name, version=None
        ).ref.hash
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def may_write_library() -> bool:
    """The import route's own ACL (`routers.library._target_org(write=True)`), as the acting user (C5)."""
    from routers.library import _target_org
    from services import chat_tools

    with chat_tools._acting() as (db, user):
        try:
            _target_org(db, user, None, write=True)
        except HTTPException:
            return False
    return True

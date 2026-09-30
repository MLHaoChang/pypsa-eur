"""
Binding a submitted commercial config to the live network (Edge Investment
Case WP1.3 / WP1.4; moved out of `routers/simulation._bind_commercial` in P2
WP2.4b-0, P1 condition 4, so the Library work reuses it).

`bind_commercial` checks the config against the network, resolves its Library
series (export price, connection envelope) through an INJECTED resolver (the
route owns the database and the org rules), and keeps the project's FCA stress
entry in step. Every refusal happens BEFORE any write: the series are resolved
and aligned and the stress registry is planned and validated first; only then
are the network columns written (under `lock`) and the registry saved.
Refusals are `BindingRefusal(status, code, message)`; the route maps them to
HTTP. `commercial=None` (clearing) only removes the FCA entry this layer owns.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import pathlib
from contextlib import nullcontext
from typing import Callable

import pandas as pd

from services.adequacy.stress import StressValidationError
from services.commercial import connection as conn
from services.commercial import lp_bindings


class BindingRefusal(Exception):
    """A refusal with the route's status and `{code, message}` detail."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


CAPACITY_FEE_SHAPE_CODE = "connection_capacity_fee_unsupported"
TARIFF_REF_CONFLICT_ON_LOAD = "import_tariff_ref_conflict_on_load"


def tariff_ref_load_issue(commercial) -> dict | None:
    """The project-LOAD re-check of a Library tariff ref (IC P4 WP4.6b, P3
    hygiene): the frontend's `replacesInline` trusts `PUT /solver_config` to
    have resolved the ref into an inline copy with the ref's hash, but a
    project or bundle on disk is a second writer. `None` when there is no ref,
    no inline copy, or the copy hashes to the ref; otherwise an issue in the
    `bundle_pins` shape (`code` = `import_tariff_ref_conflict_on_load`).
    Flagged, never repaired: the solve's own check still refuses the copy."""
    from models.commercial import CommercialConfig
    from services.commercial import hashing as _H

    if not isinstance(commercial, dict):
        return None
    ref, inline = commercial.get("import_tariff_ref"), commercial.get("import_tariff")
    if not isinstance(ref, dict) or inline is None:
        return None
    base = {"code": TARIFF_REF_CONFLICT_ON_LOAD, "kind": ref.get("kind"), "id": ref.get("id"),
            "version": ref.get("version"), "hash": ref.get("hash")}
    try:
        cfg = CommercialConfig.model_validate(commercial)
    except ValueError as exc:
        return {**base, "reason": "inline_unreadable",
                "message": f"the stored commercial config does not validate, so its inline "
                           f"tariff cannot be checked against Library tariff {ref.get('id')!r} "
                           f"({str(exc)[:200]})"}
    if _H.library_item_digest(cfg.import_tariff) == cfg.import_tariff_ref.hash:
        return None
    return {**base, "reason": "inline_differs_from_ref",
            "message": f"the saved import_tariff is not Library tariff {ref.get('id')!r} "
                       f"v{ref.get('version')} (its content does not hash to the ref); re-apply "
                       "the Library tariff, or drop import_tariff_ref to keep the edited copy"}


def resolve_tariff_ref(commercial, resolve_item: Callable[[object], dict] | None):
    """`commercial` with its `import_tariff_ref` resolved into the inline
    `import_tariff` (P2 WP2.4a); unchanged without a ref."""
    from models.commercial import CommercialConfig

    ref = commercial.import_tariff_ref
    if ref is None:
        return commercial
    if ref.kind != "tariff":
        raise BindingRefusal(422, lp_bindings.CommercialBindingError.code,
                             f"import_tariff_ref names a Library {ref.kind}, not a tariff")
    if commercial.import_tariff is not None:
        from services.commercial import hashing as _H

        if _H.library_item_digest(commercial.import_tariff) != ref.hash:
            # An edited inline copy would be silently replaced by the Library
            # tariff (WP2.4a review #1): say so, never drop the edit.
            raise BindingRefusal(
                409, "import_tariff_ref_conflict",
                f"the submitted import_tariff differs from Library tariff {ref.id!r} "
                f"v{ref.version}; drop import_tariff_ref to keep the edited tariff, or drop "
                "import_tariff to use the Library version")
    if resolve_item is None:
        raise BindingRefusal(409, "library_org_unknown",
                             "a Library tariff ref needs the project's organization")
    payload = resolve_item(ref)
    try:
        return CommercialConfig.model_validate(
            {**commercial.model_dump(mode="json"), "import_tariff": payload})
    except ValueError as exc:
        raise BindingRefusal(422, lp_bindings.CommercialBindingError.code,
                             f"Library tariff {ref.id!r} v{ref.version} does not bind: {exc}") \
            from exc


def bind_commercial(n, commercial, *, project_dir: pathlib.Path | None,
                    resolve_ref: Callable[[object], pd.Series], lock=None,
                    resolve_item: Callable[[object], dict] | None = None) -> dict | None:
    """Returns the plain dict stored on `SolverConfig.commercial` (None when
    clearing). `commercial` is a parsed `CommercialConfig` or None. A Library
    tariff ref is resolved first (`resolve_item`), so everything after checks
    the tariff the solve will use."""

    def plan_registry(entry):
        try:
            return conn.plan_fca_registry(project_dir, entry)
        except conn.StressRegistryUnreadable as exc:
            raise BindingRefusal(409, exc.code, str(exc)) from exc
        except StressValidationError as exc:
            raise BindingRefusal(422, "stress_registry_invalid", str(exc)) from exc

    if commercial is None:
        from services.commercial import settlement_inputs as SI

        with (lock if lock is not None else nullcontext()):
            # A cleared config names no reference series (review 0b #4).
            SI.write_reference_series(n, {}, frame=SI.REF_PRICE_ATTR, keep=set())
            SI.write_reference_series(n, {}, frame=SI.CFE_ATTR, keep=set())
        if project_dir is not None:
            try:
                conn.register_fca_entry(project_dir, None)
            except (conn.StressRegistryUnreadable, StressValidationError):
                pass  # clearing never fails on an unreadable registry; nothing written
        return None

    commercial = resolve_tariff_ref(commercial, resolve_item)
    try:
        lp_bindings.validate_for_network(n, commercial, refuse_settlement_contracts=True)
    except lp_bindings.CommercialBindingError as exc:
        raise BindingRefusal(422, exc.code, str(exc)) from exc
    fee = commercial.connection.capacity_fee if commercial.connection is not None else None
    if fee is not None:
        # The fee's shape (unit, one un-windowed period) is refused at SAVE, not
        # first at the solve's `connection._validate` (IC P4 WP4.6b, P3 hygiene).
        try:
            conn.fee_eur_per_mw_year(fee)
        except lp_bindings.CommercialBindingError as exc:
            raise BindingRefusal(422, CAPACITY_FEE_SHAPE_CODE, str(exc)) from exc

    def aligned(ref, code):
        try:
            values = lp_bindings.align_to_snapshots(resolve_ref(ref), n.snapshots,
                                                    commercial.timezone)
        except lp_bindings.CommercialBindingError as exc:
            raise BindingRefusal(422, exc.code, str(exc)) from exc
        if values.isna().any():
            raise BindingRefusal(422, code,
                                 f"Library series {ref.id!r} v{ref.version} does not cover "
                                 f"{int(values.isna().sum())} snapshot(s); nothing was written")
        return values

    # 1. Resolve + check everything (no writes yet).
    price = (aligned(commercial.export_price_ref, "export_price_coverage")
             if commercial.export_price_ref is not None else None)
    agreement = commercial.connection
    envelope = (aligned(agreement.envelope, "envelope_coverage")
                if agreement is not None and agreement.envelope is not None else None)
    fca = agreement is not None and agreement.kind == "fca" \
        and agreement.curtailment_hours_per_year is not None
    # Contract reference prices and the grid CFE share (P2 WP2.2-0): fully
    # covered only — an uncovered series is refused like the export price.
    from services.commercial import settlement_inputs as SI

    ref_prices = {SI.contract_column(c.id): (aligned(c.reference_price,
                                                     "reference_price_coverage"),
                                             c.reference_price)
                  for c in commercial.contracts
                  if getattr(c, "reference_price", None) is not None}
    cfe = ({SI.CFE_COLUMN: (aligned(commercial.grid_cfe_share_ref, "grid_cfe_share_coverage"),
                            commercial.grid_cfe_share_ref)}
           if commercial.grid_cfe_share_ref is not None else {})
    planned = None
    if fca:
        if project_dir is None:
            raise BindingRefusal(409, "fca_needs_saved_project",
                                 "save the project first: an FCA agreement registers its "
                                 "curtailment hours as a stress scenario in the project")
        try:
            entry = conn.fca_stress_entry(
                n, agreement, poc_link=commercial.poc_link, timezone=commercial.timezone,
                envelope_mw=None if envelope is None else envelope.to_numpy(dtype=float))
        except lp_bindings.CommercialBindingError as exc:
            raise BindingRefusal(422, exc.code, str(exc)) from exc
        planned = plan_registry(entry)
    elif project_dir is not None:
        try:
            planned = conn.plan_fca_registry(project_dir, None)  # drop a stale FCA entry
        except (conn.StressRegistryUnreadable, StressValidationError):
            planned = None  # nothing of ours to drop from an unreadable registry

    # 2. Write.
    with (lock if lock is not None else nullcontext()):
        if price is not None:
            lp_bindings.write_export_price(n, commercial.export_link, price, None)
        if envelope is not None:
            conn.write_envelope(n, commercial.poc_link, envelope, None)
        # The frames hold exactly the config's references: a contract that
        # left the config loses its column.
        SI.write_reference_series(n, ref_prices, frame=SI.REF_PRICE_ATTR,
                                  keep=set(ref_prices))
        SI.write_reference_series(n, cfe, frame=SI.CFE_ATTR, keep=set(cfe))
    if planned is not None:
        from services.adequacy import stress

        stress.save_scenarios(project_dir, planned)
    return commercial.model_dump(mode="json")

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


# ── Binding on a project context (IC U1 follow-up, GS Q15) ─────────────────

SOLVER_IN_FLIGHT_MESSAGE = ("a solve is running on this project; change the commercial "
                            "config after it finishes")


def _solve_running(ctx) -> bool:
    """A live solve worker on `ctx` (the router's `_solver_in_flight_ctx`,
    restated here so a service needs no router import)."""
    with ctx.solver_state_lock:
        t = ctx.solver_state.get("thread")
    return t is not None and t.is_alive()


def context_resolvers(ctx, user=None) -> tuple[Callable[[object], pd.Series],
                                               Callable[[object], dict]]:
    """(series resolver, item resolver) for the Library refs of `ctx`'s config.

    WHICH ORG. A ref resolves in the PROJECT's org, `ctx.org_id` (stamped on
    every loaded context by `project_registry.bind_context`); `user`'s org only
    when the context has none (an unsaved network). A fork (a study-owned
    project) inherits `org_id` from its base project
    (`project_registry.create_scenario`),
    so a study's fork resolves, and mints, its series in its base project's
    org. In local mode that is always `local_mode.LOCAL_ORG_ID`: the desktop
    build seeds one org, and every project row takes the local user's org.
    No org at all is `library_org_unknown` (409), raised only when a ref is
    actually resolved, so a config with no refs binds on an unsaved network."""
    from uuid import UUID

    from services.library import items as library_items
    from services.library import series_store

    def resolver(read):
        def resolve(ref):
            from db.models import User
            from db.session import SessionLocal
            from services import library_acl

            # The context carries the org id as a string (`org:uuid` registry key).
            org = UUID(str(ctx.org_id)) if ctx.org_id else None
            with SessionLocal() as db:
                if org is None and isinstance(user, User):
                    org = library_acl.org_of(db, user)
                if org is None:
                    raise BindingRefusal(
                        409, "library_org_unknown",
                        "save the project first: a Library ref resolves in the project's "
                        "organization")
                try:
                    return read(db, org, ref)
                except (series_store.LibraryRefNotFound, series_store.LibraryRefStale) as exc:
                    raise BindingRefusal(409, "library_ref_stale", str(exc)) from exc
        return resolve

    # Looked up at call time, so a patched store is the one used.
    return (resolver(lambda db, org, ref: series_store.resolve(db, org, ref)),
            resolver(lambda db, org, ref: library_items.resolve(db, org, ref)))


def bind_commercial_on_context(ctx, commercial, *, user=None,
                               in_flight: Callable[[object], bool] | None = None
                               ) -> dict | None:
    """`bind_commercial` on ANY project context: the active one (the
    solver-config route) or a context off the foreground (a study-owned fork,
    GS U2 `compile.bind_on_fork`). It supplies what the route used to:

      * the in-flight guard: a solve running on `ctx` refuses
        (`BindingRefusal(409, "solver_in_flight")`); `in_flight` replaces the
        check (the route passes its own `_solver_in_flight_ctx`);
      * `project_dir` from `ctx.storage_dir` (None for an unsaved context: an
        FCA agreement is then `fca_needs_saved_project`);
      * the Library resolvers in `ctx`'s org (`context_resolvers`);
      * `ctx.network`, written under `ctx.mutation_lock`.

    Nothing here touches the active context, so a fork's bind never writes a
    column into the foreground network. Returns the plain dict to store on
    `SolverConfig.commercial` (None when `commercial` is None, clearing);
    storing it is the caller's job."""
    check = in_flight if in_flight is not None else _solve_running
    if check(ctx):
        raise BindingRefusal(409, "solver_in_flight", SOLVER_IN_FLIGHT_MESSAGE)
    project_dir = pathlib.Path(ctx.storage_dir) if ctx.storage_dir else None
    resolve, resolve_item = context_resolvers(ctx, user)
    return bind_commercial(ctx.network, commercial, project_dir=project_dir,
                           resolve_ref=resolve, lock=ctx.mutation_lock,
                           resolve_item=resolve_item)


# ── The commercial root: the site connection (IC U1 follow-up, item b) ─────

SITE_CONNECTION_KEYS = ("poc_link", "export_link", "timezone")


def _grid_like(n, bus: str) -> bool:
    """A bus that looks like the grid side of a meter: named or carried
    `grid`, or the bus of a Generator tagged `eh_role=grid_supply`. A hint
    for the direction check and the candidates, never a refusal on its own."""
    if "grid" in str(bus).casefold():
        return True
    carrier = n.buses["carrier"].get(bus, "") if "carrier" in n.buses.columns else ""
    if "grid" in str(carrier).casefold():
        return True
    g = n.generators
    if "eh_role" in g.columns and len(g):
        supply = g[g["eh_role"].astype(str) == "grid_supply"]
        return bool((supply["bus"].astype(str) == str(bus)).any())
    return False


def _two_way(n, name: str) -> bool:
    if float(n.links.at[name, "p_min_pu"]) < 0:
        return True
    dyn = n.links_t.p_min_pu
    return name in dyn.columns and bool((dyn[name] < 0).any())


def _role(n, name: str) -> str:
    """The Link's `eh_role` tag, "" when untagged (absent, NaN or blank)."""
    if "eh_role" not in n.links.columns:
        return ""
    raw = n.links.at[name, "eh_role"]
    if raw is None or (isinstance(raw, float) and raw != raw):
        return ""
    tag = str(raw).strip()
    return "" if tag.casefold() in ("", "nan", "none") else tag


def site_connection_candidates(n) -> dict[str, list[str]]:
    """One-way Links that could be the meter, most likely first:
    `poc_link` from a grid-like bus (or tagged `eh_role=grid_import`) into a
    bus that is not, `export_link` the other way (or tagged `grid_export`).
    A Link tagged for the other side is never listed; two-way Links never
    are (the binding refuses them). Mirrored in the frontend's
    `pages/results/siteConnection.ts`."""
    one_way = [str(l) for l in n.links.index if not _two_way(n, l)]

    def score(link: str, into_site: bool) -> int:
        b0, b1 = str(n.links.at[link, "bus0"]), str(n.links.at[link, "bus1"])
        src, dst = (b0, b1) if into_site else (b1, b0)
        tag, other = (("grid_import", "grid_export") if into_site
                      else ("grid_export", "grid_import"))
        role = _role(n, link)
        if role == other:
            return 0    # a tag beats the name: never offered for the other side
        return (4 if role == tag else 0) + \
            (2 if _grid_like(n, src) and not _grid_like(n, dst) else 0)

    def ranked(into_site: bool) -> list[str]:
        scored = [(score(l, into_site), l) for l in one_way]
        return [l for s, l in sorted(scored, key=lambda t: (-t[0], t[1])) if s > 0]

    return {"poc_link": ranked(True), "export_link": ranked(False)}


def check_site_connection(n, poc_link: str, export_link: str | None, *,
                          direction_only: bool = False, check_poc: bool = True,
                          check_export: bool = True) -> None:
    """Refuse a meter this network cannot be: `BindingRefusal(422, code, …)`.

      * `site_connection_link_missing`: a name that is not a Link (the
        message lists the likely candidates);
      * `site_connection_invalid`: a two-way Link (the binding refuses reverse
        flow), or one Link named as both;
      * `site_connection_wrong_direction`: the PoC tagged `grid_export` or
        flowing from a site bus into a grid-like one; an export Link whose
        `bus0` is not on the site side of the PoC (it starts on the grid side)
        or whose `bus1` is (it never reaches the grid).

    The site side is what `lp_bindings._meter_sides` reaches from the PoC's
    `bus1` without crossing the meter. An explicit `eh_role` tag beats the
    name heuristic: a Link tagged `grid_import` is a PoC whatever its buses
    are called (a site bus named `microgrid_ac`); the name check runs only on
    an untagged Link, and a refusal caused by a tag says so.

    `direction_only` (the solver-config route): a missing or two-way Link is
    left to the binding, which refuses it with its own code
    (`commercial_binding_invalid`); only the direction and the one-Link-as-both
    checks run. `check_poc` / `check_export` False skip that side's own
    checks (the route re-checks only the Link that changed, so a config saved
    before the route check is not refused for a Link the user did not touch);
    the pair checks always run: one Link as both, and the export Link on the
    site side of the PoC (a new PoC moves the site side). Pure: no write."""
    from models.commercial import CommercialConfig

    def missing(name, what):
        cand = site_connection_candidates(n)[what]
        hint = f"; likely: {cand[:5]}" if cand else ""
        raise BindingRefusal(422, "site_connection_link_missing",
                             f"{what} {name!r} is not a Link in this network{hint}")

    if export_link is not None and export_link == poc_link:
        raise BindingRefusal(422, "site_connection_invalid",
                             f"{poc_link!r} cannot be both the import (poc_link) and the "
                             "export Link: model export with a second, one-way Link")
    if poc_link not in n.links.index:
        if direction_only:
            return
        missing(poc_link, "poc_link")
    if export_link is not None and export_link not in n.links.index:
        if direction_only:
            export_link = None
        else:
            missing(export_link, "export_link")
    sides = [(poc_link, "poc_link")] if check_poc else []
    if check_export:
        sides.append((export_link, "export_link"))
    for name, what in sides:
        if name is not None and _two_way(n, name):
            if direction_only:
                return
            raise BindingRefusal(422, "site_connection_invalid",
                                 f"{what} {name!r} allows reverse flow (p_min_pu < 0); the "
                                 "meter Links must be one-way (model export with a separate "
                                 "export_link)")
    b0, b1 = str(n.links.at[poc_link, "bus0"]), str(n.links.at[poc_link, "bus1"])
    role = _role(n, poc_link) if check_poc else ""
    if role == "grid_export":
        raise BindingRefusal(422, "site_connection_wrong_direction",
                             f"poc_link {poc_link!r} is tagged eh_role=grid_export: it is "
                             "tagged as the export Link, not the point of connection; name "
                             "the import Link (or retag it)")
    if check_poc and not role and _grid_like(n, b1) and not _grid_like(n, b0):
        raise BindingRefusal(422, "site_connection_wrong_direction",
                             f"poc_link {poc_link!r} runs {b0} → {b1}, into the grid: the "
                             "point of connection imports from the grid side (bus0) to the "
                             "site (bus1); name the Link that runs the other way, or tag it "
                             f"eh_role=grid_import if {b1!r} is the site")
    if export_link is None or not (check_poc or check_export):
        return
    if check_export and _role(n, export_link) == "grid_import":
        raise BindingRefusal(422, "site_connection_wrong_direction",
                             f"export_link {export_link!r} is tagged eh_role=grid_import: it "
                             "is tagged as an import Link, not the export Link; name the "
                             "export Link (or retag it)")
    site, _bypass = lp_bindings._meter_sides(
        n, CommercialConfig(poc_link=poc_link, export_link=export_link))
    e0, e1 = str(n.links.at[export_link, "bus0"]), str(n.links.at[export_link, "bus1"])
    if e0 not in site or e1 in site:
        raise BindingRefusal(422, "site_connection_wrong_direction",
                             f"export_link {export_link!r} runs {e0} → {e1}: an export Link "
                             f"runs from the site side of {poc_link!r} ({sorted(site)[:5]}) to "
                             "the grid side")

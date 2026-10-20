"""
The project flow's collector (project-workflow design §3; ticket B1).

``project_flow(project_dir, …)`` answers "which flow sections are done" for the
OPEN project, as one payload: a row per section (``flow_sections.SECTIONS``)
with its state and its items, plus ``run_ready``, ``run_blocker`` and
``notes``. It invents no physics. It files what existing checks already say:

  * ``validate_for_run`` through ``routers.simulation.preflight_validate``: the
    same three gates and the same ``deferred`` answer as
    ``POST /simulation/preflight`` (while deferred every input section reads
    ``unknown``, never ``complete``);
  * when the project carries ``eh_template.json`` (the one Run includes the
    reliability study): the study start endpoint's own refusal function,
    ``eh_study_runner.check_eh_study_start``, on the body the Goal card sends,
    and ``eh_readiness`` with the arguments the Site card sends. Both come from
    ``site_card_study_body``, the one Python copy of the frontend's
    ``buildEhStudyBody(archetype, formFromTemplate(meta))``, pinned to it by a
    parity fixture (``tests/fixtures/flow_site_card_args.json``);
  * the project's bind issues (commercial, finance, Library pins, the tariff
    reference's inline copy), re-checked read-only;
  * the template's ledger rows marked ``assumed`` (a default the user has not
    confirmed is a warning, design §3.2);
  * the presence rules (``PRESENCE_RULES``), all display-only (design §3.3 R2
    kind c): they mark a section ``incomplete`` or ``warning`` and are listed in
    ``notes``, never in ``run_ready``. Kind (a), an existing server check, is
    filed as that check (``snapshots_empty``); B1 adds no kind (b) (R3).

The renewable-profile readiness feed (RP-02) is not on master yet; when it
lands it is one more source here.

``run_ready`` is exactly the server's verdict (R1): no ``validate_for_run``
error, and, when the study is requested, ``check_eh_study_start`` passes. Those
items carry ``blocking: true``; ``run_blocker`` is the first of them in flow
order. Nothing else feeds ``run_ready``.

States (design §3.2): ``incomplete`` = an error item (blocking or display-only);
``warning`` = an unacknowledged warning; ``complete`` otherwise;
``not_needed`` for the connection of an off-grid (islanded) template;
``unknown`` while the preflight is deferred. Warnings never block Run (W3).

Acknowledgements live in the project sidecar ``flow_acks.json`` as
``{section, code, entity, value_hash, at}``. The hash is taken over the
section, the code, the entity and the item's value (its message, which states
the value, or the ledger row's value), so a changed value re-raises the
warning. Errors cannot be acknowledged.

eh_readiness copies the network; its result is cached per (network revision,
VOLL, template hash, solver-config hash).
"""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from services import flow_sections as F

logger = logging.getLogger(__name__)

SECTION_IDS_INPUT = F.INPUT_SECTIONS

ACKS_FILE = "flow_acks.json"
TEMPLATE_FILE = "eh_template.json"

# The study pipeline's order and the stages a user may toggle
# (`EhReferenceDesignPanel.tsx` PIPELINE_ORDER / OPTIONAL_STAGES).
_OPTIONAL_STAGES = ("frontier", "mc_certify", "fmea_top", "redundancy", "levers",
                    "dtc_stress", "dtc_planning")
_PIPELINE_ORDER = ("apply_pack", "ens_solve", *_OPTIONAL_STAGES, "assemble")

# Ledger rows of a template are free text; their section is read from words in
# the row's `item`, first match wins. `test_project_flow` asserts every
# committed template's `assumed` row matches a word, so the fallback (the
# project's) is never reached by a shipped template.
_LEDGER_SECTIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("discount", "money year", "finance", "tariff", "escalation", "price",
      "investment case", "depreciation"), "commercial"),
    # Island controls and their limits are asset behaviour (design §1.4 J16).
    (("island", "rocof", "frequency", "ride-through"), "assets"),
    (("poc", "connection", "fault level", "energization", "grid", "feeder",
      "transformer", "power factor"), "connection"),
    (("critical", "load", "pue", "offtake", "demand", "delivery"), "loads"),
    (("existing", "battery", "genset", "electrolyser", "inverter", "storage",
      "ups", "pv", "unit", "fuel cell"), "assets"),
    # The model horizon is section 1's (W2).
    (("horizon", "dates", "year", "period"), "project"),
)

# eh_readiness's `warnings[]` carry no code. Each known sentence family is
# filed by its prefix (what the module writes, `eh_readiness.py` and
# `archetypes.import_energy_patch`); `test_project_flow` drives the templates
# and the refusal paths and fails on a sentence no prefix matches. At runtime an
# unmatched sentence files to `readiness`, so it still shows.
READINESS_WARNING_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("pack cannot be applied", "eh_pack_not_applicable", "scenarios"),
    ("energy import cap", "eh_import_energy_note", "connection"),
    ("VOLL is 0", "eh_voll_zero", "scenarios"),
)

# The presence rules (B1 brief; design §3.3 R2). All kind (c), display only.
# `snapshots non-empty` is kind (a): the validator's own `snapshots_empty`.
PRESENCE_RULES: dict[str, tuple[str, str]] = {
    "site_location_missing": ("project", "c"),
    "presence_connection": ("connection", "c"),
    "presence_loads": ("loads", "c"),
    "presence_assets": ("assets", "c"),
    "presence_voll": ("scenarios", "c"),
}


class FlowAckError(Exception):
    """An acknowledgement the store refuses: ``status`` is the HTTP code."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class _Facts:
    """What the presence rules read, taken under the preflight's lock."""

    revision: int
    snapshots: int
    loads: int
    carriers: list[str]
    connection_links: list[str]
    import_p_nom_mw: float | None
    asset_count: int
    buses: int
    buses_placed: int
    voll: float
    ens_cap: float | None
    commercial: Any
    finance: Any
    cfg_dict: dict = field(default_factory=dict)
    cfg_hash: str = ""
    copy: Any = None          # a private copy for eh_readiness, on a cache miss


# eh_readiness copies and walks the network; cached per readiness key.
_readiness_cache: dict[str, tuple[tuple, dict | Exception]] = {}
_readiness_cache_lock = threading.Lock()


def _hash(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def _item(code: str, severity: str, message: str, source: str, section: str, *,
          component: str = "", entity: str = "", fld: str = "",
          panel: str | None = None, value: Any = None, blocking: bool = False) -> dict:
    return {
        "code": code, "severity": severity, "message": message, "source": source,
        "target": {"section": section,
                   "panel": panel if panel is not None else F.SECTION_PANEL[section],
                   "component": component or None, "entity": entity or None,
                   "field": fld or None},
        # A server refusal (R1): it, and only it, makes `run_ready` false.
        "blocking": blocking,
        "acknowledged": False,
        # The acknowledgement key's value: stripped before the payload leaves.
        "_value": message if value is None else value,
    }


# ── Template metadata and the Site / Goal card's request ────────────────────

def read_template(project_dir: pathlib.Path) -> dict | None:
    path = project_dir / TEMPLATE_FILE
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def site_card_study_body(meta: dict) -> dict | None:
    """The study request the hub-design cards build from a template:
    ``buildEhStudyBody(meta.recommended_archetype, formFromTemplate(meta)).body``
    (``EhReferenceDesignPanel.tsx``), in Python. The Site card's readiness reads
    its ``archetype`` and ``pack_overrides``; the Goal card starts the study
    with all of it. None where the frontend's body is null (a value out of its
    range: the card shows the error and sends no overrides). Pinned to the
    frontend by ``flow_site_card_args.json``."""
    archetype = str(meta.get("recommended_archetype") or "strong_grid")
    po = meta.get("pack_overrides") or {}
    # (key, applies, valid): `formFromTemplate` keeps numbers only; a number
    # `buildEhStudyBody` rejects makes the whole body null.
    rules = (("ens_cap_permyriad", True, lambda v: v > 0),
             ("target_lole_h", True, lambda v: v >= 0),
             ("import_p_nom_mw", archetype == "weak_flexible", lambda v: v > 0),
             ("import_energy_mwh_per_year", archetype == "weak_flexible", lambda v: v >= 0))
    out: dict[str, Any] = {}
    for key, applies, valid in rules:
        v = po.get(key)
        if not applies or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if not valid(v):
            return None
        out[key] = v
    if "target_lole_h" in out:
        out["certification_metric"] = "mc_lole"
    body: dict[str, Any] = {"archetype": archetype}
    if out:
        body["pack_overrides"] = out
    if meta.get("dtc_attribution"):
        body["dtc_attribution"] = meta["dtc_attribution"]
    if isinstance(meta.get("stages"), list):
        chosen = {"apply_pack", "ens_solve", "assemble",
                  *(s for s in meta["stages"] if s in _OPTIONAL_STAGES)}
        body["stages"] = [s for s in _PIPELINE_ORDER if s in chosen]
    return body


def _request_for(template: dict) -> dict:
    """The cards' request, or the archetype alone where the frontend's body is
    null (what the Site card's readiness then sends)."""
    return site_card_study_body(template) or {
        "archetype": str(template.get("recommended_archetype") or "strong_grid")}


def _pack_for(body: dict):
    from services.adequacy.eh_study_runner import _PACK_FACTORY, apply_pack_overrides

    factory = _PACK_FACTORY.get(body["archetype"]) or _PACK_FACTORY["strong_grid"]
    return apply_pack_overrides(factory(), body.get("pack_overrides"))


# ── Facts under the lock ────────────────────────────────────────────────────

def _gather(n, config, *, template: dict | None, cache_key: str) -> _Facts:
    from services import dirty_state
    from services.adequacy import archetypes as arch
    from services.network_geometry import _bus_coord

    body = _request_for(template) if template is not None else {"archetype": "strong_grid"}
    try:
        overlay = _pack_for(body).import_overlay
        imports, _rule = arch.select_import_links_with_rule(n, overlay)
    except Exception:                                    # noqa: BLE001 — presence reads "none"
        logger.exception("flow: import Link selection failed")
        imports = []
    connection = set(imports)
    commercial = getattr(config, "commercial", None)
    if isinstance(commercial, dict):
        for key in ("poc_link", "export_link"):
            if commercial.get(key) and str(commercial[key]) in set(map(str, n.links.index)):
                connection.add(str(commercial[key]))
    links = [str(i) for i in n.links.index]
    # The supply behind the fence (a market Generator on an import Link's grid
    # side) is the connection's, not an asset of the site.
    grid_side = {str(n.links.at[i, "bus0"]) for i in imports if i in n.links.index}
    gens = sum(1 for b in n.generators["bus"] if str(b) not in grid_side) \
        if len(n.generators) else 0
    assets = (gens + len(n.storage_units) + len(n.stores)
              + sum(1 for i in links if i not in connection))
    # The RP-01 rule (NaN, out of range and 0/0 all count as missing), so the
    # flow and the profile refusal agree on what "placed" means.
    placed = sum(1 for b in n.buses.index if _bus_coord(n, b) is not None)
    p_nom = None
    if imports:
        try:
            p_nom = float(n.links.loc[list(imports), "p_nom"].sum())
        except Exception:                                # noqa: BLE001
            p_nom = None
    try:
        cfg_dict = asdict(config)
    except Exception:                                    # noqa: BLE001
        cfg_dict = {}
    cfg_hash = _hash(json.dumps(cfg_dict, sort_keys=True, default=str))
    revision = dirty_state.revision()
    voll = float(getattr(config, "voll", 0.0) or 0.0)
    copy = None
    if template is not None:
        key = _readiness_key(revision, voll, template, cfg_hash)
        with _readiness_cache_lock:
            hit = _readiness_cache.get(cache_key)
        if hit is None or hit[0] != key:
            from services.adequacy.eh_study import _private_copy

            copy = _private_copy(n)
    return _Facts(
        revision=revision, snapshots=len(n.snapshots), loads=len(n.loads),
        carriers=[str(c) for c in n.carriers.index] if len(n.carriers) else
        sorted({str(b) for b in n.buses.get("carrier", []) if str(b)}),
        connection_links=sorted(connection), import_p_nom_mw=p_nom,
        asset_count=assets, buses=len(n.buses), buses_placed=placed, voll=voll,
        ens_cap=getattr(config, "ens_cap_permyriad", None),
        commercial=commercial, finance=getattr(config, "finance", None),
        cfg_dict=cfg_dict, cfg_hash=cfg_hash, copy=copy)


def _readiness_key(revision: int, voll: float, template: dict, cfg_hash: str) -> tuple:
    """The eh_readiness cache key: network revision, VOLL, template hash and
    solver-config hash."""
    return (revision, voll, _hash(json.dumps(template, sort_keys=True, default=str)),
            cfg_hash)


# ── Sources ─────────────────────────────────────────────────────────────────

def _validation_items(issues, connection_links: frozenset[str]) -> list[dict]:
    out = []
    for i in issues:
        section = F.file_issue(i.code, i.component_class or "", i.name or "", connection_links)
        out.append(_item(i.code, i.severity, i.message, "validation", section,
                         component=i.component_class or "", entity=i.name or "",
                         blocking=i.severity == "error"))
    return out


def _study_start_items(body: dict) -> list[dict]:
    """The study start endpoint's own refusal (R1): `check_eh_study_start` on
    the Goal card's body. A refusal blocks Run; it files to section 8, the run
    request it refuses."""
    from fastapi import HTTPException

    from services.adequacy.eh_study_runner import EhStudyRequest, check_eh_study_start
    from services.pypsa_service import PyPSAService

    try:
        check_eh_study_start(EhStudyRequest.model_validate(body),
                             solver_state=PyPSAService.get_solver_state())
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
        return [_item("eh_study_refused", "error",
                      f"The reliability study would not start: {detail}", "eh_study",
                      "scenarios", blocking=True)]
    return []


def _readiness(facts: _Facts, template: dict, cache_key: str) -> dict | Exception:
    """eh_readiness for the Site card's arguments, cached per readiness key."""
    key = _readiness_key(facts.revision, facts.voll, template, facts.cfg_hash)
    with _readiness_cache_lock:
        hit = _readiness_cache.get(cache_key)
    if hit is not None and hit[0] == key:
        return hit[1]
    from models.energy_hub import DEFAULT_EH_BUDGET_SOLVES
    from services.adequacy.eh_readiness import eh_readiness
    from services.adequacy.eh_study import readiness_refusal_classes

    body = _request_for(template)
    try:
        # The Site card's request: archetype and pack overrides; stages and
        # DtC attribution left to the engine (`useHubReadiness`).
        result: dict | Exception = eh_readiness(
            facts.copy, _pack_for(body), budget_solves=DEFAULT_EH_BUDGET_SOLVES,
            stages=None, voll=facts.voll, dtc_attribution=None)
    except ValueError as exc:
        if not isinstance(exc, readiness_refusal_classes()):
            logger.exception("flow: eh_readiness failed")
            exc = RuntimeError("readiness could not be computed for this network")
        result = exc
    with _readiness_cache_lock:
        _readiness_cache[cache_key] = (key, result)
    return result


def readiness_warning_filing(text: str) -> tuple[str, str] | None:
    """``(code, section)`` for an eh_readiness warning sentence, or None."""
    return next(((code, section) for prefix, code, section in READINESS_WARNING_PREFIXES
                 if text.startswith(prefix)), None)


def _readiness_items(r: dict | Exception, *, voll_noted: bool) -> list[dict]:
    if isinstance(r, Exception):
        # A typed refusal (or an internal failure) of the readiness preview:
        # one connection item, display-only; the study's own start refusal is
        # what blocks (`_study_start_items`).
        refused = isinstance(r, ValueError)
        return [_item("eh_readiness_refused" if refused else "eh_readiness_unavailable",
                      "error" if refused else "warning", str(r), "eh_readiness", "connection")]
    out: list[dict] = []
    archetype = r.get("archetype")
    imp = r.get("import") or {}
    if archetype != "off_grid" and not imp.get("links"):
        out.append(_item("eh_no_import_link", "warning",
                         "No grid connection was identified: tag the import Link "
                         "(eh_role grid_import) or the point-of-connection bus (eh_poc).",
                         "eh_readiness", "connection", fld="eh_role"))
    scr = r.get("scr") or {}
    if scr.get("status") not in (None, "ok", "not_required"):
        out.append(_item("eh_scr_not_established", "warning",
                         scr.get("note") or "The grid strength (SCR) at the point of "
                         "connection could not be established.", "eh_readiness",
                         "connection", component="Bus", fld="eh_sk_mva"))
    if not r.get("critical_buses"):
        out.append(_item("eh_no_critical_load", "warning",
                         "No load is marked must-stay-on: tag the critical bus "
                         "(eh_critical).", "eh_readiness", "loads", component="Bus",
                         fld="eh_critical"))
    missing = (r.get("outage_units") or {}).get("missing") or []
    if missing:
        names = ", ".join(m.get("name", "") for m in missing[:5])
        more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
        out.append(_item("eh_outage_data_missing", "warning",
                         f"{len(missing)} unit(s) have no outage data ({names}{more}).",
                         "eh_readiness", "assets", fld="outage_rate_value"))
    for w in r.get("warnings") or []:
        text = str(w)
        filed = readiness_warning_filing(text)
        if filed is None:
            out.append(_item("eh_readiness_note", "warning", text, "eh_readiness", "readiness"))
            continue
        code, section = filed
        if code == "eh_voll_zero" and voll_noted:
            continue                      # the presence note already says it
        out.append(_item(code, "warning", text, "eh_readiness", section,
                         fld="voll" if code == "eh_voll_zero" else ""))
    return out


def _bind_items(facts: _Facts, db, org_id, project_dir: pathlib.Path, n) -> list[dict]:
    """The project's bind issues, re-checked read-only (`routers/projects.py`
    binds them at create and load; nothing here writes). Display-only: the
    solve's own refusals are what `/run` says."""
    from pydantic import ValidationError

    out: list[dict] = []

    def add(code: str, message: str, fld: str, severity: str = "error",
            source: str = "commercial") -> None:
        out.append(_item(code, severity, message, source, "commercial", fld=fld))

    if facts.commercial:
        try:
            from models.commercial import CommercialConfig
            from services.commercial import binding
            from services.commercial import participants as P

            parsed = CommercialConfig.model_validate(facts.commercial)
            try:
                binding.check_site_connection(n, parsed.poc_link, parsed.export_link,
                                              direction_only=True,
                                              group_members=parsed.group_members)
                if parsed.value_flows is not None:
                    vf = P.parse_value_flows(parsed.value_flows)
                    problems = P.value_flows_problems(vf, parsed, n)
                    if problems:
                        add("value_flows_invalid", "; ".join(problems), "value_flows")
            except binding.BindingRefusal as exc:
                add(exc.code, exc.message, "commercial")
            except P.ValueFlowsInvalid as exc:
                add(exc.code, str(exc), "value_flows")
        except ValidationError as exc:
            add("commercial_config_invalid", str(exc.errors()[0]["msg"]), "commercial")
        except Exception:                                # noqa: BLE001 — report, never a 500
            logger.exception("flow: commercial re-check failed")
            add("commercial_bind_failed", "the commercial settings could not be checked",
                "commercial", severity="warning")
    if facts.finance:
        try:
            from models.finance import FinanceInputs

            FinanceInputs.model_validate(facts.finance)
        except ValidationError as exc:
            add("finance_inputs_invalid", str(exc.errors()[0]["msg"]), "finance")
        except Exception:                                # noqa: BLE001
            logger.exception("flow: finance re-check failed")
            add("finance_bind_failed", "the finance inputs could not be checked", "finance",
                severity="warning")
    # Library pins and the tariff ref's inline copy. Not logged here (a GET is
    # polled; the load route logs them).
    try:
        from services.commercial.binding import tariff_ref_load_issue

        issue = tariff_ref_load_issue(facts.commercial) if facts.commercial else None
        if issue is not None:
            add(issue["code"], issue["message"], "import_tariff_ref", source="library")
    except Exception:                                    # noqa: BLE001
        logger.exception("flow: tariff ref re-check failed")
    if db is not None and org_id is not None:
        try:
            import uuid as _uuid

            from services.library import bundle_pins

            for issue in bundle_pins.check_pins(db, _uuid.UUID(str(org_id)), project_dir,
                                                config=facts.cfg_dict):
                add(issue.get("code", "library_pin_invalid"), issue.get("message", ""),
                    issue.get("field") or "library", source="library")
        except Exception:                                # noqa: BLE001
            logger.exception("flow: library pin re-check failed")
    return out


def ledger_section(item: str) -> str | None:
    """The section a template ledger row's `item` files to, or None."""
    low = item.lower()
    return next((s for words, s in _LEDGER_SECTIONS if any(w in low for w in words)), None)


def _assumed_items(template: dict) -> list[dict]:
    """The template's `assumed` ledger rows and energization: a default the user
    has not confirmed is a warning (design §3.2)."""
    out: list[dict] = []
    for row in template.get("ledger") or []:
        if not isinstance(row, dict) or row.get("source") != "assumed":
            continue
        item = str(row.get("item") or "")
        value = str(row.get("value") or "")
        out.append(_item("assumed_default", "warning",
                         f"{item}: {value} is a template assumption; confirm it or set "
                         "your own.", "template", ledger_section(item) or "project",
                         entity=item, value=value))
    energ = [e for e in template.get("energization") or []
             if isinstance(e, dict) and e.get("source") == "assumed"]
    if energ:
        value = json.dumps([[e.get("year"), e.get("available_import_mw")] for e in energ])
        steps = ", ".join(f"{e.get('year')}: {e.get('available_import_mw'):g} MW"
                          if isinstance(e.get("available_import_mw"), (int, float))
                          else str(e.get("year")) for e in energ)
        out.append(_item("assumed_default", "warning",
                         f"energization schedule ({steps}) is a template assumption; "
                         "confirm it with the network operator's offer.", "template",
                         "connection", entity="energization schedule", value=value))
    return out


def _presence(facts: _Facts, *, islanded: bool, study_requested: bool) -> list[dict]:
    """The presence rules (`PRESENCE_RULES`), all display-only (kind c)."""
    out: list[dict] = []

    def add(code: str, severity: str, message: str, **kw) -> None:
        out.append(_item(code, severity, message, "flow", PRESENCE_RULES[code][0], **kw))

    if facts.buses and facts.buses_placed == 0:
        add("site_location_missing", "warning",
            "The site has no location: no bus is placed.", component="Bus", fld="x")
    if not islanded and not facts.connection_links:
        add("presence_connection", "error",
            "No grid connection: add the import Link, or declare the site islanded.",
            component="Link", fld="eh_role")
    if facts.loads == 0:
        add("presence_loads", "error", "The site has no load or product demand.",
            component="Load")
    if facts.asset_count == 0:
        add("presence_assets", "error",
            "No asset: add a generator, storage, store or conversion Link.")
    if study_requested and facts.voll <= 0:
        add("presence_voll", "error",
            "The reliability study needs a value of lost load (VOLL > 0) for its "
            "frontier and stress stages.", fld="voll")
    return out


# ── Acknowledgements ────────────────────────────────────────────────────────

def read_acks(project_dir: pathlib.Path) -> list[dict]:
    try:
        raw = json.loads((project_dir / ACKS_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return []
    acks = raw.get("acks") if isinstance(raw, dict) else None
    return [a for a in acks if isinstance(a, dict)] if isinstance(acks, list) else []


def _ack_hash(item: dict) -> str:
    return _hash(item["target"]["section"], item["code"], item["target"]["entity"] or "",
                 item["_value"])


def _apply_acks(items: list[dict], acks: list[dict]) -> None:
    held = {(a.get("code"), a.get("entity") or "", a.get("value_hash")) for a in acks}
    for it in items:
        if it["severity"] == "warning" and \
                (it["code"], it["target"]["entity"] or "", _ack_hash(it)) in held:
            it["acknowledged"] = True


def acknowledge(project_dir: pathlib.Path, items: list[dict], code: str,
                entity: str | None) -> dict:
    """Store an acknowledgement of the CURRENT item ``(code, entity)``.

    404 when no such item stands, 422 when it is an error (W3: only warnings
    can be acknowledged). Re-acknowledging replaces the stored hash."""
    from services.atomic_io import atomic_write_text

    entity = entity or ""
    match = [it for it in items if it["code"] == code and (it["target"]["entity"] or "") == entity]
    if not match:
        raise FlowAckError(404, f"no flow item {code!r} for {entity or 'the project'!r}")
    if any(it["severity"] != "warning" for it in match):
        raise FlowAckError(422, "only a warning can be acknowledged; fix an error instead")
    record = {"section": match[0]["target"]["section"], "code": code, "entity": entity,
              "value_hash": _ack_hash(match[0]),
              "at": datetime.now(UTC).isoformat(timespec="seconds")}
    acks = [a for a in read_acks(project_dir)
            if not (a.get("code") == code and (a.get("entity") or "") == entity)]
    acks.append(record)
    atomic_write_text(project_dir / ACKS_FILE,
                      json.dumps({"version": 1, "acks": acks}, indent=2, sort_keys=True) + "\n")
    return record


# ── Results freshness ───────────────────────────────────────────────────────

def _freshness(n, *, running: bool) -> str:
    if running:
        return "running"
    from services import study_state
    from services.dispatch_status import dispatch_status
    from services.pypsa_service import PyPSAService

    try:
        dispatch = dispatch_status(n)
    except Exception:                                    # noqa: BLE001
        dispatch = "none"
    try:
        record = PyPSAService.get_solver_state().get("eh_study")
    except Exception:                                    # noqa: BLE001
        record = None
    studied = isinstance(record, dict) and record.get("status") == "done"
    if dispatch == "stale" or (studied and study_state.edited_since(record) is True):
        return "stale"
    if dispatch == "fresh" or studied:
        return "fresh"
    return "not_run"


# ── The collector ───────────────────────────────────────────────────────────

def _summary(section: str, facts: _Facts) -> str | None:
    if section == "project":
        return f"{facts.snapshots} snapshots"
    if section == "carriers":
        return ", ".join(facts.carriers[:6]) or None
    if section == "connection":
        if not facts.connection_links:
            return None
        mw = f", {facts.import_p_nom_mw:g} MW" if facts.import_p_nom_mw is not None else ""
        return ", ".join(facts.connection_links[:3]) + mw
    if section == "loads":
        return f"{facts.loads} load(s)"
    if section == "assets":
        return f"{facts.asset_count} asset(s)"
    return None


def _strip(it: dict) -> dict:
    return {k: v for k, v in it.items() if not k.startswith("_")}


def _pointer(row_label: str, it: dict) -> dict:
    return {"section": it["target"]["section"], "message": f"{row_label}: {it['message']}",
            "target": it["target"]}


def project_flow(project_dir: pathlib.Path, *, cache_key: str, db=None,
                 org_id=None) -> dict:
    """The flow payload for the open project at ``project_dir``:
    ``{sections, run_ready, run_blocker, notes, deferred, deferred_reason}``.

    The caller has checked that ``project_dir`` IS the active project (the
    validator reads the foreground network). ``cache_key`` keys the
    eh_readiness cache (the registry key)."""
    payload, _items = _collect(project_dir, cache_key=cache_key, db=db, org_id=org_id)
    return payload


def _collect(project_dir: pathlib.Path, *, cache_key: str, db=None,
             org_id=None) -> tuple[dict, list[dict]]:
    from routers.simulation import _solver_in_flight, preflight_validate
    from services import study_state
    from services.pypsa_service import PyPSAService

    template = read_template(project_dir)
    body = _request_for(template) if template is not None else None
    islanded = body is not None and body["archetype"] == "off_grid"
    deferred, issues, facts = preflight_validate(
        lambda n, cfg: _gather(n, cfg, template=template, cache_key=cache_key))
    n = PyPSAService.get_network()
    running = _solver_in_flight() or study_state.running_study() is not None
    freshness = _freshness(n, running=running)

    if deferred is not None:
        sections = [{"section": s, "label": F.SECTION_LABELS[s],
                     "state": "unknown" if s in F.INPUT_SECTIONS else None,
                     "required": s in F.INPUT_SECTIONS, "items": [], "summary": None,
                     **({"freshness": freshness} if s == "results" else {})}
                    for s in F.SECTION_IDS]
        reason = deferred.get("deferred_reason") or "validation deferred"
        return ({"sections": sections, "run_ready": False,
                 "run_blocker": {"section": "readiness", "message": reason,
                                 "target": {"section": "readiness", "panel": "issues",
                                            "component": None, "entity": None,
                                            "field": None}},
                 "notes": [], "deferred": True, "deferred_reason": reason}, [])

    # The one Run includes the reliability study for a project made from an EH
    # template (the hub-design Guided flow), or when the config sets a goal.
    study_requested = template is not None or bool(facts.ens_cap)
    items: list[dict] = []
    presence = _presence(facts, islanded=islanded, study_requested=study_requested)
    items += presence
    items += _validation_items(issues, frozenset(facts.connection_links))
    if body is not None:
        items += _study_start_items(body)
        r = _readiness(facts, template, cache_key)
        items += _readiness_items(r, voll_noted=any(
            i["code"] == "presence_voll" for i in presence))
        items += _assumed_items(template)
    items += _bind_items(facts, db, org_id, project_dir, n)
    _apply_acks(items, read_acks(project_dir))

    required = {s: True for s in F.INPUT_SECTIONS}
    required["profiles"] = facts.snapshots > 1
    required["commercial"] = bool(facts.commercial or facts.finance)

    rows: list[dict] = []
    for s in F.SECTION_IDS:
        mine = [it for it in items if it["target"]["section"] == s]
        row: dict[str, Any] = {"section": s, "label": F.SECTION_LABELS[s],
                               "items": [_strip(it) for it in mine],
                               "summary": _summary(s, facts)}
        if s in F.INPUT_SECTIONS:
            errors = any(it["severity"] == "error" for it in mine)
            blocking = any(it["blocking"] for it in mine)
            warnings = any(it["severity"] == "warning" and not it["acknowledged"]
                           for it in mine)
            if s == "connection" and islanded and not blocking:
                state = "not_needed"
            elif errors:
                state = "incomplete"
            elif warnings:
                state = "warning"
            else:
                state = "complete"
            row.update(state=state, required=required[s] and state != "not_needed")
        else:
            row.update(state=None, required=False)
        rows.append(row)

    by_id = {r["section"]: r for r in rows}
    shown = [it for r in rows for it in r["items"]]
    todo = sum(1 for it in shown if it["severity"] == "error")
    check = sum(1 for it in shown if it["severity"] == "warning" and not it["acknowledged"])
    by_id["readiness"]["summary"] = f"{todo} to do, {check} to check"
    by_id["results"]["freshness"] = freshness

    # Flow order: the rows are already in it. Islanded connection items are
    # still listed on their row but do not become notes.
    blocker = next((_pointer(r["label"], it) for r in rows for it in r["items"]
                    if it["blocking"]), None)
    notes = [_pointer(r["label"], it) for r in rows for it in r["items"]
             if it["severity"] == "error" and not it["blocking"]
             and r.get("state") != "not_needed"]
    return ({"sections": rows, "run_ready": blocker is None, "run_blocker": blocker,
             "notes": notes, "deferred": False, "deferred_reason": None}, items)


def acknowledge_item(project_dir: pathlib.Path, code: str, entity: str | None, *,
                     cache_key: str, db=None, org_id=None) -> dict:
    """``POST …/flow/ack``: acknowledge a standing warning of the open project."""
    payload, items = _collect(project_dir, cache_key=cache_key, db=db, org_id=org_id)
    if payload["deferred"]:
        raise FlowAckError(409, payload["deferred_reason"] or "validation deferred")
    return acknowledge(project_dir, items, code, entity)

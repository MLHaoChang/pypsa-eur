"""
The project flow's collector (project-workflow design §3; ticket B1).

``project_flow(project_dir, …)`` answers "which flow sections are done" for the
OPEN project, as one payload: a row per section (``flow_sections.SECTIONS``)
with its state and its items, plus ``run_ready`` / ``run_blocker``. It invents
no physics. It files what existing checks already say:

  * ``validate_for_run`` through ``routers.simulation.preflight_validate``: the
    same three gates and the same ``deferred`` answer as
    ``POST /simulation/preflight`` (while deferred every input section reads
    ``unknown``, never ``complete``);
  * ``eh_readiness``, only for a project carrying ``eh_template.json``, with the
    arguments the hub-design Site card sends (the template's recommended
    archetype and its pack overrides, filtered as ``buildEhStudyBody`` does);
    a typed refusal is one connection item, not a failed endpoint;
  * the project's bind issues (commercial, finance, Library pins, the tariff
    reference's inline copy), re-checked read-only;
  * the template's ledger rows marked ``assumed`` (a default the user has not
    confirmed is a warning, design §3.2);
  * a few presence rules over data that already exists (``_presence``).

The renewable-profile readiness feed (RP-02) is not on master yet; when it
lands it is one more source here.

States (design §3.2): ``incomplete`` = a presence rule fails or an item is an
error; ``warning`` = an unacknowledged warning; ``complete`` otherwise;
``not_needed`` for the connection of an off-grid (islanded) template;
``unknown`` while the preflight is deferred. Warnings never block Run (W3).

Acknowledgements live in the project sidecar ``flow_acks.json`` as
``{code, entity, value_hash, at}``. The hash is taken over the item's value
(its message, which states the value, or the ledger row's value), so a changed
value re-raises the warning. Errors cannot be acknowledged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, UTC
from typing import Any

from services import flow_sections as F

logger = logging.getLogger(__name__)

ACKS_FILE = "flow_acks.json"
TEMPLATE_FILE = "eh_template.json"

# Ledger rows of a template are free text; their section is read from words in
# the row's `item`, first match wins, and a row no word matches is the project's.
_LEDGER_SECTIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("discount", "money year", "finance", "tariff", "escalation", "price",
      "investment case"), "commercial"),
    # Island controls and their limits are asset behaviour (design §1.4 J16).
    (("island", "rocof", "frequency", "ride-through"), "assets"),
    (("poc", "connection", "fault level", "energization", "grid", "feeder",
      "transformer", "power factor"), "connection"),
    (("critical", "load", "pue", "offtake", "demand", "delivery"), "loads"),
    (("existing", "battery", "genset", "electrolyser", "inverter", "storage",
      "ride-through", "ups", "pv", "unit"), "assets"),
)


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
    copy: Any = None          # a private copy for eh_readiness, on a cache miss


# eh_readiness copies and walks the network; cache it per network revision.
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
          panel: str | None = None, value: Any = None) -> dict:
    return {
        "code": code, "severity": severity, "message": message, "source": source,
        "target": {"section": section,
                   "panel": panel if panel is not None else F.SECTION_PANEL[section],
                   "component": component or None, "entity": entity or None,
                   "field": fld or None},
        "acknowledged": False,
        # The acknowledgement key's value: stripped before the payload leaves.
        "_value": message if value is None else value,
    }


# ── Template metadata ───────────────────────────────────────────────────────

def read_template(project_dir: pathlib.Path) -> dict | None:
    path = project_dir / TEMPLATE_FILE
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def site_card_readiness_args(template: dict) -> tuple[str, dict | None, Any]:
    """``(archetype, pack_overrides, error)`` as the hub-design Site card sends
    them: the template's recommended archetype, and its pack overrides
    filtered by ``buildEhStudyBody`` (``EhReferenceDesignPanel.tsx``): the ENS
    cap, a LOLE target (with ``certification_metric: mc_lole``), and the import
    MW / energy budget for ``weak_flexible`` only."""
    archetype = str(template.get("recommended_archetype") or "strong_grid")
    po = template.get("pack_overrides") or {}
    out: dict[str, Any] = {}

    def num(key: str) -> float | None:
        v = po.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    if num("ens_cap_permyriad") is not None and num("ens_cap_permyriad") > 0:
        out["ens_cap_permyriad"] = num("ens_cap_permyriad")
    if num("target_lole_h") is not None and num("target_lole_h") >= 0:
        out["target_lole_h"] = num("target_lole_h")
        out["certification_metric"] = "mc_lole"
    if archetype == "weak_flexible":
        if num("import_p_nom_mw") is not None and num("import_p_nom_mw") > 0:
            out["import_p_nom_mw"] = num("import_p_nom_mw")
        if num("import_energy_mwh_per_year") is not None and num("import_energy_mwh_per_year") >= 0:
            out["import_energy_mwh_per_year"] = num("import_energy_mwh_per_year")
    return archetype, (out or None), None


def _pack_for(archetype: str, overrides: dict | None):
    from services.adequacy.eh_study_runner import _PACK_FACTORY, apply_pack_overrides

    factory = _PACK_FACTORY.get(archetype) or _PACK_FACTORY["strong_grid"]
    return apply_pack_overrides(factory(), overrides)


# ── Facts under the lock ────────────────────────────────────────────────────

def _gather(n, config, *, template: dict | None, cache_key: str) -> _Facts:
    from services import dirty_state
    from services.adequacy import archetypes as arch

    if template is not None:
        archetype, overrides, _ = site_card_readiness_args(template)
    else:
        archetype, overrides = "strong_grid", None
    try:
        overlay = _pack_for(archetype, overrides).import_overlay
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
    placed = 0
    if len(n.buses):
        xs = n.buses.get("x")
        ys = n.buses.get("y")
        if xs is not None and ys is not None:
            placed = int(((xs.fillna(0) != 0) | (ys.fillna(0) != 0)).sum())
    p_nom = None
    if imports:
        try:
            p_nom = float(n.links.loc[list(imports), "p_nom"].sum())
        except Exception:                                # noqa: BLE001
            p_nom = None
    revision = dirty_state.revision()
    voll = float(getattr(config, "voll", 0.0) or 0.0)
    copy = None
    if template is not None:
        key = _readiness_key(revision, voll, template)
        with _readiness_cache_lock:
            hit = _readiness_cache.get(cache_key)
        if hit is None or hit[0] != key:
            from services.adequacy.eh_study import _private_copy

            copy = _private_copy(n)
    cfg_dict = {}
    try:
        cfg_dict = asdict(config)
    except Exception:                                    # noqa: BLE001
        cfg_dict = {}
    return _Facts(
        revision=revision, snapshots=len(n.snapshots), loads=len(n.loads),
        carriers=[str(c) for c in n.carriers.index] if len(n.carriers) else
        sorted({str(b) for b in n.buses.get("carrier", []) if str(b)}),
        connection_links=sorted(connection), import_p_nom_mw=p_nom,
        asset_count=assets, buses=len(n.buses), buses_placed=placed, voll=voll,
        ens_cap=getattr(config, "ens_cap_permyriad", None),
        commercial=commercial, finance=getattr(config, "finance", None),
        cfg_dict=cfg_dict, copy=copy)


def _readiness_key(revision: int, voll: float, template: dict) -> tuple:
    return (revision, voll, _hash(json.dumps(template, sort_keys=True, default=str)))


# ── Sources ─────────────────────────────────────────────────────────────────

def _validation_items(issues, connection_links: frozenset[str]) -> list[dict]:
    out = []
    for i in issues:
        section = F.file_issue(i.code, i.component_class or "", i.name or "", connection_links)
        out.append(_item(i.code, i.severity, i.message, "validation", section,
                         component=i.component_class or "", entity=i.name or ""))
    return out


def _readiness(facts: _Facts, template: dict, cache_key: str) -> dict | Exception:
    """eh_readiness for the Site card's arguments, cached per revision."""
    key = _readiness_key(facts.revision, facts.voll, template)
    with _readiness_cache_lock:
        hit = _readiness_cache.get(cache_key)
    if hit is not None and hit[0] == key:
        return hit[1]
    from models.energy_hub import DEFAULT_EH_BUDGET_SOLVES
    from services.adequacy.eh_readiness import eh_readiness
    from services.adequacy.eh_study import readiness_refusal_classes

    archetype, overrides, _ = site_card_readiness_args(template)
    try:
        result: dict | Exception = eh_readiness(
            facts.copy, _pack_for(archetype, overrides),
            budget_solves=DEFAULT_EH_BUDGET_SOLVES, stages=None,
            voll=facts.voll, dtc_attribution=None)
    except ValueError as exc:
        if not isinstance(exc, readiness_refusal_classes()):
            logger.exception("flow: eh_readiness failed")
            exc = RuntimeError("readiness could not be computed for this network")
        result = exc
    with _readiness_cache_lock:
        _readiness_cache[cache_key] = (key, result)
    return result


def _readiness_items(r: dict | Exception, *, voll_rule_failed: bool) -> list[dict]:
    if isinstance(r, Exception):
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
        if "VOLL" in text:
            if voll_rule_failed:
                continue                  # the presence rule already says it, as the blocker
            out.append(_item("eh_voll_zero", "warning", text, "eh_readiness", "scenarios",
                             fld="voll"))
        elif text.startswith("pack cannot be applied"):
            out.append(_item("eh_pack_not_applicable", "warning", text, "eh_readiness",
                             "connection"))
        else:
            out.append(_item("eh_readiness_note", "warning", text, "eh_readiness",
                             "connection"))
    return out


def _bind_items(facts: _Facts, db, org_id, project_dir: pathlib.Path, n) -> list[dict]:
    """The project's bind issues, re-checked read-only (`routers/projects.py`
    binds them at create and load; nothing here writes)."""
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
    # Library pins and the tariff ref's inline copy: the solve refuses a
    # mismatch, so each is an error. Not logged here (a GET is polled).
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


def _assumed_items(template: dict) -> list[dict]:
    """The template's `assumed` ledger rows and energization: a default the user
    has not confirmed is a warning (design §3.2)."""
    out: list[dict] = []
    for row in template.get("ledger") or []:
        if not isinstance(row, dict) or row.get("source") != "assumed":
            continue
        item = str(row.get("item") or "")
        low = item.lower()
        section = next((s for words, s in _LEDGER_SECTIONS if any(w in low for w in words)),
                       "project")
        value = str(row.get("value") or "")
        out.append(_item("assumed_default", "warning",
                         f"{item}: {value} is a template assumption; confirm it or set "
                         "your own.", "template", section, entity=item,
                         value=value))
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
    """The required inputs (B1 brief, "Presence rules"), as error items, plus the
    site location, which the validator does not require (a warning)."""
    out: list[dict] = []
    if facts.snapshots == 0:
        out.append(_item("presence_snapshots", "error",
                         "The model horizon has no snapshots.", "flow", "project",
                         panel="snapshots"))
    if facts.buses and facts.buses_placed == 0:
        out.append(_item("site_location_missing", "warning",
                         "The site has no location: every bus sits at 0, 0.", "flow",
                         "project", component="Bus", fld="x"))
    if not islanded and not facts.connection_links:
        out.append(_item("presence_connection", "error",
                         "No grid connection: add the import Link (or mark the site "
                         "islanded).", "flow", "connection", component="Link",
                         fld="eh_role"))
    if facts.loads == 0:
        out.append(_item("presence_loads", "error", "The site has no load or product demand.",
                         "flow", "loads", component="Load"))
    if facts.asset_count == 0:
        out.append(_item("presence_assets", "error",
                         "No asset: add a generator, storage, store or conversion Link.",
                         "flow", "assets"))
    if study_requested and facts.voll <= 0:
        out.append(_item("presence_voll", "error",
                         "The reliability study needs a value of lost load (VOLL > 0).",
                         "flow", "scenarios", fld="voll"))
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
    return _hash(item["code"], item["target"]["entity"] or "", item["_value"])


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
    if any(it["severity"] == "error" for it in match):
        raise FlowAckError(422, "an error cannot be acknowledged; fix it instead")
    if all(it["severity"] != "warning" for it in match):
        raise FlowAckError(422, "only a warning can be acknowledged")
    record = {"code": code, "entity": entity, "value_hash": _ack_hash(match[0]),
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
    record = None
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

def _summary(section: str, facts: _Facts | None) -> str | None:
    if facts is None:
        return None
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


def project_flow(project_dir: pathlib.Path, *, cache_key: str, db=None,
                 org_id=None) -> dict:
    """The flow payload for the open project at ``project_dir``:
    ``{sections, run_ready, run_blocker, deferred, deferred_reason}``.

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
    archetype = (site_card_readiness_args(template)[0] if template is not None else None)
    islanded = archetype == "off_grid"
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
                 "deferred": True, "deferred_reason": reason}, [])

    study_requested = template is not None or bool(facts.ens_cap)
    items: list[dict] = []
    presence = _presence(facts, islanded=islanded, study_requested=study_requested)
    items += presence
    items += _validation_items(issues, frozenset(facts.connection_links))
    if template is not None:
        r = _readiness(facts, template, cache_key)
        items += _readiness_items(r, voll_rule_failed=any(
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
            warnings = any(it["severity"] == "warning" and not it["acknowledged"]
                           for it in mine)
            if s == "connection" and islanded and not errors:
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
    todo = sum(1 for it in items if it["severity"] == "error")
    check = sum(1 for it in items if it["severity"] == "warning" and not it["acknowledged"])
    by_id["readiness"]["summary"] = f"{todo} to do, {check} to check"
    by_id["results"]["freshness"] = freshness

    blocker = None
    for s in (*F.INPUT_SECTIONS, "readiness"):
        row = by_id[s]
        first = next((it for it in row["items"] if it["severity"] == "error"), None)
        if first is None:
            continue
        blocker = {"section": s, "message": f"{row['label']}: {first['message']}",
                   "target": first["target"]}
        break
    return ({"sections": rows, "run_ready": blocker is None, "run_blocker": blocker,
             "deferred": False, "deferred_reason": None}, items)


def acknowledge_item(project_dir: pathlib.Path, code: str, entity: str | None, *,
                     cache_key: str, db=None, org_id=None) -> dict:
    """``POST …/flow/ack``: acknowledge a standing warning of the open project."""
    payload, items = _collect(project_dir, cache_key=cache_key, db=db, org_id=org_id)
    if payload["deferred"]:
        raise FlowAckError(409, payload["deferred_reason"] or "validation deferred")
    return acknowledge(project_dir, items, code, entity)

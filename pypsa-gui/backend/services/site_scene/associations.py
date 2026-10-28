"""
SC-02a: associations of scene features with native model targets, groups
(complexes), explicit allocation and placement of existing equipment.

A feature is a scene building id, a group id or a placement key. A target is
an existing native component, recorded by its D06 uid
(`services/component_identity.py`), never by its name: a rename keeps the
association, a delete or a same-name recreation leaves it unbound until the
user re-attaches it (`rebind`, confirmed). A placement whose sites.json
binding is stale is never re-bound by placing it again: only `rebind` does.
Plan and review conditions: `.scratch/site-scene-generation/sc-02/plan.md`.

Every action is a pair `prepare(scene, n, inputs, recorded)` ->
(stored plan, preview) and `apply(scene, n, stored, recorded)` -> (scene
changes, sites.json binding updates). `recorded` is the sites.json D06
`bindings` map. Prepare reads the open network under its lock and never
writes or mints. Apply runs under the network lock and then the scene mutex,
re-validates every target against the live network, and only then mints
uids. Nothing here creates, edits or removes a model component: a process
unit stays its Link, inventory its Store, and no Load is ever added.

Refusals are `SceneError`s with a route detail `code` (never a chat tool
error kind): `unsupported_target`, `unknown_target`, `ambiguous_target`,
`orphan_member`, `cycle`, `invalid_allocation`, `duplicate`,
`confirmation_required`, `not_roof_eligible`, `off_roof`, `unbound_target`,
`project_not_open` (422) and `target_changed` (409). Messages name things in
words (`it · load`), never as a class path.
"""
from __future__ import annotations

import copy
import math
import uuid
from typing import Any

from services import component_identity
from services.site_scene import frames
from services.site_scene.contract import SceneError, quantity

ROLES = {"Load": "load", "Link": "process_unit", "Store": "inventory", "StorageUnit": "equipment",
         "Generator": "equipment"}
KIND_WORDS = {"Load": "load", "Link": "link", "Store": "store", "StorageUnit": "storage unit",
              "Generator": "generator", "Bus": "bus", "Line": "line", "Transformer": "transformer"}
ELECTRIC_CARRIERS = {"ac", "dc", "electricity", "low voltage", "lv", "mv", "hv"}
MAX_ASSOCIATIONS = 2000
MAX_GROUPS = 500
MAX_MEMBERS = 2000
WEIGHT_TOLERANCE = 1e-9
ROOF_CARRIERS = ("solar",)


def _refuse(code: str, message: str, status_kind: str = "invalid_input", **detail: Any) -> SceneError:
    return SceneError(status_kind, message, {"code": code, **detail})


def _changed(message: str, code: str = "target_changed") -> SceneError:
    return _refuse(code, message, "revision_conflict")


def _split(key: str) -> tuple[str, str]:
    cls, _, name = key.partition(":")
    return cls, name


# ── native reads (never write) ──────────────────────────────────────────────

def _static(n, cls: str):
    try:
        return n.components[cls].static
    except (KeyError, AttributeError):
        return None


def _exists(n, cls: str, name: str) -> bool:
    df = _static(n, cls)
    return df is not None and name in df.index


def _is_process(n, name: str) -> bool:
    """`eh_process` is a lazy Link column: absent, NaN or False all mean an ordinary Link."""
    value = n.links.at[name, "eh_process"] if "eh_process" in n.links.columns else None
    return value is not None and not (isinstance(value, float) and math.isnan(value)) and bool(value)


def _label(n, cls: str, name: str) -> str:
    """`it · load`, `haber · process unit`: the name, then the kind in words (CODING_STANDARDS UI rule 2)."""
    process = n is not None and cls == "Link" and _exists(n, cls, name) and _is_process(n, name)
    return f"{name} · {'process unit' if process else KIND_WORDS.get(cls, 'model item')}"


def _text(value: Any) -> str | None:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) or value in ("", "None") \
        else str(value)


def _bus_of(n, cls: str, name: str) -> str:
    """The component's bus (its own name for a Bus, the first end for a branch)."""
    if cls == "Bus":
        return name
    row = _static(n, cls).loc[name]
    return str(row["bus"] if "bus" in row.index else row["bus0"])


def _bus_attr(n, bus: str, attr: str) -> str | None:
    if bus not in n.buses.index or attr not in n.buses.columns:
        return None
    return _text(n.buses.at[bus, attr])


def _carrier(n, cls: str, name: str) -> str | None:
    """The component's own `carrier` column when set, else its bus's carrier."""
    df = _static(n, cls)
    own = _text(df.at[name, "carrier"]) if cls != "Bus" and "carrier" in df.columns else None
    return own or _bus_attr(n, _bus_of(n, cls, name), "carrier")


def _unit(n, cls: str, name: str) -> str:
    """The bus's `unit` when set, else PyPSA's own unit for the quantity (MW for power, MWh for a Store)."""
    return _bus_attr(n, _bus_of(n, cls, name), "unit") or ("MWh" if cls == "Store" else "MW")


def _capacity(n, cls: str, name: str) -> dict[str, Any]:
    unit = _unit(n, cls, name)
    if cls == "Load":
        if name in n.loads_t.p_set.columns:
            return quantity(None, unit, "time_varying_demand")
        return quantity(float(n.loads.at[name, "p_set"]), unit)
    attr = "e_nom" if cls == "Store" else "p_nom"
    return quantity(float(_static(n, cls).at[name, attr]), unit)


def _supported(n, cls: str, name: str) -> None:
    target = {"class": cls, "name": name}
    if cls not in ROLES:
        raise _refuse("unsupported_target", f"{_label(n, cls, name)}: only a load, process unit, inventory or "
                                            "equipment can be attached", target=target)
    if not _exists(n, cls, name):
        raise _refuse("unknown_target", f"{_label(n, cls, name)}: not in the model", target=target)
    if cls == "Link" and not _is_process(n, name):
        raise _refuse("unsupported_target", f"{_label(n, cls, name)}: only a process unit can be attached",
                      target=target)


def _fingerprint(n, cls: str, name: str) -> dict[str, Any]:
    """What Apply re-checks: the same instance (uid), class support, bus and carrier."""
    return {"class": cls, "name": name, "uid": component_identity.get(n, cls, name),
            "bus": _bus_of(n, cls, name), "carrier": _carrier(n, cls, name)}


def _revalidate(n, expect: dict[str, Any], minted: dict[tuple[str, str], str] | None = None,
                *, attachable: bool = True) -> str:
    """
    Re-run every check against the live network; mint and return the uid.
    `minted` holds uids already bound to this instance (earlier in the same
    Apply, or by a placement attempt whose commit failed), so a target several
    features share stays one instance. Caller holds the network lock.
    """
    cls, name = expect["class"], expect["name"]
    label = _label(n, cls, name)
    if not _exists(n, cls, name):
        raise _changed(f"{label} is no longer in the model: review again")
    if attachable:
        try:
            _supported(n, cls, name)
        except SceneError:
            raise _changed(f"{label} can no longer be attached: review again") from None
    now = _fingerprint(n, cls, name)
    if now["uid"] != expect["uid"] and not (expect["uid"] is None and minted
                                            and now["uid"] == minted.get((cls, name))):
        raise _changed(f"{label} was replaced since you reviewed it: review again")
    if (now["bus"], now["carrier"]) != (expect["bus"], expect["carrier"]):
        raise _changed(f"{label} changed since you reviewed it: review again")
    uid = component_identity.uid_for(n, cls, name)
    if minted is not None:
        minted[(cls, name)] = uid
    return uid


# ── groups and allocation ───────────────────────────────────────────────────

def _features(scene: dict[str, Any], groups: list[dict[str, Any]]) -> set[str]:
    return ({b["id"] for b in scene.get("buildings") or []} | {g["id"] for g in groups}
            | set((scene.get("site") or {}).get("placements") or {}))


def _check_groups(scene: dict[str, Any], groups: Any) -> list[dict[str, Any]]:
    if not isinstance(groups, list) or len(groups) > MAX_GROUPS:
        raise _refuse("invalid_groups", f"Groups: send a list of at most {MAX_GROUPS}")
    out, ids = [], set()
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str) or not g["id"] \
                or not isinstance(g.get("members"), list) or len(g["members"]) > MAX_MEMBERS \
                or not all(isinstance(m, str) for m in g["members"]):
            raise _refuse("invalid_groups", "Each group needs an id and a list of member ids")
        if g["id"] in ids:
            raise _refuse("duplicate", f"Group {g['id']} is listed twice")
        ids.add(g["id"])
        out.append({"id": g["id"], "label": str(g.get("label") or g["id"]), "members": list(g["members"])})
    known = _features(scene, out)
    for g in out:
        for m in g["members"]:
            if m not in known:
                raise _refuse("orphan_member", f"Group {g['label']}: {m} is not a building, group or placed item",
                              group=g["id"], member=m)
    children = {g["id"]: [m for m in g["members"] if m in ids] for g in out}
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> None:
        state[node] = 1
        for child in children[node]:
            if state.get(child) == 1:
                cycle = path[path.index(child):] if child in path else [child]
                raise _refuse("cycle", f"Groups contain each other: {' > '.join(cycle + [child])}", path=cycle)
            if state.get(child) is None:
                visit(child, path + [child])
        state[node] = 2

    for gid in children:
        if state.get(gid) is None:
            visit(gid, [gid])
    return out


def _weight(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise _refuse("invalid_allocation", "Each share must be a number above 0")
    return float(value)


def _check_allocation(rows: list[dict[str, Any]]) -> None:
    """Per target, every feature has a share or none does; shares total 1 within `WEIGHT_TOLERANCE`."""
    by_target: dict[tuple, list] = {}
    for r in rows:
        by_target.setdefault(r["key"], []).append(r)
    for group in by_target.values():
        weights = [r.get("weight") for r in group]
        given = [w for w in weights if w is not None]
        if not given:
            continue
        label = group[0]["label"]
        if len(given) != len(weights):
            raise _refuse("invalid_allocation", f"{label}: give every building a share, or none")
        if abs(sum(given) - 1.0) > WEIGHT_TOLERANCE:
            raise _refuse("invalid_allocation", f"{label}: shares total {sum(given) * 100:g} %, not 100 %",
                          total=sum(given))


def _rows_of(n, associations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allocation rows for stored associations that have a target."""
    return [{"key": (a["class"], a["uid"]), "feature": a["feature"], "weight": a.get("weight"),
             "label": _label(n, a["class"], a.get("name_at_bind") or "")}
            for a in associations if a.get("uid")]


# ── bind_targets ────────────────────────────────────────────────────────────

def _resolve(n, target: Any) -> tuple[str, str | None, str | None]:
    """(class, name, carrier asked) for a target spec; name None means no such carrier (unavailable)."""
    if not isinstance(target, dict) or not isinstance(target.get("class"), str):
        raise _refuse("invalid_target", "Each target needs a kind and a name or a carrier")
    cls = target["class"]
    if isinstance(target.get("name"), str):
        _supported(n, cls, target["name"])
        return cls, target["name"], None
    carrier = target.get("carrier")
    if cls != "Load" or not isinstance(carrier, str):
        raise _refuse("invalid_target", "Only a load can be chosen by its carrier; otherwise name the item")
    candidates = sorted(name for name in n.loads.index if _carrier(n, "Load", name) == carrier)
    if not candidates:
        return cls, None, carrier
    if len(candidates) > 1:
        raise _refuse("ambiguous_target", f"Several {carrier} loads: choose one", candidates=candidates,
                      carrier=carrier)
    return cls, candidates[0], carrier


def _electric(carrier: str | None) -> bool:
    return (carrier or "").lower() in ELECTRIC_CARRIERS


def _findings(n, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An electricity Load and a process unit drawing electricity, on one feature, may count that demand twice."""
    out = []
    for proc in (r for r in rows if r["class"] == "Link" and r["name"]):
        link = n.links.loc[proc["name"]]
        feeds = [str(link["bus0"])] + [str(link[f"bus{i}"]) for i in range(2, 10)
                                       if _text(link.get(f"bus{i}")) and float(link.get(f"efficiency{i}", 1.0)) < 0]
        if not any(_electric(_bus_attr(n, bus, "carrier")) for bus in feeds):
            continue
        for load in (r for r in rows if r["class"] == "Load" and r["name"] and r["feature"] == proc["feature"]):
            if _electric(_carrier(n, "Load", load["name"])):
                out.append({"code": "double_counted_demand", "severity": "warn", "feature": proc["feature"],
                            "objects": [f"Link:{proc['name']}", f"Load:{load['name']}"],
                            "message": f"{_label(n, 'Load', load['name'])} and "
                                       f"{_label(n, 'Link', proc['name'])} both draw electricity here: "
                                       "this demand may be counted twice"})
    return out


def _by_id(scene: dict[str, Any], aid: Any) -> dict[str, Any]:
    for a in scene.get("associations") or []:
        if a["id"] == aid:
            return a
    raise _refuse("unknown_association", "That attachment no longer exists: reload")


def prepare_bind(scene: dict[str, Any], n, inputs: dict[str, Any],
                 _recorded: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(plan kept server-side, preview). Caller holds the network lock. Never writes or mints."""
    groups = _check_groups(scene, inputs.get("groups", []))
    raw = inputs.get("associations", [])
    if not isinstance(raw, list) or len(raw) > MAX_ASSOCIATIONS:
        raise _refuse("invalid_associations", f"Attachments: send a list of at most {MAX_ASSOCIATIONS}")
    known = _features(scene, groups)
    rows, plan, seen = [], [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise _refuse("invalid_associations", "Each attachment is an object")
        if set(item) == {"id"}:                       # keep an existing association as it is
            kept = _by_id(scene, item["id"])
            rows.append({"key": (kept["class"], kept["uid"]) if kept.get("uid") else None,
                         "feature": kept["feature"], "class": kept["class"], "name": None,
                         "carrier": kept.get("carrier"), "weight": kept.get("weight"), "status": "kept",
                         "label": _label(n, kept["class"], kept.get("name_at_bind") or "")})
            plan.append({"keep": copy.deepcopy(kept)})
            continue
        feature = item.get("feature")
        if feature not in known:
            raise _refuse("orphan_member", f"{feature} is not a building, group or placed item", member=feature)
        cls, name, carrier = _resolve(n, item.get("target"))
        weight = _weight(item.get("weight"))
        if name is None:
            if (feature, ("carrier", carrier)) in seen:
                raise _refuse("duplicate", f"A {carrier} load is asked for {feature} twice")
            seen.add((feature, ("carrier", carrier)))
            rows.append({"key": None, "feature": feature, "class": cls, "name": None, "carrier": carrier,
                         "status": "unavailable", "reason": f"No {carrier} load in the model", "weight": None})
            plan.append({"unavailable": {"feature": feature, "carrier": carrier}})
            continue
        expect = _fingerprint(n, cls, name)
        key = (cls, expect["uid"] or f"name:{name}")
        if (feature, key) in seen:
            raise _refuse("duplicate", f"{_label(n, cls, name)} is attached to {feature} twice")
        seen.add((feature, key))
        rows.append({"key": key, "feature": feature, "class": cls, "name": name, "weight": weight,
                     "label": _label(n, cls, name)})
        plan.append({"new": {"feature": feature, "expect": expect, "weight": weight, "role": ROLES[cls]}})
    _check_allocation([r for r in rows if r["key"] is not None])
    preview = {
        "associations": [
            {"feature": r["feature"], "class": r["class"], "name": r["name"],
             "carrier": r.get("carrier") if r["name"] is None else _carrier(n, r["class"], r["name"]),
             "unit": None if r["name"] is None else _unit(n, r["class"], r["name"]),
             "role": ROLES.get(r["class"]), "weight": r["weight"],
             "status": r.get("status") or "new", "reason": r.get("reason")}
            for r in rows],
        "groups": groups,
        "findings": _findings(n, [r for r in rows if r["name"]]),
    }
    return {"plan": plan, "groups": groups}, preview


def apply_bind(_scene: dict[str, Any], n, stored: dict[str, Any],
               _recorded: dict[str, str]) -> tuple[dict[str, Any], dict[str, str | None]]:
    """The changes to commit. Caller holds the network lock, then the scene mutex."""
    out, minted = [], {}
    for step in stored["plan"]:
        if "keep" in step:
            out.append(copy.deepcopy(step["keep"]))
        elif "unavailable" in step:
            gap = step["unavailable"]
            out.append({"id": f"a-{uuid.uuid4().hex[:12]}", "feature": gap["feature"], "class": "Load",
                        "uid": None, "name_at_bind": None, "carrier": gap["carrier"], "role": "load"})
        else:
            new = step["new"]
            uid = _revalidate(n, new["expect"], minted)
            row = {"id": f"a-{uuid.uuid4().hex[:12]}", "feature": new["feature"], "class": new["expect"]["class"],
                   "uid": uid, "name_at_bind": new["expect"]["name"], "role": new["role"]}
            if new["weight"] is not None:
                row["weight"] = new["weight"]
            out.append(row)
    return {"associations": out, "groups": copy.deepcopy(stored["groups"])}, {}


# ── rebind (D06 follow-up: explicit, confirmed, never by name) ──────────────

def prepare_rebind(scene: dict[str, Any], n, inputs: dict[str, Any],
                   _recorded: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-attach one association or one placement to the component the user chose."""
    if inputs.get("confirmed") is not True:
        raise _refuse("confirmation_required", "Attach: choose the item and confirm")
    target = inputs.get("target")
    if not isinstance(target, dict) or not isinstance(target.get("name"), str):
        raise _refuse("invalid_target", "Attach: choose the item")
    if ("association" in inputs) == ("placement" in inputs):
        raise _refuse("invalid_target", "Attach: name one attachment or one placed item")
    if "placement" in inputs:
        return _prepare_rebind_placement(scene, n, inputs["placement"], target)
    assoc = _by_id(scene, inputs["association"])
    if target.get("class") != assoc["class"]:
        raise _refuse("unsupported_target", f"Attach: choose a {KIND_WORDS.get(assoc['class'], 'model item')}")
    _supported(n, assoc["class"], target["name"])
    expect = _fingerprint(n, assoc["class"], target["name"])
    # The moved share must still leave every target's shares at 100 %.
    after = [dict(a, uid=expect["uid"] or f"name:{target['name']}", name_at_bind=target["name"])
             if a["id"] == assoc["id"] else a for a in scene.get("associations") or []]
    _check_allocation(_rows_of(n, after))
    preview = {"association": assoc["id"], "feature": assoc["feature"], "class": assoc["class"],
               "name": target["name"], "was": assoc.get("name_at_bind"), "findings": []}
    return {"association": assoc["id"], "expect": expect}, preview


def _prepare_rebind_placement(scene: dict[str, Any], n, key: Any, target: dict[str, Any]):
    placements = (scene.get("site") or {}).get("placements") or {}
    if key not in placements:
        raise _refuse("unknown_target", "That placed item no longer exists: reload")
    cls, was = _split(key)
    if target.get("class") != cls or not _exists(n, cls, target["name"]):
        raise _refuse("unknown_target", f"Attach: choose a {KIND_WORDS.get(cls, 'model item')} in the model")
    new_key = f"{cls}:{target['name']}"
    if new_key != key and new_key in placements:
        raise _refuse("duplicate", f"{_label(n, cls, target['name'])} is already placed")
    preview = {"placement": key, "class": cls, "name": target["name"], "was": was, "findings": []}
    return {"placement": key, "expect": _fingerprint(n, cls, target["name"])}, preview


def apply_rebind(scene: dict[str, Any], n, stored: dict[str, Any],
                 _recorded: dict[str, str]) -> tuple[dict[str, Any], dict[str, str | None]]:
    if "placement" in stored:
        return _apply_rebind_placement(scene, n, stored)
    uid = _revalidate(n, stored["expect"])
    out = []
    for a in scene.get("associations") or []:
        a = copy.deepcopy(a)
        if a["id"] == stored["association"]:
            a.update(rebound_from=a["uid"], uid=uid, name_at_bind=stored["expect"]["name"])
            a.pop("carrier", None)
        out.append(a)
    if not any(a["id"] == stored["association"] for a in out):
        raise _changed("That attachment no longer exists: reload")
    _check_allocation(_rows_of(n, out))
    return {"associations": out}, {}


def _apply_rebind_placement(scene: dict[str, Any], n, stored: dict[str, Any]):
    key, expect = stored["placement"], stored["expect"]
    placements = copy.deepcopy((scene.get("site") or {}).get("placements") or {})
    if key not in placements:
        raise _changed("That placed item no longer exists: reload")
    uid = _revalidate(n, expect, attachable=False)
    new_key = f"{expect['class']}:{expect['name']}"
    if new_key != key and new_key in placements:
        raise _changed(f"{_label(n, expect['class'], expect['name'])} is already placed: reload")
    swap = lambda k: new_key if k == key else k  # noqa: E731
    changes = {"placements": {swap(k): v for k, v in placements.items()},
               "asset_footprints": {swap(k): v for k, v in (scene.get("asset_footprints") or {}).items()},
               "groups": [{**g, "members": [swap(m) for m in g["members"]]} for g in scene.get("groups") or []],
               "associations": [{**a, "feature": swap(a["feature"])} for a in scene.get("associations") or []]}
    bindings: dict[str, str | None] = {new_key: uid}
    if new_key != key:
        bindings[key] = None
    return changes, bindings


# ── place_existing (scene only) ─────────────────────────────────────────────

def _inside(pt: tuple[float, float], ring: list) -> bool:
    x, y, inside = pt[0], pt[1], False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i][:2], ring[(i + 1) % len(ring)][:2]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _outline(entry: dict[str, Any], footprint: dict[str, Any] | None) -> list[tuple[float, float]]:
    """The anchor point, then the item's #712 footprint corners where it has one."""
    centre = (entry["x"], entry["y"])
    if not footprint:
        return [centre]
    return [centre, *frames.rectangle(centre, footprint["width_m"], footprint["depth_m"], entry["heading"])]


def _overlaps(outline: list[tuple[float, float]], ring: list) -> bool:
    corners = outline[1:]
    return any(_inside(p, ring) for p in outline) or bool(corners) and any(_inside(tuple(v), corners) for v in ring)


def _stale(n, cls: str, name: str, recorded_uid: Any) -> bool:
    """The key's sites.json binding names an instance the live component of that name is not (D06)."""
    return isinstance(recorded_uid, str) and component_identity.get(n, cls, name) != recorded_uid


def _unbound_text(n, cls: str, name: str) -> str:
    return (f"{_label(n, cls, name)}: this placement belonged to an item that was deleted or replaced: "
            "attach it again first")


def prepare_place(scene: dict[str, Any], n, inputs: dict[str, Any],
                  recorded: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    from services.site_service import PLACEABLE_CLASSES

    placements = inputs.get("placements")
    if not isinstance(placements, dict) or not placements or len(placements) > MAX_ASSOCIATIONS:
        raise _refuse("invalid_placements", "Place: send the items to place")
    buildings = {b["id"]: b for b in scene.get("buildings") or []}
    footprints = scene.get("asset_footprints") or {}
    plan, findings = {}, []
    for key, p in placements.items():
        cls, name = _split(key) if isinstance(key, str) else ("", "")
        if cls not in PLACEABLE_CLASSES or not name:
            raise _refuse("unsupported_target", f"{key}: not a placeable model item")
        label = _label(n, cls, name)
        if not _exists(n, cls, name):
            raise _refuse("unknown_target", f"{label}: not in the model", target={"class": cls, "name": name})
        if _stale(n, cls, name, recorded.get(key)):
            raise _refuse("unbound_target", _unbound_text(n, cls, name), key=key)
        if not isinstance(p, dict) or not all(isinstance(p.get(f), (int, float)) and not isinstance(p.get(f), bool)
                                              and math.isfinite(p[f]) for f in ("x", "y", "heading")):
            raise _refuse("invalid_placements", f"{label}: x, y and heading must be numbers")
        on = p.get("on", "ground")
        entry = {"x": float(p["x"]), "y": float(p["y"]), "heading": float(p["heading"])}
        outline = _outline(entry, footprints.get(key))
        if on != "ground":
            bid = on[5:] if isinstance(on, str) and on.startswith("roof:") else None
            building = buildings.get(bid)
            if building is None:
                raise _refuse("orphan_member", f"{label}: no such building roof", member=on)
            carrier = (_carrier(n, cls, name) or "").lower() if cls == "Generator" else ""
            if not any(c in carrier for c in ROOF_CARRIERS):
                raise _refuse("not_roof_eligible", f"{label}: only solar panels go on a roof")
            if not building.get("height_m") or not all(_inside(pt, building["footprint"]) for pt in outline):
                raise _refuse("off_roof", f"{label}: not within the roof of building {bid}")
            entry["on"] = on
        else:
            for bid, b in sorted(buildings.items()):
                if _overlaps(outline, b["footprint"]):
                    findings.append({"code": "overlap", "severity": "warn", "objects": [key, bid],
                                     "message": f"{label} overlaps building {bid}"})
        plan[key] = {"entry": entry, "expect": _fingerprint(n, cls, name)}
    preview = {"placements": {k: v["entry"] for k, v in plan.items()}, "findings": findings}
    return {"plan": plan}, preview


def apply_place(scene: dict[str, Any], n, stored: dict[str, Any],
                recorded: dict[str, str]) -> tuple[dict[str, Any], dict[str, str | None]]:
    """
    (scene changes, D06 bindings {key: uid} for sites.json). A key bound to
    another instance is refused, never re-bound by name. A retry after a
    failed commit finds the uid its first attempt recorded: the same
    instance. Caller holds the network lock, then the scene mutex.
    """
    placements = copy.deepcopy((scene.get("site") or {}).get("placements") or {})
    bindings: dict[str, str | None] = {}
    for key, step in stored["plan"].items():
        cls, name = step["expect"]["class"], step["expect"]["name"]
        prior = recorded.get(key)
        if _stale(n, cls, name, prior):
            raise _changed(_unbound_text(n, cls, name), "unbound_target")
        bindings[key] = _revalidate(n, step["expect"], {(cls, name): prior} if isinstance(prior, str) else {},
                                    attachable=False)
        placements[key] = step["entry"]
    return {"placements": placements}, bindings


# ── read-back (scene GET; never writes) ─────────────────────────────────────

def read_back(scene: dict[str, Any], n) -> dict[str, Any]:
    """Each association with its live name and status, and one summary per target (capacity counted once)."""
    rows, targets = [], {}
    for a in scene.get("associations") or []:
        row = {**a, "name": None, "carrier": a.get("carrier"), "unit": None, "reason": None}
        row.setdefault("rebound_from", None)
        if not a.get("uid"):
            row.update(status="unavailable", reason=f"No {a.get('carrier')} load in the model")
        elif n is None:
            row["status"] = "not_checked"
        else:
            name = component_identity.name_of(n, a["class"], a["uid"])
            row["status"] = "bound" if name else "unbound"
            if name:
                row.update(name=name, carrier=_carrier(n, a["class"], name), unit=_unit(n, a["class"], name))
                targets.setdefault((a["class"], a["uid"]), {"class": a["class"], "name": name, "uid": a["uid"],
                                                           "rows": []})["rows"].append(a)
            else:
                row["reason"] = f"{_label(None, a['class'], a.get('name_at_bind') or '')} was deleted or replaced"
        rows.append(row)
    summary = []
    for (cls, _uid), t in targets.items():
        cap = _capacity(n, cls, t["name"])
        weights = [r.get("weight") for r in t["rows"]]
        if weights and all(w is not None for w in weights):
            shares = [{"feature": r["feature"], "weight": r["weight"],
                       "allocated": quantity(cap["value"] * r["weight"], cap["unit"]) if cap["available"]
                       else quantity(None, cap["unit"], cap["reason"])} for r in t["rows"]]
            allocation = {"basis": "explicit", "shares": shares}
        else:
            allocation = {"basis": "none", "shares": []}
        summary.append({"class": cls, "name": t["name"], "uid": t["uid"],
                        "features": sorted({r["feature"] for r in t["rows"]}),
                        "enclosures": len({r["feature"] for r in t["rows"]}), "capacity": cap,
                        "allocation": allocation})
    return {"associations": rows, "targets": summary}

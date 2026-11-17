"""
PLEXOS input XML reader (PLEXOS import, slice 1; plan `.scratch/project-io/plexos-mapping.md`).

A PLEXOS input file is a relational database dumped as flat tables under one
``<MasterDataSet xmlns="http://tempuri.org/MasterDataSet.xsd">`` root:
``t_class``/``t_collection``/``t_property``/``t_unit`` are the schema,
``t_object`` the objects, ``t_membership`` the links between them, ``t_data``
one property value per row, and ``t_band``/``t_date_from``/``t_date_to``/
``t_tag``/``t_text``/``t_memo_data`` qualify a data row.

This module only reads and resolves; it builds no network and imports no
engine package (pypsa is imported only in ``*_import.py``, engine cage A).
Everything is resolved by NAME, never by numeric id, because ids differ between
PLEXOS versions:

- an object is keyed by ``(class name, object name)`` - names are unique only
  within a class (a Node and a Generator may both be called "X");
- a collection is keyed by ``(parent class, child class, collection name)``.

Nothing is dropped silently. Every row of every table lands in exactly one of
``resolved`` or ``broken`` (``PlexosModel.accounting``), and a row whose value
cannot be read keeps the raw text and a reason instead of turning into 0.

Hostile input is refused: any DOCTYPE (and so any entity declaration or
external reference) is a ``PlexosRefused`` of kind ``"dtd"`` before a single
row is read, and the parser runs with entity resolution, DTD loading and
network access off, so no external DTD or entity file is ever opened. An
internal entity used in the root element's own attributes is expanded by
libxml2 before the root is seen; that expansion is bounded by libxml2's
amplification cap, and the file is still refused.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from lxml import etree

NAMESPACE = "http://tempuri.org/MasterDataSet.xsd"
ROOT_TAG = "MasterDataSet"

#: Tables without which the file cannot be read at all.
REQUIRED_TABLES = (
    "t_class",
    "t_collection",
    "t_property",
    "t_object",
    "t_membership",
    "t_data",
)

#: Tables that qualify a data row (one entry per row, keyed by data_id).
_DATA_ROW_TABLES = (
    "t_band",
    "t_date_from",
    "t_date_to",
    "t_text",
    "t_memo_data",
    "t_tag",
)

#: Versions whose file layout this reader was written against.
SUPPORTED_MAJOR_VERSIONS = range(7, 11)


class PlexosRefused(ValueError):
    """
    The file cannot be read as a PLEXOS model. ``kind`` names why:
    ``"not_xml"``, ``"dtd"``, ``"not_plexos"`` or ``"missing_table"``.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class PlexosObject:
    object_id: str
    cls: str
    name: str
    category: str | None
    description: str | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.cls, self.name)


@dataclass(frozen=True, slots=True)
class Membership:
    membership_id: str
    collection: tuple[str, str, str]  # (parent class, child class, collection name)
    parent: tuple[str, str]  # (class, name)
    child: tuple[str, str]


@dataclass(frozen=True, slots=True)
class Tag:
    """
    A data row tagged with an object: a Scenario (the row applies only when
    that scenario is on), a Data File (the value comes from that file) or a
    Variable (the value is combined with that variable by ``action``).
    """

    cls: str
    name: str
    action: str | None  # symbol from t_action, None when the file states none


@dataclass(slots=True)
class PropertyRow:
    data_id: str
    membership_id: str
    collection: tuple[str, str, str]
    parent: tuple[str, str]
    child: tuple[str, str]
    property: str
    unit: str | None
    raw_value: str | None
    value: float | None  # None when unreadable; see ``unreadable``
    unreadable: str | None = None
    band: int = 1
    date_from: str | None = None
    date_to: str | None = None
    tags: list[Tag] = field(default_factory=list)
    texts: list[tuple[str, str]] = field(default_factory=list)  # (class, text)
    memo: str | None = None

    @property
    def scenarios(self) -> list[str]:
        return [t.name for t in self.tags if t.cls == "Scenario"]


@dataclass(frozen=True, slots=True)
class Attribute:
    object: tuple[str, str]
    name: str
    unit: str | None
    raw_value: str | None
    value: float | None
    unreadable: str | None = None


@dataclass(frozen=True, slots=True)
class Broken:
    table: str
    row_id: str
    reason: str


@dataclass
class PlexosModel:
    version: str | None
    saved_with: str | None
    config: dict[str, str]
    objects: dict[tuple[str, str], PlexosObject]
    memberships: list[Membership]
    rows: list[PropertyRow]
    attributes: list[Attribute]
    broken: list[Broken]
    warnings: list[str]
    #: table -> {"in": n, "resolved": n, "broken": n, "not_read": n};
    #: in == resolved + broken + not_read. ``not_read`` is non-zero only for
    #: tables this reader does not interpret (reports, messages, ...), whose
    #: rows are counted and named in ``warnings``.
    accounting: dict[str, dict[str, int]]

    def objects_of(self, cls: str) -> list[PlexosObject]:
        return [o for o in self.objects.values() if o.cls == cls]


# --------------------------------------------------------------------- parsing


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_tables(source) -> dict[str, list[dict[str, str | None]]]:
    """Stream the file into ``{table: [row dict]}``, freeing each element."""
    context = etree.iterparse(
        source,
        events=("start", "end"),
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
    )
    tables: dict[str, list[dict[str, str | None]]] = {}
    depth = 0
    root = None
    try:
        for event, el in context:
            if event == "start":
                if root is None:
                    root = el
                    # Any DOCTYPE at all (internal subset, entity, SYSTEM or
                    # PUBLIC reference): PLEXOS never writes one.
                    if el.getroottree().docinfo.doctype:
                        raise PlexosRefused(
                            "dtd",
                            "The file declares a DTD; PLEXOS files never do, so it is refused.",
                        )
                    if _local(el.tag) != ROOT_TAG:
                        raise PlexosRefused(
                            "not_plexos",
                            f"The root element is <{_local(el.tag)}>, not <{ROOT_TAG}>: not a PLEXOS input file.",
                        )
                depth += 1
                continue
            depth -= 1
            if depth == 1:  # a row of a table
                row = {_local(c.tag): c.text for c in el if isinstance(c.tag, str)}
                tables.setdefault(_local(el.tag), []).append(row)
                el.clear()
                while el.getprevious() is not None:
                    del el.getparent()[0]
    except etree.XMLSyntaxError as exc:  # also an empty file: lxml finds no root
        raise PlexosRefused(
            "not_xml", f"The file is not well-formed XML: {exc}"
        ) from exc
    return tables


def _number(raw: str | None) -> tuple[float | None, str | None]:
    if raw is None or raw.strip() == "":
        return None, "no value"
    try:
        value = float(raw)
    except ValueError:
        return None, f"not a number: {raw!r}"
    if not math.isfinite(value):
        return None, f"not a finite number: {raw!r}"
    return value, None


def _version_warning(version: str | None) -> str | None:
    if version is None:
        return "The file does not state its PLEXOS version; it is read as PLEXOS 7-10."
    try:
        number = float(version)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        return f"PLEXOS version {version!r} is not a number; it is read as PLEXOS 7-10."
    major = int(number)
    if major not in SUPPORTED_MAJOR_VERSIONS:
        return f"PLEXOS version {version} is outside the versions this reader was checked against (7-10)."
    return None


# ------------------------------------------------------------------ resolution


class _Ledger:
    def __init__(self, tables):
        self.broken: list[Broken] = []
        self.accounting = {
            t: {"in": len(rows), "resolved": 0, "broken": 0}
            for t, rows in tables.items()
        }

    def ok(self, table):
        self.accounting[table]["resolved"] += 1

    def bad(self, table, row_id, reason):
        self.accounting[table]["broken"] += 1
        self.broken.append(Broken(table, str(row_id), reason))


def _index(tables, ledger, table, id_field):
    """
    Rows of ``table`` by id; a duplicate or missing id is broken, never
    silently overwritten. Returns {id: row}; does NOT mark rows resolved.
    """
    out = {}
    for row in tables.get(table, []):
        rid = row.get(id_field)
        if rid is None:
            ledger.bad(table, "?", f"row without {id_field}")
        elif rid in out:
            ledger.bad(table, rid, f"duplicate {id_field} {rid}")
        else:
            out[rid] = row
    return out


def _named(tables, ledger, table, id_field, name_field):
    """``{id: name}`` for a lookup table; a row without a name is broken."""
    out = {}
    for rid, row in _index(tables, ledger, table, id_field).items():
        if row.get(name_field):
            out[rid] = row[name_field]
            ledger.ok(table)
        else:
            ledger.bad(table, rid, f"{table} row without {name_field}")
    return out


def read_plexos_xml(source) -> PlexosModel:
    """Read a PLEXOS input XML (a path or a binary file object)."""
    tables = _parse_tables(source)
    missing = [t for t in REQUIRED_TABLES if t not in tables]
    if missing:
        raise PlexosRefused(
            "missing_table",
            f"The file has no {', '.join(missing)} table: not a complete PLEXOS model.",
        )

    ledger = _Ledger(tables)
    warnings: list[str] = []

    config = {}
    for i, r in enumerate(tables.get("t_config", [])):
        element = r.get("element")
        if not element:
            ledger.bad("t_config", f"row {i}", "setting without a name")
        elif element in config:
            ledger.bad("t_config", element, f"a second {element!r} setting")
        else:
            config[element] = r.get("value")
            ledger.ok("t_config")
    version = config.get("Version")
    if (w := _version_warning(version)) is not None:
        warnings.append(w)

    # schema tables
    class_name = _named(tables, ledger, "t_class", "class_id", "name")
    unit_name = _named(tables, ledger, "t_unit", "unit_id", "value")
    if "t_unit" not in tables:
        warnings.append("The file has no t_unit table; property units are unknown.")
    action_symbol = _named(tables, ledger, "t_action", "action_id", "action_symbol")

    collections = _index(tables, ledger, "t_collection", "collection_id")
    collection_key = {}
    for cid, row in collections.items():
        parent, child = (
            class_name.get(row.get("parent_class_id")),
            class_name.get(row.get("child_class_id")),
        )
        if parent is None or child is None or not row.get("name"):
            ledger.bad(
                "t_collection",
                cid,
                "collection with an unknown parent or child class, or no name",
            )
            continue
        collection_key[cid] = (parent, child, row["name"])
        ledger.ok("t_collection")

    properties = _index(tables, ledger, "t_property", "property_id")
    prop = {}
    for pid, row in properties.items():
        if row.get("collection_id") not in collection_key or not row.get("name"):
            ledger.bad(
                "t_property",
                pid,
                "property of an unknown collection, or without a name",
            )
            continue
        uid = row.get("unit_id")
        if uid is not None and uid not in unit_name and "t_unit" in tables:
            warnings.append(
                f"Property {row['name']!r} names unit id {uid}, which the file does not define; its unit is unknown."
            )
        prop[pid] = (row["name"], row["collection_id"], unit_name.get(uid))
        ledger.ok("t_property")

    # objects
    objects_by_id: dict[str, PlexosObject] = {}
    objects: dict[tuple[str, str], PlexosObject] = {}
    category_name = _named(tables, ledger, "t_category", "category_id", "name")
    for oid, row in _index(tables, ledger, "t_object", "object_id").items():
        cls = class_name.get(row.get("class_id"))
        if cls is None:
            ledger.bad(
                "t_object", oid, f"object of unknown class id {row.get('class_id')}"
            )
            continue
        if not row.get("name"):
            ledger.bad("t_object", oid, "object without a name")
            continue
        if (cls, row["name"]) in objects:
            ledger.bad("t_object", oid, f"a second {cls} named {row['name']!r}")
            continue
        cat_id = row.get("category_id")
        if cat_id is not None and cat_id not in category_name:
            warnings.append(
                f"{cls} {row['name']!r} names category id {cat_id}, which the file does not define; it has no category."
            )
        obj = PlexosObject(
            oid, cls, row["name"], category_name.get(cat_id), row.get("description")
        )
        objects_by_id[oid] = obj
        objects[obj.key] = obj
        ledger.ok("t_object")

    # memberships
    memberships: dict[str, Membership] = {}
    for mid, row in _index(tables, ledger, "t_membership", "membership_id").items():
        coll = collection_key.get(row.get("collection_id"))
        parent, child = (
            objects_by_id.get(row.get("parent_object_id")),
            objects_by_id.get(row.get("child_object_id")),
        )
        if coll is None:
            ledger.bad(
                "t_membership", mid, f"unknown collection id {row.get('collection_id')}"
            )
        elif parent is None or child is None:
            ledger.bad("t_membership", mid, "parent or child object does not exist")
        elif (parent.cls, child.cls) != coll[:2]:
            ledger.bad(
                "t_membership",
                mid,
                f"objects are {parent.cls}/{child.cls} but the collection is {coll[0]}/{coll[1]}",
            )
        else:
            memberships[mid] = Membership(mid, coll, parent.key, child.key)
            ledger.ok("t_membership")

    broken_membership = {
        b.row_id: b.reason
        for b in ledger.broken
        if b.table == "t_membership" and b.row_id not in memberships
    }

    # data rows
    rows: dict[str, PropertyRow] = {}
    for did, row in _index(tables, ledger, "t_data", "data_id").items():
        m = memberships.get(row.get("membership_id"))
        p = prop.get(row.get("property_id"))
        if m is None:
            mid = row.get("membership_id")
            why = broken_membership.get(mid)
            ledger.bad(
                "t_data",
                did,
                f"membership {mid} is broken ({why})"
                if why
                else f"membership {mid} does not exist",
            )
            continue
        if p is None:
            ledger.bad(
                "t_data", did, f"property {row.get('property_id')} does not exist"
            )
            continue
        pname, pcoll, punit = p
        if collection_key[pcoll] != m.collection:
            ledger.bad(
                "t_data",
                did,
                f"property {pname!r} belongs to another collection than its membership",
            )
            continue
        value, why = _number(row.get("value"))
        rows[did] = PropertyRow(
            data_id=did,
            membership_id=m.membership_id,
            collection=m.collection,
            parent=m.parent,
            child=m.child,
            property=pname,
            unit=punit,
            raw_value=row.get("value"),
            value=value,
            unreadable=why,
        )
        ledger.ok("t_data")

    # qualifiers of a data row
    def each_qualifier(table):
        for i, row in enumerate(tables.get(table, [])):
            did = row.get("data_id")
            target = rows.get(did)
            if target is None:
                ledger.bad(table, did or f"row {i}", f"data row {did} does not exist")
                continue
            yield i, row, target

    seen: dict[str, set] = {t: set() for t in _DATA_ROW_TABLES}

    def once(table, did, i):
        if did in seen[table]:
            ledger.bad(
                table, f"{did} (row {i})", f"a second {table} entry for data row {did}"
            )
            return False
        seen[table].add(did)
        return True

    for i, row, target in each_qualifier("t_band"):
        if not once("t_band", target.data_id, i):
            continue
        try:
            band = int(row.get("band_id") or "")
        except ValueError:
            ledger.bad(
                "t_band",
                target.data_id,
                f"band {row.get('band_id')!r} is not a whole number",
            )
            continue
        if band < 1:
            ledger.bad("t_band", target.data_id, f"band {band} is below 1")
            continue
        target.band = band
        ledger.ok("t_band")
    for table, attr in (("t_date_from", "date_from"), ("t_date_to", "date_to")):
        for i, row, target in each_qualifier(table):
            if not once(table, target.data_id, i):
                continue
            if not row.get("date"):
                ledger.bad(table, target.data_id, "no date")
                continue
            setattr(target, attr, row["date"])
            ledger.ok(table)
    for i, row, target in each_qualifier("t_memo_data"):
        if not once("t_memo_data", target.data_id, i):
            continue
        target.memo = row.get("value")
        ledger.ok("t_memo_data")
    # several texts per row are allowed, one per class
    for i, row, target in each_qualifier("t_text"):
        cls = class_name.get(row.get("class_id"))
        if cls is None:
            ledger.bad(
                "t_text",
                target.data_id,
                f"text of unknown class id {row.get('class_id')}",
            )
            continue
        if any(c == cls for c, _ in target.texts):
            ledger.bad(
                "t_text",
                f"{target.data_id} (row {i})",
                f"a second {cls} text for data row {target.data_id}",
            )
            continue
        target.texts.append((cls, row.get("value") or ""))
        ledger.ok("t_text")
    for i, row, target in each_qualifier("t_tag"):  # several tags per row are allowed
        obj = objects_by_id.get(row.get("object_id"))
        if obj is None:
            ledger.bad(
                "t_tag",
                f"{target.data_id} (row {i})",
                f"tag object {row.get('object_id')} does not exist",
            )
            continue
        aid = row.get("action_id")
        if aid is not None and aid not in action_symbol:
            ledger.bad(
                "t_tag", f"{target.data_id} (row {i})", f"unknown action id {aid}"
            )
            continue
        target.tags.append(
            Tag(obj.cls, obj.name, action_symbol.get(aid) if aid is not None else None)
        )
        ledger.ok("t_tag")

    for t in ("t_data", "t_membership", "t_object", *_DATA_ROW_TABLES):
        tables[t] = ()  # the rows are resolved; free the raw dicts

    # object attributes (Node latitude/longitude, ...)
    attr_def = {}
    for aid, row in _index(tables, ledger, "t_attribute", "attribute_id").items():
        if not row.get("name") or row.get("class_id") not in class_name:
            ledger.bad(
                "t_attribute", aid, "attribute without a name, or of an unknown class"
            )
            continue
        attr_def[aid] = (
            row.get("name"),
            unit_name.get(row.get("unit_id")),
            class_name.get(row.get("class_id")),
        )
        ledger.ok("t_attribute")
    attributes = []
    attribute_seen = set()
    for i, row in enumerate(tables.get("t_attribute_data", [])):
        obj, adef = (
            objects_by_id.get(row.get("object_id")),
            attr_def.get(row.get("attribute_id")),
        )
        rid = f"{row.get('object_id')}/{row.get('attribute_id')} (row {i})"
        if obj is None or adef is None:
            ledger.bad("t_attribute_data", rid, "object or attribute does not exist")
            continue
        if adef[2] != obj.cls:
            ledger.bad(
                "t_attribute_data", rid, f"attribute of {adef[2]} set on a {obj.cls}"
            )
            continue
        if (obj.key, adef[0]) in attribute_seen:
            ledger.bad(
                "t_attribute_data",
                rid,
                f"a second {adef[0]} value for {obj.cls} {obj.name!r}",
            )
            continue
        attribute_seen.add((obj.key, adef[0]))
        value, why = _number(row.get("value"))
        attributes.append(
            Attribute(obj.key, adef[0], adef[1], row.get("value"), value, why)
        )
        ledger.ok("t_attribute_data")

    # Tables this reader does not interpret (reports, messages, custom columns,
    # ...) are still counted, so the report can say they were seen.
    handled = (
        set(REQUIRED_TABLES)
        | set(_DATA_ROW_TABLES)
        | {
            "t_config",
            "t_unit",
            "t_action",
            "t_category",
            "t_attribute",
            "t_attribute_data",
        }
    )
    for t, a in ledger.accounting.items():
        a["not_read"] = 0
        if t not in handled:
            a["not_read"] = a["in"]
            warnings.append(
                f"Table {t} ({a['in']} rows) holds settings this reader does not interpret."
            )

    return PlexosModel(
        version=version,
        saved_with=config.get("Saved With"),
        config=config,
        objects=objects,
        memberships=list(memberships.values()),
        rows=list(rows.values()),
        attributes=attributes,
        broken=ledger.broken,
        warnings=warnings,
        accounting=ledger.accounting,
    )

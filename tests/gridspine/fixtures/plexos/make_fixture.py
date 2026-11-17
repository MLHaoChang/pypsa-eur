"""
Cut the Ireland fixture ``world2015_irl.xml`` out of PLEXOS-World 2015.

Source: "PLEXOS-World 2015 Gold V1.1.xml", Brinkerink & Deane, Harvard
Dataverse, doi:10.7910/DVN/CBYXBY (file id 4214857), CC0 ("This dataset is made
available under a Creative Commons CC0 license"). Saved with PLEXOS 8.300 R01.
The 130 MB source is not committed; download it and run

    python tests/gridspine/fixtures/plexos/make_fixture.py <source.xml>

The cut keeps every table row that touches node EU-IRL, which means:

- the node EU-IRL, and the far-end nodes of the lines that touch it;
- those lines, the generators and batteries on EU-IRL, and their fuels,
  head storages, emissions, region and zone;
- every Scenario, Model and Horizon, with the Model->Scenarios and
  Model->Horizon links (slice 3 resolves scenarios through them);
- the Data File and Variable objects that tag a kept data row;
- each kept data row's band, dates, tags, texts and memo; attributes of kept
  objects; the schema tables, with t_property/t_attribute/t_collection/t_category cut to the ids used.

The script is deliberately independent of ``gridspine.ingest.plexos_xml``, so
the counts it prints are a check on the reader, not a copy of it. They are
pinned in ``tests/gridspine/test_plexos_xml.py`` (``WORLD_IRL_COUNTS``).
"""

import sys
from collections import Counter

from lxml import etree

NS = "{http://tempuri.org/MasterDataSet.xsd}"
SCHEMA = ("t_config", "t_action", "t_class", "t_collection", "t_unit", "t_category")


def load(path):
    tables = {}
    parser_args = dict(
        resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True
    )
    for _, el in etree.iterparse(path, events=("end",), **parser_args):
        parent = el.getparent()
        if parent is not None and parent.getparent() is None:
            tables.setdefault(el.tag.replace(NS, ""), []).append(
                [(c.tag.replace(NS, ""), c.text) for c in el]
            )
            el.clear()
    return tables


def cut(t):
    g = lambda r, k: dict(r).get(k)  # noqa: E731
    cls = {g(r, "class_id"): g(r, "name") for r in t["t_class"]}
    obj = {g(r, "object_id"): r for r in t["t_object"]}
    ocls = {oid: cls[g(r, "class_id")] for oid, r in obj.items()}
    byname = {(ocls[oid], g(r, "name")): oid for oid, r in obj.items()}
    mem = t["t_membership"]
    pc = lambda m: (ocls[g(m, "parent_object_id")], ocls[g(m, "child_object_id")])  # noqa: E731

    irl = byname[("Node", "EU-IRL")]
    keep = {irl, byname[("System", "System")]}
    lines = {
        g(m, "parent_object_id")
        for m in mem
        if pc(m)[0] == "Line" and g(m, "child_object_id") == irl
    }
    keep |= lines
    keep |= {
        g(m, "child_object_id")
        for m in mem
        if g(m, "parent_object_id") in lines and pc(m)[1] == "Node"
    }
    units = {
        g(m, "parent_object_id")
        for m in mem
        if pc(m)[0] in ("Generator", "Battery")
        and pc(m)[1] == "Node"
        and g(m, "child_object_id") == irl
    }
    keep |= units
    keep |= {
        g(m, "child_object_id")
        for m in mem
        if g(m, "parent_object_id") in units and pc(m)[1] in ("Fuel", "Storage")
    }
    keep |= {
        g(m, "child_object_id")
        for m in mem
        if g(m, "parent_object_id") in keep
        and pc(m) in (("Node", "Region"), ("Node", "Zone"))
    }
    keep |= {oid for oid in obj if ocls[oid] in ("Scenario", "Model", "Horizon")}
    keep |= {
        g(m, "parent_object_id")
        for m in mem
        if pc(m)[0] == "Emission" and g(m, "child_object_id") in keep
    }

    def kept_memberships():
        return {
            g(m, "membership_id")
            for m in mem
            if g(m, "parent_object_id") in keep
            and g(m, "child_object_id") in keep
            and (pc(m)[0] != "Model" or pc(m)[1] in ("Scenario", "Horizon"))
        }

    mids = kept_memberships()
    data_ids = {g(d, "data_id") for d in t["t_data"] if g(d, "membership_id") in mids}
    # tag objects (Data File, Variable) of kept rows join the cut, with their own rows
    keep |= {
        g(r, "object_id") for r in t.get("t_tag", []) if g(r, "data_id") in data_ids
    }
    mids = kept_memberships()
    data = [d for d in t["t_data"] if g(d, "membership_id") in mids]
    data_ids = {g(d, "data_id") for d in data}
    props = {g(d, "property_id") for d in data}

    out = {k: t[k] for k in SCHEMA if k in t}
    out["t_property"] = [r for r in t["t_property"] if g(r, "property_id") in props]
    colls = {g(m, "collection_id") for m in mem if g(m, "membership_id") in mids}
    colls |= {g(r, "collection_id") for r in out["t_property"]}
    out["t_collection"] = [
        r for r in t["t_collection"] if g(r, "collection_id") in colls
    ]
    cats = {g(r, "category_id") for r in t["t_object"] if g(r, "object_id") in keep}
    out["t_category"] = [r for r in t["t_category"] if g(r, "category_id") in cats]
    out["t_object"] = [r for r in t["t_object"] if g(r, "object_id") in keep]
    out["t_membership"] = [m for m in mem if g(m, "membership_id") in mids]
    out["t_data"] = data
    for table in (
        "t_band",
        "t_date_from",
        "t_date_to",
        "t_tag",
        "t_text",
        "t_memo_data",
    ):
        out[table] = [r for r in t.get(table, []) if g(r, "data_id") in data_ids]
    out["t_attribute_data"] = [
        r for r in t.get("t_attribute_data", []) if g(r, "object_id") in keep
    ]
    used_attr = {g(r, "attribute_id") for r in out["t_attribute_data"]}
    out["t_attribute"] = [
        r for r in t.get("t_attribute", []) if g(r, "attribute_id") in used_attr
    ]
    return {k: v for k, v in out.items() if v}, Counter(ocls[o] for o in keep)


def write(tables, path):
    root = etree.Element("MasterDataSet", nsmap={None: NS[1:-1]})
    for table, rows in tables.items():
        for row in rows:
            el = etree.SubElement(root, NS + table)
            for k, v in row:
                etree.SubElement(el, NS + k).text = v
    tree = etree.ElementTree(root)
    header = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b"<!-- SPDX-FileCopyrightText: Brinkerink & Deane, PLEXOS-World 2015 Gold V1.1, doi:10.7910/DVN/CBYXBY -->\n"
        b"<!-- SPDX-License-Identifier: CC0-1.0 -->\n"
        b"<!-- Cut to node EU-IRL by make_fixture.py in this folder. -->\n"
    )
    with open(path, "wb") as f:
        f.write(header + etree.tostring(tree, pretty_print=True, encoding="utf-8"))


if __name__ == "__main__":
    tables, classes = cut(load(sys.argv[1]))
    write(tables, sys.argv[2] if len(sys.argv) > 2 else "world2015_irl.xml")
    print({k: len(v) for k, v in tables.items()})
    print(dict(sorted(classes.items())))

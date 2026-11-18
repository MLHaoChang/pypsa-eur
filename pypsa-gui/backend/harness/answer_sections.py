"""Bounded answer sections for a checked answer above 20 result slots (issue 10, S6a).

Pure: no network, no records. One answer holds at most MAX_SECTIONS sections of
at most SECTION_SLOTS slots each. `sectionise` orders slots by component type
(fixed order), entity id and metric order, and cuts them into sections; an
answer that fits one section has no heading. Headings are written here, from a
fixed plural map, never from a class name. `gate` matches each drafted section
against its own section's facts only; heading text is compared as text and its
numbers are never matched against facts.
"""

SECTION_SLOTS = 20
MAX_SECTIONS = 5
MAX_SLOTS = SECTION_SLOTS * MAX_SECTIONS
REFUSALS = frozenset({"too_many_values"})
# Fixed type order and display plurals; a type outside the map is refused.
PLURALS = {
    "Generator": "Generators",
    "Line": "Lines",
}
TYPE_ORDER = tuple(PLURALS)
# The plain singular a refusal names (UX 25): a Line is "one branch" in branch loading.
SINGULARS = {
    "Generator": "generator",
    "Line": "branch",
}


def refusal(count, component_class):
    """The plain answer sentence for a request above MAX_SLOTS values."""
    return (
        f"I can check up to {MAX_SLOTS} values in one answer and this needs {count}: "
        f"ask for fewer metrics or one {SINGULARS[component_class]} at a time."
    )


def admit(entities, metrics):
    """Raise before any calculation when entities × metrics exceeds MAX_SLOTS."""
    count = entities * metrics
    if count > MAX_SLOTS:
        raise ValueError("too_many_values", count)
    return count


def _heading(component_class, first, last, total):
    span = f"{first}" if first == last else f"{first}–{last}"
    return f"{PLURALS[component_class]} {span} of {total}"


def sectionise(slots, metric_order):
    """Sections of the slots: [{"heading": str | None, "slots": [...]}, ...].

    Each slot is a dict with `component_class`, `entity_id` and `metric`; each
    lands in exactly one section. A section never spans two component types.
    """
    if any(s["component_class"] not in PLURALS for s in slots):
        raise ValueError("unsupported_section_type")
    ordered = sorted(
        slots,
        key=lambda s: (
            TYPE_ORDER.index(s["component_class"]),
            s["entity_id"],
            list(metric_order).index(s["metric"]),
        ),
    )
    sections = []
    for component_class in TYPE_ORDER:
        group = [s for s in ordered if s["component_class"] == component_class]
        entities = list(dict.fromkeys(s["entity_id"] for s in group))
        for start in range(0, len(group), SECTION_SLOTS):
            chunk = group[start : start + SECTION_SLOTS]
            first = entities.index(chunk[0]["entity_id"]) + 1
            last = entities.index(chunk[-1]["entity_id"]) + 1
            sections.append(
                {
                    "heading": _heading(component_class, first, last, len(entities)),
                    "slots": chunk,
                }
            )
    if len(sections) > MAX_SECTIONS:
        raise ValueError("too_many_values", len(slots))
    if len(sections) == 1:
        sections[0]["heading"] = None
    return sections


def record_sections(record, metrics):
    """The sections of a population evidence record: its slots, in member-major
    order, joined to the recorded entity list and to each member's facts."""
    raw, metrics = record["raw"], list(metrics)
    names, slots = raw["entities"], record["slots"]
    if len(slots) != len(names) * len(metrics):
        raise ValueError("population_slots_differ")
    joined = []
    for k, slot in enumerate(slots):
        i, metric = divmod(k, len(metrics))
        if slot["metric"] != metrics[metric]:
            raise ValueError("population_slots_differ")
        item = {
            **slot,
            "entity_id": names[i],
            "component_class": raw["component_class"],
        }
        fact = next(
            (f for f in record["facts"] if f["path"] == f"/{i}/{slot['metric']}"), None
        )
        if fact is not None:
            item["fact"] = fact
        joined.append(item)
    return sectionise(joined, metrics)


def gate(drafted, sections, evidence_id):
    """Match each drafted section's blocks against that section's facts only.

    `drafted` is a list of (heading, blocks); each block has entity_id, metric,
    value, unit, formula_id and source (evidence_id, path, tool_use_id). Each
    section slot that is answered carries its `fact`. Returns the facts in
    published order; raises ValueError for any claim its section does not hold.
    """
    if len(drafted) != len(sections):
        raise ValueError("section_count_differs")
    published = []
    for (heading, blocks), section in zip(drafted, sections):
        if heading != section["heading"]:
            raise ValueError("section_heading_differs")
        facts = [s["fact"] for s in section["slots"] if "fact" in s]
        if len(blocks) != len(facts):
            raise ValueError("omitted or duplicate required slot")
        seen = set()
        for block in blocks:
            key = (block["entity_id"], block["metric"])
            fact = next(
                (f for f in facts if (f["entity_id"], f["metric"]) == key), None
            )
            if (
                fact is None
                or key in seen
                or block["source"].get("tool_use_id") is not None
                or block["source"].get("evidence_id") != evidence_id
                or block["source"].get("path") != fact["path"]
                or any(block[k] != fact[k] for k in ("value", "unit", "formula_id"))
            ):
                raise ValueError(
                    "computed claim differs from independent raw calculation"
                )
            seen.add(key)
            published.append((block["id"], fact))
    return published

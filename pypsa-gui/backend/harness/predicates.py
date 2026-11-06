"""Complete-population predicates (AA-03 S4, owner freeze AA03-FREEZE-1 item 4).

A draft may claim that all, or none, of a population meets a threshold, or name
the top k of it. The population is the set of entities one cited static_page
evidence record, read this turn, holds for one metric. The software decides
the truth and the members; the model's claim is only compared, never shown.
Every failure raises ValueError("predicate_<reason>"), which limits the draft.

static_page is the only evidence kind with per-entity facts of one class and
metric: every other kind (one input, one series, one aggregate, one run, one
run section, one run comparison, a scoped result) is refused (GK B C10).
Labels never feed truth: only the certified values are compared.
"""

import operator

MAX_ENTRIES = 8
MAX_K = 20
# A page is a few dozen rows (chat_tools.MAX_PAGE_CHARS); ties beyond this
# many members are refused rather than truncated.
MAX_MEMBERS = 100
COMPARATORS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}
# quantifier -> the optional fields it takes; every other one must be absent.
QUANTIFIERS = {
    "all": ("comparator", "threshold", "holds"),
    "none": ("comparator", "threshold", "holds"),
    "top_k": ("k", "members"),
}
OPTIONAL = ("comparator", "threshold", "holds", "k", "members")
SCOPE_NOTE = (
    "configured static input, transient components excluded, "
    "time-series overrides not checked"
)


def _refuse(reason):
    raise ValueError("predicate_" + reason)


def population(record, metric):
    """{entity_id: value} of `metric` on one complete static_page record (GK B C1)."""
    scope = record["scope"]
    if scope.get("kind") != "static_page":
        _refuse("not_a_population")
    if scope["offset"] != 0:
        _refuse("not_first_page")
    if scope["has_more"] is not False:
        _refuse("has_more")
    if scope["returned"] != scope["total_count"]:
        _refuse("short_page")
    facts = [f for f in record["facts"] if f["metric"] == metric]
    if not facts and scope["returned"]:
        _refuse("unknown_metric")
    values = {f["entity_id"]: f for f in facts}
    # A masked or unreadable row has no certified fact: the population is short.
    if len(values) != len(facts) or len(values) != scope["returned"]:
        _refuse("member_not_certified")
    if not values:
        # "All of nothing" never reads true (GK B C2).
        _refuse("empty_population")
    return values


def _truth(entry, values):
    compare = COMPARATORS.get(entry.comparator)
    if compare is None:
        _refuse("unknown_comparator")
    hits = sum(compare(v, entry.threshold) for v in values)
    return hits == len(values) if entry.quantifier == "all" else hits == 0


def _top(entry, facts):
    if not 1 <= entry.k <= MAX_K:
        _refuse("k_out_of_range")
    if entry.k > len(facts):
        # Refused, not clamped (GK B (28) C6 correction).
        _refuse("k_exceeds_population")
    ranked = sorted(facts.values(), key=lambda f: (-f["value"], f["entity_id"]))
    boundary = ranked[entry.k - 1]["value"]
    # Every tie at the boundary is in; entity_id only orders the listing.
    members = [f["entity_id"] for f in ranked if f["value"] >= boundary]
    if len(members) > MAX_MEMBERS:
        _refuse("too_many_members")
    claimed = entry.members
    if len(set(claimed)) != len(claimed):
        _refuse("duplicate_member")
    if not set(claimed) <= set(facts):
        _refuse("unknown_member")
    if set(claimed) != set(members):
        _refuse("members_mismatch")
    return members


def check(entries, resolve, taken=()):
    """Check each entry against the record `resolve(entry.source)` returns.

    `resolve` returns only a record this turn read and the store re-checked,
    else None. `taken` holds ids already used by blocks and derived entries.
    Returns the public entries with the software's truth or members.
    """
    if len(entries) > MAX_ENTRIES:
        _refuse("too_many")
    seen = set(taken)
    public = []
    for entry in entries:
        if entry.id in seen:
            _refuse("repeated_id")
        seen.add(entry.id)
        wanted = QUANTIFIERS.get(entry.quantifier)
        if wanted is None:
            _refuse("unknown_quantifier")
        if any((getattr(entry, name) is None) == (name in wanted) for name in OPTIONAL):
            _refuse("wrong_fields")
        source = entry.source
        if (source.tool_use_id is None) == (source.evidence_id is None):
            _refuse("one_source")
        record = resolve(source)
        if record is None:
            _refuse("source_not_read")
        facts = population(record, entry.metric)
        if any(f["unit"] != entry.unit for f in facts.values()):
            # Exactly the metric's unit, never converted (GK B C5).
            _refuse("unit_mismatch")
        common = {
            "id": entry.id,
            "quantifier": entry.quantifier,
            "evidence_id": record["evidence_id"],
            "component_class": record["scope"]["component_class"],
            "metric": entry.metric,
            "unit": entry.unit,
            "population": len(facts),
            "warnings": list(record["validity"]["warnings"]),
        }
        if entry.quantifier == "top_k":
            members = _top(entry, facts)
            public.append({**common, "k": entry.k, "members": members})
            continue
        holds = _truth(entry, [f["value"] for f in facts.values()])
        if entry.holds is not holds:
            _refuse("truth_mismatch")
        public.append(
            {
                **common,
                "comparator": entry.comparator,
                "threshold": entry.threshold,
                "holds": holds,
            }
        )
    return public


def line(entry, label):
    """The software's sentence; `label` quotes untrusted names."""
    cls, n, metric = entry["component_class"], entry["population"], entry["metric"]
    of = f"of all {n} {cls} rows (rows 1..{n} of {n}; {SCOPE_NOTE})"
    if entry["quantifier"] == "top_k":
        size = len(entry["members"])
        count = f"top {entry['k']}" + (
            f" ({size} with ties at the boundary)" if size != entry["k"] else ""
        )
        return (
            f"Predicate {entry['id']}: the {count} by {metric} {of}: "
            + ", ".join(label(m) for m in entry["members"])
            + "."
        )
    threshold = repr(entry["threshold"]).removesuffix(".0")
    which = "every one" if entry["quantifier"] == "all" else "none"
    return (
        f"Predicate {entry['id']}: {which} {of} has {metric} {entry['comparator']} "
        f"{threshold} {entry['unit']}: {'true' if entry['holds'] else 'false'}."
    )

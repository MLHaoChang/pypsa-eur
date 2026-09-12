"""
The study write-up, assembled from what was actually established.

`AdequacyReport` is deliberately narrow: the COPT screening and the sequential
MC are SIBLING payloads, not folded in, because each answers a question the
report does not ask and folding them would grow the one shape every consumer
parses (spec §4, recorded decision). That decision is right for the wire and
wrong for a client deliverable, where the whole point is to put the screening,
the proxy, the sampler and the firm-capacity convention side by side — and to
keep them distinguishable while doing it.

So this module fuses, and the fusion's entire job is to stop the fusion from
laundering anything:

* **Every section carries its own `engine` and `fidelity`**, READ from the
  payload where the engine states them rather than asserted here. A screening
  convolution and a sequential sampler must not end up as two numbers in one
  table with no label between them.
* **`required_disclosures`** are the sentences the prose must contain, derived
  from what is actually present — a met reserve margin is not a met
  reliability target, an unconverged MC mean is not a point value, a COPT
  figure is not comparable to a statutory standard.
* **`not_established`** is the negative space, stated. A report that silently
  omits "no Monte Carlo was run" reads as though LOLE was measured. The
  omission is the finding.
* **`evidence_gaps`** are the things that would undermine the whole document —
  a frozen demand profile, an island nothing can serve, a failure rate with no
  recorded source. A study resting on those is worthless, and the report says
  so itself rather than waiting to be asked.

It writes no prose. The caller narrates, and can only narrate what is here.
"""
from __future__ import annotations

# Section id → (what it is, what a reader must be told about its fidelity).
# The caveats are the routers' own sentences, condensed; they are the reason
# this module exists, so they live next to the assembly rather than in a
# prompt that a future edit can quietly drop.
_SECTION_CAVEATS: dict[str, str] = {
    "copt": (
        "screening only: an analytic capacity-outage convolution, "
        "thermal-only, storage-excluded, network-free, zero LP solves. NOT "
        "comparable to a statutory standard"
    ),
    "adequacy": (
        "engine 'lp_proxy': a deterministic LP proxy for the enforced target, "
        "not a probabilistic result"
    ),
    "reserve_margin": (
        "a firm-capacity CONVENTION justified by its derating factors. A met "
        "margin is NOT a met reliability target"
    ),
    "mc": (
        "the only probabilistic estimate here: LOLE/EUE are SAMPLED, so the "
        "interval and the convergence flag travel with the mean"
    ),
    "frontier": (
        "one full capacity-expansion solve per target — a cost-of-reliability "
        "curve, not a plan"
    ),
    "coupling_loop": (
        "a plan driven to the target on the ENERGY lever (ENS cap); targets "
        "are horizon-basis hours, not h/yr"
    ),
    "margin_loop": (
        "a plan driven to the target on the FIRM-CAPACITY lever; targets are "
        "horizon-basis hours, not h/yr"
    ),
    "fmea_sweep": (
        "deterministic contingency screening — class B link outages and any "
        "class-C scenarios, each one LP solve"
    ),
}

# What a section is FOR, used to say what is missing when it has no data.
_SECTION_QUESTION: dict[str, str] = {
    "copt": "a zero-solve screening estimate of LOLE/EUE",
    "adequacy": "whether the enforced reliability target was met",
    "reserve_margin": "whether the firm-capacity standard was met",
    "mc": "a probabilistic LOLE/EUE with a confidence interval",
    "frontier": "what reliability costs across a range of standards",
    "coupling_loop": "the cheapest ENS cap that meets the target",
    "margin_loop": "the cheapest reserve margin that meets the target",
    "fmea_sweep": "which contingencies drive the risk",
}

# Fidelity labels for the sections whose payload does not state them. The
# engines that DO state them are read, never overridden.
_ASSUMED_PROVENANCE: dict[str, tuple[str, str]] = {
    "reserve_margin": ("lp_proxy", "deterministic_scenario"),
    "frontier": ("lp_proxy", "deterministic_scenario"),
    "coupling_loop": ("mc", "sequential_mc"),
    "margin_loop": ("mc", "sequential_mc"),
    "fmea_sweep": ("lp_proxy", "deterministic_scenario"),
}

SECTION_ORDER = (
    "adequacy", "reserve_margin", "mc", "copt",
    "frontier", "coupling_loop", "margin_loop", "fmea_sweep",
)


def _provenance(section: str, payload: dict) -> dict:
    """Engine + fidelity, read from the payload when the engine states them."""
    engine = payload.get("engine")
    fidelity = payload.get("fidelity")
    if not engine or not fidelity:
        result = payload.get("result")
        if isinstance(result, dict):
            engine = engine or result.get("engine")
            fidelity = fidelity or result.get("fidelity")
    assumed = _ASSUMED_PROVENANCE.get(section)
    if (not engine or not fidelity) and assumed:
        engine, fidelity = engine or assumed[0], fidelity or assumed[1]
    return {"engine": engine, "fidelity": fidelity,
            "stated_by_engine": bool(payload.get("engine"))}


def _mc_disclosure(payload: dict) -> str | None:
    """
    The MC's own convergence, turned into a sentence the prose must carry.

    An unconverged mean presented as a point value is the single most
    quotable wrong number a reliability study can produce.
    """
    result = payload.get("result")
    metrics = (result or {}).get("metrics") if isinstance(result, dict) else None
    if not isinstance(metrics, dict):
        return None
    converged = metrics.get("converged")
    samples = metrics.get("n_samples")
    ci = metrics.get("lole_ci")
    if converged is False:
        return (
            f"the Monte-Carlo LOLE did NOT converge in {samples} samples — "
            f"report it as an interval ({ci}), never as a point value")
    if converged is True:
        return (
            f"the Monte-Carlo LOLE converged in {samples} samples; quote its "
            f"interval ({ci}) beside the mean")
    return None


def _sections(read) -> list[dict]:
    """One row per reliability surface, present or absent, always labelled."""
    out = []
    for section in SECTION_ORDER:
        payload = read(section)
        empty = (not isinstance(payload, dict)
                 or payload.get("status") == "no_data")
        row = {
            "id": section,
            "question": _SECTION_QUESTION[section],
            "caveat": _SECTION_CAVEATS[section],
            "status": "no_data" if empty else "ok",
            "source_tool": f"get_adequacy_results('{section}')",
        }
        if empty:
            row["reason"] = (payload or {}).get("message") if isinstance(
                payload, dict) else "unavailable"
        else:
            row.update(_provenance(section, payload))
            row["payload"] = payload
        out.append(row)
    return out


def _disclosures(sections: list[dict]) -> list[str]:
    """The sentences the narrative MUST contain, given what is present."""
    present = {s["id"] for s in sections if s["status"] == "ok"}
    out = [
        "Name the engine and its fidelity beside every number — this document "
        "puts a screening convolution, an LP proxy and a sampler on one page, "
        "and they are not interchangeable.",
    ]
    if "reserve_margin" in present:
        out.append(
            "A met reserve margin is NOT a met reliability target: it is a "
            "convention justified by its derating factors.")
    if "copt" in present:
        out.append(
            "The COPT figure is a screening estimate — thermal-only, "
            "storage-excluded, network-free — and must not be compared to a "
            "statutory standard.")
    if "adequacy" in present:
        out.append(
            "The target result is engine 'lp_proxy', a deterministic proxy "
            "rather than a probabilistic one.")
    if {"coupling_loop", "margin_loop"} & present:
        out.append(
            "Loop targets are HORIZON-basis hours, not h/yr — state which "
            "basis any quoted LOLE is on before comparing it to a standard.")
    for section in sections:
        if section["id"] == "mc" and section["status"] == "ok":
            note = _mc_disclosure(section["payload"])
            if note:
                out.append(note.capitalize() + ".")
    return out


def _not_established(sections: list[dict]) -> list[str]:
    """What this study did NOT answer. The omission is the finding."""
    return [
        f"{s['question']} — not established ({s['id']} was never run "
        f"in this session)"
        for s in sections if s["status"] == "no_data"
    ]


def _evidence_gaps(n, health: dict | None) -> list[dict]:
    """
    Findings that undermine the document itself rather than one section.

    A reliability study resting on a frozen demand profile, an island nothing
    can serve, or a failure rate nobody can source is not a weaker study — it
    is a different document, and the reader has to be told before the numbers.
    """
    gaps: list[dict] = []
    from services.timeseries_qa import check_timeseries_quality
    from services.topology_analyzer import analyse_topology

    for issue in check_timeseries_quality(n):
        gaps.append({
            "kind": "input_data", "code": issue.code,
            "subject": f"{issue.component_class} {issue.name}".strip(),
            "detail": issue.message,
        })

    topology = analyse_topology(n)
    for island in topology["islands"]:
        if island["verdict"] in ("no_supply", "under_capacity"):
            gaps.append({
                "kind": "topology", "code": f"island_{island['verdict']}",
                "subject": f"island {island['id']}",
                "detail": island["reason"],
            })

    if health:
        counts = health.get("counts", {})
        if counts.get("unsourced"):
            gaps.append({
                "kind": "provenance", "code": "outage_rate_unsourced",
                "subject": f"{counts['unsourced']} asset(s)",
                "detail": (
                    "carry a failure rate that overrides the carrier library "
                    "with no recorded source — every reliability number here "
                    "rests on them"),
            })
        if counts.get("drifted"):
            gaps.append({
                "kind": "provenance", "code": "outage_rate_drifted",
                "subject": f"{counts['drifted']} asset(s)",
                "detail": (
                    "have a recorded measurement that no longer matches the "
                    "value the engines read"),
            })
    return gaps


def build_study_report(n, read, *, campaign: dict | None = None,
                       health: dict | None = None) -> dict:
    """
    Assemble the write-up.

    `read(section)` returns one reliability surface — injected rather than
    imported so this module stays pure and the caller keeps ownership of the
    no_data mapping it already does.
    """
    sections = _sections(read)
    established = [s for s in sections if s["status"] == "ok"]
    return {
        "objective": (campaign or {}).get("objective"),
        "campaign": campaign,
        "sections": sections,
        "required_disclosures": _disclosures(sections),
        "not_established": _not_established(sections),
        "evidence_gaps": _evidence_gaps(n, health),
        "counts": {
            "sections_established": len(established),
            "sections_missing": len(sections) - len(established),
        },
        "writing_note": (
            "This is evidence, not prose. Narrate only from fields present "
            "here, carry every line of required_disclosures, and state "
            "not_established explicitly — a report that omits what it did not "
            "measure reads as though it measured it."
        ),
    }

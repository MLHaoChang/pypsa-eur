"""Grid-code profiles: the connection-point limits, each with its clause and a
provenance tag (increment 10; extracted profiles, plan C10).

A profile is a small table of limits (steady-state voltage bands by nominal
voltage, the reactive range a TSO may require, the rapid-voltage-change
limit). Every limit carries the clause it comes from and one of three tags:

``code``
    A number the regulation states.
``assumed``
    An engineering choice the code leaves open.
``extracted``
    A number the copilot read from an uploaded grid-code document, which no
    person has confirmed yet. It names the ``page`` it came from (1-based) and
    a verbatim ``quote``. ``confirm_limit`` turns it into ``code`` and keeps
    both, so a confirmed limit still shows where it was read.

The same rule as the unit templates applies: a report must be able to say
which rule it applied and how sure that rule is, so a limit without a clause
or tag is refused at load, not defaulted. A ``code`` limit may carry a page
and quote too; an ``assumed`` one may not, since it cites no text.

A profile that cites pages (any ``extracted`` limit, or a ``page`` on any
limit) names its source in ``document``: ``{sha256, filename, title}``. The
hash is of the uploaded file, which the project keeps under that hash.

A profile may carry these optional keys:

``campus_voltage``
    A design band (``v_min``, ``v_max``, clause, tag) for the buses inside
    the campus. Without it a study holds those buses to the ``voltage_bands``.
``q_range_ibr``
    A reactive CAPABILITY required of the on-site inverter-based resources,
    as a fraction of their rating at rated active power (IEEE 2800), with a
    ``reference`` saying what the fraction is of. Unlike ``q_range_demand``
    (a band on the site's exchange at the connection point) it is checked per
    unit, where the unit ratings are known.
``voltage_bands[].v_max_30min``
    A time-limited upper voltage above ``v_max`` (IEEE 2800 / PRC-029's 30
    minutes). Reported with the dynamic requirements, never enforced.
``rvc_limit_infrequent_pct``
    The infrequent tier of a two-tier rapid-voltage-change limit (e.g. 1 %
    frequent, 3 % infrequent); ``rvc_limit_pct`` is then the frequent tier.
    It may not be below ``rvc_limit_pct``.
``q_capability_pq``
    A reactive band at the connection point that depends on the active
    power: ``points`` ``[[p_pu, q_min_pu, q_max_pu], ...]``, interpolated
    linearly, with ``p_pu`` strictly ascending. Every value is per unit of
    the connection capacity (``p_ref``, the base of ``q_range_demand``).
    Signs are the study's at the connection point: ``p_pu`` is positive
    when the site imports active power, and a ``q`` is positive when the
    grid supplies reactive power into the site. A site that imports and
    exports states points on both sides of zero.
``pf_min_lagging``, ``pf_min_leading``
    Asymmetric power-factor limits at the connection point, each a value in
    (0, 1]. Lagging is reactive power drawn from the grid (``q`` > 0 above),
    leading is reactive power sent to it. ``q_range_demand`` stays as the
    symmetric band.
``thd_limit_pct``
    The total harmonic voltage distortion limit at the connection point, %
    of the fundamental (grid-to-process issue 17), a positive number.
``harmonic_limits_pct``
    Per-order harmonic voltage limits at the connection point:
    ``orders`` ``{h: % of the fundamental}``, h a whole number 2..50, each
    limit a positive number. The harmonic screen judges on whichever of
    the two is set.
``harmonic_resonance_clearance_h``
    The least distance, in harmonic orders, between a network resonance
    seen from the connection point and an order that counts (6k +- 1 and
    the units' spectra) (datacenter grid readiness issue 12, the harmonic
    impedance scan), a positive number.
``dynamic_requirements``
    What a steady-state study cannot show (ride-through, RoCoF, frequency
    response, pending rules): ``{id, requirement, value, clause, source,
    applies_to}`` each, ``applies_to`` one of ``ibr``, ``load``, ``facility``,
    and optionally a ``kind`` (``DYNAMIC_KINDS``) and a ``unit``.
    ``reported_requirements`` lists them with status ``reported``, as
    "needs an RMS study"; they are never passed and never counted in a verdict.
``notes``
    Text the report carries with the profile (scope, what is unverified).

Each of these is optional and has no default: a profile without one sets
no such limit, and a check against it says the grid code sets no limit,
with status ``unknown`` (``campus_compliance.NO_LIMIT``), never pass or fail.

Any other key is loaded and logged as a warning: a project or extracted
profile may carry extras, but a misspelt key would otherwise switch a check
off without a word (``PROFILE_KEYS``).

The shipped profiles live in one file, ``data/grid_codes.yaml``. A project
keeps its own as one YAML file per profile, named ``<profile id>.yaml``, in
a directory passed as ``extra_dirs``. A project profile is listed and loaded
next to the shipped ones, and may not take a shipped profile's name. A
profile id is lower-case letters, digits and ``_``, at most 64 characters
(``PROFILE_ID``); a name that is not one never reaches the disk, and a file
whose stem is not one is not a profile.

yaml/pandas only, like the rest of ``templates/``; never the unsafe full
``yaml.load``.
"""
import copy
import logging
import math
import re
from pathlib import Path

import yaml

from gridspine.schema.contracts import ContractError

_DEFAULT = Path(__file__).parent / "data" / "grid_codes.yaml"
SOURCES = frozenset({"code", "assumed", "extracted"})
_REQUIRED = ("title", "voltage_bands", "q_range_demand", "rvc_limit_pct")
#: A profile id: also a file stem in a project directory, hence so narrow.
PROFILE_ID = re.compile(r"[a-z0-9_]{1,64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DOCUMENT_KEYS = ("sha256", "filename", "title")
_SINGLE_LIMITS = ("q_range_demand", "rvc_limit_pct", "campus_voltage", "q_range_ibr", "rvc_limit_infrequent_pct",
                  "q_capability_pq", "pf_min_lagging", "pf_min_leading", "thd_limit_pct", "harmonic_limits_pct",
                  "harmonic_resonance_clearance_h")
#: Every key a profile may carry; any other is loaded with a warning.
PROFILE_KEYS = frozenset({"title", "voltage_bands", "q_range_demand", "rvc_limit_pct", "campus_voltage",
                          "q_range_ibr", "rvc_limit_infrequent_pct", "q_capability_pq", "pf_min_lagging",
                          "pf_min_leading", "thd_limit_pct", "harmonic_limits_pct", "harmonic_resonance_clearance_h",
                          "dynamic_requirements", "notes",
                          "document", "name"})
#: Every key a voltage band may carry.
BAND_KEYS = frozenset({"kv_min", "kv_max", "kv_max_inclusive", "v_min", "v_max", "v_max_30min",
                       "clause", "source", "page", "quote"})
_APPLIES_TO = ("ibr", "load", "facility")
_DYNAMIC_KEYS = ("id", "requirement", "value", "clause", "source", "applies_to")
#: What a dynamic requirement's optional ``kind`` may be.
DYNAMIC_KINDS = ("vrt", "post_fault_recovery", "dynamic_voltage_support", "rocof_withstand", "droop",
                 "fast_frequency_response", "pod")
_log = logging.getLogger(__name__)
_BAND_PATH = re.compile(r"voltage_bands\[(0|[1-9][0-9]*)\]")


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_limit(name, lim, where):
    if not isinstance(lim, dict):
        raise ContractError(f"{where}: {name} must be a mapping, got {type(lim).__name__}")
    for key in ("clause", "source"):
        if not lim.get(key):
            raise ContractError(f"{where}: {name} has no {key}; every limit states its clause and tag")
    if lim["source"] not in SOURCES:
        raise ContractError(
            f"{where}: {name} has unknown source {lim['source']!r}; allowed {sorted(SOURCES)}"
        )
    _check_citation(name, lim, where)


def _check_citation(name, lim, where):
    """``page`` and ``quote``: required on an extracted limit, allowed on a
    code one, refused on an assumed one; well-formed wherever they appear."""
    source = lim["source"]
    if source == "extracted":
        for key in ("page", "quote"):
            if key not in lim:
                raise ContractError(
                    f"{where}: {name} is extracted but has no {key}; an extracted limit names the page "
                    "it was read from and quotes it verbatim"
                )
    if source == "assumed" and ("page" in lim or "quote" in lim):
        raise ContractError(
            f"{where}: {name} is assumed but carries a page or quote; an assumed limit cites no text "
            "(tag it code or extracted, or drop the page and quote)"
        )
    if "page" in lim:
        page = lim["page"]
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ContractError(f"{where}: {name}.page must be a whole page number from 1, got {page!r}")
    if "quote" in lim:
        quote = lim["quote"]
        if not isinstance(quote, str) or not quote.strip():
            raise ContractError(f"{where}: {name}.quote must be non-empty text, got {quote!r}")


def _limits(p):
    """``(path, limit)`` for every limit of a profile, in report order."""
    bands = p.get("voltage_bands")
    for i, b in enumerate(bands if isinstance(bands, list) else []):
        yield f"voltage_bands[{i}]", b
    for key in _SINGLE_LIMITS:
        if key in p:
            yield key, p[key]


def _check_document(p, where):
    cites = [path for path, lim in _limits(p)
             if isinstance(lim, dict) and (lim.get("source") == "extracted" or "page" in lim)]
    if "document" not in p:
        if cites:
            raise ContractError(
                f"{where}: {cites[0]} cites a page, so the profile needs a document "
                "{sha256, filename, title} naming the text it was read from"
            )
        return
    doc = p["document"]
    if not isinstance(doc, dict):
        raise ContractError(f"{where}: document must be a mapping, got {type(doc).__name__}")
    unknown = sorted(set(doc) - set(_DOCUMENT_KEYS))
    if unknown:
        raise ContractError(f"{where}: document has unknown field(s) {unknown}; allowed {list(_DOCUMENT_KEYS)}")
    if not isinstance(doc.get("sha256"), str) or not _SHA256.fullmatch(doc["sha256"]):
        raise ContractError(f"{where}: document.sha256 must be 64 lower-case hex digits, got {doc.get('sha256')!r}")
    if not isinstance(doc.get("filename"), str) or not doc["filename"].strip():
        raise ContractError(f"{where}: document.filename must be non-empty text")
    if not isinstance(doc.get("title"), str):
        raise ContractError(f"{where}: document.title must be text")


def _check_profile(name, p):
    where = f"grid-code profile {name!r}"
    if not isinstance(p, dict):
        raise ContractError(f"{where} must be a mapping, got {type(p).__name__}")
    missing = [k for k in _REQUIRED if k not in p]
    if missing:
        raise ContractError(f"{where} is missing {missing}")
    for key in ("q_range_demand", "rvc_limit_pct", "q_range_ibr", "rvc_limit_infrequent_pct", "thd_limit_pct",
                "harmonic_resonance_clearance_h"):
        if key not in p:
            continue                                   # q_range_demand and rvc_limit_pct are required
        _check_limit(key, p[key], where)
        if not _is_number(p[key].get("value")) or p[key]["value"] <= 0:
            raise ContractError(f"{where}: {key} value must be a positive number, got {p[key].get('value')!r}")
    if "rvc_limit_infrequent_pct" in p and p["rvc_limit_infrequent_pct"]["value"] < p["rvc_limit_pct"]["value"]:
        raise ContractError(
            f"{where}: rvc_limit_infrequent_pct ({p['rvc_limit_infrequent_pct']['value']:g} %) is below "
            f"rvc_limit_pct ({p['rvc_limit_pct']['value']:g} %), the frequent tier"
        )
    for key in ("pf_min_lagging", "pf_min_leading"):
        if key in p:
            _check_limit(key, p[key], where)
            v = p[key].get("value")
            if not _is_number(v) or not 0 < v <= 1:
                raise ContractError(f"{where}: {key} value must be a power factor in (0, 1], got {v!r}")
    if "q_capability_pq" in p:
        _check_q_capability(p["q_capability_pq"], where)
    if "harmonic_limits_pct" in p:
        _check_harmonic_orders(p["harmonic_limits_pct"], where)
    bands = p["voltage_bands"]
    if not isinstance(bands, list) or not bands:
        raise ContractError(f"{where}: voltage_bands must be a non-empty list")
    for i, b in enumerate(bands):
        _check_limit(f"voltage_bands[{i}]", b, where)
        for key in ("kv_min", "kv_max", "v_min", "v_max"):
            if not _is_number(b.get(key)):
                raise ContractError(f"{where}: voltage_bands[{i}].{key} must be a number")
        if not (0 < b["v_min"] < 1 < b["v_max"]):
            raise ContractError(
                f"{where}: voltage_bands[{i}] needs v_min < 1 < v_max, got v_min={b['v_min']} v_max={b['v_max']}"
            )
        if not b["kv_min"] < b["kv_max"]:
            raise ContractError(f"{where}: voltage_bands[{i}] kv_min must be below kv_max")
        if "v_max_30min" in b and not (_is_number(b["v_max_30min"]) and b["v_max_30min"] > b["v_max"]):
            raise ContractError(
                f"{where}: voltage_bands[{i}].v_max_30min must be a number above v_max, got {b['v_max_30min']!r}"
            )
    if "campus_voltage" in p:
        _check_campus_voltage(p["campus_voltage"], where)
    if "dynamic_requirements" in p:
        _check_dynamic(p["dynamic_requirements"], where)
    if "notes" in p and not isinstance(p["notes"], str):
        raise ContractError(f"{where}: notes must be text, got {type(p['notes']).__name__}")
    unknown = sorted(set(p) - PROFILE_KEYS)
    unknown += sorted(f"voltage_bands[{i}].{k}" for i, b in enumerate(bands) for k in set(b) - BAND_KEYS)
    if unknown:
        _log.warning("%s carries unknown key(s) %s; they are loaded but no check reads them", where, unknown)
    ordered = sorted(bands, key=lambda b: b["kv_min"])
    for a, b in zip(ordered, ordered[1:]):
        if b["kv_min"] < a["kv_max"]:
            raise ContractError(
                f"{where}: voltage bands overlap ({a['kv_min']}-{a['kv_max']} kV and "
                f"{b['kv_min']}-{b['kv_max']} kV)"
            )
    _check_document(p, where)


def _check_campus_voltage(cv, where):
    _check_limit("campus_voltage", cv, where)
    for key in ("v_min", "v_max"):
        if not _is_number(cv.get(key)):
            raise ContractError(f"{where}: campus_voltage.{key} must be a number")
    if not 0 < cv["v_min"] < cv["v_max"]:
        raise ContractError(
            f"{where}: campus_voltage needs 0 < v_min < v_max, got v_min={cv['v_min']} v_max={cv['v_max']}"
        )


def _check_q_capability(lim, where):
    _check_limit("q_capability_pq", lim, where)
    points = lim.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise ContractError(f"{where}: q_capability_pq.points must be a list of at least two "
                            "[p_pu, q_min_pu, q_max_pu] points")
    for i, pt in enumerate(points):
        if not isinstance(pt, (list, tuple)) or len(pt) != 3 or not all(_is_number(x) for x in pt):
            raise ContractError(f"{where}: q_capability_pq.points[{i}] must be three finite numbers "
                                f"[p_pu, q_min_pu, q_max_pu], got {pt!r}")
        if pt[1] > pt[2]:
            raise ContractError(f"{where}: q_capability_pq.points[{i}] has q_min_pu {pt[1]:g} above q_max_pu {pt[2]:g}")
        if i and not pt[0] > points[i - 1][0]:
            raise ContractError(f"{where}: q_capability_pq.points[{i}] p_pu {pt[0]:g} does not follow "
                                f"{points[i - 1][0]:g}; p_pu must rise strictly, each value once")


def _check_harmonic_orders(lim, where):
    _check_limit("harmonic_limits_pct", lim, where)
    orders = lim.get("orders")
    if not isinstance(orders, dict) or not orders:
        raise ContractError(f"{where}: harmonic_limits_pct.orders must be a non-empty mapping of order to %")
    for h, v in orders.items():
        # A profile read from JSON has string keys: "5" is order 5.
        order = h if isinstance(h, int) and not isinstance(h, bool) else \
            int(h) if isinstance(h, str) and h.isdecimal() else None
        if order is None or not 2 <= order <= 50:
            raise ContractError(f"{where}: harmonic_limits_pct.orders has order {h!r}; an order is a whole number 2..50")
        if not _is_number(v) or v <= 0:
            raise ContractError(f"{where}: harmonic_limits_pct.orders[{h}] must be a positive number, got {v!r}")


def q_band_at(lim: dict, p_pu: float):
    """``(q_min_pu, q_max_pu)`` of a ``q_capability_pq`` limit at ``p_pu``,
    interpolated linearly between its points; None outside their p range."""
    points = lim["points"]
    if not points[0][0] <= p_pu <= points[-1][0]:
        return None
    for (p0, lo0, hi0), (p1, lo1, hi1) in zip(points, points[1:]):
        if p0 <= p_pu <= p1:
            t = (p_pu - p0) / (p1 - p0)
            return lo0 + t * (lo1 - lo0), hi0 + t * (hi1 - hi0)
    return None                                        # pragma: no cover - the range check above covers it


def _check_dynamic(rows, where):
    if not isinstance(rows, list):
        raise ContractError(f"{where}: dynamic_requirements must be a list, got {type(rows).__name__}")
    seen = set()
    for i, r in enumerate(rows):
        at = f"dynamic_requirements[{i}]"
        if not isinstance(r, dict):
            raise ContractError(f"{where}: {at} must be a mapping")
        for key in _DYNAMIC_KEYS:
            if not isinstance(r.get(key), str) or not r[key].strip():
                raise ContractError(f"{where}: {at} has no {key}; every dynamic requirement states "
                                    f"{', '.join(_DYNAMIC_KEYS)}")
        if r["source"] not in ("code", "assumed"):
            raise ContractError(f"{where}: {at} has source {r['source']!r}; a dynamic requirement is code or assumed")
        if r["applies_to"] not in _APPLIES_TO:
            raise ContractError(f"{where}: {at} applies_to {r['applies_to']!r}; allowed {list(_APPLIES_TO)}")
        if "kind" in r and r["kind"] not in DYNAMIC_KINDS:
            raise ContractError(f"{where}: {at} has kind {r['kind']!r}; allowed {list(DYNAMIC_KINDS)}")
        if "unit" in r and (not isinstance(r["unit"], str) or not r["unit"].strip()):
            raise ContractError(f"{where}: {at}.unit must be non-empty text, got {r['unit']!r}")
        if r["id"] in seen:
            raise ContractError(f"{where}: {at} has duplicate id {r['id']!r}")
        seen.add(r["id"])


def reported_requirements(profile: dict) -> list:
    """What the profile requires that a steady-state study cannot show, one
    row each with status ``reported`` (never ``pass``): its
    ``dynamic_requirements`` ("needs an RMS study"), then every band's
    time-limited ``v_max_30min`` ("dynamic study needed"). No verdict counts
    them, and nothing here reads the value, so no value can pass or fail."""
    out = [{**r, "status": "reported",
            "detail": f"{r['requirement']}: {r['value']}{' ' + r['unit'] if r.get('unit') else ''} (needs an RMS study)"}
           for r in profile.get("dynamic_requirements") or []]
    for b in profile.get("voltage_bands") or []:
        if "v_max_30min" in b:
            value = f"up to {b['v_max_30min']:.2f} pu for 30 min at {b['kv_min']:g}-{b['kv_max']:g} kV"
            out.append({"id": "v_max_30min", "requirement": "Operate above the continuous band for a limited time",
                        "value": value, "clause": b["clause"], "source": b["source"], "applies_to": "facility",
                        "status": "reported",
                        "detail": f"time-limited voltage {value}: reported, not enforced (dynamic study needed)"})
    return out


def validate_profile(name: str, profile: dict) -> dict:
    """The checks a profile passes at load, on a mapping from anywhere (a
    draft, an edit). Returns a validated copy with ``name`` set; the argument
    is left as it was. Raises ``ContractError`` naming the field."""
    p = copy.deepcopy(profile)
    if isinstance(p, dict):
        p.pop("name", None)
    _check_profile(name, p)
    p["name"] = name
    return p


def _shipped(path=None) -> dict:
    data = yaml.safe_load(Path(path or _DEFAULT).read_text())
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict):
        raise ContractError("the grid-code file has no 'profiles' mapping")
    return profiles


def _project_files(extra_dirs, shipped) -> dict:
    """``{profile id: file}`` across the project directories, refusing a name
    that shadows a shipped profile or that two directories both define."""
    found = {}
    for d in extra_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            name = f.stem
            if not f.is_file() or not PROFILE_ID.fullmatch(name):
                continue
            if name in shipped:
                raise ContractError(
                    f"project grid-code profile {name!r} ({f}) takes the name of a shipped profile; "
                    "rename it, since a shipped profile cannot be replaced"
                )
            if name in found:
                raise ContractError(f"grid-code profile {name!r} is defined more than once ({found[name]} and {f})")
            found[name] = f
    return found


def _read_project(name, f) -> dict:
    try:
        p = yaml.safe_load(f.read_text())
    except yaml.YAMLError as exc:
        raise ContractError(f"project grid-code profile {name!r} ({f}) is not valid YAML: {exc}") from exc
    if not isinstance(p, dict):
        raise ContractError(f"project grid-code profile {name!r} ({f}) must be a mapping of the profile's fields")
    return p


def load_grid_code(name: str, path=None, raw: bool = False, extra_dirs=()) -> dict:
    """One profile, validated: a shipped one from ``path`` (the shipped file
    by default), or a project one from ``extra_dirs``. ``raw`` returns an
    independent copy of the mapping exactly as written, which tests use to
    build broken variants."""
    profiles = _shipped(path)
    project = _project_files(extra_dirs, profiles)
    if name in profiles:
        profile = copy.deepcopy(profiles[name])
    elif isinstance(name, str) and PROFILE_ID.fullmatch(name) and name in project:
        profile = _read_project(name, project[name])
    else:
        raise ContractError(
            f"unknown grid-code profile {name!r}; known {sorted({*profiles, *project})}"
        )
    if raw:
        return profile
    return validate_profile(name, profile)


def band_for(profile: dict, kv: float) -> dict:
    """The steady-state voltage band for a bus of nominal voltage ``kv``."""
    for b in profile["voltage_bands"]:
        upper_ok = kv <= b["kv_max"] if b.get("kv_max_inclusive") else kv < b["kv_max"]
        if b["kv_min"] <= kv and upper_ok:
            return b
    raise ContractError(
        f"grid-code profile {profile.get('name', '?')!r} sets no voltage band for {kv:g} kV"
    )


def uncovered_kv_ranges(profile: dict) -> list:
    """The nominal voltages from 0 kV up to the profile's highest band that no
    band covers, as ``[(kv_from, kv_to, from_inclusive), ...]`` in ascending
    order. A range is ``[kv_from, kv_to)`` when ``from_inclusive`` and
    ``(kv_from, kv_to)`` when not. It never extends past the highest
    ``kv_max``, so a profile is not asked for bands above its top one.

    This follows ``band_for``'s rules: ``kv_min`` is inclusive and ``kv_max``
    exclusive unless ``kv_max_inclusive``. A gap always ends where a band
    starts, and that band covers its own ``kv_min``, so the end is exclusive.
    It reads the bands as they are and does not validate them; a profile with
    no bands has no top and so no ranges. The campus study looks a band up for
    every bus inside the campus, so a gap there fails the run; the backend
    checks this where a profile is published, since a partial profile is
    legitimate for the engine's own tests.
    """
    bands = profile.get("voltage_bands") or []
    reach, reach_covered = 0.0, False      # voltages below `reach` are covered; `reach` itself iff reach_covered
    gaps = []
    for b in sorted(bands, key=lambda b: b["kv_min"]):
        if b["kv_min"] > reach:
            gaps.append((float(reach), float(b["kv_min"]), not reach_covered))
        top, top_covered = b["kv_max"], bool(b.get("kv_max_inclusive"))
        if top > reach or (top == reach and top_covered):
            reach, reach_covered = top, top_covered
    return gaps


def list_grid_codes(path=None, extra_dirs=()) -> dict:
    """``{profile name: title}`` of every shipped profile, then every project
    profile in ``extra_dirs``. This is what a study chooses from."""
    profiles = _shipped(path)
    out = {name: str(p.get("title", name)) for name, p in profiles.items()}
    for name, f in _project_files(extra_dirs, profiles).items():
        out[name] = str(_read_project(name, f).get("title", name))
    return out


def unconfirmed(profile: dict) -> list:
    """The paths (``voltage_bands[0]``, ``q_range_demand``, ...) of every limit
    still tagged ``extracted``: read from a document, not yet confirmed."""
    return [path for path, lim in _limits(profile) if isinstance(lim, dict) and lim.get("source") == "extracted"]


def confirm_limit(profile: dict, limit_path: str) -> dict:
    """A copy of ``profile`` with the extracted limit at ``limit_path`` tagged
    ``code``: a person has checked it against the quoted page. The page and
    the quote stay. Only an extracted limit can be confirmed."""
    p = copy.deepcopy(profile)
    where = f"grid-code profile {p.get('name', '?')!r}"
    m = _BAND_PATH.fullmatch(limit_path) if isinstance(limit_path, str) else None
    if m:
        bands = p.get("voltage_bands") or []
        i = int(m.group(1))
        if i >= len(bands):
            raise ContractError(f"{where} has no limit {limit_path!r}; it has {len(bands)} voltage band(s)")
        lim = bands[i]
    elif limit_path in _SINGLE_LIMITS:
        if limit_path not in p:
            raise ContractError(f"{where} has no limit {limit_path!r}")
        lim = p[limit_path]
    else:
        raise ContractError(
            f"{where}: {limit_path!r} is not a limit; a limit is voltage_bands[i], "
            f"{', '.join(_SINGLE_LIMITS)}"
        )
    if lim.get("source") != "extracted":
        raise ContractError(
            f"{where}: {limit_path} is {lim.get('source')!r}, not extracted; only an extracted limit is confirmed"
        )
    lim["source"] = "code"
    return p

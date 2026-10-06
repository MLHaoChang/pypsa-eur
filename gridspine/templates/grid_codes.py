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

A profile may carry one optional key, ``campus_voltage``: a design band
(``v_min``, ``v_max``, clause, tag) for the buses inside the campus. Without
it a study holds those buses to the ``voltage_bands``.

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
_SINGLE_LIMITS = ("q_range_demand", "rvc_limit_pct", "campus_voltage")
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
    for key in ("q_range_demand", "rvc_limit_pct"):
        _check_limit(key, p[key], where)
        if not _is_number(p[key].get("value")) or p[key]["value"] <= 0:
            raise ContractError(f"{where}: {key} value must be a positive number, got {p[key].get('value')!r}")
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
    if "campus_voltage" in p:
        _check_campus_voltage(p["campus_voltage"], where)
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

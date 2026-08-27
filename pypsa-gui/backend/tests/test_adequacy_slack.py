"""
Pin the centralised slack-generator identification (`services/adequacy/slack.py`).

Design: docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md §4.4.
The VOLL slack used to be special-cased by inline `carrier == "load_shedding"` /
`name.startswith("__voll_")` tests scattered across the backend. Phase 1 adds a
second slack tier (`demand_response`), and any site still testing equality with
one string would silently let the new tier set marginal prices or appear as a
real generator. These tests pin two things:

1. the shared mask/helpers behave exactly like the defence-in-depth mask they
   replace (formerly inline in `ac_pf_service._strip_voll_slacks` — carrier OR
   name prefix, legacy `voll_slack` spellings included);
2. a source-level guard: no code under `services/` or `routers/` may test the
   slack convention inline again. The literals may exist ONLY in
   `services/adequacy/slack.py`.
"""
from __future__ import annotations

import pathlib
import re

import pandas as pd

from services.adequacy import slack


# ── the mask ───────────────────────────────────────────────────────────────

def _gens(rows: dict[str, str]) -> pd.DataFrame:
    """rows: {generator_name: carrier}"""
    return pd.DataFrame({"carrier": list(rows.values())}, index=list(rows.keys()))


def test_mask_selects_every_slack_spelling():
    df = _gens({
        "__voll_bus1": "load_shedding",      # current convention: prefix + carrier
        "renamed_slack": "load_shedding",    # carrier only (drifted name)
        "__voll_bus2": "gas",                # prefix only (drifted carrier)
        "legacy_gen": "voll_slack",          # pre-restore netcdf from an old build
        "voll_slack_bus3": "wind",           # legacy name prefix
        "ocgt1": "gas",                      # real generators must NOT match
        "load_shedder": "coal",              # name merely *containing* text ≠ prefix
    })
    mask = slack.slack_generator_mask(df)
    assert list(df.index[mask]) == [
        "__voll_bus1", "renamed_slack", "__voll_bus2", "legacy_gen", "voll_slack_bus3",
    ]


def test_mask_is_safe_on_empty_and_carrierless_frames():
    empty = pd.DataFrame(columns=["carrier"])
    assert not slack.slack_generator_mask(empty).any()
    # No carrier column at all → name-prefix matching still works.
    no_carrier = pd.DataFrame(index=["__voll_b", "gen1"])
    mask = slack.slack_generator_mask(no_carrier)
    assert list(no_carrier.index[mask]) == ["__voll_b"]


def test_is_slack_carrier():
    assert slack.is_slack_carrier("load_shedding")
    assert slack.is_slack_carrier("voll_slack")
    assert not slack.is_slack_carrier("gas")
    assert not slack.is_slack_carrier(None)
    assert not slack.is_slack_carrier(float("nan"))


def test_strip_slack_prefix_removes_prefix_only():
    assert slack.strip_slack_prefix("__voll_bus_eh1") == "bus_eh1"
    # A prefix, not a substring replace — interior occurrences stay put.
    assert slack.strip_slack_prefix("bus__voll_x") == "bus__voll_x"
    assert slack.strip_slack_prefix("plain_bus") == "plain_bus"


def test_creation_convention_matches_the_constants():
    """The solver creates slacks as f"{VOLL_SLACK_PREFIX}{bus}" with the
    involuntary carrier; if either constant changes, every consumer moves with
    it — but the netcdf/back-compat spellings must never silently change."""
    assert slack.VOLL_SLACK_PREFIX == "__voll_"
    assert slack.INVOLUNTARY_SLACK_CARRIER == "load_shedding"
    assert slack.INVOLUNTARY_SLACK_CARRIER in slack.SLACK_CARRIERS
    assert slack.VOLL_SLACK_PREFIX in slack.SLACK_NAME_PREFIXES


# ── the source guard ───────────────────────────────────────────────────────

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_SCAN_DIRS = (_BACKEND / "services", _BACKEND / "routers")
_ALLOWED = {_BACKEND / "services" / "adequacy" / "slack.py"}

# Inline slack tests that must not reappear outside slack.py. These patterns
# are code-shaped (equality / startswith / replace / f-string on the literal),
# so prose mentions like "__voll_<bus>" in comments stay legal.
_FORBIDDEN = (
    re.compile(r"""==\s*["']load_shedding["']"""),
    re.compile(r"""==\s*["']voll_slack["']"""),
    re.compile(r"""startswith\(\s*["']__voll_"""),
    re.compile(r"""startswith\(\s*["']voll_slack_"""),
    re.compile(r"""replace\(\s*["']__voll_"""),
    re.compile(r"""f["']__voll_"""),
)


def test_no_inline_slack_tests_outside_slack_py():
    offenders: list[str] = []
    for root in _SCAN_DIRS:
        for path in sorted(root.rglob("*.py")):
            if path in _ALLOWED or "__pycache__" in path.parts:
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                for pat in _FORBIDDEN:
                    if pat.search(line):
                        offenders.append(f"{path.relative_to(_BACKEND)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Inline slack-convention tests found outside services/adequacy/slack.py "
        "— use SLACK_CARRIERS / slack_generator_mask / is_slack_carrier / "
        "strip_slack_prefix instead:\n" + "\n".join(offenders)
    )

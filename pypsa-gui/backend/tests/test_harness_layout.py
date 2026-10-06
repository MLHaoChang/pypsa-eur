"""
The chat harness's structural guards (chat harness phase 0, 2026-10-05).

  * the moved modules are the SAME objects under their old names;
  * no provider word leaks above `harness/providers/`;
  * every frame the turn loop yields is named in `harness.events.FRAMES`;
  * every workflow and skill file loads and is well-formed, and every
    tool an active workflow names exists in the catalogue.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
HARNESS = BACKEND / "harness"


# ── 1. re-export moves ─────────────────────────────────────────────────────


def test_llm_provider_shim_is_the_protocol_module():
    import harness.protocol as protocol
    from services import llm_provider

    assert llm_provider is protocol
    assert llm_provider.LLMRequest is protocol.LLMRequest
    assert llm_provider.ProviderError is protocol.ProviderError
    assert llm_provider.EVENT_TYPES is protocol.EVENT_TYPES


def test_chat_tools_schema_shim_is_the_catalogue_module():
    import harness.catalogue as catalogue
    from services import chat_tools_schema

    assert chat_tools_schema is catalogue
    assert chat_tools_schema.TOOLS is catalogue.TOOLS
    assert chat_tools_schema.TOOL_ROUTES is catalogue.TOOL_ROUTES
    assert chat_tools_schema.safety_tier_for is catalogue.safety_tier_for


def test_chat_service_shim_is_the_loop_module():
    import harness.loop as loop
    from services import chat_service

    assert chat_service is loop
    assert chat_service.run_turn is loop.run_turn
    assert chat_service.ChatSession is loop.ChatSession
    assert chat_service.CONFIRMATION_TTL_SECONDS is loop.CONFIRMATION_TTL_SECONDS
    from harness import sse
    assert chat_service.sse_frame is sse.sse_frame


def test_a_monkeypatch_on_the_old_name_is_seen_through_the_new_one(monkeypatch):
    import harness.catalogue as catalogue
    from services import chat_tools_schema

    monkeypatch.setattr(chat_tools_schema, "TOOL_COUNT", -1)
    assert catalogue.TOOL_COUNT == -1


# ── 2. layering ────────────────────────────────────────────────────────────

_PROVIDER_WORDS = re.compile(r"\b(anthropic|openai|cache_control)\b", re.I)

# The loop's known provider-word code sites on the day it moved (issue 08).
LOOP_VENDOR_WORD_SITES = 7


def _vendor_word_code_sites(path: pathlib.Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            mod = getattr(node, "module", None) or ""
            if _PROVIDER_WORDS.search(mod) or any(_PROVIDER_WORDS.search(x) for x in names):
                n += 1
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str) \
                    and not isinstance(node, ast.Expr) and _PROVIDER_WORDS.search(child.value):
                n += 1
    return n


def test_no_provider_word_above_the_provider_layer():
    """
    The seam spec's rule, now a grep: nothing in `harness/` outside
    `harness/providers/` names a vendor, an SDK or a wire detail. Cache
    intent is the `stable` flag. The protocol module's docstring is allowed
    to SAY that "cache_control is an Anthropic word and lives in
    llm_anthropic only" — that sentence is the rule itself — so docstrings
    and comments are stripped before the scan and only code is checked.
    """
    offenders: list[str] = []
    for path in sorted(HARNESS.rglob("*.py")):
        if "providers" in path.relative_to(HARNESS).parts:
            continue
        if path.name == "loop.py":
            # The loop moved in whole (issue 08) carrying the seam spec's
            # KNOWN leaks — the wire branches for history portability, the
            # vision sub-call's client, the capability refusals. Pinned at
            # their count so they can only go down, into providers/.
            assert _vendor_word_code_sites(path) <= LOOP_VENDOR_WORD_SITES, (
                f"harness/loop.py gained a provider-word code site "
                f"(limit {LOOP_VENDOR_WORD_SITES}); push it into harness/providers/"
            )
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                mod = getattr(node, "module", None) or ""
                if _PROVIDER_WORDS.search(mod) or any(_PROVIDER_WORDS.search(n) for n in names):
                    offenders.append(f"{path.name}:{node.lineno} import")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # String constants that are docstrings are skipped below.
                pass
        # Code strings (dict keys, literals) — not docstrings — must not
        # carry a vendor word either. Walk expression statements apart.
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # a docstring / bare string statement
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str) \
                        and not isinstance(node, ast.Expr) \
                        and _PROVIDER_WORDS.search(child.value) \
                        and path.name != "catalogue.py":
                    offenders.append(f"{path.name}:{child.lineno} {child.value[:40]!r}")
    assert offenders == [], "\n".join(offenders)


def test_catalogue_names_no_vendor_in_code_outside_tool_descriptions():
    """
    `catalogue.py` is the one file where a vendor name is legitimate inside
    a TOOL DESCRIPTION (e.g. the `set_active_profile` tool explains the
    Anthropic-only PDF path to the model). It still must not import one.
    """
    tree = ast.parse((HARNESS / "catalogue.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not _PROVIDER_WORDS.search(node.module or ""), node.module
        if isinstance(node, ast.Import):
            for a in node.names:
                assert not _PROVIDER_WORDS.search(a.name), a.name


# ── 3. events ──────────────────────────────────────────────────────────────


def _yielded_frame_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Yield) and isinstance(node.value, ast.Tuple) and node.value.elts:
            first = node.value.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
    return names


def _harness_yielded_frames() -> set[str]:
    """Every frame name yielded anywhere in the harness (the loop, the
    confirmation gate, the stub loop, …) — the set the route can frame."""
    names: set[str] = set()
    for path in sorted(HARNESS.rglob("*.py")):
        if "providers" in path.relative_to(HARNESS).parts:
            continue
        names |= _yielded_frame_names(path.read_text(encoding="utf-8"))
    return names


def test_every_frame_the_loop_yields_is_in_the_vocabulary():
    from harness.events import FRAMES

    yielded = _harness_yielded_frames()
    assert yielded, "the scan found no yields — the loop moved; update the path"
    assert yielded <= FRAMES, sorted(yielded - FRAMES)


def test_the_vocabulary_has_no_frame_the_loop_never_yields():
    """Both directions: a frame nobody yields is sediment."""
    from harness.events import FRAMES

    assert FRAMES <= _harness_yielded_frames()


def test_the_tripwire_is_red_on_a_planted_frame():
    from harness.events import FRAMES

    planted = 'def g():\n    yield "not_a_frame", {}\n'
    assert not _yielded_frame_names(planted) <= FRAMES


# ── 4. workflows ───────────────────────────────────────────────────────────

_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]*)`")
# Tools the harness itself adds in phase 2 (issues 04–06); allowed in a
# workflow body before they exist in the catalogue.
_HARNESS_TOOLS = {"ask_user", "use_skill", "start_workflow", "advance_workflow"}


def test_every_workflow_loads():
    from harness import workflows

    reg = workflows.load_all()
    assert set(reg) >= {
        "open-project", "build-network", "import-data", "run-study",
        "explain-results", "improve-design", "hub-design", "investment-decision",
    }
    for wf in reg.values():
        assert wf.steps
        assert wf.opening_request
        for step in wf.steps:
            assert step.body, f"{wf.id}: step {step.id} has an empty body"


def test_active_workflows_name_only_tools_that_exist():
    from harness import workflows
    from harness.catalogue import TOOLS

    known = {t["name"] for t in TOOLS} | _HARNESS_TOOLS
    unknown: list[str] = []
    for wf in workflows.load_all().values():
        if wf.status != "active":
            continue
        text = wf.preamble + "\n" + "\n".join(s.body for s in wf.steps)
        for name in _BACKTICKED.findall(text):
            if "_" in name and name not in known:
                unknown.append(f"{wf.id}: `{name}`")
    assert unknown == [], "\n".join(unknown)


def test_hub_design_carries_the_guided_rules_as_its_preamble():
    """The Guided addendum (guided-mode spec §6.2) is the preamble of the
    `hub-design` workflow — the same sentences the loop appends today, so
    issue 06 can swap the mechanism without changing the words."""
    from harness import workflows

    wf = workflows.get("hub-design")
    assert wf.preamble.startswith("Guided mode is on.")
    assert "never apply a change the user has not asked for" in " ".join(wf.preamble.split())
    assert [s.id for s in wf.steps] == ["start", "site", "goal", "results", "improve"]
    # Owner decisions Q11/Q13: offered in Expert too, but the Guided rules
    # are bound to Guided mode, not to the workflow.
    assert wf.offered_in("expert") and wf.offered_in("guided")
    assert wf.preamble_for("guided") == wf.preamble
    assert wf.preamble_for("expert") == ""


@pytest.mark.parametrize("context,expect_present,expect_absent", [
    ("unbound", {"open-project"}, {"build-network", "hub-design"}),
    ("expert", {"build-network", "explain-results", "hub-design"}, {"open-project"}),
    ("guided", {"hub-design", "explain-results"}, {"open-project", "build-network"}),
])
def test_menu_per_context(context, expect_present, expect_absent):
    from harness import workflows

    ids = [wf.id for wf in workflows.menu(context)]
    assert expect_present <= set(ids)
    assert not (expect_absent & set(ids))
    assert "investment-decision" not in ids, "planned workflows are never offered"
    assert ids == sorted(ids, key=lambda i: (workflows.get(i).order, i))


def test_a_malformed_workflow_fails_at_load(tmp_path):
    from harness import workflows

    (tmp_path / "broken.md").write_text(
        "---\nid: broken\ntitle: x\nintent: y\nwhen: [expert]\norder: 1\n"
        "opening_request: go\nsteps:\n  - id: a\n    title: A\n    done_when: z\n---\n"
        "## Step: b\nwrong heading\n", encoding="utf-8",
    )
    with pytest.raises(workflows.WorkflowError, match="has no '## Step: a'"):
        workflows.load_all(tmp_path)


def test_preamble_when_must_be_within_when(tmp_path):
    from harness import workflows

    (tmp_path / "w.md").write_text(
        "---\nid: w\ntitle: x\nintent: y\nwhen: [expert]\npreamble_when: [guided]\norder: 1\n"
        "opening_request: go\nsteps:\n  - id: a\n    title: A\n    done_when: z\n---\n"
        "rules\n\n## Step: a\nbody\n", encoding="utf-8",
    )
    with pytest.raises(workflows.WorkflowError, match="preamble_when"):
        workflows.load_all(tmp_path)


# ── 5. skills ──────────────────────────────────────────────────────────────


def test_every_skill_loads_and_grill_exists():
    from harness import skills

    reg = skills.load_all()
    assert "grill" in reg
    grill = reg["grill"]
    assert "ask_user" in grill.body
    assert grill.description.startswith("Interview the user")
    block = skills.catalogue_block()
    assert "grill —" in block and grill.body not in block


def test_a_skill_whose_name_differs_from_its_folder_fails(tmp_path):
    from harness import skills

    d = tmp_path / "foo"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: bar\ndescription: d\n---\nbody\n", encoding="utf-8")
    with pytest.raises(skills.SkillError, match="must equal the folder name"):
        skills.load_all(tmp_path)


# ── 6. the splitting rule: a moved tunable is patched on its new module ───

# Tunables that left harness/loop.py for another harness module (issue 08).
# Patching them through the `chat_service` alias rebinds the loop's
# re-exported name only; the readers live in the new module and never see
# it — the patch silently no-ops. So the suite must patch the new home.
MOVED_TUNABLES = {
    "SESSION_MESSAGES_MAX": "harness.history",
    "ROTATE_BYTES": "harness.history",
    "MAX_TOOL_RESULT_CHARS_PER_TURN": "harness.results",
    "CONFIRMATION_TTL_SECONDS": "harness.session",
    "SESSION_IDLE_TTL_SECONDS": "harness.session",
    "SESSION_MAX_RESIDENT": "harness.session",
    "AUTO_APPROVE_TIERS": "harness.confirm",
    "STREAM_RATE_CAPACITY": "harness.ratelimit",
    "STREAM_RATE_REFILL_PER_SEC": "harness.ratelimit",
    "_build_anthropic_client": "harness.providers.wiring",
    "_today_token_spend": "harness.history",
    "MAX_OUTPUT_TOKENS_PER_TURN": "harness.budget",
    "MAX_TOOL_CALLS_PER_TURN": "harness.budget",
    "MAX_TURNS_PER_SESSION": "harness.budget",
    "MAX_OUTPUT_TOKENS_PER_SESSION": "harness.budget",
    "PYPSA_GUI_CHAT_DAILY_TOKEN_CAP": "harness.budget",
    "MAX_STREAM_RETRIES": "harness.budget",
    "BASE_STREAM_RETRY_DELAY": "harness.budget",
    "MAX_STREAM_RETRY_DELAY": "harness.budget",
    "_RETRYABLE_SDK_KINDS": "harness.budget",
    "PER_TOOL_TIMEOUT_SECONDS": "harness.budget",
    "_profile_awareness_block": "harness.compose",
    "_skills_block": "harness.compose",
    "_neutralise_untrusted_delimiters": "harness.fence",
}


def test_no_test_patches_a_moved_tunable_through_the_alias():
    offenders: list[str] = []
    for path in sorted((BACKEND / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        for name, home in MOVED_TUNABLES.items():
            for pat in (rf'setattr\(\s*chat_service\s*,\s*"{name}"',
                        rf'chat_service\.{name}\s*=[^=]',
                        rf'"services\.chat_service\.{name}"',
                        rf'"harness\.loop\.{name}"'):
                for m in re.finditer(pat, text):
                    line = text.count("\n", 0, m.start()) + 1
                    offenders.append(f"{path.name}:{line}: patch {name} on {home}, not the alias")
    assert offenders == [], "\n".join(offenders)


def test_moved_tunables_are_read_where_they_are_patched():
    """The re-exported name and the new module's name are the same object
    until someone patches; the readers are in the new module."""
    import importlib

    from services import chat_service

    for name, home in MOVED_TUNABLES.items():
        mod = importlib.import_module(home)
        assert getattr(chat_service, name) == getattr(mod, name)

    # The alias forwards (PEP 562), never copies: a patch on the home is
    # what the alias reads, and the loop declares every forwarded name.
    from harness import loop

    assert set(loop._FORWARDED_TUNABLES) == set(MOVED_TUNABLES)
    assert {k: v for k, v in loop._FORWARDED_TUNABLES.items()} == MOVED_TUNABLES


def test_a_patch_on_the_home_is_what_the_alias_reads(monkeypatch):
    from harness import session
    from services import chat_service

    monkeypatch.setattr(session, "CONFIRMATION_TTL_SECONDS", 7.5)
    assert chat_service.CONFIRMATION_TTL_SECONDS == 7.5


def test_the_loop_has_no_bare_read_of_a_moved_tunable():
    """A reader left in loop.py must go through the home module's attribute
    (`harness_session.CONFIRMATION_TTL_SECONDS`), or a patch on the home
    would not reach it."""
    tree = ast.parse((HARNESS / "loop.py").read_text(encoding="utf-8"))
    bare = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in MOVED_TUNABLES})
    assert bare == []

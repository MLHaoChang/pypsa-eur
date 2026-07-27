"""
Turning a project's display name into a directory name that survives both
platforms (phase 1b, Task 2).

Until this phase a project directory was a UUID, so none of this mattered. E1
puts the user's own name on disk, and D1 targets Windows and macOS — where the
rules disagree:

  Windows  rejects <>:"/\\|?* and control characters; reserves CON, PRN, AUX,
           NUL, COM1-9 and LPT1-9 **including with an extension** (`CON.nc` is
           still the console device); silently strips trailing dots and spaces
           from a path component, so `Study.` and `Study` are the same
           directory; and defaults to a 260-character path limit.
  macOS    is case-insensitive by default, so `Belgium Grid` and
           `belgium grid` collide.
  POSIX    treats a leading dot as "hidden" — a legacy `.foo` would import
           invisible. `routers/projects.py:188` already rejects those on the
           way in, for the same reason.

`_MAX_LEN` caps the DIRECTORY at 96 characters. Truncating the row's own name
is Task 7's job: `Project.name` is `String(64)` but SQLite does not enforce it
(constraint #1), so the two limits are enforced in different places.
"""
from __future__ import annotations

import pytest

from services.safe_names import _MAX_LEN, safe_dir_name, unique_dir_name


# ── safe_dir_name ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name",
    [
        "Belgium Grid",
        "3_nodes_system",
        "H2 Demand 250MW",
        "new_project_test.pypsaproj",
        "Étude Française",
        "日本のプロジェクト",
    ],
)
def test_ordinary_names_are_untouched(name):
    """Including non-ASCII: both platforms store UTF-8 names fine, and folding
    them to ASCII would make `Étude` and `Etude` collide for no reason."""
    assert safe_dir_name(name) == name


@pytest.mark.parametrize("char", list('<>:"/\\|?*'))
def test_every_windows_forbidden_character_is_replaced(char):
    result = safe_dir_name(f"a{char}b")
    assert char not in result
    assert result == "a_b"


@pytest.mark.parametrize("name", ["a\x00b", "a\nb", "a\tb"])
def test_control_characters_are_replaced(name):
    result = safe_dir_name(name)
    assert result == "a_b"


@pytest.mark.parametrize(
    "name",
    [
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM9", "LPT1", "LPT9",
        "CON.nc",          # the extension does NOT make it safe on Windows
        "con",             # matching is case-insensitive
        "nul.txt",
    ],
)
def test_reserved_device_names_are_defused(name):
    result = safe_dir_name(name)
    stem = result.split(".")[0]
    assert stem.upper() not in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    # Defused, not destroyed — the user must still recognise their project.
    assert result.split(".")[0].rstrip("_").upper() == name.split(".")[0].upper()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Study.", "Study"),
        ("Study ", "Study"),
        ("Study. . ", "Study"),
        ("Study...", "Study"),
    ],
)
def test_trailing_dots_and_spaces_are_stripped(name, expected):
    """Windows strips these silently, so `Study.` would land in `Study` and
    two rows would share one directory without anything raising."""
    assert safe_dir_name(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (".hidden", "hidden"),
        ("..hidden", "hidden"),
        (".", "project"),
        ("..", "project"),
    ],
)
def test_leading_dot_is_stripped(name, expected):
    assert safe_dir_name(name) == expected


@pytest.mark.parametrize("name", ["", "   ", "\t\n", "..."])
def test_empty_or_whitespace_falls_back(name):
    assert safe_dir_name(name) == "project"


def test_long_names_are_truncated_to_the_cap():
    assert safe_dir_name("x" * 200) == "x" * _MAX_LEN


def test_truncation_cannot_leave_a_trailing_dot():
    """`("a" * 95) + "." + ...` truncates to exactly 96 chars ending in a dot,
    which Windows would then strip — reintroducing the collision the cap is
    supposed to be orthogonal to."""
    result = safe_dir_name("a" * 95 + "." + "b" * 100)
    assert not result.endswith((".", " "))
    assert result == "a" * 95


# ── unique_dir_name ──────────────────────────────────────────────────────────

def test_no_collision_returns_the_plain_name():
    assert unique_dir_name("Belgium Grid", []) == "Belgium Grid"


def test_collision_is_suffixed():
    assert unique_dir_name("Belgium Grid", ["Belgium Grid"]) == "Belgium Grid (2)"


def test_suffixing_keeps_counting():
    taken = ["Belgium Grid", "Belgium Grid (2)", "Belgium Grid (3)"]
    assert unique_dir_name("Belgium Grid", taken) == "Belgium Grid (4)"


def test_collision_detection_is_case_insensitive():
    """macOS and Windows are both case-insensitive by default: creating
    `Belgium Grid` next to `belgium grid` opens the existing directory."""
    assert unique_dir_name("Belgium Grid", ["belgium grid"]) == "Belgium Grid (2)"


def test_a_name_that_sanitises_onto_a_taken_directory_is_suffixed():
    """
    The data-loss case. A project named `Study:1` lives in `Study_1`. A later
    project literally named `Study_1` must not be handed that same directory —
    both rows would resolve to one path and each save would clobber the other.
    """
    assert safe_dir_name("Study:1") == "Study_1"
    assert unique_dir_name("Study_1", ["Study_1"]) == "Study_1 (2)"


def test_the_suffix_stays_within_the_cap():
    """Otherwise the cap silently stops being a cap exactly when directories
    start colliding — which is when path length is already worst."""
    long_name = "x" * 200
    taken = [safe_dir_name(long_name)]
    result = unique_dir_name(long_name, taken)
    assert len(result) <= _MAX_LEN
    assert result.endswith(" (2)")
    assert result not in taken


def test_taken_accepts_any_iterable():
    """
    Annotated `Iterable[str]`, and the body iterates it. A one-shot generator
    must work, so the implementation may not iterate `taken` twice.
    """
    assert unique_dir_name("Belgium Grid", (n for n in ["Belgium Grid"])) == "Belgium Grid (2)"
    assert unique_dir_name("Belgium Grid", {"Belgium Grid"}) == "Belgium Grid (2)"
    assert unique_dir_name("Belgium Grid", frozenset()) == "Belgium Grid"


def test_a_suffixed_name_is_never_a_reserved_device_name():
    """`CON` is defused before suffixing, so the suffixed form cannot revert."""
    result = unique_dir_name("CON", ["CON_"])
    assert result.split(".")[0].split(" ")[0].upper() != "CON"

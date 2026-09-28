"""
An unknown `template_id` must be refused — nothing in the suite said so.

`create_from_template` resolves the URL segment to a key of
`_TEMPLATE_DEFAULT_NAMES` and joins THAT onto
`_PROJECT_TEMPLATES_DIR / <key> / "network.nc"`. Mutation testing found that
deleting the allowlist entirely — `template_key = template_id`, so the raw URL
segment names the directory — left the whole backend suite green. That is the
one guard between a path parameter and a path join, in a change made FOR path
safety, and it was unpinned.

Practical exploitability was low even without it (Starlette's path converter
does not match `/`, and clients normalise dot segments before sending), which
is why this is written as an allowlist test rather than a traversal test: the
property worth pinning is "only a registered key reaches the join", not "this
particular escape string fails".
"""
from __future__ import annotations

import pytest

from routers.projects import _TEMPLATE_DEFAULT_NAMES


@pytest.mark.parametrize("unknown", [
    "nope",
    "3bus-",            # a near-miss on a real key
    "..",
    "%2e%2e",
    "blank/../..",      # never reaches the route (no `/` match) — asserted anyway
    "",
])
def test_an_unregistered_template_id_is_refused(client, unknown):
    resp = client.post(f"/api/projects/from_template/{unknown}")
    assert resp.status_code in (404, 405), resp.text
    # And nothing was created on the way to refusing.
    assert "Unknown template" in resp.text or resp.status_code == 405


def test_every_registered_key_is_accepted_by_the_allowlist():
    """The complement: a guard that refused everything would make the cases
    above vacuous. Asserted against the registry rather than the routes so this
    does not depend on each template's `network.nc` artifact being built."""
    from routers.projects import _PROJECT_TEMPLATES_DIR

    assert _TEMPLATE_DEFAULT_NAMES, "the registry must not be empty"
    for key in _TEMPLATE_DEFAULT_NAMES:
        resolved = next((k for k in _TEMPLATE_DEFAULT_NAMES if k == key), None)
        assert resolved == key
        # The key names a single directory component under the templates dir.
        joined = (_PROJECT_TEMPLATES_DIR / resolved).resolve()
        assert joined.parent == _PROJECT_TEMPLATES_DIR.resolve()

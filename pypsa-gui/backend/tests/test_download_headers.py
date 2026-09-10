"""
Every download route that puts a caller-controlled name in its filename.

`test_content_disposition.py` covers the helper. This file covers the WIRING —
that each route actually calls it — because the helper being correct is no use
if a route still interpolates the name itself. It is the regression test for
the defect recorded in
`docs/superpowers/findings/2026-09-06-rename-accepts-any-name-and-it-reaches-a-header.md`.

The names below are ones the product genuinely accepts today. Component names
come from `POST /api/network/loads` and friends, which apply no character
validation; project names come from rename, which checks only non-empty and
unique. Both were verified by creating them through the real routes.

What this canNOT see, and why the last test exists: `TestClient` does not run
h11's header validation, so a control character in a header reaches the test
assertions intact rather than blowing up. Against a real uvicorn it raises
`RuntimeError: Invalid HTTP header value.` mid-send and the client gets an empty
reply. So asserting "no control characters" here is asserting exactly the
property that keeps the real server working.
"""
from __future__ import annotations

import pytest

# Names the API accepts and that used to break the header.
HOSTILE_NAMES = ['ev"il', "a\nb", "a\rb", "tab\there", "Grüße", "../esc"]


def _assert_header_is_sendable(header: str | None, *, label: str) -> None:
    assert header, f"{label}: no Content-Disposition at all"
    # The property uvicorn enforces at send time.
    assert not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in header), (
        f"{label}: control character in header {header!r} — a real server "
        f"raises RuntimeError('Invalid HTTP header value.') on this and the "
        f"client gets an empty reply"
    )
    # Exactly one quoted string: an embedded quote used to close it early, so a
    # parser read `filename="ev"il.zip"` as `ev`.
    assert header.count('"') == 2, f"{label}: not one quoted string: {header!r}"


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_the_load_template_download_survives_a_hostile_component_name(
    client, install_network, name
):
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "B1")
    install_network(n)

    resp = client.post("/api/network/loads", json={"name": name, "bus": "B1", "p_set": 5.0})
    assert resp.status_code == 201, resp.text

    resp = client.get("/api/network/loads/template", params={"load_name": name})
    assert resp.status_code == 200, resp.text
    _assert_header_is_sendable(
        resp.headers.get("content-disposition"), label=f"load template {name!r}"
    )


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_the_project_bundle_download_survives_a_hostile_project_name(
    client, api_project, project_row, name
):
    project = api_project("bundle_src")
    row = project_row(project)
    assert row is not None

    resp = client.post(f"/api/projects/{row.id}/rename", json={"new_name": name})
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/api/projects/{row.id}/bundle")
    assert resp.status_code == 200, resp.text
    _assert_header_is_sendable(
        resp.headers.get("content-disposition"), label=f"bundle {name!r}"
    )


def test_an_ordinary_download_header_is_unchanged(client, install_network):
    """
    The fix is applied to every download route, so an ordinary name must come
    out exactly as it did before — no `filename*`, same quoting. Otherwise this
    stops being an edge-case fix and becomes a behaviour change for everyone.
    """
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=5.0)
    install_network(n)

    resp = client.get("/api/network/loads/template", params={"load_name": "L1"})
    assert resp.status_code == 200
    assert (
        resp.headers["content-disposition"]
        == 'attachment; filename="load_L1_template.xlsx"'
    )


def test_no_download_route_builds_the_header_by_interpolation():
    """
    Static sweep. A new download route added next year will not know about
    `content_disposition`, and the failure it reintroduces is invisible in
    tests — `TestClient` sends the broken header happily and only a real
    server refuses it.

    An f-string here is only a bug when it interpolates something; a fixed
    literal like `filename=network.nc` is fine, and those are the ones this
    check deliberately allows.
    """
    import pathlib
    import re

    backend = pathlib.Path(__file__).resolve().parent.parent
    # `f'... filename="{...}"'` — an f-string with a placeholder inside it.
    pattern = re.compile(r'Content-Disposition"\s*:\s*f[\'"].*\{')
    offenders = []
    for path in sorted((backend / "routers").glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"routers/{path.name}:{lineno}: {line.strip()[:90]}")
    assert not offenders, (
        "these routes interpolate a name straight into Content-Disposition; use "
        "`services.http_filenames.content_disposition()`:\n  " + "\n  ".join(offenders)
    )

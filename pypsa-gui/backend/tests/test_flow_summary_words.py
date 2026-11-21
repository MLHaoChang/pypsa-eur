"""The rail's one-line section summaries read as plain words (guided-results 03 follow-up, UX 2 on #1688):
section 3 names its grid connections as a person reads them, never the engine id ("grid_import"), and section 1
counts time steps, not "snapshots" (CONTEXT.md, Snapshot)."""

from types import SimpleNamespace

from services.flow_service import ImportCapacity, _summary


def facts(**kw):
    base = dict(
        snapshots=168,
        carriers=[],
        connection_links=[],
        import_cap=ImportCapacity(links=(), mw=None),
        loads=0,
        asset_count=0,
    )
    return SimpleNamespace(**{**base, **kw})


def test_connection_names_its_links_in_plain_words():
    assert (
        _summary(
            "connection",
            facts(
                connection_links=["grid_import"],
                import_cap=ImportCapacity(links=("grid_import",), mw=40.0),
            ),
        )
        == "Grid import, 40 MW"
    )


def test_connection_keeps_a_name_already_written_for_people():
    assert (
        _summary(
            "connection", facts(connection_links=["Grid import North", "grid_import_2"])
        )
        == "Grid import North, Grid import 2"
    )


def test_connection_with_no_link_has_no_summary():
    assert _summary("connection", facts()) is None


def test_project_counts_time_steps():
    assert _summary("project", facts(snapshots=168)) == "168 time steps"
    assert _summary("project", facts(snapshots=1)) == "1 time step"

"""
The ENTSO-E day-ahead routes (SCU06 S1): the install-wide token (super-admin)
and a project's fetch into the org Library. The service and its replayed
fixtures are in `tests/test_market_prices.py`; its helpers are reused here.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from services import app_secrets
from services import market_prices as M
from tests.test_market_prices import TOKEN, Replay, _fixture, isolated  # noqa: F401 (fixture)


@pytest.fixture()
def db(_auth_db):
    _engine, session_local = _auth_db
    with session_local() as s:
        yield s


def _project(tmp_path, org_id):
    from routers.deps import AuthorizedProject

    return AuthorizedProject(
        name="Demo",
        directory=tmp_path,
        uuid="not-a-uuid",
        org_id=str(org_id),
        registry_key="k",
    )


def _user(db, seeded_identity):
    from db.models import User

    return db.get(User, seeded_identity["user_id"])


def test_the_project_route_offers_zones_the_pin_zone_and_the_pick(
    db, seeded_identity, tmp_path
):
    import routers.market_prices as R

    (tmp_path / "sites.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sites": [{"id": "s", "origin": {"lat": 52.52, "lng": 13.40}}],
            }
        )
    )
    out = R.get_project_market_prices(
        project=_project(tmp_path, seeded_identity["org_id"]),
        db=db,
        user=_user(db, seeded_identity),
    )
    assert (
        out["pin_zone"] == "DE-LU"
        and out["zone"] == "DE-LU"
        and out["chosen_zone"] is None
    )
    assert out["can_fetch"] is True
    assert out["token_set"] is False and out["can_set_token"] is False
    assert {"code": "NO-2", "label": "Southern Norway", "first_year": 2015} in out[
        "zones"
    ]
    M.store_zone(tmp_path, "NL")
    out = R.get_project_market_prices(
        project=_project(tmp_path, seeded_identity["org_id"]),
        db=db,
        user=_user(db, seeded_identity),
    )
    assert out["zone"] == "NL" and out["pin_zone"] == "DE-LU"


def test_the_fetch_route_lands_the_series_and_remembers_the_zone(
    db, seeded_identity, tmp_path, monkeypatch
):
    import routers.market_prices as R

    app_secrets.set_secret(M.TOKEN_NAME, TOKEN)
    real = M.fetch_day_ahead
    monkeypatch.setattr(
        M,
        "fetch_day_ahead",
        lambda zone, year, *, token, transport=None: real(
            zone, year, token=token, transport=Replay(body=_fixture()).transport
        ),
    )
    out = R.post_day_ahead(
        body=R.DayAheadIn(zone="DE-LU", year=2024),
        project=_project(tmp_path, seeded_identity["org_id"]),
        db=db,
        user=_user(db, seeded_identity),
    )
    assert out.ref.id == "entsoe_da_DE-LU_2024" and out.source == "ENTSO-E day-ahead"
    assert (out.zone, out.year) == ("DE-LU", 2024) and len(out.fetched) == 10
    assert M.chosen_zone(tmp_path) == "DE-LU"


@pytest.mark.parametrize(
    "body, status, kind",
    [
        ({"zone": "XX", "year": 2024}, 422, "invalid_zone"),
        ({"zone": "DE-LU", "year": 2010}, 422, "invalid_year"),
        ({"zone": "DE-LU", "year": 2024}, 409, "no_token"),
    ],
)
def test_the_fetch_route_refuses_in_words(
    db, seeded_identity, tmp_path, body, status, kind
):
    import routers.market_prices as R
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        R.post_day_ahead(
            body=R.DayAheadIn(**body),
            project=_project(tmp_path, seeded_identity["org_id"]),
            db=db,
            user=_user(db, seeded_identity),
        )
    assert e.value.status_code == status and e.value.detail["kind"] == kind
    assert M.chosen_zone(tmp_path) is None


def test_the_fetch_route_refuses_another_org_s_library(
    db, seeded_identity, second_identity, tmp_path
):
    import routers.market_prices as R
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        R.post_day_ahead(
            body=R.DayAheadIn(zone="DE-LU", year=2024),
            project=_project(tmp_path, second_identity["org_id"]),
            db=db,
            user=_user(db, seeded_identity),
        )
    assert e.value.status_code == 403


def test_another_org_s_member_may_not_fetch(
    db, seeded_identity, second_identity, tmp_path
):
    import routers.market_prices as R

    out = R.get_project_market_prices(
        project=_project(tmp_path, second_identity["org_id"]),
        db=db,
        user=_user(db, seeded_identity),
    )
    assert out["can_fetch"] is False


def test_a_project_viewer_may_not_fetch(db, seeded_identity, tmp_path, monkeypatch):
    import routers.market_prices as R
    from services import project_write_gate

    monkeypatch.setattr(project_write_gate, "is_viewer", lambda db, user, uuid: True)
    out = R.get_project_market_prices(
        project=_project(tmp_path, seeded_identity["org_id"]),
        db=db,
        user=_user(db, seeded_identity),
    )
    assert out["can_fetch"] is False


# ── token routes over HTTP ─────────────────────────────────────────────────


@pytest.fixture
def super_admin_client(_auth_db, seeded_identity):
    from fastapi.testclient import TestClient

    import main
    from db.models import OrgMembership, User
    from tests.conftest import attach_session

    _engine, session_local = _auth_db
    with session_local() as s:
        user = User(
            id=uuid.uuid4(),
            email=f"entsoe-super-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=None,
            status="active",
            is_super_admin=True,
            created_at=datetime.now(tz=UTC),
        )
        s.add(user)
        s.flush()
        s.add(
            OrgMembership(
                id=uuid.uuid4(),
                user_id=user.id,
                org_id=seeded_identity["org_id"],
                role="admin",
            )
        )
        s.commit()
        user_id = user.id
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        with session_local() as s:
            row = s.get(User, user_id)
            if row is not None:
                s.delete(row)
                s.commit()


@pytest.mark.parametrize(
    "method, kwargs", [("put", {"json": {"value": TOKEN}}), ("delete", {})]
)
def test_an_org_admin_cannot_set_or_clear_the_token(client, method, kwargs):
    response = getattr(client, method)("/api/market-prices/token", **kwargs)
    assert response.status_code == 403 and "super-admins" in response.json()["detail"]
    assert M.token() is None


def test_a_super_admin_sets_and_clears_the_token_and_status_says_only_set(
    super_admin_client, client
):
    r = super_admin_client.put("/api/market-prices/token", json={"value": TOKEN})
    assert r.status_code == 200 and r.json() == {
        "token_set": True,
        "can_set_token": True,
    }
    assert TOKEN not in r.text
    s = client.get("/api/market-prices/status")
    assert (
        s.json() == {"token_set": True, "can_set_token": False}
        and TOKEN[-4:] not in s.text
    )
    r = super_admin_client.delete("/api/market-prices/token")
    assert r.json()["token_set"] is False and M.token() is None


def test_an_anonymous_caller_gets_no_status(anon_client):
    assert anon_client.get("/api/market-prices/status").status_code == 401

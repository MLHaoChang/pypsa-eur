#!/usr/bin/env python3
"""
Live API smoke for the multi-user auth browser journey.

Run against a started backend (auth enabled + Postgres + Mailpit):

  cd pypsa-gui/backend
  pixi run python tools/auth_e2e_smoke.py \\
    --base-url http://127.0.0.1:8000 \\
    --super-email admin@example.com \\
    --super-password 'your-secure-password'

Prerequisites (see README "Browser walkthrough"):
  1. docker compose -f docker-compose.auth.yml up -d
  2. .env + alembic upgrade head
  3. bootstrap_super_admin.py
  4. uvicorn + vite with VITE_AUTH_ENABLED=true

The script prints the org/user it created and the set-password URL from Mailpit
(or the API outbox fallback) so you can finish the flow in the browser.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

_TOKEN_RE = re.compile(r"/set-password\?token=([^\s\"'<>]+)")


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookie: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        expect: int | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.cookie:
            headers["Cookie"] = self.cookie

        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                payload = json.loads(raw) if raw.strip() else None
                headers_out = {k.lower(): v for k, v in resp.headers.items()}
                set_cookie = resp.headers.get("Set-Cookie")
                if set_cookie:
                    self.cookie = set_cookie.split(";", 1)[0]
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw.strip() else {"detail": raw}
            except json.JSONDecodeError:
                payload = {"detail": raw}
            headers_out = {k.lower(): v for k, v in exc.headers.items()}
            status = exc.code

        if expect is not None and status != expect:
            raise SystemExit(
                f"{method} {path} expected {expect}, got {status}: {payload!r}"
            )
        return status, payload, headers_out


def _mailpit_latest_set_password_link(mailpit_url: str, to_email: str) -> str | None:
    """Return the newest set-password link for ``to_email`` from Mailpit, if any."""
    url = f"{mailpit_url.rstrip('/')}/api/v1/messages"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — best-effort helper for operators
        print(f"[warn] could not query Mailpit at {url}: {exc}", file=sys.stderr)
        return None

    messages = data.get("messages") or data.get("Messages") or []
    for msg in messages:
        tos = msg.get("To") or msg.get("to") or []
        addresses = []
        for item in tos:
            if isinstance(item, dict):
                addresses.append((item.get("Address") or item.get("address") or "").lower())
            else:
                addresses.append(str(item).lower())
        if to_email.lower() not in addresses:
            continue
        msg_id = msg.get("ID") or msg.get("id")
        if not msg_id:
            continue
        detail_url = f"{mailpit_url.rstrip('/')}/api/v1/message/{msg_id}"
        try:
            with urllib.request.urlopen(detail_url, timeout=10) as detail_resp:
                detail = json.loads(detail_resp.read().decode("utf-8"))
        except Exception:
            continue
        body = detail.get("Text") or detail.get("text") or detail.get("HTML") or ""
        match = _TOKEN_RE.search(body)
        if match:
            public = "http://localhost:5173"
            return f"{public}/set-password?token={match.group(1)}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend-url", default="http://localhost:5173")
    parser.add_argument("--mailpit-url", default="http://127.0.0.1:8025")
    parser.add_argument("--super-email", required=True)
    parser.add_argument("--super-password", required=True)
    parser.add_argument("--org-name", default=None)
    parser.add_argument("--member-email", default=None)
    parser.add_argument("--member-role", default="member", choices=("member", "admin"))
    args = parser.parse_args(argv)

    org_name = args.org_name or f"Browser Review Org {uuid.uuid4().hex[:6]}"
    member_email = args.member_email or f"member-{uuid.uuid4().hex[:8]}@example.com"

    client = ApiClient(args.base_url)

    print("== 1) Login as super-admin ==")
    client.request(
        "POST",
        "/api/auth/login",
        body={"email": args.super_email, "password": args.super_password},
        expect=200,
    )
    _, me, _ = client.request("GET", "/api/auth/me", expect=200)
    assert isinstance(me, dict)
    if not me.get("is_super_admin"):
        raise SystemExit(f"Account {args.super_email} is not a super-admin: {me}")
    print(f"  logged in as {me['email']}")

    print("== 2) Create organization ==")
    _, org, _ = client.request(
        "POST",
        "/api/admin/organizations",
        body={"name": org_name},
        expect=201,
    )
    assert isinstance(org, dict)
    print(f"  org id={org['id']} name={org['name']}")

    print("== 3) Invite user ==")
    _, invited, _ = client.request(
        "POST",
        "/api/admin/users",
        body={"email": member_email, "role": args.member_role, "org_id": org["id"]},
        expect=201,
    )
    assert isinstance(invited, dict)
    print(
        f"  invited {invited['email']} status={invited['status']} "
        f"email_sent={invited.get('email_sent')}"
    )
    if invited.get("warning"):
        print(f"  warning: {invited['warning']}")

    print("== 4) Resolve set-password link ==")
    link = _mailpit_latest_set_password_link(args.mailpit_url, member_email)
    if link is None:
        print(
            "  Mailpit link not found automatically.\n"
            f"  Open {args.mailpit_url} and copy the set-password URL for {member_email}."
        )
    else:
        # Rewrite host to the configured frontend URL if needed
        token_match = _TOKEN_RE.search(link)
        if token_match:
            link = (
                f"{args.frontend_url.rstrip('/')}/set-password?token={token_match.group(1)}"
            )
        print(f"  set-password URL:\n    {link}")

    print()
    print("Browser checklist (finish manually):")
    print(f"  1. Open {args.frontend_url}/login and sign in as {args.super_email}")
    print("  2. Open /admin → Organizations — confirm the new org is listed")
    print("  3. Open /admin → Users — confirm the invited user shows as Invited")
    print(f"  4. Open the set-password link for {member_email} and choose a password")
    print(f"  5. Sign in as {member_email} → should land on /projects")
    print("  6. Confirm member cannot open /admin (redirect / forbidden)")
    print()
    print("PASS: API portion of the auth journey succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

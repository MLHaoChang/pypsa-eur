# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""LiveKit Cloud media transport behind `live_media.MediaTransport` (live-voice 01).

Server side only: the API secret signs short-lived JWTs here and never leaves
the process. The browser receives one room-scoped participant token (join,
publish the microphone, subscribe; no room admin, no data channel); the
LiveKit server API is called over its Twirp HTTP endpoints with a separate
admin token that lives for one minute. No LiveKit SDK is needed, so nothing
vendor-specific is imported at module load.

``open_session`` creates a bounded room (empty/departure timeouts, at most
three participants), optionally dispatches the hosted audio worker with the
owner-scoped bridge token in its job metadata, and returns the grant. If any
step after room creation fails, the room is deleted before the error is
raised. ``close_session`` deletes the room and treats "not found" as done.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from harness.providers.live_media import LiveMediaError, MediaGrant

ADMIN_TOKEN_TTL_S = 60
MAX_GRANT_TTL_S = 900
EMPTY_TIMEOUT_S = 60
DEPARTURE_TIMEOUT_S = 20
MAX_PARTICIPANTS = 3  # the browser, the audio worker, one spare for a rejoin
API_TIMEOUT_S = 10.0
MAX_API_RESPONSE_BYTES = 64 * 1024

_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ROOM_SERVICE = "/twirp/livekit.RoomService"
_DISPATCH_SERVICE = "/twirp/livekit.AgentDispatchService"

SyncClientFactory = Callable[[], httpx.Client]


def default_sync_client() -> httpx.Client:
    return httpx.Client(timeout=API_TIMEOUT_S, follow_redirects=False)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_token(
    *, api_key: str, api_secret: str, identity: str, ttl_s: int, video: dict, now: float
) -> str:
    """An HS256 LiveKit access token (the format the LiveKit server SDKs mint)."""
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": api_key,
        "sub": identity,
        "nbf": int(now) - 5,
        "exp": int(now) + int(ttl_s),
        "jti": uuid.uuid4().hex,
        "video": video,
    }
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":"), sort_keys=True).encode())
        for part in (header, claims)
    )
    signature = hmac.new(
        api_secret.encode(), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url(signature)}"


@dataclass(frozen=True)
class LiveKitConfig:
    url: str  # wss://<project>.livekit.cloud, the browser's signalling URL
    api_key: str
    api_secret: str
    agent_name: str = ""  # empty: no worker dispatch (connectivity probe only)

    def __repr__(self) -> str:  # the key and secret never reach a log line
        return f"LiveKitConfig(url={self.url!r}, agent_name={self.agent_name!r})"


def _api_base(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "wss" or not parts.hostname or parts.username or parts.password:
        raise LiveMediaError(
            "not_configured", "The media service URL must be a wss:// host.", 503
        )
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        raise LiveMediaError(
            "not_configured", "The media service URL must be a bare wss:// host.", 503
        )
    return f"https://{parts.netloc}"


class LiveKitTransport:
    """`MediaTransport` over the LiveKit server API; one room per speech session."""

    name = "livekit"

    def __init__(
        self,
        config: LiveKitConfig,
        *,
        client_factory: SyncClientFactory = default_sync_client,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not config.api_key or not config.api_secret:
            raise LiveMediaError(
                "not_configured", "The media service is not configured.", 503
            )
        if config.agent_name and not _NAME_RE.match(config.agent_name):
            raise LiveMediaError(
                "not_configured", "The audio worker name is not valid.", 503
            )
        self._config = config
        self._base = _api_base(config.url)
        self._factory = client_factory
        self._clock = clock

    def __repr__(self) -> str:
        return f"LiveKitTransport(url={self._config.url!r})"

    # ── tokens ──────────────────────────────────────────────────────────────

    def _admin_token(self, video: dict) -> str:
        return sign_token(
            api_key=self._config.api_key,
            api_secret=self._config.api_secret,
            identity="pypsa-gui-server",
            ttl_s=ADMIN_TOKEN_TTL_S,
            video=video,
            now=self._clock(),
        )

    def participant_token(
        self, *, room: str, identity: str, ttl_s: int
    ) -> tuple[str, float]:
        now = self._clock()
        ttl = max(1, min(int(ttl_s), MAX_GRANT_TTL_S))
        video = {
            "room": room,
            "roomJoin": True,
            "canPublish": True,
            "canPublishSources": ["microphone"],
            "canSubscribe": True,
            "canPublishData": False,
            "canUpdateOwnMetadata": False,
        }
        token = sign_token(
            api_key=self._config.api_key,
            api_secret=self._config.api_secret,
            identity=identity,
            ttl_s=ttl,
            video=video,
            now=now,
        )
        return token, float(int(now) + ttl)

    # ── server API ──────────────────────────────────────────────────────────

    def _call(self, service: str, method: str, body: dict, video: dict) -> dict:
        """One Twirp POST: no redirects, no retries, bounded body, typed errors."""
        url = f"{self._base}{service}/{method}"
        headers = {"Authorization": f"Bearer {self._admin_token(video)}"}
        try:
            with self._factory() as client:
                with client.stream("POST", url, json=body, headers=headers) as response:
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_API_RESPONSE_BYTES:
                            raise LiveMediaError(
                                "oversize_output",
                                "The media service returned too much data.",
                            )
                        chunks.append(chunk)
                    status = response.status_code
        except httpx.TimeoutException as exc:
            raise LiveMediaError(
                "timeout", "The media service timed out.", 504
            ) from exc
        except httpx.HTTPError as exc:
            raise LiveMediaError(
                "unavailable", "The media service could not be reached.", 503
            ) from exc
        payload = b"".join(chunks)
        try:
            doc = json.loads(payload) if payload else {}
        except ValueError:
            doc = None
        if not 200 <= status < 300:
            code = doc.get("code") if isinstance(doc, dict) else None
            if code == "not_found" or status == 404:
                raise LiveMediaError(
                    "provider_error", "The media room does not exist.", 404
                )
            if status in (401, 403):
                raise LiveMediaError(
                    "provider_auth", "The media service rejected its credentials."
                )
            if status == 429 or code == "resource_exhausted":
                raise LiveMediaError(
                    "provider_limit", "The media service reached its quota.", 429
                )
            raise LiveMediaError(
                "provider_error", "The media service refused the request."
            )
        if not isinstance(doc, dict):
            raise LiveMediaError(
                "malformed_response", "The media service returned an invalid response."
            )
        return doc

    # ── MediaTransport ──────────────────────────────────────────────────────

    def open_session(
        self,
        *,
        room: str,
        identity: str,
        ttl_s: int,
        bridge_token: str,
        language: str,
    ) -> MediaGrant:
        if not _NAME_RE.match(room or "") or not _NAME_RE.match(identity or ""):
            raise LiveMediaError(
                "invalid_request", "Room or identity is not valid.", 422
            )
        if not isinstance(bridge_token, str) or not bridge_token:
            raise LiveMediaError(
                "invalid_request", "A bridge credential is required.", 422
            )
        created = self._call(
            _ROOM_SERVICE,
            "CreateRoom",
            {
                "name": room,
                "empty_timeout": EMPTY_TIMEOUT_S,
                "departure_timeout": DEPARTURE_TIMEOUT_S,
                "max_participants": MAX_PARTICIPANTS,
            },
            {"roomCreate": True},
        )
        try:
            if created.get("name") not in (None, room):
                raise LiveMediaError(
                    "malformed_response", "The media service created a different room."
                )
            if self._config.agent_name:
                self._call(
                    _DISPATCH_SERVICE,
                    "CreateDispatch",
                    {
                        "room": room,
                        "agent_name": self._config.agent_name,
                        "metadata": json.dumps(
                            {
                                "room": room,
                                "language": language,
                                "bridge_token": bridge_token,
                            }
                        ),
                    },
                    {"roomAdmin": True, "room": room},
                )
            token, expires_at = self.participant_token(
                room=room, identity=identity, ttl_s=ttl_s
            )
        except BaseException:
            # A failed or abandoned start never leaves a billable room behind.
            with contextlib.suppress(Exception):
                self.close_session(room=room)
            raise
        return MediaGrant(url=self._config.url, token=token, expires_at=expires_at)

    def close_session(self, *, room: str) -> None:
        if not _NAME_RE.match(room or ""):
            raise LiveMediaError("invalid_request", "Room is not valid.", 422)
        try:
            self._call(
                _ROOM_SERVICE, "DeleteRoom", {"room": room}, {"roomCreate": True}
            )
        except LiveMediaError as exc:
            if exc.status != 404:
                raise

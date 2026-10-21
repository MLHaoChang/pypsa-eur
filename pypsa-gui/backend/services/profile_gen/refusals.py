"""What a profile refusal may tell its caller (CodeQL follow-up to #466).

One rule for the routes and the chat tools: a typed refusal says the sentence
it was raised with (`engine.ProfileGenError.public`), which we author; any
other exception, and a typed one raised with no sentence, says only its type.
Its text and traceback go to the server log with the route and job id, never
the request body.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def public_message(exc: BaseException, *, route: str, job_id: str | None = None) -> str:
    """The text a response may carry for ``exc``."""
    from services.profile_gen.engine import ProfileGenError

    public = getattr(exc, "public", None) if isinstance(exc, ProfileGenError) else None
    if public:
        return public
    logger.error("profile route %s (job %s) failed: %s", route, job_id,
                 type(exc).__name__, exc_info=exc)
    return f"unexpected error ({type(exc).__name__}); see the server log"

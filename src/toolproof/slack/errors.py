"""Typed Slack errors. Every error knows whether it is safe to retry, which is the single most
important fact an agent (or a retry loop) needs about a failure."""

from __future__ import annotations

from typing import Any

AUTH_ERROR_CODES = frozenset(
    {"invalid_auth", "not_authed", "token_expired", "token_revoked", "account_inactive", "no_permission"}
)
# ok:false codes that Slack documents as transient.
RETRYABLE_ERROR_CODES = frozenset(
    {"internal_error", "fatal_error", "service_unavailable", "request_timeout", "ratelimited"}
)


class SlackError(Exception):
    """Base class. ``code`` mirrors Slack's machine-readable error string where one exists."""

    retryable: bool = False

    def __init__(self, code: str, message: str | None = None, *, data: dict[str, Any] | None = None):
        super().__init__(message or code)
        self.code = code
        self.data = data or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": str(self), "retryable": self.retryable}


class SlackApiError(SlackError):
    """Slack answered ``ok: false`` with a non-transient error (e.g. ``channel_not_found``)."""


class AuthError(SlackError):
    """Credentials are missing, expired, revoked or lack a scope."""


class MissingScopeError(AuthError):
    def __init__(self, needed: str | None, provided: str | None, data: dict[str, Any] | None = None):
        super().__init__("missing_scope", f"token is missing scope '{needed}'", data=data)
        self.needed = needed
        self.provided = provided

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "needed_scope": self.needed}


class TransientError(SlackError):
    """5xx, network failure or a documented transient ok:false code. Safe to retry for idempotent calls."""

    retryable = True


class RateLimitedError(TransientError):
    def __init__(self, retry_after: float, method: str):
        super().__init__("ratelimited", f"{method} rate limited; retry after {retry_after:g}s")
        self.retry_after = retry_after
        self.method = method

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "retry_after_seconds": self.retry_after}


class OutcomeUnknownError(SlackError):
    """A non-idempotent write failed *after* the request may have reached Slack.

    Blindly retrying would risk a duplicate side effect, so this is deliberately not retryable.
    Callers must verify state before deciding to retry.
    """

    retryable = False

    def __init__(self, method: str, cause: str):
        super().__init__(
            "outcome_unknown", f"{method} failed ambiguously ({cause}); the write may have applied"
        )
        self.method = method
        self.cause = cause

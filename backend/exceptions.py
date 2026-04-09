"""RxBuddy custom exception types."""

from __future__ import annotations


class RxBuddyError(Exception):
    """Base exception for all RxBuddy errors."""


class FDAUnavailable(RxBuddyError):
    """Raised when the FDA API is unreachable or returns an error."""

    def __init__(self, drug_name: str = "", detail: str = ""):
        self.drug_name = drug_name
        self.detail = detail
        msg = f"FDA API unavailable for '{drug_name}'" if drug_name else "FDA API unavailable"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class ClaudeError(RxBuddyError):
    """Raised when the Anthropic Claude API fails."""

    def __init__(self, detail: str = "", status_code: int | None = None):
        self.detail = detail
        self.status_code = status_code
        msg = "Claude API error"
        if detail:
            msg += f": {detail}"
        if status_code:
            msg += f" (status={status_code})"
        super().__init__(msg)

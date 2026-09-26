"""
orchestrator/failures.py
What a user is told when answering a question fails (docs/V3_PLAN.md 0.4).

Kept out of main.py so it can be tested without importing the web server.
"""

from __future__ import annotations

# Exception class names, matched through the MRO so no provider SDK has to be
# importable here (the openai package is only needed when a hosted provider is
# selected). docs/V3_PLAN.md 0.4.
_RATE_LIMITED = frozenset({"RateLimitError"})
_MISCONFIGURED = frozenset({"GroqConfigError", "AzureConfigError", "GeminiConfigError"})
_UNAVAILABLE = frozenset({"APIConnectionError", "APITimeoutError", "InternalServerError",
                          "ConnectError", "ConnectTimeout", "ReadTimeout", "HTTPStatusError"})


def user_facing_failure(exc: Exception, query_id: str) -> tuple[int, str]:
    """
    The status and message a user sees when answering a question fails.

    The detail used to be f"Graph execution error: {exc}", which put internal
    URLs, hostnames and SDK messages in front of the user -- for a missing
    local model, "Client error '404 Not Found' for url
    'http://host.docker.internal:11434/api/chat'". The full exception still goes
    to the log and the audit record; the user gets what happened in plain words
    and a reference that finds the log line.
    """
    names = {cls.__name__ for cls in type(exc).__mro__}
    ref = f" (reference {query_id})"
    if names & _RATE_LIMITED:
        return 503, "The language model is rate-limited right now. Please try again in a minute." + ref
    if names & _MISCONFIGURED:
        return 503, "The language model is not configured correctly on the server." + ref
    if names & _UNAVAILABLE:
        return 503, "A service this answer depends on is unavailable right now. Please try again shortly." + ref
    return 500, "Something went wrong while answering this question." + ref

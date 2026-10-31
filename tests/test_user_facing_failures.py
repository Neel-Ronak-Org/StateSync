"""
tests/test_user_facing_failures.py
Pins what a user sees when answering a question fails (docs/V3_PLAN.md 0.4).

WHY THIS EXISTS. /query used to return detail=f"Graph execution error: {exc}".
When the configured local model was missing, every user saw "Client error
'404 Not Found' for url 'http://host.docker.internal:11434/api/chat'" -- an
internal hostname and route, and nothing they could act on. The message is now
chosen by failure class, never contains the exception text, and carries a
reference that finds the full error in the log.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

from failures import user_facing_failure  # noqa: E402

LEAKY = "Client error '404 Not Found' for url 'http://host.docker.internal:11434/api/chat'"


def _exc(class_name: str, base: type = Exception) -> Exception:
    """An exception whose class carries the given name, like the SDK's would."""
    return type(class_name, (base,), {})(LEAKY)


@pytest.mark.parametrize("class_name, status, phrase", [
    ("RateLimitError", 503, "rate-limited"),
    ("OpenAICompatConfigError", 503, "not configured correctly"),
    ("HTTPStatusError", 503, "unavailable right now"),
    ("APIConnectionError", 503, "unavailable right now"),
    ("KeyError", 500, "Something went wrong"),
])
def test_message_by_failure_class(class_name, status, phrase):
    code, message = user_facing_failure(_exc(class_name), "q-123")

    assert code == status
    assert phrase in message
    assert message.endswith("(reference q-123)")


def test_subclasses_are_recognised_through_the_mro():
    timeout = type("APITimeoutError", (_exc("APIConnectionError").__class__,), {})("x")
    assert user_facing_failure(timeout, "q")[0] == 503


@pytest.mark.parametrize("class_name", ["HTTPStatusError", "RuntimeError", "OpenAICompatConfigError"])
def test_internal_detail_never_reaches_the_user(class_name):
    _, message = user_facing_failure(_exc(class_name), "q")

    assert "host.docker.internal" not in message
    assert "11434" not in message
    assert LEAKY not in message

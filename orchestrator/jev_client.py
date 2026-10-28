"""
orchestrator/jev_client.py
The one HTTP call to Jev, TypeSafe's typed decision model, through OpenCode Zen's "systemone" endpoint.

Jev reads an application state (a JSON text) and answers typed questions with probabilities; it writes no
text. Two kinds of question are used:
  - a yes/no ("noul") question, sent as {"type": "noul", "instructions": text}; the answer is P(yes) in
    [0, 1] (the router's "does the documents part depend on the database?", and passage_jev.py's "does
    this passage contain the information needed?");
  - a choice question, {"type": "choice", "instructions": text, "criteria": {option: description}}; the
    answer is the chosen option, a confidence and a probability per option (the router's sql / docs /
    both / neither).

It never raises to the caller: a failure is a JevAnswer with `error` set, so the router falls back to the
LLM router and the passages keep the search's order, and the trace records why. Answers are rounded to two
decimals by the server and are not deterministic; nothing here caches them. The key is read from a file at
call time and never logged. One retry after a rate limit or a server error ([front_door] jev_retry_wait_s).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from config_loader import cfg

logger = logging.getLogger(__name__)

JEV_KEY_FILE_ENV = "JEV_API_KEY_FILE"
JEV_KEY_FILE_DEFAULT = "/run/secrets/opencode_api_key"
USER_AGENT = "statesync-orchestrator/1.0"
BODY_SNIPPET_CHARS = 200        # noqa: literals - how much of an error body goes into the error text
_RETRY_STATUS_FLOOR = 500       # noqa: literals - 5xx is a server error, worth one retry


@dataclass
class JevAnswer:
    """One Jev request's result. `error` is None on success; then every asked noul question has a
    probability and every asked choice question has a choice."""
    probabilities: dict[str, float] = field(default_factory=dict)      # the noul questions' P(yes)
    choices: dict[str, dict[str, Any]] = field(default_factory=dict)   # the choice questions: {"choice", "confidence", "probabilities"}
    model: str = ""                   # the reply's model if it names one, else the requested one
    ms: int = 0
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)    # input_tokens, output_tokens when the reply has them

    @property
    def ok(self) -> bool:
        return self.error is None


def _key() -> str:
    path = os.environ.get(JEV_KEY_FILE_ENV, JEV_KEY_FILE_DEFAULT)
    try:
        key = Path(path).read_text().strip()
    except OSError as exc:
        raise RuntimeError(f"no Jev API key file at {path}") from exc
    if not key:
        raise RuntimeError(f"the Jev API key file at {path} is empty")
    return key


Question = str | dict[str, Any]     # a noul instruction text, or a choice question dict


def is_choice(question: Question) -> bool:
    return isinstance(question, dict) and question.get("type") == "choice"


def request_body(model: str, state: str, questions: dict[str, Question]) -> dict[str, Any]:
    """The request. A string question is a noul instruction; a dict question is sent as it is (a choice:
    {"type": "choice", "instructions": text, "criteria": {option: description}})."""
    return {"model": model, "state": state,
            "questions": {name: dict(q) if isinstance(q, dict) else {"type": "noul", "instructions": q}
                          for name, q in questions.items()}}


def _probability(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 1


def parse_reply(reply: Any, questions: dict[str, Question], requested_model: str) -> JevAnswer:
    """The reply's answers, served model and usage; an error value when an asked noul question has no
    probability in [0, 1], or an asked choice question has no choice among its criteria. A choice answer is
    kept as {"choice", "confidence", "probabilities"}: the option probabilities that are numbers in [0, 1]
    ({} when the reply has none) and the confidence as a float (None when the reply has none)."""
    if not isinstance(reply, dict) or not isinstance(reply.get("answers"), dict):
        return JevAnswer(model=requested_model, error="reply has no answers")
    probabilities: dict[str, float] = {}
    choices: dict[str, dict[str, Any]] = {}
    for name, question in questions.items():
        entry = reply["answers"].get(name)
        if is_choice(question):
            picked = entry.get("choice") if isinstance(entry, dict) else None
            if not isinstance(picked, str) or picked not in (question.get("criteria") or {}):
                return JevAnswer(model=requested_model, error=f"reply has no valid choice for {name!r}")
            by_option = entry.get("probabilities")
            confidence = entry.get("confidence")
            choices[name] = {
                "choice": picked,
                "confidence": float(confidence) if _probability(confidence) else None,
                "probabilities": ({str(k): float(v) for k, v in by_option.items() if _probability(v)}
                                  if isinstance(by_option, dict) else {})}
            continue
        value = entry.get("noul") if isinstance(entry, dict) else None
        if not _probability(value):
            return JevAnswer(model=requested_model, error=f"reply has no probability for {name!r}")
        probabilities[name] = float(value)
    served = reply.get("model")
    usage = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
    return JevAnswer(
        probabilities=probabilities, choices=choices,
        model=served if isinstance(served, str) and served else requested_model,
        usage={k: int(usage[k]) for k in ("input_tokens", "output_tokens")
               if isinstance(usage.get(k), (int, float)) and not isinstance(usage.get(k), bool)})


async def ask(state: str, questions: dict[str, Question], *,
              transport: httpx.AsyncBaseTransport | None = None) -> JevAnswer:
    """One Jev request: every question in `questions` ({name: noul text | choice dict}) over `state`. One retry, after
    front_door.jev_retry_wait_s, on HTTP 429 or 5xx; then an error value. Never raises. `transport` is for tests."""
    url, model = str(cfg.front_door.jev_url), str(cfg.front_door.jev_model)
    start = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - start) * 1000)    # noqa: literals - seconds to milliseconds

    try:
        key = _key()
    except RuntimeError as exc:
        return JevAnswer(model=model, ms=elapsed_ms(), error=str(exc))
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": USER_AGENT}
    body = request_body(model, state, questions)

    def redact(text: str) -> str:
        return text.replace(key, "[redacted]")

    attempts = 2        # noqa: literals - one try and one retry (the spec's single retry)
    error = "no attempt"
    try:
        async with httpx.AsyncClient(timeout=float(cfg.front_door.jev_timeout_s), transport=transport) as client:
            for attempt in range(attempts):
                try:
                    response = await client.post(url, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    error = redact(f"{type(exc).__name__}: {exc}")
                    logger.warning("jev: request failed (%s)", error)
                    return JevAnswer(model=model, ms=elapsed_ms(), error=error)
                status = response.status_code
                if status == 429 or status >= _RETRY_STATUS_FLOOR:
                    error = redact(f"HTTP {status}: {response.text[:BODY_SNIPPET_CHARS]}")
                    if attempt < attempts - 1:
                        logger.warning("jev: HTTP %d; retrying in %ss", status, cfg.front_door.jev_retry_wait_s)
                        await asyncio.sleep(float(cfg.front_door.jev_retry_wait_s))
                        continue
                    logger.error("jev: HTTP %d after the retry", status)
                    return JevAnswer(model=model, ms=elapsed_ms(), error=error)
                if status != 200:
                    error = redact(f"HTTP {status}: {response.text[:BODY_SNIPPET_CHARS]}")
                    logger.error("jev: HTTP %d", status)
                    return JevAnswer(model=model, ms=elapsed_ms(), error=error)
                try:
                    reply = response.json()
                except ValueError:
                    return JevAnswer(model=model, ms=elapsed_ms(), error="reply is not JSON")
                answer = parse_reply(reply, questions, model)
                answer.ms = elapsed_ms()
                if answer.error:
                    logger.error("jev: %s", answer.error)
                return answer
    except Exception as exc:  # noqa: BLE001 -- an error is a value; the caller falls back to the LLM arm
        error = redact(f"{type(exc).__name__}: {exc}")
        logger.error("jev: unexpected failure (%s)", error)
    return JevAnswer(model=model, ms=elapsed_ms(), error=error)

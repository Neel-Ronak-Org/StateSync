"""
orchestrator/context_carry.py
Multi-turn context with no extra model call (pipeline.context_carry = "on";
docs/specs/partd_tasks/T4d_context_carry.md, the second part of T4c).

WHY THIS EXISTS. A follow-up ("And in fiscal 2022?", "Has it reported any legal battles?") needs the
conversation. The shipped rewriter spends a model call per turn to write a standalone question. For the
front-door arms that do not rewrite the question, this module resolves it deterministically instead:
detect a follow-up, carry the company and year the question leaves out from the previous turn (its
question, and its answer: "it" in the second turn of "Which of Coca-Cola, PepsiCo and General Mills had the
highest revenue? / Has it reported any legal battles?" is PepsiCo, named only in the first turn's answer),
and give the answering models one line of context. The writers read the line; the document search text
gains the carried company and year.

Everything here is a pure function. Names are the workspace's document title names (title_name, T5b),
already reduced to lower-case letters and digits; both sides are reduced the same way before matching.
`previous` is the previous turn as the front door holds it: a mapping with the standalone question
(`rewritten_query`) and the answer (`answer_text`); `question` and `answer` are read when those are absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from config_loader import cfg

# Four-digit years the module recognises, and the century a two-digit year ("FY22") is read in.
_YEAR_MIN = 1990       # noqa: literals - the earliest year read
_YEAR_MAX = 2039       # noqa: literals - the latest year read
_CENTURY = 2000        # noqa: literals - FY22 is 2022 ...
_PREV_CENTURY = 1900   # noqa: literals - ... and FY95 is 1995, whichever falls in range

ANAPHORS = (
    "it", "its", "it's", "they", "their", "them", "that company", "this company", "the company", "same",
    "those", "these", "which ones", "and in", "and for", "what about", "how about", "also", "then",
)

REASON_FIRST_TURN = "first turn"
REASON_NAMES_COMPANY = "the question names a company"
REASON_NO_SIGNAL = "no anaphor, and the previous turn named no company"

_YEAR_RE = re.compile(r"(?<!\d)(?:(?:fy|fiscal)\s*(\d{4}|\d{2})|(\d{4}))(?!\d)", re.IGNORECASE)
_POSSESSIVE_RE = re.compile(r"['’]s\b")


def _anaphor_re() -> re.Pattern[str]:
    def phrase(text: str) -> str:
        return r"\s+".join(re.escape(word).replace("'", "['’]") for word in text.split(" "))
    alternatives = "|".join(phrase(a) for a in sorted(ANAPHORS, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


_ANAPHOR_RE = _anaphor_re()


def _letters_digits(text: str) -> str:
    """T5b's reduction (nodes.unstructured_knowledge._letters_digits: lower-case letters and digits only),
    imported when first used because that module imports this one."""
    from nodes.unstructured_knowledge import _letters_digits as reduce
    return reduce(text)


# ── Companies and years ──────────────────────────────────────────────────────
def entities(text: str, names: list[str]) -> list[str]:
    """The names (as given) that `text` names, longest first, each once, in order of appearance. Both sides
    are reduced to lower-case letters and digits ("Coca-Cola" names "cocacola"). As in match_titles (T5b), a
    name occurs anywhere in the text's reduced letters ("JPMORGAN CHASE & CO" names "jpmorgan") when it has
    at least [hybrid] title_name_min_chars of them; a shorter name ("3m") must equal a whole word. A longer
    name's match is used up, so a shorter name inside it ("morgan" in "morganstanley") is not reported."""
    min_chars = int(cfg.hybrid.title_name_min_chars)
    words = [w for w in (_letters_digits(w) for w in _POSSESSIVE_RE.sub("", text or "").split()) if w]
    starts: list[int] = []
    offset = 0
    for word in words:
        starts.append(offset)
        offset += len(word)
    buffer = list("".join(words))
    keyed: dict[str, str] = {}
    for name in names:
        key = _letters_digits(name)
        if key and key not in keyed:
            keyed[key] = name
    found: list[tuple[int, str]] = []
    for key in sorted(keyed, key=lambda k: -len(k)):
        position = -1
        if len(key) >= min_chars:
            position = "".join(buffer).find(key)
        else:
            for word, start in zip(words, starts):
                if word == key and "".join(buffer[start:start + len(word)]) == word:
                    position = start
                    break
        if position >= 0:
            buffer[position:position + len(key)] = "\0" * len(key)
            found.append((position, keyed[key]))
    return [name for _, name in sorted(found)]


def _year_of(digits: str) -> str | None:
    """A four-digit string for a year in range; a two-digit one is read in the century that puts it in range."""
    if len(digits) == 4:  # noqa: literals - a full year
        return digits if _YEAR_MIN <= int(digits) <= _YEAR_MAX else None
    for century in (_CENTURY, _PREV_CENTURY):
        if _YEAR_MIN <= century + int(digits) <= _YEAR_MAX:
            return str(century + int(digits))
    return None


def years(text: str) -> list[str]:
    """The years a text names, as four-digit strings, each once, in order of appearance: four-digit years
    1990-2039 and the forms FY22, FY2022 and fiscal 22."""
    found: list[str] = []
    for match in _YEAR_RE.finditer(text or ""):
        year = _year_of(match.group(1) or match.group(2))
        if year and year not in found:
            found.append(year)
    return found


# ── The previous turn ────────────────────────────────────────────────────────
def _text(turn: Mapping[str, Any] | None, *keys: str) -> str:
    for key in keys:
        value = (turn or {}).get(key)
        if isinstance(value, str):
            return value
    return ""


def previous_question(previous: Mapping[str, Any] | None) -> str:
    return _text(previous, "rewritten_query", "question")


def previous_answer(previous: Mapping[str, Any] | None) -> str:
    return _text(previous, "answer_text", "answer")


# ── Detecting a follow-up ────────────────────────────────────────────────────
def is_follow_up(question: str, previous: Mapping[str, Any] | None, names: list[str]) -> tuple[bool, str]:
    """(follow-up?, why). True when there is a previous turn and either the question has an anaphor (whole
    words, case-insensitive: ANAPHORS) or it names no company from `names` while the previous turn's
    question or answer did."""
    if not previous:
        return False, REASON_FIRST_TURN
    anaphor = _ANAPHOR_RE.search(question or "")
    if anaphor:
        return True, f'anaphor "{" ".join(anaphor.group(0).lower().split())}"'
    if entities(question, names):
        return False, REASON_NAMES_COMPANY
    earlier = entities(previous_question(previous), names) or entities(previous_answer(previous), names)
    if earlier:
        return True, f"no company named; the previous turn named {earlier[0]}"
    return False, REASON_NO_SIGNAL


# ── Carrying the missing slots ───────────────────────────────────────────────
@dataclass(frozen=True)
class Carry:
    """What a turn's context holds. company and year are the resolved values (the question's own, else the
    previous turn's); company_from and year_from say where each came from ("question", "previous answer",
    "previous question", or "" when there is none). line is the context line for the writers ("" on a first
    turn); its "Carried from it" part lists only what came from the previous turn."""
    follow_up: bool
    reason: str
    company: str | None
    year: str | None
    line: str
    company_from: str = ""
    year_from: str = ""

    def trace(self) -> dict[str, Any]:
        return {"follow_up": self.follow_up, "reason": self.reason, "company": self.company, "year": self.year}

    def carried(self) -> dict[str, Any]:
        """The state's `carried`: the resolved company and year, and why."""
        return {"company": self.company, "year": self.year, "reason": self.reason}


def carry(question: str, previous: Mapping[str, Any] | None, names: list[str]) -> Carry:
    """The follow-up verdict, the company and year, and the context line. company: the question's own, else
    the previous answer's first, else the previous question's first. year: the question's own, else the
    previous question's, else the previous answer's. line: `Earlier in this conversation: "<previous
    question>"; its answer began: "<first answer_chars characters of the answer>".`, then, for whatever was
    carried from the previous turn, ` Carried from it: company <company>; year <year>.`"""
    follow_up, reason = is_follow_up(question, previous, names)
    if not previous:
        return Carry(follow_up, reason, None, None, "")
    asked, answered = previous_question(previous), previous_answer(previous)
    company, company_from = None, ""
    for source, text in (("question", question), ("previous answer", answered), ("previous question", asked)):
        found = entities(text, names)
        if found:
            company, company_from = found[0], source
            break
    year, year_from = None, ""
    for source, text in (("question", question), ("previous question", asked), ("previous answer", answered)):
        found = years(text)
        if found:
            year, year_from = found[0], source
            break
    line = (f'Earlier in this conversation: "{asked}"; '
            f'its answer began: "{answered[:int(cfg.context_carry.answer_chars)]}".')
    parts = []
    if company and company_from != "question":
        parts.append(f"company {company}")
    if year and year_from != "question":
        parts.append(f"year {year}")
    if parts:
        line += f" Carried from it: {'; '.join(parts)}."
    return Carry(follow_up, reason, company, year, line, company_from, year_from)


# ── What the answering steps read ────────────────────────────────────────────
def question_context(state_dict: Mapping[str, Any]) -> str:
    """The context line, ready to follow a question in a prompt ("\\n\\n" + the line); "" when the state
    has none, so a prompt without context is unchanged."""
    line = state_dict.get("conversation_context")
    return f"\n\n{line}" if line else ""


def search_suffix(state_dict: Mapping[str, Any], query: str) -> str:
    """` <company> <year>` for the document search text, for the carried slots the query does not already
    hold; "" without a context line (so the search text is unchanged) or when nothing is missing."""
    carried = state_dict.get("carried")
    if not state_dict.get("conversation_context") or not carried:
        return ""
    reduced = _letters_digits(query)
    company, year = carried.get("company"), carried.get("year")
    slots = []
    if company and _letters_digits(company) not in reduced:
        slots.append(company)
    if year and year not in years(query):
        slots.append(year)
    return "".join(f" {slot}" for slot in slots)

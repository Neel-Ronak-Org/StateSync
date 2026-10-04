"""
orchestrator/number_grounding.py
Every figure in an answer's prose, traced to where it came from (docs/V3_PLAN.md
Phase 3c, "answers you can check").

WHY. The SQL can be right and the rows correct while the sentence written about
them states a number that is in neither: a misread row, a sum the model did in
its head, a percentage of the wrong base. Readers trust the sentence more than
the table, so each figure in it is matched, in code and with no model call,
against what the answer is built on:

  cell       a value in the result rows (a fraction written as a percent counts)
  text       part of a text value in the result ("Seller Store 28", "port=5432")
  aggregate  a column's sum, average, minimum or maximum, or the row count
  question   a number the question itself contains ("top 5", "last 30 days")
  passage    for document answers, a figure written in a cited passage
  derived    a ratio or difference of two figures traced above ("97.0%" from two counts)
  shown      (version 2) how many rows the answer writer was shown, when that is fewer than the
             result has ("the first 50 rows"); a "remaining 610" then traces as derived

A figure matches within the rounding its own digits imply: "1.2M" matches
anything from 1,150,000 to 1,250,000; "45%" matches 0.4461 or 45.3. Figures
that match nothing are returned as unmatched; the caller turns them into a
caveat, which also moves the answer out of the "checked" evidence tier.

Not figures: four-digit years, dates and times, numbered-list markers ("1. "
at the start of a line), and digits inside identifiers (S001, LP003, order_12).
Version 2 also skips inline enumerations "(1) ... (2) ...".

Versions ([pipeline] grounding_version): v1 is what shipped and what the BIRD pilot measured.
Every one of the pilot's 7 grounding flags came from the pipeline's own wording ("the first 50
rows", "(1) (2) (3)", a remainder of the two), none from a wrong figure
(docs/eval/bird_pilot_report.md); v2 traces those.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9,  # noqa: literals - unit words, not settings
          "billion": 1e9, "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7}  # noqa: literals - unit words, not settings

# A number not glued to letters or digits on its left (so S001 and 2026-09 parts are skipped),
# optionally followed by a scale word and/or a percent sign.
_NUMBER = re.compile(
    r"(?<![\w.\-/:])"
    r"(?P<sign>-)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?:\s?(?P<scale>k|thousand|mn|m|million|bn|b|billion|lakhs?|crores?)\b)?"
    r"(?P<pct>\s?%|\s?percent\b)?"
    r"(?![\w\-/:]|\.\d)",
    re.IGNORECASE,
)
_DATE_OR_TIME = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}:\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_YEAR = re.compile(r"^(1[89]\d{2}|2[01]\d{2})$")
# A numbered-list marker ("1. ", "2) ") at the start of a line is not a figure.
_LIST_MARKER = re.compile(r"^[ \t]*\d{1,3}[.)](?=\s)", re.MULTILINE)
# Version 2: an inline enumerator "(1)", only when the text also has "(2)", so a lone "(5)" stays a figure.
_ENUMERATOR = re.compile(r"\((\d{1,2})\)")


@dataclass(frozen=True)
class Mention:
    text: str
    start: int
    end: int
    value: float
    tolerance: float
    percent: bool


def _tolerance(num: str, scale: float) -> float:
    decimals = len(num.split(".", 1)[1]) if "." in num else 0
    return 0.5 * (10 ** -decimals) * scale  # noqa: literals - half a unit of the last digit


_DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2212": "-"})


def extract_mentions(text: str, enumerations: bool = False) -> list[Mention]:
    """The figures in `text`, with the rounding tolerance each one's digits imply. With
    `enumerations`, inline "(1) ... (2)" markers are not figures (version 2)."""
    # Models write dates with non-breaking hyphens ("2026\u201109\u201120"); one-for-one
    # replacement keeps every offset valid.
    text = text.translate(_DASHES)
    blocked = [(m.start(), m.end()) for m in _DATE_OR_TIME.finditer(text)]
    blocked += [(m.start(), m.end()) for m in _LIST_MARKER.finditer(text)]
    if enumerations and {"1", "2"} <= set(_ENUMERATOR.findall(text)):
        blocked += [(m.start(), m.end()) for m in _ENUMERATOR.finditer(text)]
    mentions: list[Mention] = []
    for m in _NUMBER.finditer(text):
        if any(s <= m.start() < e for s, e in blocked):
            continue
        num = m.group("num").replace(",", "")
        scale_word = (m.group("scale") or "").lower()
        percent = bool(m.group("pct"))
        if not scale_word and not percent and _YEAR.match(num):
            continue
        scale = _SCALE.get(scale_word, 1.0)
        value = float(num) * scale * (-1 if m.group("sign") else 1)
        mentions.append(Mention(text=m.group(0).strip(), start=m.start(), end=m.end(), value=value,
                                tolerance=_tolerance(num, scale), percent=percent))
    return mentions


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        try:
            number = float(value.replace(",", ""))
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    try:  # Decimal and friends
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _close(mention: Mention, candidate: float) -> bool:
    targets = [candidate]
    if mention.percent:
        targets.append(candidate * 100)          # noqa: literals - a fraction written as a percent
    return any(abs(abs(mention.value) - abs(t)) <= mention.tolerance + 1e-9 * max(1.0, abs(t)) for t in targets)  # noqa: literals - float comparison slack


def _aggregates(rows: list[dict], columns: list[str]) -> list[tuple[str, str, float]]:
    found: list[tuple[str, str, float]] = [("count", "rows", float(len(rows)))]
    for column in columns:
        values = [v for v in (_numeric(r.get(column)) for r in rows) if v is not None]
        if len(values) < 2:  # noqa: literals - one value is already a cell
            continue
        found += [("sum", column, sum(values)), ("avg", column, sum(values) / len(values)),
                  ("min", column, min(values)), ("max", column, max(values))]
    return found


def _in_text_cell(mention: Mention, rows: list[dict], columns: list[str]) -> tuple[int, str] | None:
    """(row, column) of a text value containing the figure as a whole token ("Seller Store 28")."""
    token = re.compile(r"(?<![\w.])" + re.escape(mention.text) + r"(?![\w])")
    for index, row in enumerate(rows):
        for column in columns:
            value = row.get(column)
            if isinstance(value, str) and token.search(value):
                return index, column
    return None


def _derived(mention: Mention, grounded: list[float]) -> bool:
    """A ratio or difference of two figures already traced in this answer ("97.0%" of 96,478 / 99,441)."""
    for a in grounded:
        for b in grounded:
            if a == b or b == 0:
                continue
            for candidate in (a / b, a - b):
                if _close(mention, candidate):
                    return True
    return False


def applies(route: str | None, executed_sql: str | None) -> bool:
    """Whether an answer has anything to ground against. A database answer whose query never ran has
    no result: its text explains the failure and quotes error codes, which would come back as
    "figures not found in the result" on top of the error (seen on BIRD: 3 of grounding's 4 catches,
    docs/eval/bird_v2_report.md). Document and hybrid answers keep their passages."""
    return not (route == "sql" and not executed_sql)


def ground(answer_text: str, rows: list[dict], columns: list[str], question: str = "",
           passages: list[str] | None = None, version: int = 1,
           shown_rows: int | None = None) -> dict[str, Any]:
    """Each figure in the answer with where it came from; see the module docstring. Version 2
    takes `shown_rows`, how many result rows the answer writer was shown."""
    v2 = version >= 2  # noqa: literals - grounding version
    shown = shown_rows if v2 and shown_rows and 0 < shown_rows < len(rows) else None
    question_values = [m.value for m in extract_mentions(question or "")]
    passage_mentions = [m for p in (passages or []) for m in extract_mentions(p)]
    aggregates = _aggregates(rows, columns)
    results: list[dict[str, Any]] = []
    for mention in extract_mentions(answer_text or "", enumerations=v2):
        source: dict[str, Any] | None = None
        for index, row in enumerate(rows):
            for column in columns:
                cell = _numeric(row.get(column))
                if cell is not None and _close(mention, cell):
                    source = {"kind": "cell", "row": index, "column": column}
                    break
            if source:
                break
        if source is None:
            for name, column, value in aggregates:
                if _close(mention, value):
                    source = {"kind": "aggregate", "aggregate": name, "column": column}
                    break
        if source is None and shown is not None and _close(mention, float(shown)):
            source = {"kind": "shown"}
        if source is None and any(abs(mention.value - q) <= mention.tolerance for q in question_values):
            source = {"kind": "question"}
        if source is None and any(abs(mention.value - p.value) <= max(mention.tolerance, p.tolerance)
                                  for p in passage_mentions):
            source = {"kind": "passage"}
        if source is None:
            where = _in_text_cell(mention, rows, columns)
            if where:
                source = {"kind": "text", "row": where[0], "column": where[1]}
        results.append({"text": mention.text, "start": mention.start, "end": mention.end,
                        "matched": source is not None, "source": source, "_value": mention})
    # Second pass: arithmetic on figures traced above (a share computed from two counts).
    grounded = [r["_value"].value for r in results if r["matched"] and r["source"]["kind"] != "text"]
    if shown is not None:
        grounded.append(float(shown))   # "remaining 610" of 660 needs the 50 shown even when unsaid
    for r in results:
        mention = r.pop("_value")
        if not r["matched"] and _derived(mention, grounded):
            r["matched"], r["source"] = True, {"kind": "derived"}
    unmatched = [r["text"] for r in results if not r["matched"]]
    return {"figures": results, "matched": len(results) - len(unmatched), "unmatched": unmatched}


def caveat(unmatched: list[str]) -> str | None:
    """The caveat for figures that match nothing, or None."""
    if not unmatched:
        return None
    listed = ", ".join(unmatched)
    noun = "figure" if len(unmatched) == 1 else "figures"
    return f"The explanation mentions {noun} not found in the result ({listed}); check them against the table."

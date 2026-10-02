"""
orchestrator/table_selection.py
Schema linking for large databases (docs/specs/table_selection.md): pick the few tables a
question needs, instead of showing the SQL model every table.

WHY. Shown all 152 FIBEN tables, the SQL prompt was 8,300-9,000 tokens: above Groq's free-tier
limit, so every question was refused before it ran, and a real enterprise database is larger
still. Selection here is deterministic and model-free:
  - a card per table: its name, column names and comments split into words, plus cached
    example values (so a value such as "Alphabet" points at the table that holds it);
  - BM25 over the cards, with words AND their character 4-grams, because legacy and ontology
    schemas run words together (haslasttradedvalue) and whole words alone cannot match them;
  - the top tables, plus their foreign-key neighbours (join paths survive), plus any table a
    matched approved example uses, under a hard cap.
Small schemas (at most max_tables_full) are shown whole, exactly as before.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

_WORD = re.compile(r"[A-Za-z]+|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
NGRAM, NGRAM_MIN_WORD = 4, 4  # noqa: literals - character 4-grams, fixed in docs/specs/table_selection.md before measuring
K1, B = 1.2, 0.75  # noqa: literals - BM25's textbook constants, as the RAG keyword leg (Robertson & Zaragoza)


def words(text: str) -> list[str]:
    """Lower-case words, splitting snake_case and camelCase ('OrderItems' -> order, items)."""
    out: list[str] = []
    for chunk in _WORD.findall(text or ""):
        out.extend(part.lower() for part in _CAMEL.findall(chunk) or [chunk])
    return [w for w in out if len(w) > 1]


def tokens(text: str) -> list[str]:
    """Words plus the character 4-grams of every word of 4+ characters."""
    out: list[str] = []
    for w in words(text):
        out.append(w)
        if len(w) >= NGRAM_MIN_WORD:
            out.extend("#" + w[i:i + NGRAM] for i in range(len(w) - NGRAM + 1))
    return out


def card(table: str, description: dict, values: dict[str, list[str]] | None = None) -> str:
    """The text a table is matched on: name, columns, comments, example values."""
    parts = [table]
    for col in description.get("columns", []):
        parts.append(col.get("name", ""))
        if col.get("comment"):
            parts.append(col["comment"])
    for key, vals in (values or {}).items():
        if key.split(".", 1)[0] == table:
            parts.extend(str(v) for v in vals)
    return " ".join(parts)


def rank(question: str, cards: dict[str, str]) -> list[tuple[str, float]]:
    """Tables by BM25 score of the question against their cards, best first (ties by name)."""
    docs = {t: Counter(tokens(text)) for t, text in cards.items()}
    n = len(docs)
    avgdl = (sum(sum(c.values()) for c in docs.values()) / n) if n else 1.0
    df: Counter = Counter()
    for c in docs.values():
        df.update(set(c))
    q = set(tokens(question))
    scored = []
    for table, c in docs.items():
        dl = sum(c.values()) or 1
        s = 0.0
        for term in q:
            tf = c.get(term, 0)
            if tf:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))  # noqa: literals - the RSJ smoothing term of BM25 itself
                s += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * dl / avgdl))
        scored.append((table, s))
    return sorted(scored, key=lambda x: (-x[1], x[0]))


def fk_graph(descriptions: dict[str, dict]) -> dict[str, set[str]]:
    """Undirected table graph from describe_table foreign keys."""
    graph: dict[str, set[str]] = defaultdict(set)
    for table, desc in descriptions.items():
        for fk in desc.get("foreign_keys", []):
            other = fk.get("references_table")
            if other and other != table and other in descriptions:
                graph[table].add(other)
                graph[other].add(table)
    return graph


def select(question: str, descriptions: dict[str, dict], values: dict[str, list[str]] | None,
           top_tables: int, max_selected: int, hops: int, must_include: list[str] | None = None,
           empty: set[str] | None = None) -> list[str]:
    """The tables to show, capped at max_selected:
      1. must_include (approved-example tables);
      2. the top-ranked tables, with empty tables ranked after every table that has rows
         (an empty table cannot hold an answer);
      3. foreign-key neighbours, expanded BEST TABLE FIRST: all of rank 1's neighbours before
         rank 2's, so the best match keeps its join partners when the cap bites.
    Points 2 (empty last) and 3 (best-first order) were changed on 2026-10-02 after the first
    FIBEN measurement (docs/specs/table_selection.md, "Post-hoc changes")."""
    empty = empty or set()
    ranked = rank(question, {t: card(t, d, values) for t, d in descriptions.items()})
    ranked = [r for r in ranked if r[0] not in empty] + [r for r in ranked if r[0] in empty]
    chosen: list[str] = [t for t in (must_include or []) if t in descriptions]
    for t, _ in ranked[:top_tables]:
        if t not in chosen:
            chosen.append(t)
    graph = fk_graph(descriptions)
    order = {t: i for i, (t, _) in enumerate(ranked)}
    frontier = list(chosen)
    for _ in range(hops):
        nxt: list[str] = []
        for t in frontier:  # best first
            for n in sorted(graph.get(t, ()), key=lambda x: (x in empty, order.get(x, len(order)))):
                if n not in chosen and n not in nxt:
                    nxt.append(n)
        chosen.extend(nxt)
        frontier = nxt
    return chosen[:max_selected]


def tables_in_sql(sql: str) -> list[str]:
    """Table names a SQL statement reads (schema prefix dropped); [] if it does not parse."""
    try:
        from pglast import parse_sql
        from pglast.visitors import Visitor
    except ImportError:
        return []
    found: list[str] = []

    class _V(Visitor):
        def visit_RangeVar(self, ancestors: Any, node: Any) -> None:  # noqa: N802 -- pglast naming
            if node.relname not in found:
                found.append(node.relname)
    try:
        _V()(parse_sql(sql))
    except Exception:  # noqa: BLE001 -- an unparsable example contributes no tables
        return []
    return found

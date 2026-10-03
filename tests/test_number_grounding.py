"""
tests/test_number_grounding.py
Every figure in an answer's prose traced to its source (orchestrator/number_grounding.py).

WHY THIS EXISTS. The value of the check is in both directions: a figure that
is in the result must match (or every answer gets a false caveat), and a
figure that is not must be flagged (or the check is decoration). Identifiers,
dates and years must not count as figures, and a figure matches only within
the rounding its own digits imply.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import number_grounding as g  # noqa: E402

ROWS = [{"city": "Mumbai", "n": 134, "total": 1204321.0, "share": 0.3351},
        {"city": "Delhi", "n": 133, "total": 35687.5, "share": 0.2963}]
COLS = ["city", "n", "total", "share"]


def _texts(text: str) -> list[str]:
    return [m.text for m in g.extract_mentions(text)]


def test_identifiers_dates_and_years_are_not_figures():
    assert _texts("Seller S001 and LP003 on 2026-09-01 at 14:30, order_12, in 2025 and 03/04/2026.") == []


def test_numbered_list_markers_are_not_figures():
    # Seen live: a ranked answer's "1." and "2." were flagged as unfound figures.
    text = "Top categories:\n1. **health_beauty** - 1,441,248.07\n2) watches_gifts - 9.22%\n  3. bed_bath - 3 items"
    assert _texts(text) == ["1,441,248.07", "9.22%", "3"]


def test_formats_are_read_with_their_scale_and_precision():
    mentions = {m.text: m for m in g.extract_mentions("R$ 1.2M, 3k items, 45%, 12.5 percent, 1,234.56, -7 and 2 lakh")}
    assert mentions["1.2M"].value == 1_200_000 and mentions["1.2M"].tolerance == 50_000
    assert mentions["3k"].value == 3000
    assert mentions["45%"].percent and mentions["12.5 percent"].percent
    assert mentions["1,234.56"].value == 1234.56 and mentions["1,234.56"].tolerance == 0.005
    assert mentions["-7"].value == -7
    assert mentions["2 lakh"].value == 200_000


def test_figures_in_the_result_match_cells_totals_and_percentages():
    text = "Mumbai leads with 1.2M across 134 orders (33.5%); together 267 orders and 1,240,008.5 in total."
    result = g.ground(text, ROWS, COLS)
    assert result["unmatched"] == []
    sources = {f["text"]: f["source"] for f in result["figures"]}
    assert sources["1.2M"] == {"kind": "cell", "row": 0, "column": "total"}
    assert sources["33.5%"] == {"kind": "cell", "row": 0, "column": "share"}
    assert sources["267"] == {"kind": "aggregate", "aggregate": "sum", "column": "n"}
    assert sources["1,240,008.5"]["aggregate"] == "sum"


def test_invented_figures_are_flagged():
    result = g.ground("Mumbai grew 12% and averages 4,105 per order.", ROWS, COLS)
    assert result["unmatched"] == ["12%", "4,105"]
    assert g.caveat(result["unmatched"]).startswith("The explanation mentions figures not found in the result (12%, 4,105)")


def test_precision_decides_the_match():
    assert g.ground("about 1.2M", [{"v": 1_230_000}], ["v"])["unmatched"] == []
    assert g.ground("exactly 1,200,000", [{"v": 1_230_000}], ["v"])["unmatched"] == ["1,200,000"]
    assert g.ground("35,687.49", ROWS, COLS)["unmatched"] == ["35,687.49"]


def test_the_question_and_cited_passages_are_sources_too():
    result = g.ground("The top 5 cities; the SLA is 36 hours.", ROWS, COLS, question="top 5 cities",
                      passages=["Metro NCR | 36 hours | 2% penalty"])
    kinds = [f["source"]["kind"] for f in result["figures"]]
    assert kinds == ["question", "passage"]


def test_figures_inside_text_values_and_unicode_dates_are_not_flagged():
    # Seen in the pilot over stored answers (docs/research): both were false alarms.
    rows = [{"name": "Seller Store 28", "day": "2026-09-20", "total": 31642}]
    result = g.ground("Seller Store 28 leads on 2026\u201109\u201120 with 31,642.", rows, ["name", "day", "total"])
    assert result["unmatched"] == []
    assert [f["source"]["kind"] for f in result["figures"]] == ["text", "cell"]


def test_arithmetic_on_traced_figures_is_derived_not_invented():
    rows = [{"delivered": 96478, "total": 99441}]
    result = g.ground("96,478 of 99,441 were delivered, about 97.0%; 2,963 were not.", rows, ["delivered", "total"])
    assert result["unmatched"] == []
    assert [f["source"]["kind"] for f in result["figures"]] == ["cell", "cell", "derived", "derived"]
    assert g.ground("96,478 of 99,441, about 91.0%.", rows, ["delivered", "total"])["unmatched"] == ["91.0%"]


def test_no_figures_no_caveat():
    assert g.ground("Mumbai leads.", ROWS, COLS) == {"figures": [], "matched": 0, "unmatched": []}
    assert g.caveat([]) is None


def test_offsets_point_at_the_figure_in_the_text():
    text = "Delhi had 133 orders."
    figure = g.ground(text, ROWS, COLS)["figures"][0]
    assert text[figure["start"]:figure["end"]].strip() == "133"

#!/usr/bin/env python3
"""
scripts/gen_phase_e2_fixture.py
Generates fixtures/golden/phase_e2.json — E2 Deliverable 6: 30 chart-warranting
queries with frozen reference Vega-Lite specifications.

Unlike the phase_c1/c2/d/e1 generators, this one makes NO live calls. A
reference spec is a design decision about what the correct chart for a question
is -- it is authored, not observed -- so deriving it from a running system
would just freeze whatever the model happened to emit, which is exactly the
circularity E2-V1 exists to avoid. The generator exists so the fixture is
reproducible and diffable, not because anything is measured here.

Every reference spec is A7-conformant by construction, which is what lets
E2-V4 use them as the "30 valid specs" it renders under CSP.

`data.values` carries a small illustrative sample. SpecScorer deliberately
does not score `data` (only mark / encoding / transform, per Appendix 1's
weights), so these values affect rendering only, never M7.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_e2.json"

SCHEMA_URL = "https://vega.github.io/schema/vega-lite/v5.json"


def enc(channel: str, field: str, type_: str, aggregate: str | None = None,
        title: str | None = None) -> dict:
    d: dict = {"field": field, "type": type_}
    if aggregate:
        d["aggregate"] = aggregate
    if title:
        d["title"] = title
    return d


def spec(mark: str, encoding: dict, values: list[dict],
         transform: list[dict] | None = None, title: str | None = None) -> dict:
    s: dict = {
        "$schema": SCHEMA_URL,
        "mark": mark,
        "data": {"values": values},
        "encoding": encoding,
    }
    if transform:
        s["transform"] = transform
    if title:
        s["title"] = title
    return s


# Small reusable sample payloads. Deliberately tiny — they exist so the spec
# renders, not so it says anything.
CAT = [{"k": "A", "v": 12}, {"k": "B", "v": 7}, {"k": "C", "v": 19}]
TS = [{"t": "2026-01-01", "v": 5}, {"t": "2026-02-01", "v": 9}, {"t": "2026-03-01", "v": 14}]
XY = [{"x": 1, "y": 2}, {"x": 3, "y": 5}, {"x": 6, "y": 4}]
GEOISH = [{"lat": 19.07, "lon": 72.87, "v": 3}, {"lat": 28.61, "lon": 77.20, "v": 8}]

# (id, question, mark, encoding, values, transform, title)
QUERIES: list[tuple] = [
    ("e2_001", "How many orders did each seller place last quarter?",
     "bar", {"x": enc("x", "k", "nominal", title="Seller"), "y": enc("y", "v", "quantitative", "count")}, CAT, None, "Orders per seller"),
    ("e2_002", "Show total order value per month.",
     "line", {"x": enc("x", "t", "temporal"), "y": enc("y", "v", "quantitative", "sum")}, TS, None, None),
    ("e2_003", "Plot delivery delay against order value.",
     "point", {"x": enc("x", "x", "quantitative"), "y": enc("y", "y", "quantitative")}, XY, None, None),
    ("e2_004", "What is the cumulative dispute count over time?",
     "area", {"x": enc("x", "t", "temporal"), "y": enc("y", "v", "quantitative", "sum")}, TS, None, None),
    ("e2_005", "Show the distribution of fulfillment attempt counts.",
     "tick", {"x": enc("x", "v", "quantitative")}, CAT, None, None),
    ("e2_006", "Break down order volume by city and channel.",
     "rect", {"x": enc("x", "k", "nominal"), "y": enc("y", "k", "nominal"), "color": enc("color", "v", "quantitative", "sum")}, CAT, None, None),
    ("e2_007", "What share of orders is B2B versus B2C?",
     "arc", {"theta": enc("theta", "v", "quantitative", "sum"), "color": enc("color", "k", "nominal")}, CAT, None, None),
    ("e2_008", "Average order value per logistics provider.",
     "bar", {"x": enc("x", "k", "nominal"), "y": enc("y", "v", "quantitative", "mean")}, CAT, None, None),
    ("e2_009", "Median delivery time by seller category.",
     "bar", {"x": enc("x", "k", "nominal"), "y": enc("y", "v", "quantitative", "median")}, CAT, None, None),
    ("e2_010", "Trend of late deliveries per month.",
     "line", {"x": enc("x", "t", "temporal"), "y": enc("y", "v", "quantitative", "count")}, TS, None, "Late deliveries"),
    ("e2_011", "Minimum contracted SLA hours per seller category.",
     "bar", {"x": enc("x", "k", "nominal"), "y": enc("y", "v", "quantitative", "min")}, CAT, None, None),
    ("e2_012", "Maximum order value per buyer app.",
     "bar", {"x": enc("x", "k", "nominal"), "y": enc("y", "v", "quantitative", "max")}, CAT, None, None),
    ("e2_013", "Order count by event type, sorted descending.",
     "bar", {"x": {"field": "k", "type": "nominal", "sort": "-y"}, "y": enc("y", "v", "quantitative", "count")}, CAT, None, None),
    ("e2_014", "Delivery events over time by provider.",
     "line", {"x": enc("x", "t", "temporal"), "y": enc("y", "v", "quantitative", "count"), "color": enc("color", "t", "nominal")}, TS, None, None),
    ("e2_015", "Scatter of promised versus actual delivery hours.",
     "point", {"x": enc("x", "x", "quantitative", title="Promised"), "y": enc("y", "y", "quantitative", title="Actual")}, XY, None, None),
    ("e2_016", "Total line value per item category.",
     "bar", {"x": enc("x", "k", "nominal"), "y": enc("y", "v", "quantitative", "sum")}, CAT, None, None),
    ("e2_017", "Unresolved disputes by category.",
     "bar", {"y": enc("y", "k", "nominal"), "x": enc("x", "v", "quantitative", "count")}, CAT, None, None),
    ("e2_018", "Fulfillment status mix.",
     "arc", {"theta": enc("theta", "v", "quantitative", "count"), "color": enc("color", "k", "nominal")}, CAT, None, None),
    ("e2_019", "Order value distribution across locations.",
     "tick", {"x": enc("x", "v", "quantitative"), "y": enc("y", "k", "nominal")}, CAT, None, None),
    ("e2_020", "Monthly average order value with a filter on delivered orders.",
     "line", {"x": enc("x", "t", "temporal"), "y": enc("y", "v", "quantitative", "mean")}, TS,
     # Predicate-object form, not the expression-string form: a string filter
     # is expression text and is rejected by the BD7 audit in both validators.
     [{"filter": {"field": "k", "equal": "delivered"}}], None),
    ("e2_021", "Seller count per state.",
     "bar", {"x": enc("x", "k", "nominal"), "y": enc("y", "v", "quantitative", "count")}, CAT, None, None),
    ("e2_022", "Heatmap of delays by city and month.",
     "rect", {"x": enc("x", "t", "temporal"), "y": enc("y", "k", "nominal"), "color": enc("color", "v", "quantitative", "mean")}, TS, None, None),
    ("e2_023", "Cumulative order value by channel over time.",
     "area", {"x": enc("x", "t", "temporal"), "y": enc("y", "v", "quantitative", "sum"), "color": enc("color", "k", "nominal")}, TS, None, None),
    ("e2_024", "Top catalog items by quantity sold.",
     "bar", {"x": {"field": "k", "type": "nominal", "sort": "-y"}, "y": enc("y", "v", "quantitative", "sum")}, CAT, None, "Top items"),
    ("e2_025", "Dispute resolution latency per category.",
     "bar", {"x": enc("x", "k", "nominal"), "y": enc("y", "v", "quantitative", "mean")}, CAT, None, None),
    ("e2_026", "Orders per hub location.",
     "point", {"x": enc("x", "lon", "quantitative"), "y": enc("y", "lat", "quantitative"), "size": enc("size", "v", "quantitative", "sum")}, GEOISH, None, None),
    ("e2_027", "Attempt counts across fulfillments.",
     "bar", {"x": enc("x", "v", "ordinal"), "y": enc("y", "v", "quantitative", "count")}, CAT, None, None),
    ("e2_028", "Order value trend with an aggregate transform.",
     "line", {"x": enc("x", "t", "temporal"), "y": enc("y", "v", "quantitative")}, TS,
     [{"aggregate": [{"op": "sum", "field": "v", "as": "v"}], "groupby": ["t"]}], None),
    ("e2_029", "B2B share of value per seller.",
     "bar", {"x": enc("x", "k", "nominal"), "y": enc("y", "v", "quantitative", "sum"), "color": enc("color", "k", "nominal")}, CAT, None, None),
    ("e2_030", "Delivery event volume by type over time.",
     "area", {"x": enc("x", "t", "temporal"), "y": enc("y", "v", "quantitative", "count")}, TS, None, None),
]


def main() -> None:
    queries = []
    for qid, text, mark, encoding, values, transform, title in QUERIES:
        queries.append({
            "id": qid,
            "lang": "en",
            "text": text,
            "expected_chart": True,
            "reference_spec": spec(mark, encoding, values, transform, title),
        })

    payload = {
        "version": 1,
        "phase": "e2",
        "generated_by": "scripts/gen_phase_e2_fixture.py",
        "note": (
            "30 chart-warranting queries with frozen A7-conformant reference "
            "Vega-Lite specs (E2 Deliverable 6). Reference specs are authored, "
            "not observed -- see the generator docstring. data.values is "
            "illustrative only and is not scored by SpecScorer."
        ),
        "queries": queries,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")

    marks = sorted({q["reference_spec"]["mark"] for q in queries})
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}: {len(queries)} queries")
    print(f"  marks covered: {marks}")
    print(f"  with transform: {sum(1 for q in queries if 'transform' in q['reference_spec'])}")


if __name__ == "__main__":
    main()

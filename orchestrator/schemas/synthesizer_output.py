"""
orchestrator/schemas/synthesizer_output.py
The answer's output contract: {answer_text, chart, citations}, checked against a JSON Schema.

Why a chart is checked at all: the chart is a Vega-Lite specification the browser draws, and Vega-Lite can
evaluate expression strings. So only a fixed list of chart properties is allowed, and no expression text
anywhere. The check runs twice, independently: here, before the response leaves the server, and in the web
app before drawing (web/src/charts/a7.ts). A chart that fails is dropped (chart: null) and the failure is
logged; the answer text still goes out. A failing chart is never repaired: a silent repair would hide a
problem.
"""

from __future__ import annotations

import logging
from typing import Any

import jsonschema
from pydantic import BaseModel, Field, field_validator

from config_loader import cfg

logger = logging.getLogger(__name__)

# ── JSON Schema (limits from config/statesync.toml) ───────────────────────────
def _build_schema() -> dict[str, Any]:
    """The JSON Schema, with its limits read from config so they stay in step with the rest of the system."""
    return {
        "$id": "statesync://schemas/synthesizer-output.json",
        "type": "object",
        "required": ["answer_text", "chart", "citations"],
        "additionalProperties": False,
        "properties": {
            "answer_text": {"type": "string", "maxLength": cfg.sql.answer_max_chars},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["kind", "ref"],
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"enum": ["sql", "chunk"]},
                        "ref":  {"type": "string"},
                        "page": {"type": ["integer", "null"]},
                        "title": {"type": "string", "maxLength": cfg.library.title_max_chars},
                    },
                },
            },
            "chart": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "required": ["mark", "encoding", "data"],
                        "additionalProperties": False,
                        "properties": {
                            "$schema":  {"const": "https://vega.github.io/schema/vega-lite/v5.json"},
                            "mark":     {"enum": ["bar", "line", "point", "area", "tick", "rect", "arc"]},
                            "data": {
                                "type": "object",
                                "required": ["values"],
                                "additionalProperties": False,
                                "properties": {
                                    "values": {
                                        "type": "array",
                                        "items": {"type": "object"},
                                        "maxItems": cfg.sql.chart_max_data_rows,
                                    },
                                },
                            },
                            "encoding": {
                                "type": "object",
                                "additionalProperties": {
                                    "type": "object",
                                    "properties": {
                                        "field":     {"type": "string"},
                                        "type":      {"enum": ["quantitative", "nominal", "ordinal", "temporal"]},
                                        "aggregate": {"enum": ["sum", "mean", "count", "min", "max", "median"]},
                                        "title":     {"type": "string"},
                                        "sort":      {"type": ["string", "array", "null"]},
                                    },
                                    "additionalProperties": False,
                                },
                            },
                            "transform": {
                                "type": "array",
                                "maxItems": cfg.sql.chart_max_transforms,
                                "items": {"type": "object", "additionalProperties": True},
                            },
                            "title":  {"type": "string"},
                            "width":  {"type": "integer", "minimum": cfg.sql.chart_min_dimension, "maximum": cfg.sql.chart_max_width},
                            "height": {"type": "integer", "minimum": cfg.sql.chart_min_dimension, "maximum": cfg.sql.chart_max_height},
                        },
                    },
                ],
            },
        },
    }


SYNTHESIZER_OUTPUT_SCHEMA: dict[str, Any] = _build_schema()

_validator = jsonschema.Draft7Validator(SYNTHESIZER_OUTPUT_SCHEMA)


# ── Pydantic model (for type-safety inside orchestrator) ──────────────────────
class Citation(BaseModel):
    model_config = {"extra": "forbid"}
    kind: str
    ref:  str
    page: int | None = None
    title: str | None = None   # the cited document's title

    @field_validator("kind")
    @classmethod
    def kind_must_be_sql_or_chunk(cls, v: str) -> str:
        if v not in ("sql", "chunk"):
            raise ValueError(f"Citation.kind must be 'sql' or 'chunk', got {v!r}")
        return v


class SynthesizerOutput(BaseModel):
    model_config = {"extra": "forbid"}
    answer_text: str = Field(max_length=4000)  # noqa: literals — Pydantic class-level constraint; enforced by JSON Schema validator above
    chart:       dict | None = None
    citations:   list[Citation] = Field(default_factory=list)


# ── Expression-string audit ───────────────────────────────────────────────────
# The schema forbids unknown properties everywhere except inside `transform`, whose items it leaves open, so
# a {"calculate": "<expression>"} transform would pass the schema alone (fixtures/malicious_specs.json ms_05).
# This second check walks `transform` and rejects any expression-bearing property.
# Always an expression string in Vega/Vega-Lite, wherever it appears.
_EXPRESSION_KEYS = frozenset({
    "expr", "signal", "calculate", "test", "init", "update", "on",
})
# Dual-form: `filter` takes EITHER an expression string ("datum.x > 5") or a
# non-executable predicate object ({"field": "x", "gt": 5}). Only the string
# form is an expression, so only the string form is rejected -- rejecting the
# key outright would ban ordinary filtering for no security gain.
_DUAL_FORM_EXPRESSION_KEYS = frozenset({"filter"})


def _audit_expression_strings(node: Any, path: str = "chart/transform") -> list[str]:
    """Every expression-bearing property in a `transform` subtree. A chart that needs one (`calculate`, say)
    is rejected on purpose: an unsupported chart is a correct rejection, not a reason to allow expressions."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _EXPRESSION_KEYS:
                found.append(f"[{path}/{key}] expression-bearing property is not permitted")
            elif key in _DUAL_FORM_EXPRESSION_KEYS and isinstance(value, str):
                found.append(
                    f"[{path}/{key}] string form is an expression; use the "
                    f"predicate-object form instead"
                )
            found.extend(_audit_expression_strings(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found.extend(_audit_expression_strings(item, f"{path}/{i}"))
    return found


def _flatten_errors(errors: Any) -> list[tuple[str, str]]:
    """
    Expands jsonschema's `oneOf`/`anyOf` errors into their underlying causes.

    Without this, `chart`'s `oneOf` [null, object] collapses every chart
    violation into one generic "is not valid under any of the given schemas"
    whose message merely embeds the whole spec; the log should name each real reason.

    The trivial "not of type 'null'" branch of that oneOf is dropped: a
    non-null chart failing the null branch is never the interesting reason.

    Sub-error paths are already absolute (jsonschema prefixes them as it
    descends), so they are used as-is rather than re-joined onto the parent's.
    """
    out: list[tuple[str, str]] = []
    for err in errors:
        context = list(getattr(err, "context", None) or [])
        if context:
            informative = [c for c in context if "is not of type 'null'" not in c.message]
            if informative:
                out.extend(_flatten_errors(informative))
                continue
        out.append(("/".join(str(p) for p in err.absolute_path), err.message))
    return out


def validate_synthesizer_output(raw: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Checks the writer's output against the JSON Schema, then audits the chart's `transform` subtree for
    expression strings.

    Returns:
        (True, None)  — valid
        (False, error_message) — invalid; caller must nullify chart and log.
    """
    messages = [
        f"[{path or 'root'}] {message}"
        for path, message in _flatten_errors(_validator.iter_errors(raw))
    ]

    chart = raw.get("chart")
    if isinstance(chart, dict) and "transform" in chart:
        messages.extend(_audit_expression_strings(chart["transform"]))

    if not messages:
        return True, None

    error_messages = "; ".join(messages)
    logger.error(
        "Synthesizer output failed the output schema: %s",
        error_messages,
    )
    return False, error_messages


def safe_synthesizer_output(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Returns the output if it validates, or a safe fallback with chart: null
    if validation fails. Never repairs — only nullifies the chart.

    This is the first of the two independent checks; the web app performs the second.
    """
    ok, error = validate_synthesizer_output(raw)
    if ok:
        return raw

    # Nullify chart; preserve answer_text and citations if they are strings/lists.
    logger.warning(
        "Returning chart: null after an output schema failure. Error: %s", error
    )
    return {
        "answer_text": raw.get("answer_text", ""),
        "chart": None,
        "citations": raw.get("citations", []),
    }

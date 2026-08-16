"""
orchestrator/vision.py
Chart rendering and vision-model resolution for the Vision Score (D11).

D11's deferred stretch item is a VLM that looks at a RENDERED chart and judges
it, closing the blind spot the deterministic SpecScorer cannot see: a chart
with the right shape drawn from the WRONG column scores as correct today,
because `field` is excluded from the encoding key on purpose (see
scorers.py::SpecScorer and docs/limitations.md). VegaChat reports ~0.71 Pearson
for VLM judging against ~0.65 for deterministic spec scoring; that difference
is the reason this exists.

The item splits cleanly into two halves, and they are treated differently
because their evidence is different:

  RENDERING  -- built and verified. Vega-Lite spec -> PNG with `vl-convert`,
                a Rust renderer with no browser and no network. This is the
                part that would otherwise need a headless Chrome in the
                evaluation loop, and it works today.

  JUDGING    -- a real dispatch point that resolves a vision model from
                config. No vision-capable model is available in this
                environment (both Ollama instances hold only
                `mxbai-embed-large`, an embedding model), so the call cannot
                be exercised here. It raises a message naming exactly what is
                missing rather than returning a fabricated score.

That asymmetry is deliberate. A scorer that invented a number when it could
not see the chart would be worse than one that refuses.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from config_loader import cfg, flags

logger = logging.getLogger(__name__)


class ChartRenderUnavailable(RuntimeError):
    """The renderer itself is missing (vl-convert not installed)."""


class VisionModelUnavailable(NotImplementedError):
    """
    No vision-capable model is configured or reachable. Distinct from
    ChartRenderUnavailable: rendering may work perfectly while judging cannot
    run, and conflating them would hide which half is missing.
    """


def render_spec_to_png(spec: dict[str, Any]) -> bytes:
    """
    Render a Vega-Lite spec to PNG bytes.

    `vl-convert` is used rather than vega-embed in a headless browser: it is a
    self-contained Rust implementation, so the evaluation loop needs no Chrome,
    no display, and no network. That also keeps the evaluator container able to
    run on `db_net`/`app_net` without an outbound path (A1).

    Raises ChartRenderUnavailable when the renderer is absent, so a caller can
    tell "no renderer" apart from "no vision model".
    """
    try:
        import vl_convert as vlc
    except ImportError as exc:            # pragma: no cover - environment-dependent
        raise ChartRenderUnavailable(
            "vl-convert is not installed, so a chart cannot be rendered to an "
            "image. Install `vl-convert-python` (pure Rust, no browser) to "
            "enable the Vision Score's rendering half."
        ) from exc

    scale = float(cfg.evaluation.vision_scorer.render_scale)
    return vlc.vegalite_to_png(vl_spec=spec, scale=scale)


def get_vision_model() -> str:
    """
    Resolve the configured vision model name, or explain precisely what is
    missing. Never falls back to a text model: asking a text-only model to
    score a picture it cannot see would produce a confident, meaningless
    number -- the exact failure this scorer exists to avoid.
    """
    if not bool(flags.evaluation.vision_scorer.enabled):
        raise VisionModelUnavailable(
            "evaluation.vision_scorer.enabled=false (the shipped default). "
            "The Vision Score is D11 stretch scope and is off by design; "
            "F-V5 checks that flipping this flag reaches a real dispatch "
            "point rather than a missing registration."
        )

    model = str(cfg.evaluation.vision_scorer.model).strip()
    if not model:
        raise VisionModelUnavailable(
            "evaluation.vision_scorer.enabled=true but "
            "evaluation.vision_scorer.model is empty. Name a vision-capable "
            "model (e.g. an Anthropic vision model, or a locally pulled "
            "llava/qwen2-vl through the Ollama provider)."
        )
    return model


def encode_png_for_model(png: bytes) -> str:
    """base64 payload, the form every vision API takes an inline image in."""
    return base64.b64encode(png).decode("ascii")


VISION_JUDGE_PROMPT = """\
You are scoring a generated data visualisation against a reference.

You are shown two rendered charts: GENERATED first, then REFERENCE. Judge how
well the generated chart conveys the same information as the reference.

Score these three, each 0.0 to 1.0:
  mark_match      - is it the same kind of chart (bar/line/point/area/...)?
  encoding_match  - are the same quantities on the same channels, and does the
                    data plotted appear to be the same data?
  readability     - is the generated chart legible and not visually broken?

Respond with ONLY a JSON object:
{"mark_match": <float>, "encoding_match": <float>, "readability": <float>, "reason": "<one short sentence>"}

Judge what you can SEE. If the generated chart plots a different column than
the reference, encoding_match must be low even when the chart type matches --
that specific confusion is the reason this visual check exists alongside the
deterministic one."""

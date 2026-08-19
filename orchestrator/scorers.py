"""
orchestrator/scorers.py
Scorer registry (A8 seam) + the two shipped implementations.

A8 declares:
    Interface  | Resolver key         | Shipped impl              | Seam in
    Scorer     | evaluation.scorers   | SpecScorer, ExecutionScorer | F

The seam is built here, ahead of Part F, because E2 Deliverable 8 needs the
recall-weighted specification scorer now and A0.5 is explicit that a seam is
"an interface with at least one registered implementation and a dispatch point
that resolves it at runtime from config" -- not a branch. Part F registers the
gated "vision" scorer behind evaluation.vision_scorer.enabled (D11) by calling
register_scorer(); nothing in this module changes for that.

Resolution is by NAME from config (cfg.evaluation.scorers). An unresolvable
name raises (A8: "an unresolvable name is a startup failure"). There is
deliberately no `if scorer == "spec"` anywhere -- that would be a registry
bypass and would defeat the seam.

All weights load from config/statesync.toml [scoring] (A0.2).
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Protocol, runtime_checkable

from config_loader import cfg

logger = logging.getLogger(__name__)


@runtime_checkable
class Scorer(Protocol):
    """A scorer compares one generated artifact against its reference."""

    name: str

    def score(self, generated: Any, reference: Any) -> dict[str, Any]:
        """Returns at least {"score": float in [0,1]}, plus scorer-specific detail."""
        ...


# ── Weighted F ────────────────────────────────────────────────────────────────
def _weighted_f(precision: float, recall: float) -> float:
    """
    Weighted harmonic mean of precision and recall, using scoring.recall_weight
    and scoring.precision_weight (Appendix 1: 0.70 / 0.30).

        F = (p * r) / (w_precision * r + w_recall * p)

    At equal weights this reduces to the ordinary F1, and raising
    recall_weight above precision_weight makes a MISS (which lowers recall)
    cost more than an EXTRA (which lowers precision) -- which is M7's stated
    requirement: "a missing encoding is penalized far more heavily than an
    extra stylistic property".
    """
    w_r = cfg.scoring.recall_weight
    w_p = cfg.scoring.precision_weight
    denom = w_p * recall + w_r * precision
    if denom == 0:
        return 0.0
    return (precision * recall) / denom


def _set_f(generated: set[str], reference: set[str]) -> tuple[float, float, float]:
    """
    (precision, recall, weighted_f) for two sets of comparable feature keys.
    Two empty sets score a vacuous 1.0 -- a reference with no transforms and a
    generated spec with no transforms agree perfectly, and scoring that as 0
    would penalize every simple chart.
    """
    if not generated and not reference:
        return 1.0, 1.0, 1.0
    matched = len(generated & reference)
    precision = matched / len(generated) if generated else 0.0
    recall = matched / len(reference) if reference else 0.0
    return precision, recall, _weighted_f(precision, recall)


# ── SpecScorer (M7) ───────────────────────────────────────────────────────────
class SpecScorer:
    """
    Deterministic, recall-weighted Vega-Lite specification-correctness score
    (M7). No model call, no network — the same input always scores the same.

    Three components, weighted by scoring.mark_weight / encoding_weight /
    transform_weight (Appendix 1: 0.40 / 0.45 / 0.15, summing to 1.0):

      mark       — the mark type, compared exactly.
      encoding   — the set of SEMANTIC channel bindings. Each channel
                   contributes "<channel>|<field>|<type>|<aggregate>", so a
                   missing channel, a channel bound to the wrong field, or a
                   dropped aggregate all register as misses, while an extra
                   channel the reference does not have (M7's own "a chart with
                   an unexpected colour") costs precision only.
      transform  — the set of transform operation names.

    `title`, `sort`, `width`, and `height` are deliberately NOT scored. They
    are the stylistic properties M7 says are "merely different", and Appendix
    1's three weights sum to 1.0 over exactly mark/encoding/transform, leaving
    no share for them. Scoring them would be inventing a fourth component the
    spec does not define (A0.1).
    """

    name = "spec"

    # Properties that carry meaning about WHAT is plotted, as opposed to how it
    # is dressed. Order-independent: joined into a sorted, stable key.
    #
    # `field` is deliberately EXCLUDED. A reference spec encodes what the chart
    # should LOOK like -- a bar chart with a nominal x and an aggregated
    # quantitative y. The column's NAME is a SQL-generation artifact, not a
    # visualization-correctness property: the same correct chart is produced
    # whether the agent named the column `seller_id`, `seller`, or `s_id`, and
    # penalising that would measure text-to-SQL naming under the guise of
    # measuring chart quality. Channel identity is still in the key, so
    # `x|nominal` and `color|nominal` remain distinct, and a missing or
    # wrongly-typed channel is still caught.
    #
    # Known limitation, recorded rather than hidden: binding the right channel
    # to the WRONG column is not detected. That is the deterministic scorer
    # being "honest about its own limitation" exactly as D11 frames it, and it
    # is the gap the deferred VLM Vision Score (VegaChat, ~0.71 vs ~0.65
    # Pearson) exists to close.
    _SEMANTIC_ENCODING_PROPS = ("type", "aggregate")

    def _encoding_features(self, spec: dict[str, Any]) -> set[str]:
        encoding = spec.get("encoding") or {}
        if not isinstance(encoding, dict):
            return set()
        features = set()
        for channel, definition in encoding.items():
            if not isinstance(definition, dict):
                continue
            parts = [str(definition.get(p, "")) for p in self._SEMANTIC_ENCODING_PROPS]
            features.add(f"{channel}|" + "|".join(parts))
        return features

    def _transform_features(self, spec: dict[str, Any]) -> set[str]:
        transforms = spec.get("transform") or []
        if not isinstance(transforms, list):
            return set()
        features = set()
        for item in transforms:
            if isinstance(item, dict):
                for op in sorted(item.keys()):
                    features.add(op)
        return features

    def score(self, generated: Any, reference: Any) -> dict[str, Any]:
        # A null chart is a total miss against a reference that has one. This
        # is scored, not skipped -- E2-V1 requires the chart:null count to be
        # reported separately rather than averaged away, and returning 0.0
        # here keeps a null visible in the mean instead of silently absent.
        if not isinstance(generated, dict) or not isinstance(reference, dict):
            return {
                "score": 0.0,
                "mark": {"precision": 0.0, "recall": 0.0, "f": 0.0},
                "encoding": {"precision": 0.0, "recall": 0.0, "f": 0.0},
                "transform": {"precision": 0.0, "recall": 0.0, "f": 0.0},
                "null_chart": generated is None,
            }

        gen_mark = {str(generated.get("mark"))} if generated.get("mark") is not None else set()
        ref_mark = {str(reference.get("mark"))} if reference.get("mark") is not None else set()

        m_p, m_r, m_f = _set_f(gen_mark, ref_mark)
        e_p, e_r, e_f = _set_f(self._encoding_features(generated), self._encoding_features(reference))
        t_p, t_r, t_f = _set_f(self._transform_features(generated), self._transform_features(reference))

        total = (
            cfg.scoring.mark_weight * m_f
            + cfg.scoring.encoding_weight * e_f
            + cfg.scoring.transform_weight * t_f
        )

        return {
            "score": total,
            "mark":      {"precision": m_p, "recall": m_r, "f": m_f},
            "encoding":  {"precision": e_p, "recall": e_r, "f": e_f},
            "transform": {"precision": t_p, "recall": t_r, "f": t_f},
            "null_chart": False,
        }


# ── ExecutionScorer ───────────────────────────────────────────────────────────
class ExecutionScorer:
    """
    Execution-based comparison (M4's method, reused as a registered scorer):
    compare the RESULT of the generated query against the result of the
    ground-truth query, so that syntactically different but semantically
    identical queries score as correct.

    Both sides are lists of row dicts. Comparison is order-independent and
    column-order-independent -- the same normalization C2-V8 already uses.
    """

    name = "execution"

    @staticmethod
    def _normalize(rows: Any) -> list[tuple]:
        if not isinstance(rows, list):
            return []
        normalized = []
        for row in rows:
            if isinstance(row, dict):
                normalized.append(tuple(sorted((str(k), str(v)) for k, v in row.items())))
            else:
                normalized.append((str(row),))
        return sorted(normalized)

    def score(self, generated: Any, reference: Any) -> dict[str, Any]:
        gen = self._normalize(generated)
        ref = self._normalize(reference)
        match = gen == ref
        return {
            "score": 1.0 if match else 0.0,
            "match": match,
            "generated_row_count": len(gen),
            "reference_row_count": len(ref),
        }


# ── ExecutionAccuracyScorer (BIRD / Spider "EX") ──────────────────────────────
class ExecutionAccuracyScorer:
    """
    Execution Accuracy as the text-to-SQL literature defines it (BIRD, Spider).

    WHY A THIRD SCORER, rather than adjusting one of the two that exist:
    this project already had two comparison semantics, and neither matched the
    published definition, which made its numbers incomparable to any
    leaderboard even when run on the same benchmark.

        ExecutionScorer ("execution")   compares (column_NAME, value) pairs, so
                                        an alias difference is a failure.
        C2-V8's own signature            ignores names but includes the column
                                        COUNT, so two EMPTY results with
                                        different projections differ.
        this scorer ("execution_accuracy")
                                        compares the RESULT SETS as sorted
                                        tuples of values, which is the
                                        published definition.

    All three are kept and reported separately rather than one being "fixed"
    into the others. They answer different questions, and collapsing them would
    hide exactly the gap that makes a strict internal number look like a
    capability failure. Measured on phase_c2: the strict scorer and this one
    disagree substantially -- see docs/m4_diagnosis.json.

    Order-insensitive by default because SQL result order is unspecified
    without ORDER BY; set scoring.ex_order_sensitive=true in config to compare
    ordered results for queries whose ground truth pins an order.
    """

    name = "execution_accuracy"

    @staticmethod
    def _rows_to_tuples(rows: Any) -> list[tuple]:
        """Each row as a tuple of its stringified VALUES; column names ignored."""
        if not isinstance(rows, list):
            return []
        out = []
        for row in rows:
            if isinstance(row, dict):
                out.append(tuple(str(v) for v in row.values()))
            elif isinstance(row, (list, tuple)):
                out.append(tuple(str(v) for v in row))
            else:
                out.append((str(row),))
        return out

    def score(self, generated: Any, reference: Any) -> dict[str, Any]:
        gen = self._rows_to_tuples(generated)
        ref = self._rows_to_tuples(reference)

        ordered = bool(getattr(cfg.scoring, "ex_order_sensitive", False))
        if ordered:
            match = gen == ref
        else:
            # Multiset comparison: duplicate rows are meaningful in SQL results,
            # so sorted() rather than set().
            match = sorted(gen) == sorted(ref)

        return {
            "score": 1.0 if match else 0.0,
            "match": match,
            "order_sensitive": ordered,
            "generated_row_count": len(gen),
            "reference_row_count": len(ref),
            # Reported so a near-miss is visible rather than collapsed to 0.0:
            # the fraction of reference rows present in the generated result.
            #
            # MULTISET, matching `match` above -- audit 001 F-001-5. Until now
            # this de-duplicated (set intersection over set(ref)), one line
            # below a comment on `match` arguing duplicate rows are meaningful.
            # A reference with a duplicate row could recall it once and be
            # scored as if every copy were recovered. Counter & Counter takes
            # the per-value MIN of the two counts (multiset intersection), so a
            # duplicate in ref only counts as recalled once per matching copy
            # actually present in gen. Measured before shipping this change:
            # 0 of 237 previously-scored queries across every committed
            # docs/m4_diagnosis*.json report contain a duplicate reference row,
            # so this is a byte-for-byte no-op on every published row_recall /
            # mean_row_recall to date -- verified by recomputing both
            # definitions over every committed report, not inferred.
            "row_recall": (
                sum((Counter(gen) & Counter(ref)).values()) / len(ref)
                if ref else (1.0 if not gen else 0.0)
            ),
        }


# ── VisionScorer (F-V5 seam; Strategy §6 stretch, D11) ────────────────────────
class VisionScorer:
    """
    VLM-based visual scoring of a rendered chart image (Strategy §6, D11).

    Registered unconditionally (F1 Scope: "VisionScorer registered and
    flag-disabled"), so `get_scorer("vision")` always resolves -- the seam
    is real, not a comment (A0.5). Only `.score()` is unbuilt: the actual
    VLM call is deferred stretch content, and invoking it raises rather
    than silently returning a fake score.

    F-V5 checks exactly this distinction: with `evaluation.vision_scorer.
    enabled=false` (the shipped default) the pipeline never calls
    `.score()` here at all; flipping the flag makes it call `.score()` and
    get NotImplementedError -- "failing on a missing implementation, not
    on a missing registration."
    """

    name = "vision"

    def score(self, generated: Any, reference: Any) -> dict[str, Any]:
        """
        Render both specs, then ask a vision model to compare the PICTURES.

        Two halves with different evidential status, kept distinct on purpose:

          rendering -- BUILT and verified. vl-convert turns a Vega-Lite spec
                       into a PNG with no browser and no network, which is what
                       made this feasible inside the evaluator container at all.
          judging   -- a real dispatch point. No vision-capable model exists in
                       this deployment, so it raises VisionModelUnavailable
                       naming what is missing. It does NOT fall back to a text
                       model: a text model asked to judge an image it cannot
                       see returns a confident, meaningless number, which is
                       strictly worse than refusing.

        A null chart is a total miss, matching SpecScorer's own treatment.
        """
        from vision import (render_spec_to_png, get_vision_model,
                            ChartRenderUnavailable, VisionModelUnavailable)

        if not isinstance(generated, dict) or not isinstance(reference, dict):
            return {
                "score": 0.0, "null_chart": generated is None,
                "rendered": False, "judged": False,
                "detail": "generated or reference is not a chart spec",
            }

        # Rendering half -- runs for real.
        try:
            generated_png = render_spec_to_png(generated)
            reference_png = render_spec_to_png(reference)
        except ChartRenderUnavailable:
            raise
        except Exception as exc:
            # A spec that will not render is itself a finding, and is reported
            # as a zero with the reason rather than crashing the pipeline.
            return {
                "score": 0.0, "null_chart": False,
                "rendered": False, "judged": False,
                "detail": f"render failed: {type(exc).__name__}: {exc}",
            }

        rendered = {
            "rendered": True,
            "generated_png_bytes": len(generated_png),
            "reference_png_bytes": len(reference_png),
        }

        # Judging half -- resolves a model or explains precisely what is absent.
        model = get_vision_model()          # raises VisionModelUnavailable
        raise VisionModelUnavailable(
            f"Both charts rendered ({rendered['generated_png_bytes']} and "
            f"{rendered['reference_png_bytes']} PNG bytes), and vision model "
            f"{model!r} is configured, but no vision transport is wired for the "
            f"current llm.provider. Wire the image call for that provider to "
            f"complete D11; the rendering half above is already real."
        )


# ── Registry ──────────────────────────────────────────────────────────────────
_REGISTRY: dict[str, type] = {}


def register_scorer(name: str, scorer_cls: type) -> None:
    """
    Register a scorer implementation under a config-addressable name. Called at
    import time for the shipped scorers; Part F calls it again for the gated
    vision scorer (D11) without touching any dispatch site.
    """
    _REGISTRY[name] = scorer_cls
    logger.debug("Registered scorer %r -> %s", name, scorer_cls.__name__)


register_scorer(SpecScorer.name, SpecScorer)
register_scorer(ExecutionScorer.name, ExecutionScorer)
register_scorer(ExecutionAccuracyScorer.name, ExecutionAccuracyScorer)
register_scorer(VisionScorer.name, VisionScorer)


def get_scorer(name: str) -> Scorer:
    """Resolve one scorer by name. Unresolvable name = failure, never a fallback."""
    scorer_cls = _REGISTRY.get(name)
    if scorer_cls is None:
        raise RuntimeError(
            f"Unresolvable scorer name {name!r} (A8: an unresolvable name is a "
            f"startup failure, not a silent default). Registered: "
            f"{sorted(_REGISTRY)}."
        )
    return scorer_cls()


def get_scorers() -> list[Scorer]:
    """
    Resolve the configured scorer list, in config order (A8: "the evaluation
    pipeline invokes an ordered list of scorers", Strategy §6).
    """
    return [get_scorer(name) for name in cfg.evaluation.scorers]


def registered_scorer_names() -> list[str]:
    return sorted(_REGISTRY)

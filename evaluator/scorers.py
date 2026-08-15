"""
evaluator/scorers.py
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
    _SEMANTIC_ENCODING_PROPS = ("field", "type", "aggregate")

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
        raise NotImplementedError(
            "VisionScorer content is Strategy §6 stretch scope (D11) -- the "
            "VLM call itself is not built. Only the registry seam is (A0.5). "
            "See Appendix 4: evaluation.vision_scorer.enabled, Part F."
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

#!/usr/bin/env python3
"""
scripts/plot_bird.py
The paper's risk-coverage figure (docs/paper/), from the held-out scores and judge files. No model
calls. Curves use analyze_bird's exact expectation under random tie-breaking, so each curve's mean
error equals the AURC the analysis reports.

    python3 scripts/plot_bird.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_bird as ab  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SCORES = REPO / "docs" / "eval" / "bird_scores_heldout_gpt5mini_v2_corrected.json"
LABEL = "heldout_gpt5mini"
OUT = REPO / "docs" / "paper" / "fig_risk_coverage"
SECONDARY_JUDGE = "gpt-oss-120b"
FIG_SIZE_IN = (3.3, 2.5)    # noqa: literals - one ACL column
FONT_PT = 7                 # noqa: literals - figure text size
Y_HEADROOM = 1.12           # noqa: literals - space above the no-abstention line


def curve(risk: list[float], wrong: list[bool]) -> tuple[list[float], list[float]]:
    """Coverage m/n and expected error rate on the m least suspect answers, m = 1..n."""
    groups = ab._groups(risk, wrong, descending=False)
    n = len(risk)
    xs = [m / n for m in range(1, n + 1)]
    ys = [ab.expected_wrong_in_first(groups, m) / m for m in range(1, n + 1)]
    return xs, ys


def main() -> int:
    rows, judges = ab.load(SCORES, LABEL)
    wrong = [not r["correct"] for r in rows]
    judge = judges[ab.PRIMARY_JUDGE]
    checks = [float(bool(r["signals"].get("any_data_check"))) for r in rows]
    series = {
        "self-consistency (5 samples)": [float(r["self_consistency_risk"]) for r in rows],
        "LLM judge (Gemini 3.8 Flash)": judge,
        "data checks, then judge": [2 * c + j for c, j in zip(checks, judge)],
        "judge ensemble (mean)": [sum(col) / len(col) for col in zip(*judges.values())],
        "LLM judge (gpt-oss-120b)": judges[SECONDARY_JUDGE],
    }
    plt.rcParams.update({"font.size": FONT_PT, "font.family": "serif"})
    fig, ax = plt.subplots(figsize=FIG_SIZE_IN)
    styles = ["-", "-", "--", ":", "-."]
    for (name, risk), style in zip(series.items(), styles):
        xs, ys = curve(risk, wrong)
        ax.plot(xs, ys, style, linewidth=1, label=f"{name} ({ab.aurc(risk, wrong):.3f})")
    base = sum(wrong) / len(wrong)
    ax.axhline(base, color="grey", linewidth=0.6, linestyle=(0, (1, 2)))
    ax.text(0.02, base - 0.004, f"answer everything ({base:.3f})", color="grey", fontsize=FONT_PT - 1, va="top")
    ax.set_xlabel("coverage (share of answers kept)")
    ax.set_ylabel("error rate on kept answers")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, base * Y_HEADROOM)
    ax.legend(title="ranking (AURC)", fontsize=FONT_PT - 1, title_fontsize=FONT_PT - 1, frameon=False, loc="lower right")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(OUT.with_suffix(f".{ext}"), dpi=300)
    print(f"wrote {OUT}.pdf/.png; n = {len(rows)}, wrong = {sum(wrong)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

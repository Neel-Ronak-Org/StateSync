# Strategy Document Addendum — Development vs Graded Model Configuration

**APPEND-READY.** Insert into `StateSync_Strategy_Document.md` as a short
subsection at the head of §7 (Success Metrics), or as an Appendix note. This
closes an audit finding: the entire "Anthropic will lift the quality numbers"
assumption was load-bearing but written nowhere in the three core documents.

---

## §7.0 — Model configuration and what the reported numbers mean

All development and Verification to date runs on a **local generator
(`qwen2.5:7b`) and local embedder (`mxbai-embed-large`)**, selected through the
BD1 provider seam (`flags.llm.provider`) and BD2 embedding config. The
**graded/production configuration uses Anthropic** through that same seam; the
key is provisioned separately.

The metric set in §7 divides into two classes with respect to model choice:

**Model-independent — stand as measured on any model:**
- **M1 direction** (memory reduces tokens/trajectory — a control-vs-treatment A/B).
- **M8** (adversarial security suite — binary, structural).
- **M9** (routing determinism ≥ 90% — a classification property).
- **M10** (reproducibility — the stack stands up and answers).
- All structural Verification steps (network isolation, credential absence, the
  three SELECT-only layers, role separation, tool ceiling, seam resolution,
  output-contract enforcement).

**Model-sensitive — measured and reported on the local model, but re-baselined
on the graded configuration before being reported as final:**
- **M4** (query correctness, cache-hit and novel).
- **M6** (grounding precision / recall / faithfulness).
- **M7** (visualization *quality* score; M7's *validity/rejection* half is
  model-independent and stands).
- **M2** (consistency) and any Verification step whose written pass-condition is
  sensitive to generation quality — specifically **C1-V3**, and the
  quality-dependent conditions of **C2-V1** and **D-V6** (see BD6).

Local-model figures are retained on record for honesty and for the A/B, and are
never deleted or smoothed. They are simply **not** the graded figures. The
day the Anthropic key lands, the re-baseline runbook (`docs/rebaseline_runbook.md`)
is executed and BD6 is resolved (confirmed model-ceiling, or reverted to open
blocks) before any model-sensitive metric is claimed as final.

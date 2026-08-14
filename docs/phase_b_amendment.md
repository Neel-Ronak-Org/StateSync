# Phase B Amendment — Build-Level Decisions

Standalone BD log (Appendix 5 equivalent) for this repo. `StateSync_Build_Spec.md`
is not yet checked in under `docs/`; when it is, migrate these entries into its
Appendix 5 verbatim, per the format A0.3 defines: Decided / Alternative considered
/ Why / Hook.

## BD1 — LLM provider seam implemented in Phase B (not deferred to D)

**Decided**: get_agent_llm(role) factory in orchestrator/llm/provider.py,
dispatching on flags.llm.provider. Implemented as part of the Phase B
walking skeleton commit (31d314d).
**Alternative**: Defer to Phase D per A8 interface registry.
**Why**: The seam was cheaper to build alongside the node wiring than to
retrofit later. The A8 registry and Appendix 3 do not list this interface
because they were written before Phase B code was committed — this BD entry
closes that documentation gap per A0.3.
**Hook**: flags.llm.provider — add "anthropic" entry to [llm.models.anthropic]
in flags.toml and set ANTHROPIC_API_KEY when the key is available.

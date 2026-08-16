#!/usr/bin/env python3
"""
scripts/verify_azure_seam.py
Verifies the Azure provider (BD1 seam, third implementation) is WIRED, and
that it fails for the right reason when it is not yet CONFIGURED.

WHY THIS IS SEPARATE FROM verify_seams.py. F-V4 checks that a seam's name
resolves from config at a real dispatch point. That is the right check for a
seam whose implementation is deliberately absent. Azure's implementation is
present but its CREDENTIALS are not, which is a third state the existing
checks have no vocabulary for -- and the distinction matters, because
"provider not wired" and "provider wired, your key is missing" send you to
completely different places.

This is the same three-state distinction D11's VisionScorer draws
(registered / implemented / usable), applied to a provider.

RUNS WITHOUT A KEY, BY DESIGN. Every check below passes on a machine that has
never seen an Azure credential. That is the point: the wiring is verifiable
tonight and the key is verifiable tomorrow. The one thing it CANNOT check is
whether a deployment name matches the portal -- nothing local can, and it says
so rather than implying coverage it does not have.

    python3 scripts/verify_azure_seam.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ PASS: {label}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  ✗ FAIL: {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 72)
    print("Azure provider seam (BD1 third implementation)")
    print("=" * 72)

    from config_loader import flags
    import llm.provider as provider_mod
    from llm.adapters.azure_adapter import AzureAdapter, AzureConfigError
    from llm.adapters.base import ChatModel

    print("\n── Wiring ──────────────────────────────────────────────")

    # The dispatch point, not a comment: provider.py must branch on "azure".
    src = (REPO_ROOT / "orchestrator" / "llm" / "provider.py").read_text()
    check("provider.py dispatches on provider == 'azure'",
          'if provider == "azure"' in src)
    check("the unknown-provider error lists azure as valid",
          "'local', 'anthropic', 'azure'" in src)

    check("AzureAdapter satisfies the ChatModel protocol",
          isinstance(AzureAdapter, type) and hasattr(AzureAdapter, "complete"))

    # Structural: the adapter must expose the same async complete() signature
    # the other two do, or nodes break on switchover rather than at review.
    import inspect
    sig = inspect.signature(AzureAdapter.complete)
    check("complete() has the ChatModel signature (system, messages, max_tokens)",
          {"system", "messages", "max_tokens"} <= set(sig.parameters),
          str(sig))
    check("complete() is async (non-blocking, like the other two adapters)",
          inspect.iscoroutinefunction(AzureAdapter.complete))

    print("\n── Config surface ──────────────────────────────────────")
    check("flags.llm.azure.endpoint exists as a config key",
          hasattr(flags.llm, "azure") and hasattr(flags.llm.azure, "endpoint"))
    check("flags.llm.azure.api_version is set",
          bool(str(getattr(flags.llm.azure, "api_version", "") or "").strip()),
          str(getattr(flags.llm.azure, "api_version", "")))

    roles = sorted(provider_mod._VALID_ROLES)
    resolved = {}
    ok_roles = True
    for role in roles:
        try:
            resolved[role] = provider_mod._resolve_model("azure", role)
        except RuntimeError as exc:
            ok_roles = False
            resolved[role] = f"MISSING ({exc})"
    check(f"every one of the {len(roles)} roles resolves a deployment name",
          ok_roles, ", ".join(f"{r}={resolved[r]}" for r in roles))

    print("\n── Failure modes (the point of this script) ────────────")

    # With no key configured, the adapter must fail with a NAMED, actionable
    # error -- not an ImportError, not a generic RuntimeError, not a silent
    # fallback to another provider.
    saved = {k: os.environ.pop(k, None)
             for k in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_KEY_FILE")}
    os.environ["AZURE_OPENAI_API_KEY_FILE"] = "/nonexistent/azure_key"
    try:
        try:
            AzureAdapter._read_api_key()
            check("missing key raises rather than returning empty", False,
                  "no exception raised")
        except AzureConfigError as exc:
            check("missing key raises AzureConfigError (not a generic error)", True)
            check("the error says WHERE to put the key",
                  "AZURE_OPENAI_API_KEY" in str(exc))
            check("the error offers the local-provider escape hatch",
                  "provider = 'local'" in str(exc))
    finally:
        os.environ.pop("AZURE_OPENAI_API_KEY_FILE", None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    endpoint = str(getattr(flags.llm.azure, "endpoint", "") or "").strip()
    if not endpoint:
        try:
            AzureAdapter._read_endpoint()
            check("empty endpoint is refused", False, "it was accepted")
        except AzureConfigError as exc:
            check("empty endpoint is refused with portal-specific guidance", True)
            check("the error names where to find the endpoint",
                  "Foundry" in str(exc) or "portal" in str(exc))
    else:
        check("endpoint is configured — startup will not refuse on it", True, endpoint)

    print("\n── What this CANNOT check ──────────────────────────────")
    print("  Whether flags.llm.models.azure.* match real deployments in your")
    print("  Foundry resource. Nothing local can: Azure addresses DEPLOYMENT")
    print("  names you chose, and only the API can confirm they exist. A 404")
    print("  DeploymentNotFound on the first query means exactly that, and")
    print("  validate_llm_config() logs a warning naming it at startup.")
    print("\n  To check for real once a key exists:")
    print("    1. secrets/azure_openai_api_key  <- paste the key")
    print("    2. config/flags.toml             <- llm.azure.endpoint, then provider = \"azure\"")
    print("    3. docker compose build orchestrator && docker compose up -d orchestrator")
    print("    4. docker compose logs orchestrator | grep -i azure")
    print("    5. python3 scripts/diagnose_m4.py --out docs/m4_diagnosis_azure.json")

    print(f"\n{'─'*20} Summary {'─'*20}")
    print(f"  PASSED: {passed}")
    print(f"  FAILED: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

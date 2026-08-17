#!/usr/bin/env python3
"""
scripts/verify_provider_seam.py
Verifies a hosted LLM provider (BD1 seam) is WIRED, and that it fails for the
right reason when it is not yet CONFIGURED.

    python3 scripts/verify_provider_seam.py --provider azure
    python3 scripts/verify_provider_seam.py --provider gemini
    python3 scripts/verify_provider_seam.py --all            (default)

    # one live call, from inside the container that will serve queries:
    docker compose exec -T orchestrator python3 - --provider gemini --smoke \
        < scripts/verify_provider_seam.py

WHY THIS IS SEPARATE FROM verify_seams.py. F-V4 checks that a seam's name
resolves from config at a real dispatch point -- the right check for a seam
whose implementation is deliberately absent. A hosted provider's
implementation is present but its CREDENTIALS are not, which is a third state
the existing checks have no vocabulary for. "Provider not wired" and "provider
wired, your key is missing" send you to completely different places. Same
three-state distinction D11's VisionScorer draws (registered / implemented /
usable), applied to a provider.

WHY IT IS PARAMETERIZED RATHER THAN COPIED. This started as
verify_azure_seam.py. Adding Gemini by duplicating it would have created two
near-identical 200-line scripts that drift -- the exact failure this repo has
now hit four times (BD4/BD5/BD7's two-file key lists; the Dockerfile's second
dependency list; two implementations of "standard EX"; two copies of the
shipped-defaults table). One file, a per-provider table.

RUNS WITHOUT ANY CREDENTIAL, BY DESIGN. Every wiring check passes on a machine
that has never seen a key. --smoke is the half that needs one.
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from pathlib import Path

# Runs on the host (wiring checks) or INSIDE the orchestrator container
# (--smoke, which needs the provider SDK and the mounted secret). Same
# dual-path shape verify_tenancy.py uses.
if Path("/app/main.py").exists() and Path("/app/llm").exists():
    ORCH_DIR, CONFIG_DIR = Path("/app"), Path("/app/config")
    REPO_ROOT = Path("/app")
else:
    REPO_ROOT = Path(__file__).resolve().parent.parent
    ORCH_DIR, CONFIG_DIR = REPO_ROOT / "orchestrator", REPO_ROOT / "config"
sys.path.insert(0, str(ORCH_DIR))
os.environ.setdefault("CONFIG_DIR", str(CONFIG_DIR))

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


# ── Per-provider facts. Everything provider-specific lives HERE, once. ────────
PROVIDERS: dict[str, dict] = {
    "azure": {
        "module": "llm.adapters.azure_adapter",
        "adapter": "AzureAdapter",
        "error": "AzureConfigError",
        "key_env": "AZURE_OPENAI_API_KEY",
        "key_env_file": "AZURE_OPENAI_API_KEY_FILE",
        "secret": "secrets/azure_openai_api_key",
        # Azure addresses DEPLOYMENTS you named, not published model ids.
        "names_are": "DEPLOYMENT names you chose in the Foundry portal",
        "extra_config": ["endpoint", "api_version"],
        "cannot_check": (
            "Whether flags.llm.models.azure.* match real deployments in your\n"
            "  resource. Nothing local can: Azure addresses deployment names you\n"
            "  chose, and only the API can confirm they exist. A 404\n"
            "  DeploymentNotFound on the first call means exactly that."
        ),
    },
    "gemini": {
        "module": "llm.adapters.gemini_adapter",
        "adapter": "GeminiAdapter",
        "error": "GeminiConfigError",
        "key_env": "GEMINI_API_KEY",
        "key_env_file": "GEMINI_API_KEY_FILE",
        "secret": "secrets/gemini_api_key",
        "names_are": "PUBLISHED model names (no deployment indirection)",
        "extra_config": [],
        "cannot_check": (
            "Whether flags.llm.models.gemini.* name models your key can reach,\n"
            "  and whether the free tier's PER-MINUTE limit will throttle a full\n"
            "  fixture run. The adapter retries 429 with backoff; a wrong model\n"
            "  name surfaces as a 404 naming the model."
        ),
    },
}


def verify(name: str) -> None:
    spec = PROVIDERS[name]
    print("\n" + "=" * 72)
    print(f"{name} provider seam (BD1)")
    print("=" * 72)

    from config_loader import flags
    import llm.provider as provider_mod
    import importlib

    mod = importlib.import_module(spec["module"])
    Adapter = getattr(mod, spec["adapter"])
    ConfigError = getattr(mod, spec["error"])

    print("\n── Wiring ──────────────────────────────────────────────")
    src = (ORCH_DIR / "llm" / "provider.py").read_text()
    check(f"provider.py dispatches on provider == {name!r}",
          f'if provider == "{name}"' in src)
    check(f"the unknown-provider error lists {name} as valid",
          f"'{name}'" in src)
    check(f"{spec['adapter']} exposes complete()", hasattr(Adapter, "complete"))

    sig = inspect.signature(Adapter.complete)
    check("complete() has the ChatModel signature (system, messages, max_tokens)",
          {"system", "messages", "max_tokens"} <= set(sig.parameters))
    check("complete() is async (non-blocking, like every other adapter)",
          inspect.iscoroutinefunction(Adapter.complete))

    print("\n── Config surface ──────────────────────────────────────")
    for key in spec["extra_config"]:
        holder = getattr(flags.llm, name, None)
        check(f"flags.llm.{name}.{key} exists as a config key",
              holder is not None and hasattr(holder, key))

    roles = sorted(provider_mod._VALID_ROLES)
    resolved, ok_roles = {}, True
    for role in roles:
        try:
            resolved[role] = provider_mod._resolve_model(name, role)
        except RuntimeError as exc:
            ok_roles = False
            resolved[role] = f"MISSING ({exc})"
    check(f"every one of the {len(roles)} roles resolves a model name",
          ok_roles, ", ".join(f"{r}={resolved[r]}" for r in roles))
    print(f"    (these are {spec['names_are']})")

    print("\n── Failure modes (the point of this script) ────────────")
    saved = {k: os.environ.pop(k, None)
             for k in (spec["key_env"], spec["key_env_file"], "GOOGLE_API_KEY")}
    os.environ[spec["key_env_file"]] = "/nonexistent/key"
    try:
        try:
            Adapter._read_api_key()
            check("missing key raises rather than returning empty", False,
                  "no exception raised")
        except ConfigError as exc:
            check(f"missing key raises {spec['error']} (not a generic error)", True)
            check("the error says WHERE to put the key",
                  spec["key_env"] in str(exc))
            check("the error offers the local-provider escape hatch",
                  "provider = 'local'" in str(exc))
    finally:
        os.environ.pop(spec["key_env_file"], None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    print("\n── What this CANNOT check ──────────────────────────────")
    print("  " + spec["cannot_check"])


def smoke(name: str) -> int:
    """ONE real completion, with a plain-English diagnosis when it fails."""
    import asyncio
    import time
    import importlib

    from config_loader import flags
    from llm.provider import get_agent_llm

    spec = PROVIDERS[name]
    ConfigError = getattr(importlib.import_module(spec["module"]), spec["error"])

    provider = str(flags.llm.provider)
    print("=" * 72)
    print(f"{name} smoke test — one live completion (provider = {provider!r})")
    print("=" * 72)

    if provider != name:
        print(f"\n  flags.llm.provider is {provider!r}, not {name!r}.")
        print(f"  Set it to \"{name}\" in config/flags.toml and restart the")
        print("  orchestrator, or this would test the wrong provider.")
        return 1

    role = "sql"
    try:
        model = getattr(getattr(flags.llm.models, name), role)
        print(f"\n  role={role}  model={model!r}")
        for key in spec["extra_config"]:
            print(f"  {key}={getattr(getattr(flags.llm, name), key)}")
        llm = get_agent_llm(role)
    except ConfigError as exc:
        print(f"\n  ✗ CONFIG INCOMPLETE\n    {exc}")
        return 1
    except Exception as exc:
        print(f"\n  ✗ {type(exc).__name__}: {exc}")
        return 1

    async def _one():
        return await llm.complete(
            system="You are a terse assistant.",
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=16,
        )

    t0 = time.time()
    try:
        resp = asyncio.run(_one())
    except Exception as exc:
        text, low = str(exc), str(exc).lower()
        print(f"\n  ✗ CALL FAILED: {type(exc).__name__}\n    {text[:400]}")
        print("\n  Most likely cause:")
        if "404" in text or "not found" in low:
            if name == "azure":
                print("    The DEPLOYMENT NAME does not exist in this resource.")
                print("    It is the name YOU typed when deploying, not the model id.")
            else:
                print(f"    The model name in flags.llm.models.{name}.{role} is not")
                print("    available to this key. Try another (e.g. gemini-2.0-flash).")
        elif "401" in text or "403" in text or "unauthor" in low or "permission" in low:
            print("    The KEY is wrong, revoked, or lacks access to this model.")
        elif "429" in text or "quota" in low or "exhaust" in low or "rate" in low:
            print("    Rate limited. On Gemini's FREE tier this is per-MINUTE and")
            print("    expected under load; the adapter already retries with backoff.")
            print("    Seeing it on a single call means the key's quota is exhausted.")
        elif "getaddrinfo" in low or "connect" in low or "resolution" in low:
            print("    Host unreachable — check the endpoint value and that this")
            print("    container has outbound network access.")
        else:
            print("    Unrecognised. The raw error above is the ground truth.")
        return 1

    # An empty reply is a FAILURE, not a pass. The first version of this check
    # printed OK for text='' / tokens_out=0 on a Gemini thinking model that had
    # spent its whole budget reasoning -- a vacuous pass, in the script written
    # to prevent vacuous passes.
    if not resp.text.strip():
        print(f"\n  ✗ EMPTY REPLY after {time.time()-t0:.2f}s "
              f"(tokens in={resp.tokens_in} out={resp.tokens_out})")
        print("    The call succeeded but produced no text. On a thinking model")
        print("    the token budget was consumed by reasoning: lower")
        print("    models.gemini_thinking_level, or raise the role's max_tokens.")
        return 1

    print(f"\n  ✓ LIVE COMPLETION OK in {time.time()-t0:.2f}s")
    print(f"    reply      : {resp.text[:60]!r}")
    print(f"    tokens in  : {resp.tokens_in}")
    print(f"    tokens out : {resp.tokens_out}")
    print("\n  The provider seam is live end to end. Next:")
    print("    python3 scripts/diagnose_m4.py --out docs/m4_diagnosis_%s.json" % name)
    print("    (25 queries — the probe that says whether the model was the ceiling)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    args, _ = ap.parse_known_args()

    if args.smoke:
        if not args.provider:
            print("--smoke requires --provider", file=sys.stderr)
            return 2
        return smoke(args.provider)

    targets = [args.provider] if args.provider else sorted(PROVIDERS)
    for name in targets:
        verify(name)

    print(f"\n{'─'*20} Summary {'─'*20}")
    print(f"  PASSED: {passed}")
    print(f"  FAILED: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

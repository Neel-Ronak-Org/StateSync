#!/usr/bin/env python3
"""
scripts/verify_deployed.py
Does the code in the repo match the code that is actually RUNNING?

WHY THIS EXISTS. This build has now hit the same defect twice:

  d5a5d35  D11's rendering half was committed but never shipped into the image.
  124bf8e  ExecutionAccuracyScorer was committed to BOTH scorer registries --
           and the evaluator container was never rebuilt, so the running
           evaluator's registry did not contain it. The commit message claimed
           "added to BOTH registries" and cited verify_seams 24/24 as evidence.

Neither was caught, because nothing checked it. `scripts/verify_seams.py` runs
on the host and imports from `orchestrator/` on the repo filesystem, so it
verifies SOURCE seams and is structurally incapable of noticing that an image
is stale. `scripts/verify_e2.py` and the tenancy suite hit live services, but
only over the narrow surface they exercise.

"Code in the repo != code running" is therefore an unverified assumption that
every other verification in this project silently rests on. This script checks
it directly.

DELIBERATELY NOT SKIPPABLE. If the stack is not running, this FAILS rather than
skipping. A parity check that passes when you turn the stack off is exactly the
vacuous pass this repo keeps finding elsewhere (F-V1 Class 4's chart check,
M4 measuring column width). Same reason the per-service file counts are
asserted to be non-zero: an empty comparison set must never report success.

Read-only. Touches no container state.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from api_auth import auth_headers

REPO_ROOT = Path(__file__).resolve().parent.parent

PASSED = 0
FAILED = 0

# service -> (repo dir, in-container dir, minimum tracked .py files expected)
#
# Files are enumerated with `git ls-files`, i.e. exactly the COMMITTED source.
# Walking the filesystem instead would sweep in `mcp/.venv` (an untracked local
# virtualenv that `COPY . .` does bake into the image) and compare 600 vendored
# files that say nothing about whether our code shipped.
PY_SERVICES = {
    "orchestrator": ("orchestrator", "/app", 10),
    "mcp":          ("mcp",          "/app", 5),
    "evaluator":    ("evaluator",    "/app", 3),
    "simulator":    ("simulator",    "/app", 2),    # Phase 2b: the live demo data
}

# The web app (web/, docs/V3_PLAN.md Phase 2) is a compiled bundle with hashed
# file names, so its served files cannot be compared to repo files one by one.
# Instead the image build writes a fingerprint of every SOURCE input
# (web/scripts/build-info.mjs -> /build-info.json), and this script computes
# the same fingerprint from the repo. The rule below mirrors build-info.mjs
# exactly: these inputs, dot-files and dot-directories skipped, paths sorted,
# each file contributing "path\0sha256(content)\n". If either side changes
# alone, the check fails loudly rather than passing a stale image.
WEB_DIR = REPO_ROOT / "web"
WEB_INPUTS = ["index.html", "package-lock.json", "vite.config.ts",
              "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json", "src", "public"]
WEB_HTML_DIR = "/usr/share/nginx/html"


def web_source_fingerprint(root: Path = WEB_DIR) -> tuple[str, int]:
    files: list[Path] = []
    for name in WEB_INPUTS:
        path = root / name
        if path.is_dir():
            files.extend(f for f in path.rglob("*") if f.is_file())
        elif path.is_file():
            files.append(path)
    rels = sorted(
        f.relative_to(root).as_posix() for f in files
        if not any(part.startswith(".") for part in f.relative_to(root).parts)
    )
    digest = hashlib.sha256()
    for rel in rels:
        file_hash = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        digest.update(f"{rel}\0{file_hash}\n".encode())
    return digest.hexdigest(), len(rels)


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ✓ PASS: {label}")
    else:
        FAILED += 1
        print(f"  ✗ FAIL: {label}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def sh(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, cwd=REPO_ROOT, timeout=timeout)


def container_sha256(service: str, path: str) -> str | None:
    """sha256 of a file inside a running container, or None if unreadable."""
    out = sh(["docker", "compose", "exec", "-T", service, "sha256sum", path])
    if out.returncode != 0:
        return None
    parts = out.stdout.strip().split()
    return parts[0] if parts else None


def local_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def service_running(service: str) -> bool:
    out = sh(["docker", "compose", "ps", "-q", service])
    return out.returncode == 0 and bool(out.stdout.strip())


def tracked_py_files(repo_dir: str) -> list[Path]:
    """Committed .py files under repo_dir, per git — not a filesystem walk."""
    out = sh(["git", "ls-files", "--", f"{repo_dir}/*.py"])
    if out.returncode != 0:
        return []
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line.strip()]


def check_py_service(service: str, repo_dir: str, app_dir: str, minimum: int) -> None:
    section(f"{service} — source parity with the running image")

    if not service_running(service):
        check(f"{service} container is running", False, "not running; cannot verify parity")
        return
    check(f"{service} container is running", True)

    root = REPO_ROOT / repo_dir
    files = sorted(tracked_py_files(repo_dir))

    # An empty comparison set must never look like a pass.
    check(f"{service}: found at least {minimum} source files to compare",
          len(files) >= minimum, f"found {len(files)}")
    if not files:
        return

    mismatched: list[str] = []
    missing: list[str] = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        img = container_sha256(service, f"{app_dir}/{rel}")
        if img is None:
            missing.append(rel)
        elif img != local_sha256(p):
            mismatched.append(rel)

    check(f"{service}: every repo source file exists in the image ({len(files)} checked)",
          not missing, f"absent from image: {missing}")
    check(f"{service}: no repo source file differs from the image",
          not mismatched,
          f"STALE IMAGE — rebuild required; differing: {mismatched}")


def check_web_service(service: str = "frontend") -> None:
    section(f"{service} — web bundle built from the repo's current source")

    if not service_running(service):
        check(f"{service} container is running", False, "not running; cannot verify parity")
        return
    check(f"{service} container is running", True)

    out = sh(["docker", "compose", "exec", "-T", service, "cat", f"{WEB_HTML_DIR}/build-info.json"])
    try:
        info = json.loads(out.stdout)
    except (json.JSONDecodeError, TypeError):
        check(f"{service}: image carries build-info.json", False, out.stderr.strip()[-160:])
        return
    check(f"{service}: image carries build-info.json", True)

    expected, count = web_source_fingerprint()
    check(f"{service}: bundle was built from the repo's current web source ({count} files)",
          info.get("source_sha256") == expected,
          f"STALE IMAGE — rebuild required: image {str(info.get('source_sha256'))[:12]}… "
          f"({info.get('files')} files) vs repo {expected[:12]}… ({count} files)")

    # entrypoint.sh writes the runtime config from the container's own
    # ORCHESTRATOR_URL; read both rather than assuming either.
    env = sh(["docker", "compose", "exec", "-T", service, "sh", "-c", "echo \"$ORCHESTRATOR_URL\""])
    orch_url = env.stdout.strip() or "http://localhost:8090"
    cfg = sh(["docker", "compose", "exec", "-T", service, "cat", f"{WEB_HTML_DIR}/config.js"])
    check(f"{service}: /config.js carries the container's ORCHESTRATOR_URL",
          f'orchestratorUrl: "{orch_url}"' in cfg.stdout, cfg.stdout.strip()[:160])


def check_scorer_registries() -> None:
    """
    The repo claims scorers are registered in BOTH registries (orchestrator and
    evaluator). That claim was false in the running system for ~1.5h and no
    check covered it, because verify_seams.py only ever imports the
    orchestrator's copy. Compare the two registries as they exist at RUNTIME,
    inside their own containers.
    """
    section("Scorer registries agree across services (as deployed)")

    names: dict[str, list[str] | None] = {}
    for service in ("orchestrator", "evaluator"):
        if not service_running(service):
            check(f"{service} scorer registry readable", False, "container not running")
            names[service] = None
            continue
        out = sh(["docker", "compose", "exec", "-T", service, "python3", "-c",
                  "from scorers import registered_scorer_names as n; print(','.join(sorted(n())))"])
        ok = out.returncode == 0 and out.stdout.strip()
        check(f"{service} scorer registry readable", bool(ok), out.stderr.strip()[-160:])
        names[service] = sorted(out.stdout.strip().split(",")) if ok else None

    if names.get("orchestrator") and names.get("evaluator"):
        check("orchestrator and evaluator register the SAME scorer names",
              names["orchestrator"] == names["evaluator"],
              f"orchestrator={names['orchestrator']} evaluator={names['evaluator']}")
        # Guard against both being trivially empty, which would compare equal.
        check("the shared registry is non-empty",
              len(names["orchestrator"]) >= 2, str(names["orchestrator"]))


def main() -> int:
    print("=" * 72)
    print("Deployed-image parity — does the running system contain the committed code?")
    print("=" * 72)

    probe = sh(["docker", "compose", "ps", "-q"])
    if probe.returncode != 0:
        print("\n  docker compose is unavailable. This check FAILS rather than skipping:")
        print("  a parity check that passes with the stack down verifies nothing.")
        return 1

    for service, (repo_dir, app_dir, minimum) in PY_SERVICES.items():
        check_py_service(service, repo_dir, app_dir, minimum)

    check_web_service("frontend")

    check_scorer_registries()

    print("\n" + "─" * 20 + " Summary " + "─" * 20)
    print(f"  PASSED: {PASSED}")
    print(f"  FAILED: {FAILED}")
    if FAILED:
        print("\n  A stale image means every other verification in this repo was")
        print("  measuring source that is not what the stack actually runs.")
        print("  Rebuild:  DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 \\")
        print("            docker compose build <service> && docker compose up -d --no-deps <service>")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

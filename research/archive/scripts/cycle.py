#!/usr/bin/env python3
"""
scripts/cycle.py
Enforces the two rules in docs/CYCLE_PROTOCOL.md that are worth nothing as
conventions: the SEAL (§2) and the CRITERIA FREEZE (§4.4).

    python3 scripts/cycle.py status
    python3 scripts/cycle.py unseal 001     # gate on a substantive audit
    python3 scripts/cycle.py freeze 001     # hash the acceptance criteria
    python3 scripts/cycle.py verify 001     # were they moved after the freeze?
    python3 scripts/cycle.py --self-test    # both directions

WHY A SCRIPT AND NOT A PARAGRAPH. This repo already learned that a rule which
is only remembered is a rule that is eventually forgotten: the shipped-flag
state was a convention until it contaminated three measurements, at which point
scripts/shipped_flags.py made it a refusal. The same reasoning applies here.
"Read the audit before the briefing" and "do not move the criteria mid-build"
are both trivially violable and both invisible afterwards -- unless something
records the state at the moment it mattered.

WHAT IT CANNOT DO. It cannot make an audit honest, only substantive; §3.2's
"what this does NOT establish" line is checked for PRESENCE, not for truth. And
nothing here stops a determined agent deleting a hash file. It raises the cost
of an accident, not of a decision. See CYCLE_PROTOCOL §7.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = REPO_ROOT / "docs" / "audits"
CYCLE_DIR = REPO_ROOT / "docs" / "cycles"

CRITERIA_BEGIN = "<!-- CRITERIA:BEGIN -->"
CRITERIA_END = "<!-- CRITERIA:END -->"
CHECKLIST_CLASSES = 10


class CycleError(RuntimeError):
    """A protocol violation, phrased so the fix is obvious."""


# ── paths ────────────────────────────────────────────────────────────────────

def audit_path(n: str) -> Path:
    return AUDIT_DIR / f"{n}.md"


def cycle_path(n: str) -> Path:
    return CYCLE_DIR / f"{n}.md"


def hash_path(n: str) -> Path:
    return CYCLE_DIR / f"{n}.criteria.sha256"


# ── the seal (protocol §2) ───────────────────────────────────────────────────

def audit_defects(n: str, root: Path | None = None) -> list[str]:
    """
    Every reason this audit does not yet unseal the briefings. Empty list == may
    proceed. Substantive, NOT honest -- see the module docstring.
    """
    ad = (root / "docs" / "audits") if root else AUDIT_DIR
    path = ad / f"{n}.md"
    if not path.exists():
        return [f"{path.relative_to(root or REPO_ROOT)} does not exist -- "
                f"the audit is written BEFORE the briefings are read (§2)"]

    src = path.read_text()
    out: list[str] = []

    # §3.4 -- the standing checklist was walked, all ten classes
    m = re.search(r"CHECKLIST:\s*(\d+)\s*/\s*(\d+)", src)
    if not m:
        out.append("no 'CHECKLIST: n/10' marker -- §3.4 requires the standing "
                   "failure-class checklist to be walked and the walk recorded")
    elif int(m.group(1)) < CHECKLIST_CLASSES or int(m.group(2)) != CHECKLIST_CLASSES:
        out.append(f"CHECKLIST: {m.group(1)}/{m.group(2)} -- all "
                   f"{CHECKLIST_CLASSES} classes must be walked; finding "
                   f"nothing in a class is a result, skipping it is not")

    # §3.1 -- evidence, or an explicit and deliberate absence of findings
    findings = re.findall(r"^###\s+(F-\S+)", src, re.M)
    if not findings:
        if "NO FINDINGS" not in src:
            out.append("no findings (### F-...) and no explicit 'NO FINDINGS' "
                       "line -- §3.5: an empty audit with no evidence of "
                       "looking is a failed audit, not a clean one")
    else:
        if "```" not in src:
            out.append("findings present but no transcript block -- §3.1: a "
                       "finding without a reproduction is a HYPOTHESIS")

        # §3.2 -- the scope line that would have caught the coder:14b overclaim
        blocks = re.split(r"^###\s+F-\S+", src, flags=re.M)[1:]
        missing = [f for f, b in zip(findings, blocks)
                   if "does not establish" not in b.lower()]
        if missing:
            out.append(f"findings missing the mandatory 'Does not establish:' "
                       f"line (§3.2): {', '.join(missing)}")
    return out


# ── the freeze (protocol §4.4) ───────────────────────────────────────────────

def criteria_digest(n: str, root: Path | None = None) -> str:
    """SHA-256 of the criteria block. Cosmetic whitespace is normalised away so
    a stray trailing space is not mistaken for a moved goalpost; anything that
    changes a criterion's content changes the digest."""
    cd = (root / "docs" / "cycles") if root else CYCLE_DIR
    path = cd / f"{n}.md"
    if not path.exists():
        raise CycleError(f"{path} does not exist")
    src = path.read_text()
    if CRITERIA_BEGIN not in src or CRITERIA_END not in src:
        raise CycleError(
            f"{path.name} has no {CRITERIA_BEGIN} / {CRITERIA_END} block. The "
            f"acceptance criteria must be delimited before the build starts "
            f"(§4.4) -- otherwise 'we met the criteria' is unfalsifiable.")
    block = src.split(CRITERIA_BEGIN, 1)[1].split(CRITERIA_END, 1)[0]
    lines = [ln.rstrip() for ln in block.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_unseal(n: str) -> int:
    defects = audit_defects(n)
    if defects:
        print(f"✗ SEALED — cycle {n}'s audit is not yet substantive:\n")
        for d in defects:
            print(f"    · {d}")
        print(f"\n  Write {audit_path(n).relative_to(REPO_ROOT)} first. "
              f"docs/cycles/ stays sealed until then (protocol §2).")
        return 1
    print(f"✓ UNSEALED — cycle {n}'s audit is substantive.\n")
    print("  You may now read docs/cycles/*.md (previous briefings).")
    print("  Next: plan, then delimit acceptance criteria between")
    print(f"        {CRITERIA_BEGIN} / {CRITERIA_END} in "
          f"{cycle_path(n).relative_to(REPO_ROOT)}, then `freeze {n}`.")
    return 0


def cmd_freeze(n: str) -> int:
    digest = criteria_digest(n)
    hp = hash_path(n)
    if hp.exists():
        prior = hp.read_text().split()[0]
        if prior == digest:
            print(f"✓ already frozen, unchanged — {digest[:16]}…")
            return 0
        print(f"✗ REFUSING to re-freeze cycle {n}.\n")
        print(f"    frozen: {prior[:16]}…\n    now:    {digest[:16]}…\n")
        print("  The criteria changed after being frozen. §4.4: append a")
        print("  DEVIATION entry saying the criterion was wrong. Do not edit")
        print("  the criterion. Moving a goalpost mid-build is the same class")
        print("  of act as editing a scorer to improve a number.")
        return 1
    hp.write_text(f"{digest}  criteria block of docs/cycles/{n}.md\n")
    print(f"✓ frozen — {digest[:16]}…\n  → {hp.relative_to(REPO_ROOT)}")
    print("  Commit this file. The build may now start.")
    return 0


def cmd_verify(n: str) -> int:
    hp = hash_path(n)
    if not hp.exists():
        print(f"✗ cycle {n} was never frozen — no {hp.name}. The criteria were "
              f"not fixed before the build (§4.4).")
        return 1
    frozen = hp.read_text().split()[0]
    now = criteria_digest(n)
    if frozen == now:
        print(f"✓ criteria intact — {now[:16]}…")
        return 0
    print(f"✗ CRITERIA MOVED after the freeze.\n    frozen: {frozen[:16]}…"
          f"\n    now:    {now[:16]}…")
    return 1


def cmd_status() -> int:
    audits = sorted(AUDIT_DIR.glob("*.md")) if AUDIT_DIR.exists() else []
    cycles = sorted(CYCLE_DIR.glob("[0-9]*.md")) if CYCLE_DIR.exists() else []
    print("cycle state\n" + "─" * 60)
    ns = sorted({p.stem for p in audits} | {p.stem for p in cycles})
    if not ns:
        print("  no cycles yet")
        return 0
    for n in ns:
        # An absent audit file is not the same state as an inadequate one:
        # cycle 000 predates this protocol and has no audit by design.
        if not audit_path(n).exists():
            seal = "none"
        else:
            seal = "unsealed" if not audit_defects(n) else "SEALED"
        if hash_path(n).exists():
            frz = "intact" if cmd_verify_quiet(n) else "MOVED"
        else:
            frz = "not frozen"
        print(f"  {n}   audit: {seal:<9}  criteria: {frz}")
    return 0


def cmd_verify_quiet(n: str) -> bool:
    try:
        return hash_path(n).read_text().split()[0] == criteria_digest(n)
    except CycleError:
        return False


# ── self-test: both directions, per the repo norm ────────────────────────────

def self_test() -> int:
    import tempfile
    ok = fail = 0

    def check(label: str, cond: bool) -> None:
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  ✓ PASS: {label}")
        else:
            fail += 1
            print(f"  ✗ FAIL: {label}")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docs" / "audits").mkdir(parents=True)
        (root / "docs" / "cycles").mkdir(parents=True)

        # ── the seal REFUSES ────────────────────────────────────────────────
        check("missing audit file is refused", bool(audit_defects("900", root)))

        a = root / "docs" / "audits" / "900.md"
        a.write_text("# audit\nCHECKLIST: 4/10\nNO FINDINGS\n")
        d = audit_defects("900", root)
        check("a partial checklist walk is refused",
              any("must be walked" in x for x in d))

        a.write_text("# audit\nNO FINDINGS\n")
        check("a missing checklist marker is refused",
              any("CHECKLIST" in x for x in audit_defects("900", root)))

        a.write_text("# audit\nCHECKLIST: 10/10\n\n### F-900-1 thing\nbad\n")
        d = audit_defects("900", root)
        check("a finding with no transcript is refused",
              any("HYPOTHESIS" in x for x in d))
        check("a finding with no 'does not establish' line is refused",
              any("Does not establish" in x for x in d))

        a.write_text("# audit\nCHECKLIST: 10/10\n\n### F-900-1 thing\n"
                     "```\n$ cmd\nout\n```\nDoes not establish: scope.\n")
        check("a complete audit unseals", not audit_defects("900", root))

        a.write_text("# audit\nCHECKLIST: 10/10\nNO FINDINGS after walking all "
                     "ten classes.\n")
        check("an explicit NO FINDINGS with a full walk unseals",
              not audit_defects("900", root))

        # ── the freeze DETECTS ──────────────────────────────────────────────
        c = root / "docs" / "cycles" / "900.md"
        c.write_text("# cycle\n")
        try:
            criteria_digest("900", root)
            check("an undelimited criteria block is refused", False)
        except CycleError as exc:
            check("an undelimited criteria block is refused",
                  "CRITERIA:BEGIN" in str(exc))

        c.write_text(f"# cycle\n{CRITERIA_BEGIN}\n- QM >= 0.60\n{CRITERIA_END}\n")
        first = criteria_digest("900", root)

        c.write_text(f"# cycle\n{CRITERIA_BEGIN}\n- QM >= 0.60   \n\n"
                     f"{CRITERIA_END}\n")
        check("cosmetic whitespace does NOT move the digest",
              criteria_digest("900", root) == first)

        c.write_text(f"# cycle\n{CRITERIA_BEGIN}\n- QM >= 0.40\n{CRITERIA_END}\n")
        check("a LOWERED threshold DOES move the digest",
              criteria_digest("900", root) != first)

        c.write_text(f"# cycle\n{CRITERIA_BEGIN}\n- QM >= 0.60\n{CRITERIA_END}\n"
                     f"\n## DEVIATIONS\n- criterion was wrong because ...\n")
        check("appending a DEVIATION leaves the digest intact",
              criteria_digest("900", root) == first)

    print(f"\n  {ok} passed, {fail} failed")
    return 1 if fail else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", default="status",
                   choices=["status", "unseal", "freeze", "verify"])
    p.add_argument("cycle", nargs="?", help="cycle number, e.g. 001")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()

    if args.self_test:
        return self_test()
    if args.command == "status":
        return cmd_status()
    if not args.cycle:
        p.error(f"`{args.command}` needs a cycle number, e.g. `{args.command} 001`")
    try:
        return {"unseal": cmd_unseal, "freeze": cmd_freeze,
                "verify": cmd_verify}[args.command](args.cycle)
    except CycleError as exc:
        print(f"✗ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

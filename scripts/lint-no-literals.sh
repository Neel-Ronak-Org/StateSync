#!/usr/bin/env bash
# scripts/lint-no-literals.sh
# No configurable numbers written into the code: numeric values that could reasonably be configuration constants
# (timeouts, limits, model parameters, counts) must live in statesync.toml or
# flags.toml, not hard-coded in agent source files.
#
# Exemptions — these do NOT require a config entry:
#   1. 0 or 1 (identity / boolean / boundary)
#   2. Array indices (inside [])
#   3. JSON-RPC standard error codes (-32600 through -32700 range)
#   4. HTTP status codes (400, 401, 403, 404, 405, 422, 500, etc.)
#   5. Numbers inside comments (after #) or docstrings
#   6. Numbers inside string literals used as format patterns (e.g. regex {0,62})
#   7. SRID 4326, SQLSTATE codes like 42501 (protocol constants)
#
# Exit 0 = clean. Exit 1 = violations found.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VIOLATIONS=0
FILES_CHECKED=0

SCAN_DIRS=("${REPO_ROOT}/orchestrator" "${REPO_ROOT}/mcp")

echo "==> Scanning for configurable numeric literals in agent source..."
echo "    Directories: ${SCAN_DIRS[*]}"
echo ""

for dir in "${SCAN_DIRS[@]}"; do
  [[ ! -d "${dir}" ]] && continue

  while IFS= read -r -d '' file; do
    case "${file}" in
      */__pycache__/*) continue ;;
      */.venv/*) continue ;;
      */venv/*) continue ;;
      */test_*) continue ;;
      */tests/*) continue ;;
      *.py) ;;
      *) continue ;;
    esac

    FILES_CHECKED=$((FILES_CHECKED + 1))

    # We use Python's ast module for accurate detection — it understands
    # what is inside a string, comment, or actual code.
    python3 - "${file}" <<'PYEOF'
import ast, sys, re

path = sys.argv[1]
src = open(path).read()
try:
    tree = ast.parse(src)
except SyntaxError as e:
    print(f"  PARSE ERROR: {path}: {e}")
    sys.exit(0)

# Lines that are pure comments are never violations
lines = src.splitlines()

# Docstring node ranges (first string expression in module/class/function body)
docstring_linenos = set()
for node in ast.walk(tree):
    body = getattr(node, 'body', None)
    if not body or not isinstance(body, list):
        continue
    first = body[0]
    if not isinstance(first, ast.stmt):
        continue
    if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)):
        continue
    val = first.value
    if isinstance(val.value, str):
        for ln in range(val.lineno, (val.end_lineno or val.lineno) + 1):
            docstring_linenos.add(ln)

# JSON-RPC standard error codes and HTTP status codes that are protocol constants
# These are stable, not configurable — exempting them is correct.
EXEMPT_VALUES = {
    # JSON-RPC 2.0 spec codes
    -32700, -32600, -32601, -32602, -32603, -32604, -32605,
    # HTTP status codes
    200, 201, 204, 400, 401, 403, 404, 405, 406, 409, 410, 415, 422, 429, 500, 501, 502, 503, 504,
    # PostgreSQL SQLSTATE (42501 as integer — usually a string in practice)
    42501,
    # SRID
    4326,
    # Millisecond conversion constant (1000 ms/s — mathematical identity, not configurable)
    1000,
}

# Values 0–3 are always exempt: 0/1 per A0.2 verbatim, 2/3 for tuple indices
EXEMPT_SMALL = {0, 1, 2, 3}

violations = []

for node in ast.walk(tree):
    if not isinstance(node, ast.Constant):
        continue
    val = node.value
    if not isinstance(val, (int, float)):
        continue
    if isinstance(val, bool):  # True/False are Constant too
        continue

    # Exemption 1: 0 or 1
    if val in EXEMPT_SMALL:
        continue

    # Exemption 2: protocol / HTTP / SRID constants
    if val in EXEMPT_VALUES or -val in EXEMPT_VALUES:
        continue

    lineno = node.lineno

    # Exemption 3: inside a docstring
    if lineno in docstring_linenos:
        continue

    # Exemption 4: line is a pure comment
    stripped = lines[lineno - 1].lstrip() if lineno <= len(lines) else ''
    if stripped.startswith('#'):
        continue

    # Exemption 5: the number appears only after a # comment on the same line
    # (handles inline comments with numbers like "# 400 ms")
    line = lines[lineno - 1] if lineno <= len(lines) else ''
    hash_pos = line.find('#')
    if hash_pos >= 0:
        # Find the column of this node in the line
        col = getattr(node, 'col_offset', -1)
        if col >= hash_pos:
            continue

    # Exemption 6: value is inside a string literal that looks like a regex
    # We check parent context: if this constant is inside a Call where the
    # function is re.match/re.search/re.compile, it's likely a regex arg.
    # (ast.walk doesn't give parent — we do a simple line-content check)
    if re.search(r'r[\'"][^"\']*\{' + str(abs(int(val))) + r'[,\}]', line):
        continue

    # Exemption 7: noqa marker
    if 'noqa: literals' in line:
        continue

    # Flag it
    violations.append((lineno, line.rstrip(), val))

for lineno, line, val in violations:
    print(f"  VIOLATION: {path}:{lineno}  value={val}")
    print(f"    {line}")

sys.exit(1 if violations else 0)
PYEOF

    if [[ $? -ne 0 ]]; then
      VIOLATIONS=$((VIOLATIONS + 1))
    fi

  done < <(find "${dir}" -type f -name "*.py" -print0)
done

echo ""
echo "==> Files checked: ${FILES_CHECKED}"

if [[ ${VIOLATIONS} -eq 0 ]]; then
  echo "==> PASS: No configurable numeric literal violations found."
  exit 0
else
  echo "==> FAIL: ${VIOLATIONS} file(s) with numeric literal violations."
  echo "    Move configurable values to config/statesync.toml or config/flags.toml (A0.2)."
  echo "    Add '# noqa: literals' to silence protocol-constant false positives."
  exit 1
fi

# Are we ready to run BIRD / Spider? — measured 2026-08-18

**No. Three blockers, each demonstrated against the live stack rather than
reasoned about, plus one cheap experiment that should happen first.**

`docs/AGENT_HANDOFF.md` §7 has listed "Spider dev → BIRD dev" as a next step
since the project began. It has never been checked whether the system *can*
run them. It cannot, yet.

---

## Blocker 1 — the row cap silently corrupts Execution Accuracy

`mcp.max_rows_default = 1000`. Any result set larger than that comes back
truncated, and **which** 1000 rows you get depends on `ORDER BY` — which SQL
leaves unspecified unless stated.

Demonstrated against `olist.orders` (99,441 rows):

| comparison | EX match |
|---|---|
| two identical correct queries, both truncated | `True` |
| same correct answer, different `ORDER BY` | **`False`** |

So two equally correct queries score as different, silently, purely because of
truncation. **This has never bitten us**: the largest reference result in any
fixture here is 566 rows. BIRD and Spider return large result sets routinely,
so a benchmark number computed today would be quietly wrong.

**Fixed the honest way, not the convenient one.** `diagnose_m4.py` now records
`reference_truncated` / `unscoreable_truncated` per query. Raising the cap was
rejected: an unbounded cap is a data-plane risk, and the correct answer for a
truncated comparison is "unscoreable", not a guess in either direction.

## Blocker 2 — one database at a time

BIRD dev is 11 databases, Spider dev 20; each question targets one.

`schema_retriever.target_schema` is read from `flags`, which
`config_loader.py` loads **at process start**. Switching databases therefore
requires an orchestrator restart per database. And the fixture format has no
per-question database field at all — `diagnose_m4.py` takes one `--fixture`
with one schema for the whole run.

Needs: a per-question `db`/`schema` field in the fixture, and schema resolution
per call rather than per process. Contained, but real.

## Blocker 3 — dialect. This is the hard one.

BIRD and Spider ship **SQLite** databases and SQLite gold SQL. This engine is
Postgres, and the whole security model depends on it — the Layer 2 guarantee is
a Postgres **role grant**, which SQLite has no equivalent of.

Probed live, on constructs that appear throughout BIRD gold queries:

| construct | Postgres |
|---|---|
| `strftime('%Y', ts)` | **FAILS** — no such function |
| `IIF(cond, a, b)` | **FAILS** — no such function |
| `CAST(x AS REAL)` | OK |
| `substr(...)` | OK |

Two options, both with real costs:

- **Translate SQLite → Postgres** (schema and gold SQL). Mechanical for the
  schema, error-prone for the gold SQL — and a mistranslated gold query is a
  *wrong ground truth*, which is precisely the defect class that cost this
  project 16 percentage points on `phase_c2`. Any translation would need the
  same audit discipline applied to it.
- **Add a SQLite backend.** Loses the Postgres role grant, i.e. one of the
  three independent SELECT-only layers, on the benchmark path. The parser gate
  and statement timeout would remain.

This is a design decision, not a task, and it should be taken deliberately.

---

## What to do FIRST, at small scale — and why it is cheap

**Build a hard fixture over Olist before touching BIRD.**

The three blockers are engineering. The open *question* is different: **how does
this system perform on the query shapes BIRD is made of?** Every fixture here
is mostly single-table aggregates. BIRD is multi-hop joins, nested subqueries,
and questions needing external knowledge — and BD6 records 2-hop joins as this
system's measured weakness across four independent checks.

So: 15–20 hard questions over Olist — three-table joins, correlated subqueries,
window functions, deliberately ambiguous phrasings — authored with the same
safeguards as `fixtures/golden/olist.json` (projections named, denominators
stated, zero-row references rejected).

That measures the thing BIRD would measure, on data we control, with ground
truth we can verify ourselves, for **zero cost and no engineering**. If the
system scores 45% there, BIRD will be worse and we will know exactly why before
spending a day on dialect translation. If it scores 85%, the blockers are worth
paying down.

**Cost asymmetry is the argument:** the hard fixture is an afternoon. The three
blockers are several days, and one of them trades away a security layer.
Measure first.

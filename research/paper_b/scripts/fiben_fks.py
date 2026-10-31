"""
demo/fiben_fks.py
FIBEN's own foreign keys (159 in FIBEN.sql) as Postgres statements for the lower-case port that
demo/fiben_db.sh builds. NOT VALID: existing rows are not re-checked; the constraints are metadata
that StateSync reads from pg_constraint, to show join paths and to keep a table's join partners when
it selects tables for a question (docs/specs/table_selection.md).

    python3 demo/fiben_fks.py data/external/fiben/FIBEN.sql | psql ...
"""

import hashlib
import re
import sys

FK = re.compile(r"ALTER TABLE (\w+) ADD CONSTRAINT (\w+) FOREIGN KEY \((\w+)\) REFERENCES (\w+) \((\w+)\)")


def short(name: str) -> str:
    """The loader's name rule: lower case; over 63 characters, 54 + '_' + 8 hex of the SHA-1."""
    name = name if len(name) <= 63 else name[:54] + "_" + hashlib.sha1(name.encode()).hexdigest()[:8]
    return name.lower()


def main() -> None:
    for i, (table, _name, col, ref_table, ref_col) in enumerate(FK.findall(open(sys.argv[1]).read())):
        print(f"ALTER TABLE fiben.{short(table)} ADD CONSTRAINT fk_{i} FOREIGN KEY ({short(col)}) "
              f"REFERENCES fiben.{short(ref_table)} ({short(ref_col)}) NOT VALID;")


if __name__ == "__main__":
    main()

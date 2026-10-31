"""
orchestrator/schema_graph.py
The shape of a database as the web app's Schema page draws it (inspect_api.py serves it): tables with their
columns, and one edge per foreign key. Built from what the MCP's schema.list_tables and schema.describe_table
return. Pure, so it is tested without the web framework.
"""

from __future__ import annotations

from typing import Any


def schema_graph(dataset: str, listing: dict[str, Any], described: list[dict[str, Any]], total: int) -> dict[str, Any]:
    """The graph the page draws: tables (with their columns) and one edge per foreign key whose target table is
    among those described. `total` is how many tables the database has. Pure."""
    estimates = {t["name"]: t.get("row_estimate") for t in listing.get("tables", [])}
    names = {d["table"] for d in described}
    tables, edges = [], []
    for d in described:
        fk_columns = {fk["column"] for fk in d.get("foreign_keys", [])}
        tables.append({
            "name": d["table"], "row_estimate": estimates.get(d["table"]),
            "columns": [{"name": c["name"], "type": c.get("type"), "nullable": bool(c.get("nullable")),
                         "foreign_key": c["name"] in fk_columns} for c in d.get("columns", [])],
        })
        for fk in d.get("foreign_keys", []):
            if fk.get("references_table") in names:
                edges.append({"from_table": d["table"], "from_column": fk["column"],
                              "to_table": fk["references_table"], "to_column": fk.get("references_column")})
    return {"dataset": dataset, "tables": tables, "edges": edges, "tables_total": total}

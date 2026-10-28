#!/usr/bin/env python3
"""
scripts/check_dv6_spatial.py
D-V6 -- spatial aggregation worked example (English, then Hinglish, must
match). Takes the first entry in fixtures/digipin_reference.json (guaranteed
by gen_digipin_fixture.py to have >= 3 nearby seeded locations within 2km),
independently computes the expected count with a hand-written PostGIS query
run directly against postgres as statesync_reader (bypassing mcp/data.run_select
entirely, since Layer 1 blocks EXPLAIN -- this also lets us check the GIST
index directly, which data.run_select cannot), then submits both an English
and a Hinglish form of the same question through the real orchestrator
pipeline and checks both match the independent count exactly.

Usage:
    python3 scripts/check_dv6_spatial.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bench_memory import REPO_ROOT, run_query

DIGIPIN_REF_PATH = REPO_ROOT / "fixtures" / "digipin_reference.json"


def _reader_dsn_env() -> dict[str, str]:
    pw = (REPO_ROOT / "secrets" / "pg_reader_password").read_text().strip()
    return {"PGPASSWORD": pw}


def psql_reader(sql: str) -> str:
    pw = (REPO_ROOT / "secrets" / "pg_reader_password").read_text().strip()
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "env", f"PGPASSWORD={pw}",
         "psql", "-U", "statesync_reader", "-d", "statesync", "-t", "-A", "-c", sql],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    return result.stdout.strip()


def main() -> None:
    ref = json.loads(DIGIPIN_REF_PATH.read_text())
    pair = ref["pairs"][0]
    assert len(pair["nearby_location_ids"]) >= 3, (
        "digipin_reference.json entry 0 must have >= 3 nearby locations (Appendix 2.3 / D-V6)."
    )
    digipin, lat, lon = pair["digipin"], pair["lat"], pair["lon"]
    radius_m = 5000
    print(f"D-V6 reference: digipin={digipin} lat={lat} lon={lon} radius_m={radius_m}")
    print(f"  (nearby_location_ids: {pair['nearby_location_ids']})")

    # ── Independent hand-written ground truth, direct against postgres ─────
    ground_truth_sql = (
        f"SELECT COUNT(*) FROM orders o "
        f"JOIN locations l ON o.ship_to_location_id = l.location_id "
        f"JOIN fulfillments f ON f.order_id = o.order_id "
        f"WHERE ST_DWithin(l.geom, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography, {radius_m}) "
        f"AND f.status = 'delivered';"
    )
    expected_count = int(psql_reader(ground_truth_sql))
    print(f"\n[1/4] Independent ground truth (statesync_reader, direct): {expected_count}")

    # ── GIST index check (EXPLAIN; only possible via a direct role connection --
    #    Layer 1 rejects ExplainStmt through data.run_select) ───────────────
    explain_out = psql_reader(
        f"EXPLAIN SELECT * FROM locations l WHERE ST_DWithin(l.geom, "
        f"ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography, {radius_m});"
    )
    uses_gist = "locations_geom_gix" in explain_out and "Seq Scan" not in explain_out
    print(f"\n[2/4] EXPLAIN plan:\n{explain_out}")
    print(f"  GIST index in use (not seq scan): {uses_gist}")

    # ── English form through the real pipeline ──────────────────────────────
    en_text = f"How many orders were delivered within {radius_m // 1000}km of {digipin}?"
    print(f"\n[3/4] English form: {en_text!r}")
    en_data = run_query(en_text)
    en_trace = en_data.get("state_trace", {})
    en_spatial = en_trace.get("spatial", {})
    en_sql = en_trace.get("executed_sql") or ""
    en_count = (en_trace.get("sql_rows") or [{}])[0].get(next(iter((en_trace.get("sql_rows") or [{}])[0]), ""), None) \
        if en_trace.get("sql_rows") else None
    print(f"  spatial: {en_spatial}")
    print(f"  executed_sql: {en_sql}")
    print(f"  result row: {(en_trace.get('sql_rows') or [None])[0]}")

    en_digipin_ok = en_spatial.get("digipin") == digipin
    en_latlon_ok = (
        en_spatial.get("lat") is not None and abs(en_spatial["lat"] - lat) < 1e-4
        and en_spatial.get("lon") is not None and abs(en_spatial["lon"] - lon) < 1e-4
    )
    en_predicate_ok = "ST_DWithin" in en_sql and "geography" in en_sql
    en_radius_ok = str(radius_m) in en_sql
    en_row = (en_trace.get("sql_rows") or [{}])[0]
    en_count_value = next(iter(en_row.values()), None) if en_row else None
    en_count_ok = en_count_value == expected_count

    print(f"  digipin match: {en_digipin_ok} | latlon match: {en_latlon_ok} | "
          f"predicate uses ST_DWithin+geography: {en_predicate_ok} | radius=5000 in SQL: {en_radius_ok} | "
          f"count == {expected_count}: {en_count_ok} (got {en_count_value})")

    # ── Hinglish form, normalization enabled, same question ────────────────
    hi_text = f"{digipin} ke {radius_m // 1000}km ke andar kitne orders deliver hue?"
    print(f"\n[4/4] Hinglish form: {hi_text!r}")
    hi_data = run_query(hi_text)
    hi_trace = hi_data.get("state_trace", {})
    hi_row = (hi_trace.get("sql_rows") or [{}])[0]
    hi_count_value = next(iter(hi_row.values()), None) if hi_row else None
    hi_count_ok = hi_count_value == expected_count
    print(f"  detected_language: {hi_trace.get('detected_language')}")
    print(f"  normalized_query: {hi_trace.get('normalized_query')}")
    print(f"  spatial: {hi_trace.get('spatial')}")
    print(f"  executed_sql: {hi_trace.get('executed_sql')}")
    print(f"  count == {expected_count}: {hi_count_ok} (got {hi_count_value})")

    overall = all([uses_gist, en_digipin_ok, en_latlon_ok, en_predicate_ok, en_radius_ok,
                    en_count_ok, hi_count_ok])
    print(f"\nD-V6: {'PASS' if overall else 'FAIL'}")
    raise SystemExit(0 if overall else 1)


if __name__ == "__main__":
    main()

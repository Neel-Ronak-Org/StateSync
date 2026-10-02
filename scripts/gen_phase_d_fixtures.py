#!/usr/bin/env python3
"""
scripts/gen_phase_d_fixtures.py
Generates fixtures/golden/phase_d_multilingual.json (40: 20 Hindi, 20
Hinglish; Deliverable 6) and fixtures/golden/phase_d_spatial.json (15;
Deliverable 8).

Every ground_truth_sql is executed live against mcp (bypassing the LLM
entirely, same discipline as C1/C2's golden fixtures) to freeze
expected_row_count -- never guessed.

Entities (seller names, city names) are pulled from the live
fixtures/ondc_seed.json, not hand-typed, so display_name/display_name_local
pairs can never drift from what's actually seeded.

Run once; the output is frozen (Appendix 2.2).

Usage:
    python3 scripts/gen_phase_d_fixtures.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
import mcp_auth  # noqa: E402 -- scripts/mcp_auth.py: the MCP service token

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / "fixtures" / "ondc_seed.json"
DIGIPIN_REF_PATH = REPO_ROOT / "fixtures" / "digipin_reference.json"
ML_OUT_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_d_multilingual.json"
SPATIAL_OUT_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_d_spatial.json"

MCP_URL = "http://localhost:8080"
TENANT = "t_capstone_default"


def mcp_run_select(sql: str) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "1", "method": "data.run_select", "params": {"sql": sql},
    })
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{MCP_URL}/rpc",
         "-H", "Content-Type: application/json",
         "-H", f"X-Tenant-ID: {TENANT}", *mcp_auth.curl_header_args(),
         "-H", "X-Session-ID: gen-phase-d-fixtures",
         "-d", payload],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["result"]


def freeze_row_counts(queries: list[dict], label: str) -> None:
    print(f"\n[{label}] Executing ground_truth_sql live to freeze expected_row_count...")
    for q in queries:
        r = mcp_run_select(q["ground_truth_sql"])
        if not r.get("ok"):
            print(f"  [{q['id']}] GROUND TRUTH SQL FAILED: {r.get('error')}")
            print(f"    SQL: {q['ground_truth_sql']}")
            raise SystemExit(1)
        q["expected_row_count"] = r["row_count"]
        print(f"  [{q['id']}] rows={r['row_count']}  ({q['lang']}) {q['text'][:60]}")


# ── Multilingual fixture (Deliverable 6) ────────────────────────────────────

def _seller(sellers_by_id: dict, sid: str) -> dict:
    return sellers_by_id[sid]


def build_multilingual_queries() -> list[dict]:
    seed = json.loads(SEED_PATH.read_text())
    sellers_by_id = {s["seller_id"]: s for s in seed["sellers"]}
    city_local_by_city = {l["city"]: l["city_local"] for l in seed["locations"]}

    queries: list[dict] = []
    n = 0

    def add(lang: str, text: str, english_equivalent: str, sql: str, proper_nouns: list[str], notes: str) -> None:
        nonlocal n
        n += 1
        queries.append({
            "id": f"d_ml_{n:03d}",
            "lang": lang,
            "text": text,
            "english_equivalent": english_equivalent,
            "expected_route": "sql",
            "ground_truth_sql": sql,
            "expected_row_count": None,
            "proper_nouns": proper_nouns,
            "notes": notes,
        })

    # ── Hindi (Devanagari script), entity-bearing (8 templates x 2 = 16) ────
    hi_seller_pairs = {
        "orders_count":  ("S001", "S002"),
        "total_value":   ("S003", "S004"),
        "category":      ("S005", "S006"),
        "disputes":      ("S007", "S008"),
        "sla":           ("S001", "S003"),
    }
    hi_city_pairs = {
        "delivered":     ("Mumbai", "Delhi"),
        "seller_count":  ("Bengaluru", "Chennai"),
        "avg_delay":     ("Hyderabad", "Pune"),
    }

    for sid in hi_seller_pairs["orders_count"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hi",
            f"{s['display_name_local']} ने कितने ऑर्डर प्राप्त किए हैं?",
            f"How many orders has {s['display_name']} received?",
            f"SELECT COUNT(*) FROM orders o JOIN sellers s ON o.seller_id = s.seller_id "
            f"WHERE s.display_name_local = '{s['display_name_local']}'",
            [s['display_name_local']],
            "Devanagari seller name must survive into a display_name_local match, not translated to display_name.",
        )

    for sid in hi_seller_pairs["total_value"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hi",
            f"{s['display_name_local']} के ऑर्डर का कुल मूल्य कितना है?",
            f"What is the total order value for {s['display_name']}?",
            f"SELECT SUM(o.order_value) FROM orders o JOIN sellers s ON o.seller_id = s.seller_id "
            f"WHERE s.display_name_local = '{s['display_name_local']}'",
            [s['display_name_local']],
            "",
        )

    for sid in hi_seller_pairs["category"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hi",
            f"{s['display_name_local']} की श्रेणी क्या है?",
            f"What category is {s['display_name']} in?",
            f"SELECT category FROM sellers WHERE display_name_local = '{s['display_name_local']}'",
            [s['display_name_local']],
            "",
        )

    for sid in hi_seller_pairs["disputes"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hi",
            f"{s['display_name_local']} के लिए कितने विवाद दर्ज हुए हैं?",
            f"How many disputes have been raised for {s['display_name']}?",
            f"SELECT COUNT(*) FROM disputes d JOIN orders o ON d.order_id = o.order_id "
            f"JOIN sellers s ON o.seller_id = s.seller_id WHERE s.display_name_local = '{s['display_name_local']}'",
            [s['display_name_local']],
            "",
        )

    for sid in hi_seller_pairs["sla"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hi",
            f"{s['display_name_local']} की अनुबंधित SLA कितने घंटे की है?",
            f"What is {s['display_name']}'s contracted SLA in hours?",
            f"SELECT contracted_sla_hours FROM sellers WHERE display_name_local = '{s['display_name_local']}'",
            [s['display_name_local']],
            "",
        )

    for city in hi_city_pairs["delivered"]:
        city_local = city_local_by_city[city]
        add(
            "hi",
            f"{city_local} में कितने ऑर्डर डिलीवर हुए हैं?",
            f"How many orders have been delivered to {city}?",
            f"SELECT COUNT(*) FROM orders o JOIN locations l ON o.ship_to_location_id = l.location_id "
            f"JOIN fulfillments f ON f.order_id = o.order_id "
            f"WHERE l.city_local = '{city_local}' AND f.status = 'delivered'",
            [city_local],
            "",
        )

    for city in hi_city_pairs["seller_count"]:
        city_local = city_local_by_city[city]
        add(
            "hi",
            f"{city_local} में कितने विक्रेता स्थित हैं?",
            f"How many sellers are located in {city}?",
            f"SELECT COUNT(*) FROM sellers s JOIN locations l ON s.location_id = l.location_id "
            f"WHERE l.city_local = '{city_local}'",
            [city_local],
            "",
        )

    for city in hi_city_pairs["avg_delay"]:
        city_local = city_local_by_city[city]
        add(
            "hi",
            f"{city_local} के ऑर्डर के लिए औसत डिलीवरी देरी कितने घंटे की है?",
            f"What is the average delivery delay in hours for orders to {city}?",
            f"SELECT AVG(EXTRACT(EPOCH FROM (f.delivered_at - f.promised_at)) / 3600) "
            f"FROM fulfillments f JOIN orders o ON f.order_id = o.order_id "
            f"JOIN locations l ON o.ship_to_location_id = l.location_id "
            f"WHERE l.city_local = '{city_local}' AND f.delivered_at IS NOT NULL",
            [city_local],
            "",
        )

    # ── Hindi, no proper noun (4) ───────────────────────────────────────────
    add(
        "hi",
        "पिछले 30 दिनों में कितने ऑर्डर असफल या वापस हुए हैं?",
        "How many orders failed or were returned in the last 30 days?",
        "SELECT COUNT(*) FROM fulfillments WHERE status IN ('failed', 'returned') "
        "AND promised_at >= now() - interval '30 days'",
        [], "Category words (failed/returned) are common nouns, not proper nouns -- translation is expected here.",
    )
    add(
        "hi",
        "पिछले 60 दिनों में कितने विवाद दर्ज हुए हैं?",
        "How many disputes have been raised in the last 60 days?",
        "SELECT COUNT(*) FROM disputes WHERE raised_at >= now() - interval '60 days'",
        [], "",
    )
    add(
        "hi",
        "कितने ऑर्डर b2b चैनल के हैं?",
        "How many orders are on the b2b channel?",
        "SELECT COUNT(*) FROM orders WHERE order_channel = 'b2b'",
        [], "",
    )
    add(
        "hi",
        "कितने ऑर्डर b2c चैनल के हैं?",
        "How many orders are on the b2c channel?",
        "SELECT COUNT(*) FROM orders WHERE order_channel = 'b2c'",
        [], "",
    )

    # ── Hinglish (Roman script, code-mixed), entity-bearing (8 x 2 = 16) ────
    hg_seller_pairs = {
        "orders_count": ("S009", "S010"),
        "total_value":  ("S011", "S012"),
        "category":     ("S013", "S014"),
        "disputes":     ("S015", "S016"),
        "sla":          ("S009", "S011"),
    }
    hg_city_pairs = {
        "delivered":    ("Jaipur", "Surat"),
        "seller_count": ("Lucknow", "Nagpur"),
        "avg_delay":    ("Patna", "Indore"),
    }

    for sid in hg_seller_pairs["orders_count"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hinglish",
            f"{s['display_name']} ne kitne orders receive kiye hain?",
            f"How many orders has {s['display_name']} received?",
            f"SELECT COUNT(*) FROM orders o JOIN sellers s ON o.seller_id = s.seller_id "
            f"WHERE s.display_name = '{s['display_name']}'",
            [s['display_name']],
            "Entity already Latin-script in the Hinglish input (D-V2 worked example pattern) -- must not be paraphrased to 'Seller 1' etc.",
        )

    for sid in hg_seller_pairs["total_value"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hinglish",
            f"{s['display_name']} ke orders ki total value kitni hai?",
            f"What is the total order value for {s['display_name']}?",
            f"SELECT SUM(o.order_value) FROM orders o JOIN sellers s ON o.seller_id = s.seller_id "
            f"WHERE s.display_name = '{s['display_name']}'",
            [s['display_name']],
            "",
        )

    for sid in hg_seller_pairs["category"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hinglish",
            f"{s['display_name']} ki category kya hai?",
            f"What category is {s['display_name']} in?",
            f"SELECT category FROM sellers WHERE display_name = '{s['display_name']}'",
            [s['display_name']],
            "",
        )

    for sid in hg_seller_pairs["disputes"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hinglish",
            f"{s['display_name']} ke against kitne disputes file hue hain?",
            f"How many disputes have been filed against {s['display_name']}?",
            f"SELECT COUNT(*) FROM disputes d JOIN orders o ON d.order_id = o.order_id "
            f"JOIN sellers s ON o.seller_id = s.seller_id WHERE s.display_name = '{s['display_name']}'",
            [s['display_name']],
            "",
        )

    for sid in hg_seller_pairs["sla"]:
        s = _seller(sellers_by_id, sid)
        add(
            "hinglish",
            f"{s['display_name']} ka contracted SLA kitne hours ka hai?",
            f"What is {s['display_name']}'s contracted SLA in hours?",
            f"SELECT contracted_sla_hours FROM sellers WHERE display_name = '{s['display_name']}'",
            [s['display_name']],
            "",
        )

    for city in hg_city_pairs["delivered"]:
        add(
            "hinglish",
            f"{city} mein kitne orders deliver hue hain?",
            f"How many orders have been delivered to {city}?",
            f"SELECT COUNT(*) FROM orders o JOIN locations l ON o.ship_to_location_id = l.location_id "
            f"JOIN fulfillments f ON f.order_id = o.order_id "
            f"WHERE l.city = '{city}' AND f.status = 'delivered'",
            [city],
            "",
        )

    for city in hg_city_pairs["seller_count"]:
        add(
            "hinglish",
            f"{city} mein kitne sellers located hain?",
            f"How many sellers are located in {city}?",
            f"SELECT COUNT(*) FROM sellers s JOIN locations l ON s.location_id = l.location_id "
            f"WHERE l.city = '{city}'",
            [city],
            "",
        )

    for city in hg_city_pairs["avg_delay"]:
        add(
            "hinglish",
            f"{city} ke orders ke liye average delivery delay kitne hours ki hai?",
            f"What is the average delivery delay in hours for orders to {city}?",
            f"SELECT AVG(EXTRACT(EPOCH FROM (f.delivered_at - f.promised_at)) / 3600) "
            f"FROM fulfillments f JOIN orders o ON f.order_id = o.order_id "
            f"JOIN locations l ON o.ship_to_location_id = l.location_id "
            f"WHERE l.city = '{city}' AND f.delivered_at IS NOT NULL",
            [city],
            "",
        )

    # ── Hinglish, no proper noun (4) ────────────────────────────────────────
    add(
        "hinglish",
        "Pichhle 30 dino mein kitne orders fail ya return hue hain?",
        "How many orders failed or were returned in the last 30 days?",
        "SELECT COUNT(*) FROM fulfillments WHERE status IN ('failed', 'returned') "
        "AND promised_at >= now() - interval '30 days'",
        [], "",
    )
    add(
        "hinglish",
        "Pichhle 60 dino mein kitne disputes file hue hain?",
        "How many disputes have been raised in the last 60 days?",
        "SELECT COUNT(*) FROM disputes WHERE raised_at >= now() - interval '60 days'",
        [], "",
    )
    add(
        "hinglish",
        "Kitne orders b2b channel ke hain?",
        "How many orders are on the b2b channel?",
        "SELECT COUNT(*) FROM orders WHERE order_channel = 'b2b'",
        [], "",
    )
    add(
        "hinglish",
        "Kitne orders b2c channel ke hain?",
        "How many orders are on the b2c channel?",
        "SELECT COUNT(*) FROM orders WHERE order_channel = 'b2c'",
        [], "",
    )

    return queries


# ── Spatial fixture (Deliverable 8) ─────────────────────────────────────────

def build_spatial_queries() -> list[dict]:
    ref = json.loads(DIGIPIN_REF_PATH.read_text())
    pairs = ref["pairs"]

    templates = [
        (
            "How many orders were shipped within {rk}km of {digipin}?",
            "SELECT COUNT(*) FROM orders o JOIN locations l ON o.ship_to_location_id = l.location_id "
            "WHERE ST_DWithin(l.geom, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography, {rm})",
        ),
        (
            "How many sellers are located within {rk}km of {digipin}?",
            "SELECT COUNT(*) FROM sellers s JOIN locations l ON s.location_id = l.location_id "
            "WHERE ST_DWithin(l.geom, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography, {rm})",
        ),
        (
            "How many delivery events occurred within {rk}km of {digipin}?",
            "SELECT COUNT(*) FROM delivery_events e JOIN locations l ON e.location_id = l.location_id "
            "WHERE ST_DWithin(l.geom, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography, {rm})",
        ),
        (
            "How many orders were delivered within {rk}km of {digipin}?",
            "SELECT COUNT(*) FROM orders o JOIN locations l ON o.ship_to_location_id = l.location_id "
            "JOIN fulfillments f ON f.order_id = o.order_id "
            "WHERE ST_DWithin(l.geom, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography, {rm}) "
            "AND f.status = 'delivered'",
        ),
    ]

    radii_km = [2, 5, 10, 5, 3, 5, 8, 5, 2, 5, 15, 5, 4, 5, 5]
    queries: list[dict] = []
    for i in range(15):
        pair = pairs[i % len(pairs)]
        template_text, template_sql = templates[i % len(templates)]
        rk = radii_km[i]
        rm = rk * 1000
        text = template_text.format(rk=rk, digipin=pair["digipin"])
        sql = template_sql.format(lon=pair["lon"], lat=pair["lat"], rm=rm)
        queries.append({
            "id": f"d_sp_{i + 1:03d}",
            "lang": "en",
            "text": text,
            "expected_route": "sql",
            "ground_truth_sql": sql,
            "expected_row_count": None,
            "digipin": pair["digipin"],
            "lat": pair["lat"],
            "lon": pair["lon"],
            "radius_m": rm,
            "notes": "",
        })
    return queries


def main() -> None:
    ml_queries = build_multilingual_queries()
    hi_count = sum(1 for q in ml_queries if q["lang"] == "hi")
    hg_count = sum(1 for q in ml_queries if q["lang"] == "hinglish")
    proper_noun_count = sum(1 for q in ml_queries if q["proper_nouns"])
    print(f"Built {len(ml_queries)} multilingual queries: {hi_count} hi, {hg_count} hinglish, "
          f"{proper_noun_count} with a proper noun (need >= 15).")
    assert len(ml_queries) == 40
    assert hi_count == 20
    assert hg_count == 20
    assert proper_noun_count >= 15

    freeze_row_counts(ml_queries, "multilingual")
    ML_OUT_PATH.write_text(json.dumps(
        {"version": 1, "phase": "d_multilingual", "queries": ml_queries}, indent=2, ensure_ascii=False,
    ))
    print(f"Written to: {ML_OUT_PATH}")

    sp_queries = build_spatial_queries()
    assert len(sp_queries) == 15
    freeze_row_counts(sp_queries, "spatial")
    SPATIAL_OUT_PATH.write_text(json.dumps(
        {"version": 1, "phase": "d_spatial", "queries": sp_queries}, indent=2, ensure_ascii=False,
    ))
    print(f"Written to: {SPATIAL_OUT_PATH}")
    print("\nFreeze both files (Appendix 2.2). Do not regenerate without cause.")


if __name__ == "__main__":
    main()

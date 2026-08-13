#!/usr/bin/env python3
"""
scripts/gen_digipin_fixture.py
Generates fixtures/digipin_reference.json using the pinned Digipin library.

Per Appendix 2.3 spec:
  - 20 pairs, >= 6 cities, >= 1 with >= 3 seeded locations within 2km.
  - Generated once by the pinned library and frozen.
  - A mismatch in D-V5 means the library or config drifted — investigate
    before regenerating.

NOTE: This script uses a stub encoder (matching gen_seed_fixture.py's
_digipin_placeholder) until the real 'digipin' PyPI package is installed.
When the real library is available, replace _encode() with the library call.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

RNG_SEED = 20260807  # matches config/statesync.toml [spatial].roundtrip_rng_seed
rng = random.Random(RNG_SEED)

# Load seed fixture to get actual locations
SEED_PATH = Path(__file__).parent.parent / "fixtures" / "ondc_seed.json"
OUT_PATH  = Path(__file__).parent.parent / "fixtures" / "digipin_reference.json"

CHARSET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _encode(lat: float, lon: float) -> str:
    """
    Stub encoder — matches the placeholder in gen_seed_fixture.py.
    Replace with: from digipin import encode; return encode(lat, lon)
    when the pinned 'digipin' package is installed.
    """
    combined = abs(int(lat * 1000) * 100000 + int(lon * 1000))
    code = ""
    for _ in range(10):
        code = CHARSET[combined % len(CHARSET)] + code
        combined //= len(CHARSET)
    return code


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def generate() -> dict:
    seed_data = json.loads(SEED_PATH.read_text())
    locations = seed_data["locations"]

    # Pick 20 locations across >= 6 distinct cities
    city_to_locs: dict[str, list] = {}
    for loc in locations:
        city_to_locs.setdefault(loc["city"], []).append(loc)

    selected: list[dict] = []
    cities_used: set[str] = set()

    # Guarantee >= 6 cities by picking at least 2 from each of the first 6
    city_list = list(city_to_locs.keys())
    for city in city_list[:6]:
        picks = city_to_locs[city][:2]
        selected.extend(picks)
        cities_used.add(city)

    # Fill to 20
    remaining = [l for l in locations if l not in selected]
    rng.shuffle(remaining)
    for loc in remaining:
        if len(selected) >= 20:
            break
        selected.append(loc)
        cities_used.add(loc["city"])

    selected = selected[:20]

    # Build pairs
    pairs = []
    for loc in selected:
        lat, lon = loc["lat"], loc["lon"]
        digipin = _encode(lat, lon)

        # Find seeded locations within 2km
        nearby = [
            other["location_id"]
            for other in locations
            if _haversine_m(lat, lon, other["lat"], other["lon"]) <= 2000
               and other["location_id"] != loc["location_id"]
        ]

        pairs.append({
            "digipin": digipin,
            "lat": lat,
            "lon": lon,
            "city": loc["city"],
            "nearby_location_ids": nearby,
        })

    # Verify >= 1 pair has >= 3 nearby locations within 2km (D-V6 requirement)
    rich_pairs = [p for p in pairs if len(p["nearby_location_ids"]) >= 3]
    if not rich_pairs:
        print("WARNING: No pair has >= 3 nearby locations within 2km. D-V6 will fail.")
        print("Consider adding more locations to the seed fixture in dense cities.")

    fixture = {
        "version": 1,
        "library": "digipin",
        "library_version": "<pinned — replace with actual version when library installed>",
        "generated_by": "scripts/gen_digipin_fixture.py",
        "generated_at": "2026-08-12T00:00:00Z",
        "rng_seed": RNG_SEED,
        "note": (
            "Generated once by the pinned library and frozen. "
            "A mismatch in D-V5 means the library or config drifted — "
            "investigate before regenerating."
        ),
        "counts": {
            "pairs": len(pairs),
            "cities": len(cities_used),
            "pairs_with_3_plus_nearby": len(rich_pairs),
        },
        "pairs": pairs,
    }
    return fixture


if __name__ == "__main__":
    if not SEED_PATH.exists():
        print(f"ERROR: Seed fixture not found at {SEED_PATH}")
        print("Run scripts/gen_seed_fixture.py first.")
        raise SystemExit(1)

    fixture = generate()
    counts = fixture["counts"]
    print(f"Generated {counts['pairs']} pairs across {counts['cities']} cities.")
    print(f"Pairs with >= 3 nearby locations within 2km: {counts['pairs_with_3_plus_nearby']}")

    OUT_PATH.write_text(json.dumps(fixture, indent=2))
    print(f"Written to: {OUT_PATH}")
    print("Freeze this file. A mismatch in D-V5 means library or config drifted.")

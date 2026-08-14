#!/usr/bin/env python3
"""
scripts/check_digipin_roundtrip.py
D-V4 -- the primary DIGIPIN decode test (property-based, not fixture-based).

For 200 coordinate pairs sampled deterministically (spatial.roundtrip_rng_seed)
within India's bounding box: encode -> decode -> measure geodesic distance
from the original point. Pass: 200/200 within spatial.roundtrip_tolerance_m
(4.0m), consistent with the real library's 4m x 4m grid resolution at 10
levels of 4-way subdivision.

Bounds match the pinned 'digipin' library's own BOUNDS (module.py:
minLat=2.5, maxLat=38.5, minLon=63.5, maxLon=99.5) so every sampled point is
guaranteed encodable -- this IS the library's definition of "India's
bounding box" for this algorithm, not an independently invented one.

Usage:
    python3 scripts/check_digipin_roundtrip.py
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import digipin

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
from config_loader import cfg  # noqa: E402

# Matches the pinned library's own encode()/decode() BOUNDS exactly.
MIN_LAT, MAX_LAT = 2.5, 38.5
MIN_LON, MAX_LON = 63.5, 99.5


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def main() -> None:
    seed = cfg.spatial.roundtrip_rng_seed
    sample_size = cfg.spatial.roundtrip_sample_size
    tolerance_m = cfg.spatial.roundtrip_tolerance_m

    print(f"D-V4: DIGIPIN round-trip property test -- {sample_size} pairs, "
          f"seed={seed}, tolerance={tolerance_m}m")

    rng = random.Random(seed)
    within_tolerance = 0
    max_distance = 0.0
    failures = []

    for i in range(sample_size):
        lat = rng.uniform(MIN_LAT, MAX_LAT)
        lon = rng.uniform(MIN_LON, MAX_LON)

        code = digipin.encode(lat, lon).replace(" ", "")
        assert len(code) == 10, f"encode() produced a non-canonical length: {code!r}"

        decoded_lat, decoded_lon = digipin.decode(code)
        distance_m = _haversine_m(lat, lon, decoded_lat, decoded_lon)
        max_distance = max(max_distance, distance_m)

        if distance_m <= tolerance_m:
            within_tolerance += 1
        else:
            failures.append({
                "i": i, "lat": lat, "lon": lon, "digipin": code, "distance_m": distance_m,
            })

    print(f"\n{within_tolerance}/{sample_size} within {tolerance_m}m")
    print(f"Max observed round-trip distance: {max_distance:.3f}m")

    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for f in failures[:10]:
            print(f"  #{f['i']}: ({f['lat']:.6f}, {f['lon']:.6f}) -> {f['digipin']} "
                  f"-> {f['distance_m']:.3f}m")

    passed = within_tolerance == sample_size
    print(f"\nD-V4: {'PASS' if passed else 'FAIL'}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

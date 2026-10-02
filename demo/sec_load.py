"""
demo/sec_load.py
SEC XBRL company facts for the 32 FinanceBench companies (data/external/sec, fetched 2026-10-02;
docs/eval/external_datasets.md) as two tab-separated files for COPY: companies and facts.
Used by demo/sec_db.sh.

    python3 demo/sec_load.py <out_dir>
"""

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "data" / "external" / "sec"


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((SRC / "financebench_companies.json").read_text())
    by_cik = {int(v["cik"]): (name, v.get("ticker")) for name, v in meta.items()}
    with open(out / "companies.tsv", "w", newline="") as fc, open(out / "facts.tsv", "w", newline="") as ff:
        wc = csv.writer(fc, delimiter="\t", lineterminator="\n")
        wf = csv.writer(ff, delimiter="\t", lineterminator="\n")
        for path in sorted((SRC / "companyfacts").glob("CIK*.json")):
            data = json.loads(path.read_text())
            cik = int(data["cik"])
            fb_name, ticker = by_cik.get(cik, (data["entityName"], None))
            wc.writerow([cik, ticker or "", data["entityName"], fb_name])
            for taxonomy, tags in data["facts"].items():
                for tag, body in tags.items():
                    label = (body.get("label") or "").replace("\t", " ").replace("\n", " ")
                    for unit, rows in body.get("units", {}).items():
                        for r in rows:
                            wf.writerow([cik, taxonomy, tag, label, unit, r.get("start") or "", r["end"], r["val"],
                                         r.get("fy") or "", r.get("fp") or "", r.get("form") or "",
                                         r.get("filed") or "", r.get("accn") or "", r.get("frame") or ""])


if __name__ == "__main__":
    main()

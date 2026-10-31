#!/usr/bin/env python3
"""
scripts/gen_document_corpus.py
Generates fixtures/documents/ -- 18 synthetic PDFs (Part E1 Callout 1: 12-20
required), each with >= 1 table, via reportlab. Also writes
fixtures/documents/manifest.json (doc_type/party_id/title/source_uri per
file), read by scripts/ingest.py so ingestion doesn't have to re-derive
document metadata from filenames.

Every table carries at least one numeric value that does not appear
anywhere in the relational store (fixtures/ondc_seed.json / the live DB):
zone-based SLA/penalty/transit figures and policy-committed timeframes that
have no column anywhere in A5's schema. This is what makes E1-V3 (grounding)
meaningful -- see E1 Scope's own framing: "If a document only restates
what's in orders/fulfillments, E1-V3 proves nothing."

supplier_agreement_S014.pdf is REQUIRED by E1-V2 to contain, verbatim, the
row: Metro NCR | 36 hours | 2% penalty. All other documents' numbers are
deterministically varied (seeded RNG, matching this repo's fixture-generation
convention) but otherwise arbitrary -- there is no other frozen requirement.

Run once; the output is frozen (Appendix 2 spirit: fixtures are populated
once, then used, not regenerated without cause).

Usage:
    python3 scripts/gen_document_corpus.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / "fixtures" / "ondc_seed.json"
OUT_DIR = REPO_ROOT / "fixtures" / "documents"

RNG_SEED = 20260807  # matches every other fixture generator in this repo
rng = random.Random(RNG_SEED)

ZONES = ["Metro NCR", "Metro Mumbai", "Tier-2 West", "Tier-2 South", "Rural North", "Rural East"]

SUPPLIER_AGREEMENT_SELLERS = ["S001", "S005", "S010", "S014", "S020", "S025"]
SLA_ANNEXURE_PROVIDERS = ["LP001", "LP003", "LP005", "LP007"]


def _styles():
    styles = getSampleStyleSheet()
    return styles


def _build_table(header: list[str], rows: list[list[str]]) -> Table:
    data = [header] + rows
    table = Table(data, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    return table


def _write_pdf(path: Path, title: str, paragraphs: list[str], table_header: list[str], table_rows: list[list[str]]) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(str(path), pagesize=letter, topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    story = [Paragraph(title, styles["Title"]), Spacer(1, 16)]
    for p in paragraphs:
        story.append(Paragraph(p, styles["BodyText"]))
        story.append(Spacer(1, 10))
    story.append(Spacer(1, 6))
    story.append(_build_table(table_header, table_rows))
    doc.build(story)


def _sla_table_rows(zones: list[str], base_sla: int, base_penalty: int, force_metro_ncr_36: bool) -> list[list[str]]:
    rows = []
    for i, zone in enumerate(zones):
        if force_metro_ncr_36 and zone == "Metro NCR":
            rows.append(["Metro NCR", "36 hours", "2% penalty"])
            continue
        sla_hours = base_sla + i * rng.randint(8, 16)
        penalty_pct = base_penalty + (i // 2)
        rows.append([zone, f"{sla_hours} hours", f"{penalty_pct}% penalty"])
    return rows


def gen_supplier_agreements(sellers_by_id: dict) -> list[dict]:
    manifest = []
    for sid in SUPPLIER_AGREEMENT_SELLERS:
        s = sellers_by_id[sid]
        zones = ZONES[:4] if sid != "S014" else ["Metro NCR", "Metro Mumbai", "Tier-2 West", "Rural North"]
        base_sla = rng.randint(24, 40)
        base_penalty = rng.randint(2, 4)
        rows = _sla_table_rows(zones, base_sla, base_penalty, force_metro_ncr_36=(sid == "S014"))
        filename = f"supplier_agreement_{sid}.pdf"
        title = f"Supplier Agreement -- {s['display_name']} (Seller ID: {sid})"
        paragraphs = [
            f"This Supplier Agreement is entered into between the ONDC Network "
            f"(\"Network\") and {s['display_name']} (\"Seller\", Seller ID: {sid}), "
            f"operating in the {s['category']} category.",
            f"Seller agrees to the following zone-based delivery service level "
            f"commitments. A breach of the committed SLA for orders shipped to a "
            f"given zone entitles the affected buyer app to the penalty specified "
            f"for that zone, applied as a percentage credit against the order value.",
            "These zone-level commitments supplement, and are more granular than, "
            "the Seller's general contracted_sla_hours on file with the Network.",
        ]
        _write_pdf(OUT_DIR / filename, title, paragraphs, ["Zone", "SLA", "Penalty"], rows)
        manifest.append({
            "filename": filename, "doc_type": "supplier_agreement",
            "title": title, "party_id": sid,
        })
    return manifest


def gen_sla_annexures(lp_by_id: dict) -> list[dict]:
    manifest = []
    for lid in SLA_ANNEXURE_PROVIDERS:
        lp = lp_by_id[lid]
        zones = ZONES[1:5]
        base_transit = rng.randint(18, 30)
        rows = []
        for i, zone in enumerate(zones):
            transit_hours = base_transit + i * rng.randint(6, 12)
            on_time_pct = 99 - i * rng.randint(2, 5)
            rows.append([zone, f"{transit_hours} hours", f"{on_time_pct}%"])
        filename = f"sla_annexure_{lid}.pdf"
        title = f"SLA Annexure -- {lp['display_name']} (Provider ID: {lid})"
        paragraphs = [
            f"This SLA Annexure specifies {lp['display_name']}'s ({lid}) committed "
            f"transit times and on-time delivery rate by zone, as agreed with the "
            f"ONDC Network's logistics coordination office.",
            "Transit time is measured from hub dispatch to first delivery attempt. "
            "On-time rate is a rolling quarterly commitment, reviewed independently "
            "of any single fulfillment's recorded delivery timestamps.",
        ]
        _write_pdf(OUT_DIR / filename, title, paragraphs, ["Zone", "Committed Transit Time", "On-Time Rate"], rows)
        manifest.append({
            "filename": filename, "doc_type": "sla_annexure",
            "title": title, "party_id": lid,
        })
    return manifest


_POLICY_DOCS = [
    {
        "filename": "policy_dispute_resolution.pdf",
        "title": "ONDC Network Dispute Resolution Policy",
        "paragraphs": [
            "This policy governs how disputes raised against orders on the ONDC "
            "Network are triaged and resolved. Every dispute is assigned a "
            "category at intake; each category carries its own committed "
            "resolution window and escalation tier, independent of when the "
            "dispute happens to be marked resolved in operational tracking.",
        ],
        "table_header": ["Dispute Category", "Committed Resolution (business days)", "Escalation Tier"],
        "table_rows": [
            ["late_delivery", "3", "Tier 1"],
            ["damaged_goods", "5", "Tier 2"],
            ["wrong_item", "4", "Tier 1"],
            ["not_delivered", "7", "Tier 2"],
            ["refund_delay", "10", "Tier 3"],
        ],
    },
    {
        "filename": "policy_returns_refunds.pdf",
        "title": "ONDC Network Returns and Refunds Policy",
        "paragraphs": [
            "This policy sets the return eligibility window and refund processing "
            "commitment for each catalog category. These windows are policy "
            "commitments to buyers and are not derived from any single seller's "
            "contracted SLA.",
        ],
        "table_header": ["Category", "Return Window (days)", "Refund Processing (business days)"],
        "table_rows": [
            ["fashion", "15", "5"],
            ["electronics", "10", "7"],
            ["grocery", "2", "3"],
            ["home_decor", "12", "6"],
            ["pharma", "0", "2"],
            ["food", "1", "2"],
            ["sports", "15", "5"],
        ],
    },
    {
        "filename": "policy_data_retention.pdf",
        "title": "ONDC Network Data Retention and Privacy Policy",
        "paragraphs": [
            "This policy specifies the retention period the Network commits to "
            "for each class of operational data collected in the course of "
            "fulfilling orders across the network.",
        ],
        "table_header": ["Data Class", "Retention Period"],
        "table_rows": [
            ["Order transaction records", "7 years"],
            ["Delivery event logs", "3 years"],
            ["Dispute narratives", "5 years"],
            ["Buyer location data", "18 months"],
            ["Seller onboarding documents", "10 years"],
        ],
    },
    {
        "filename": "policy_seller_compliance.pdf",
        "title": "ONDC Network Seller Onboarding and Compliance Policy",
        "paragraphs": [
            "This policy specifies the compliance checks every seller must "
            "satisfy at onboarding and on a recurring renewal cycle to remain "
            "active on the Network.",
        ],
        "table_header": ["Compliance Check", "Required Document", "Renewal Cycle"],
        "table_rows": [
            ["GST registration", "GST certificate", "Annual"],
            ["Business address verification", "Utility bill or lease", "Biennial"],
            ["Bank account verification", "Cancelled cheque", "Annual"],
            ["Category-specific licensing", "Category license", "Annual"],
        ],
    },
]

_SOP_DOCS = [
    {
        "filename": "sop_delivery_exceptions.pdf",
        "title": "Delivery Exception Handling SOP",
        "paragraphs": [
            "This standard operating procedure specifies the required action and "
            "escalation window for each class of delivery exception encountered "
            "by logistics providers operating on the Network.",
        ],
        "table_header": ["Exception Type", "Required Action", "Escalation Window (hours)"],
        "table_rows": [
            ["Recipient unavailable", "Reattempt next business day", "24"],
            ["Address undeliverable", "Contact buyer app for correction", "12"],
            ["Package damaged in transit", "Hold and photograph before delivery", "4"],
            ["Refused by recipient", "Return to origin hub", "48"],
        ],
    },
    {
        "filename": "sop_warehouse_damage.pdf",
        "title": "Warehouse Damage Assessment SOP",
        "paragraphs": [
            "This standard operating procedure governs how warehouse-reported "
            "damage is assessed and compensated, by severity tier.",
        ],
        "table_header": ["Damage Severity", "Assessment Window (hours)", "Compensation Tier"],
        "table_rows": [
            ["Cosmetic (packaging only)", "12", "No compensation"],
            ["Minor (product intact)", "24", "10% of item value"],
            ["Major (product unusable)", "48", "100% of item value"],
        ],
    },
    {
        "filename": "sop_customer_escalation.pdf",
        "title": "Customer Complaint Escalation SOP",
        "paragraphs": [
            "This standard operating procedure specifies the response time "
            "commitment and resolving tier for customer complaints, by severity.",
        ],
        "table_header": ["Severity", "Response Time (hours)", "Resolving Tier"],
        "table_rows": [
            ["Low", "48", "Frontline support"],
            ["Medium", "24", "Category specialist"],
            ["High", "4", "Network operations"],
            ["Critical", "1", "Network operations lead"],
        ],
    },
    {
        "filename": "sop_cold_chain.pdf",
        "title": "Cold Chain and Perishables Handling SOP",
        "paragraphs": [
            "This standard operating procedure specifies the maximum transit "
            "time and required temperature range for perishable product "
            "categories moved through the Network's logistics providers.",
        ],
        "table_header": ["Product Category", "Max Transit Time (hours)", "Temperature Range"],
        "table_rows": [
            ["Fresh produce", "18", "2-8 C"],
            ["Dairy", "12", "2-6 C"],
            ["Frozen goods", "24", "-18 to -12 C"],
            ["Pharma (cold chain)", "8", "2-8 C"],
        ],
    },
]


def gen_static_docs(specs: list[dict], doc_type: str) -> list[dict]:
    manifest = []
    for spec in specs:
        _write_pdf(
            OUT_DIR / spec["filename"], spec["title"], spec["paragraphs"],
            spec["table_header"], spec["table_rows"],
        )
        manifest.append({
            "filename": spec["filename"], "doc_type": doc_type,
            "title": spec["title"], "party_id": None,
        })
    return manifest


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seed = json.loads(SEED_PATH.read_text())
    sellers_by_id = {s["seller_id"]: s for s in seed["sellers"]}
    lp_by_id = {lp["provider_id"]: lp for lp in seed["logistics_providers"]}

    manifest: list[dict] = []
    manifest += gen_supplier_agreements(sellers_by_id)
    manifest += gen_sla_annexures(lp_by_id)
    manifest += gen_static_docs(_POLICY_DOCS, "policy")
    manifest += gen_static_docs(_SOP_DOCS, "sop")

    assert 12 <= len(manifest) <= 20, f"Corpus must be 12-20 documents, got {len(manifest)}"
    for entry in manifest:
        entry["document_id"] = f"doc_{Path(entry['filename']).stem}"
        entry["source_uri"] = f"fixtures/documents/{entry['filename']}"

    (OUT_DIR / "manifest.json").write_text(json.dumps({"version": 1, "documents": manifest}, indent=2))

    print(f"Generated {len(manifest)} PDFs in {OUT_DIR}:")
    for entry in manifest:
        print(f"  {entry['filename']:<40} {entry['doc_type']:<20} party={entry['party_id']}")
    print(f"\nManifest written to {OUT_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()

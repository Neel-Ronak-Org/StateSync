#!/usr/bin/env python3
"""
demo/make_documents.py
Builds the demo documents (PDF) for the olist sample dataset: a seller handbook
with business definitions, a returns and refunds policy, and a delivery SLA.

They are FICTIONAL policies written for the demo (the olist data is real, these
documents are not Olist's). Each definition is one sentence, so "Find in
documents" can quote it exactly. Requires reportlab.

Usage: python3 demo/make_documents.py   (writes demo/documents/*.pdf)
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parent / "documents"
NOTE = "Demo document: a fictional policy written for the Olist sample dataset in StateSync."

DOCS = {
    "Marketplace seller handbook 2026.pdf": [
        ("h1", "Marketplace Seller Handbook 2026"),
        ("note", NOTE),
        ("h2", "1. Purpose"),
        ("p", "This handbook sets out how the marketplace measures sellers and orders. The reporting team "
              "uses these definitions in every dashboard and monthly review."),
        ("h2", "2. Definitions"),
        ("p", "For the purposes of reporting, the following terms apply."),
        ("p", "An order is On time when it is delivered to the customer on or before the estimated delivery "
              "date shown at purchase."),
        ("p", "A Late order is a delivered order whose delivery date is after the estimated delivery date."),
        ("p", "Active seller means a seller with at least one delivered order in the last 90 days."),
        ("p", "GMV means the sum of item prices of all delivered orders, excluding freight."),
        ("p", "Net revenue is defined as GMV minus the value of refunded orders."),
        ("p", "A Repeat customer is a customer who has placed two or more orders."),
        ("p", "The Cancellation rate is the number of canceled orders divided by all orders placed in the "
              "same period."),
        ("h2", "3. Seller levels"),
        ("table", [["Level", "Delivered orders (90 days)", "On-time share"],
                   ["Bronze", "1 - 49", "at least 85%"],
                   ["Silver", "50 - 199", "at least 90%"],
                   ["Gold", "200 or more", "at least 95%"]]),
        ("p", "A seller whose on-time share falls below the level's threshold for two consecutive months "
              "moves down one level."),
    ],
    "Returns and refunds policy.pdf": [
        ("h1", "Returns and Refunds Policy"),
        ("note", NOTE),
        ("h2", "1. Return window"),
        ("p", "Customers may request a return within 7 days of delivery for any product category except "
              "perishable food and personal care items."),
        ("h2", "2. Refunds"),
        ("p", "Refunds are issued to the original payment method within 10 business days after the returned "
              "item has been inspected at the seller's warehouse."),
        ("p", "Orders paid with a voucher are refunded as a new voucher of the same value."),
        ("h2", "3. Damaged items"),
        ("p", "Items reported as damaged within 48 hours of delivery are refunded in full, including freight. "
              "After 48 hours a 15% restocking fee applies."),
        ("h2", "4. Review follow-up"),
        ("p", "A review with a score of 1 or 2 opens a customer-care case, which must receive a first "
              "response within 24 hours."),
    ],
    "Delivery SLA by region.pdf": [
        ("h1", "Delivery Service Levels by Region"),
        ("note", NOTE),
        ("p", "Estimated delivery dates shown at purchase follow the commitments below, counted in business "
              "days from payment approval."),
        ("table", [["Customer state", "Committed delivery (business days)", "Late-delivery credit"],
                   ["SP", "5", "5% of freight"],
                   ["RJ, MG, PR", "7", "10% of freight"],
                   ["Other South and Southeast", "9", "10% of freight"],
                   ["North and Northeast", "14", "15% of freight"]]),
        ("p", "A delivery counts against the commitment when the carrier hand-off happens more than 2 business "
              "days after payment approval."),
        ("p", "Freight value is charged per item and is never included in GMV."),
    ],
}


def build(name: str, blocks: list) -> None:
    styles = getSampleStyleSheet()
    story = []
    for kind, content in blocks:
        if kind == "h1":
            story += [Paragraph(content, styles["Title"]), Spacer(1, 0.2 * cm)]
        elif kind == "h2":
            story += [Spacer(1, 0.3 * cm), Paragraph(content, styles["Heading2"])]
        elif kind == "note":
            story += [Paragraph(f"<i>{content}</i>", styles["Normal"]), Spacer(1, 0.3 * cm)]
        elif kind == "p":
            story += [Paragraph(content, styles["BodyText"]), Spacer(1, 0.15 * cm)]
        elif kind == "table":
            table = Table(content, hAlign="LEFT")
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, "#999999"),
                                       ("BACKGROUND", (0, 0), (-1, 0), "#eeeeee"),
                                       ("FONTSIZE", (0, 0), (-1, -1), 9)]))
            story += [Spacer(1, 0.2 * cm), table, Spacer(1, 0.2 * cm)]
    SimpleDocTemplate(str(OUT / name), pagesize=A4, title=name[:-4]).build(story)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, blocks in DOCS.items():
        build(name, blocks)
        print("wrote", OUT / name)

#!/usr/bin/env python3
"""
scripts/gen_phase_f_routing_fixture.py
Generates fixtures/golden/phase_f_routing.json — F-V2's fixed 60-query
routing set (Appendix 2.2: 20 per route).

No live calls, same reasoning as gen_phase_e2_fixture.py: `expected_route` is
a design fact about what each question needs, not something to derive by
running the system and freezing whatever it happened to output. Queries are
authored fresh (not reused verbatim from phase_c2/e1's golden sets) so F-V2
tests routing on questions the Supervisor prompt was never tuned against.

Route assignment follows the same rule the Supervisor's own system prompt
uses (orchestrator/nodes/supervisor.py): sql = answerable entirely from the
relational schema (A5); docs = the fact exists ONLY in a document (a penalty
percentage, an SLA hours figure, a policy window) and never as a database
column; hybrid = needs a database fact AND a document fact joined together.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_f_routing.json"

SQL_QUERIES = [
    "How many orders were placed in the last 90 days?",
    "What is the total order value for seller S014?",
    "List the top 5 logistics providers by number of fulfillments handled.",
    "How many disputes are still unresolved?",
    "What is the average delivery delay across all fulfillments?",
    "Which buyer apps have placed more than 50 orders?",
    "How many catalog items does each seller offer?",
    "What percentage of orders are B2B versus B2C?",
    "List all fulfillments with more than one delivery attempt.",
    "How many delivery events were recorded last month, broken down by event type?",
    "Which sellers have never had a dispute raised against them?",
    "What is the median order value across the network?",
    "How many locations are registered in Maharashtra?",
    "List the 10 most recent orders placed in Mumbai.",
    "What is the total line value of items sold in the grocery category?",
    "How many logistics providers are B2B-enabled?",
    "Which seller has the highest total order value this quarter?",
    "How many fulfillments are currently in transit?",
    "What is the count of orders per order channel?",
    "List sellers whose contracted SLA hours field is null in the database.",
]

DOCS_QUERIES = [
    "What is the contracted delivery SLA for supplier S014 in the Metro NCR zone?",
    "What penalty percentage does the SLA annexure for logistics provider LP003 specify for missed deliveries?",
    "According to the network's returns policy, what is the window for a customer to initiate a return?",
    "What does the data retention policy say about how long delivery event notes are kept?",
    "What is the resolution window specified in the dispute resolution policy?",
    "What are the seller compliance policy's requirements for catalog listing accuracy?",
    "What temperature range does the cold chain SOP require for perishable goods in transit?",
    "What is the first step a support agent should take per the customer escalation SOP?",
    "According to the delivery exceptions SOP, what should a courier do if a customer is unavailable at the address?",
    "What does the warehouse damage SOP require before a damaged item can be written off?",
    "What is the penalty clause in supplier S001's agreement for late delivery?",
    "What SLA does supplier S010's agreement commit to for the western region?",
    "What refund timeline does the returns and refunds policy specify for electronics?",
    "What escalation tier handles disputes that remain unresolved after the SOP's first response window?",
    "What does the seller compliance policy say about repeated late fulfillment?",
    "What is the SLA annexure penalty for logistics provider LP007 in a missed pickup?",
    "What documentation does the warehouse damage SOP require for a damage claim?",
    "What does the data retention policy say about dispute narrative retention after resolution?",
    "What is supplier S020's contracted SLA per their supplier agreement?",
    "What steps does the cold chain SOP specify for a temperature excursion during transit?",
]

HYBRID_QUERIES = [
    "Which sellers breached their contracted delivery SLA last quarter, and what penalty does their agreement specify?",
    "Does seller S014's actual average delivery time exceed the SLA committed in their supplier agreement?",
    "How many fulfillments did logistics provider LP003 deliver late, and what penalty does their SLA annexure impose for that?",
    "Are the number of unresolved disputes within the window the dispute resolution policy allows, or has it been exceeded?",
    "How many returns has seller S001 processed, and does that exceed what the returns policy considers a compliance concern?",
    "Which cold-chain category orders had a dispute raised, and does the cold chain SOP's temperature requirement explain the failure?",
    "How many disputes are categorized as damage-related, and what does the warehouse damage SOP require for each to be resolved?",
    "Which sellers have late-fulfillment rates that the seller compliance policy would flag, and what does that policy say should happen next?",
    "Are the unresolved dispute counts for any category approaching the threshold the customer escalation SOP defines for escalation?",
    "What is supplier S010's actual fulfillment performance in the western region, and does it meet the SLA in their agreement?",
    "How many of logistics provider LP007's fulfillments involved a missed pickup, and what penalty does their SLA annexure specify?",
    "Does the count of late deliveries for supplier S020 exceed the tolerance implied by the penalty terms in their agreement?",
    "How many orders has seller S025 had returned, and what refund timeline does the returns policy require for those?",
    "Which suppliers' contracted_sla_hours in the database differ from the SLA their own agreement document states?",
    "How many disputes for seller S005 remain open past the resolution policy's window?",
    "What is the average delivery delay for logistics provider LP001, and how does their SLA annexure define an acceptable delay?",
    "How many warehouse-damage disputes exist for seller S014, and did their handling follow the warehouse damage SOP's documentation requirement?",
    "Which sellers exceed the compliance policy's late-fulfillment threshold based on their actual fulfillment records?",
    "How many disputes for supplier S001 were escalated, and does that match what the customer escalation SOP defines as an escalation trigger?",
    "Does logistics provider LP005's actual missed-pickup rate exceed what their SLA annexure's penalty clause is priced for?",
]


def main() -> None:
    assert len(SQL_QUERIES) == 20 and len(DOCS_QUERIES) == 20 and len(HYBRID_QUERIES) == 20, (
        "F-V2 requires exactly 20 per route (Appendix 2.2)."
    )

    queries = []
    for i, text in enumerate(SQL_QUERIES, start=1):
        queries.append({"id": f"f_sql_{i:03d}", "lang": "en", "expected_route": "sql", "text": text})
    for i, text in enumerate(DOCS_QUERIES, start=1):
        queries.append({"id": f"f_docs_{i:03d}", "lang": "en", "expected_route": "docs", "text": text})
    for i, text in enumerate(HYBRID_QUERIES, start=1):
        queries.append({"id": f"f_hybrid_{i:03d}", "lang": "en", "expected_route": "hybrid", "text": text})

    payload = {
        "version": 1,
        "phase": "f",
        "generated_by": "scripts/gen_phase_f_routing_fixture.py",
        "note": (
            "F-V2's fixed 60-query routing set (Appendix 2.2). 20 per route, "
            "authored fresh (not reused from other phases' golden sets) so "
            "routing is tested on questions the Supervisor prompt was never "
            "tuned against. Frozen."
        ),
        "queries": queries,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}: {len(queries)} queries (20/route)")


if __name__ == "__main__":
    main()

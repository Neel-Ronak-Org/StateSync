# Routing set: owner's label check

> Review scope: assistant assessment of the supplied routing rules, wording and coverage claims. The SQL table and underlying filing inventory were not provided or queried. Agreement that depends on source coverage is provisional, not an independent coverage attestation. Original labels and source text are preserved.
>
> Summary: 27 agree, 3 disagree (items 1, 8 and 18).

30 labels drawn at random (seed 20261006) from `docs/eval/routing_set.json`. For each,
mark **agree** or **disagree** (and why). The classes and rules are at the end.

**1. `c15` turn 1**: Has Microsoft increased its debt on balance sheet between FY2023 and the FY2022 period?
- earlier turns: (none)
- depends on history: **False**; standalone: Has Microsoft increased its debt on balance sheet between FY2023 and the FY2022 period?
- label: **either** (acceptable routes: sql, docs)
- owner: agree / disagree: **disagree** — The history flag and standalone wording are right, but the duplicate item fb-00552 supports the route using long_term_debt alone. An unqualified debt comparison may also need current debt and current maturities. Clarify that debt means long-term debt, or confirm coverage of all required components before accepting either; otherwise use docs.

**2. `fb-00720`**: What drove gross margin change as of the FY2022 for American Express? If gross margin is not a useful metric for a company like this, then please state that and explain why.
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — The question asks for drivers and for an explanation of whether gross margin is meaningful for this business. Annual metrics alone do not supply that narrative; docs fits the full question.

**3. `fb-00566`**: Has Verizon increased its debt on balance sheet between 2022 and the 2021 fiscal period?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — Provisional coverage exception: docs fits if at least one required debt component for FY2021 or FY2022 is absent from SQL. An annual debt comparison is not inherently narrative, so the missing component should be recorded; with complete SQL coverage and the filing, the route would be either.

**4. `fb-06247`**: What is FY2018 days payable outstanding (DPO) for Walmart? DPO is defined as: 365 * (average accounts payable between FY2017 and FY2018) / (FY2018 COGS + change in inventory between FY2017 and FY2018). Round your answer to two decimal places. Please base your judgments on the information provided primarily in the statement of financial position and the P&L statement.
- label: **either** (acceptable routes: sql, docs)
- why: sec_crosscheck_coverage: all items in database
- owner: agree / disagree: **agree** — The requested DPO is arithmetic on accounts payable, COGS and inventory for the specified years. Either follows from the supplied all-items-in-database claim, provided the corresponding filing is available and the COGS definition is consistent.

**5. `c19` turn 1**: Which companies spent more than 15% of their revenue on research and development in fiscal 2022?
- earlier turns: (none)
- depends on history: **False**; standalone: Which companies spent more than 15% of their revenue on research and development in fiscal 2022?
- label: **sql** (acceptable routes: sql)
- owner: agree / disagree: **agree** — Filtering R&D/revenue above 15% across the company universe is a database comparison. Define the eligible companies and do not treat missing R&D values as zero.

**6. `fb-04412`**: We need to calculate a reasonable approximation (or exact number if possible) of a financial metric. Basing your judgment by information plainly provided in the balance sheet and the P&L statement, what is Lockheed Martin's FY2020 asset turnover ratio? Asset turnover ratio is defined as: FY2020 revenue / (average total assets between FY2019 and FY2020). Round your answer to two decimal places.
- label: **either** (acceptable routes: sql, docs)
- why: sec_crosscheck_coverage: all items in database
- owner: agree / disagree: **agree** — Revenue for FY2020 and total assets for FY2019/FY2020 suffice for the stated asset-turnover formula. Either fits the asserted coverage and filing availability.

**7. `fb-04980`**: What is the FY2021 capital expenditure amount (in USD billions) for PepsiCo? Respond to the question by assuming the perspective of an investment analyst who can only use the details shown within the statement of cash flows.
- label: **docs** (acceptable routes: docs)
- why: coverage: an item is not in the database
- owner: agree / disagree: **agree** — The sheet explicitly states that a required capex item is missing from SQL. The cash-flow statement is therefore required; docs fits. The specific missing company/year/metric should be retained in the coverage evidence.

**8. `fb-00540`**: Roughly how many times has AES Corporation sold its inventory in FY2022? Calculate inventory turnover ratio for the FY2022; if conventional inventory management is not meaningful for the company then state that and explain why.
- label: **either** (acceptable routes: sql, docs)
- why: standard annual figures, all in the database: cost_of_revenue, inventory for [2021, 2022]
- owner: agree / disagree: **disagree** — Recommend docs for the full question: the alternative branch asks whether conventional inventory management is meaningful for AES and why. SQL coverage of cost_of_revenue and inventory supports the calculation, but does not establish coverage of that business explanation. Either would fit a numeric-only version.

**9. `fb-01902`**: Which Best Buy product category performed the best (by top line) in the domestic (USA) Market during Q2 of FY2024?
- label: **docs** (acceptable routes: docs)
- why: novel-generated: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — Domestic product-category revenue for Q2 FY2024 is quarterly segment/category detail, which the stated annual-metrics database does not cover.

**10. `fb-00807`**: Does 3M have a reasonably healthy liquidity profile based on its quick ratio for Q2 of FY2023? If the quick ratio is not relevant to measure liquidity, please state that and explain why.
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — The question needs Q2 FY2023 balance-sheet values and a liquidity interpretation. Quarterly inputs fall outside the stated annual-metrics scope.

**11. `fb-01009`**: What are the geographies that Pepsico primarily operates in as of FY2022?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — Operating geographies are descriptive business/segment information from the filing, rather than standard annual financial metrics.

**12. `fb-10285`**: We need to calculate a financial metric by using information only provided within the balance sheet. Please answer the following question: what is Boeing's year end FY2018 net property, plant, and equipment (in USD millions)?
- label: **either** (acceptable routes: sql, docs)
- why: sec_crosscheck_coverage: all items in database
- owner: agree / disagree: **agree** — Year-end net PP&E is a standard annual balance-sheet figure. Either follows from the supplied coverage claim and availability of the FY2018 filing.

**13. `c24` turn 1**: Does Corning have positive working capital based on FY2022 data?
- earlier turns: (none)
- depends on history: **False**; standalone: Does Corning have positive working capital based on FY2022 data?
- label: **either** (acceptable routes: sql, docs)
- owner: agree / disagree: **agree** — The question is self-contained, so history dependence is correctly False. Positive working capital can be determined from FY2022 current assets minus current liabilities, subject to coverage of both values and the filing.

**14. `fb-00702`**: Were there any potential events that are not in Pfizer's standard business operations that substantially increased net income in 2019?
- label: **docs** (acceptable routes: docs)
- why: novel-generated: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — Identifying unusual events and linking them to increased net income requires narrative explanations or notes, rather than just a net-income series.

**15. `c06` turn 2**: What does its 10-K say about inventory levels?
- earlier turns: T1: What was Nike's revenue in fiscal 2023?
- depends on history: **True**; standalone: What does Nike's fiscal 2023 10-K say about its inventory levels?
- label: **docs** (acceptable routes: docs)
- owner: agree / disagree: **agree** — Its resolves to Nike from the earlier turn. The rewrite carries forward fiscal 2023, and the request explicitly asks what the 10-K says about inventory, so docs and history dependence are appropriate.

**16. `n15`**: What were Nike's sales at its Paris flagship store last week?
- label: **neither** (acceptable routes: neither)
- why: future, live, personal or not data analysis
- owner: agree / disagree: **agree** — Store-level sales for last week are outside historical annual metrics and public filings. Neither fits the stated source boundaries.

**17. `h09`**: Which of Adobe, PayPal and AMD had the highest gross margin in fiscal 2022, and what does its 2022 10-K list as its main competitive risks?
- label: **hybrid** (acceptable routes: hybrid)
- why: the documents half is about the company the database half finds
- owner: agree / disagree: **agree** — The SQL comparison selects the FY2022 gross-margin leader; that result determines which company’s 2022 filing must supply competitive risks. This is a dependent hybrid.

**18. `fb-00552`**: Has Microsoft increased its debt on balance sheet between FY2023 and the FY2022 period?
- label: **either** (acceptable routes: sql, docs)
- why: standard annual figures, all in the database: long_term_debt for [2022, 2023]
- owner: agree / disagree: **disagree** — Same coverage ambiguity as item 1: long_term_debt alone does not establish coverage of unqualified balance-sheet debt. Define the metric as long-term debt, or verify current debt/current maturities as well. Either is acceptable only after that clarification or coverage check.

**19. `fb-00499`**: Is 3M a capital-intensive business based on FY2022 data?
- label: **either** (acceptable routes: sql, docs)
- why: standard annual figures, all in the database: capital_expenditure, revenue, property_plant_equipment_net, total_assets, net_income for [2022]
- owner: agree / disagree: **agree** — Capital intensity can be assessed using the supplied annual capex/revenue and net PP&E/assets inputs, with ROA as supporting context. Either fits if those figures and the filing are covered; the judgment should use relative measures rather than absolute spending alone.

**20. `fb-03882`**: What is Amcor's year end FY2020 net AR (in USD millions)? Address the question by adopting the perspective of a financial analyst who can only use the details shown within the balance sheet.
- label: **either** (acceptable routes: sql, docs)
- why: sec_crosscheck_coverage: all items in database
- owner: agree / disagree: **agree** — Net accounts receivable at FY2020 year end is a standard annual balance-sheet figure. Either fits the supplied coverage claim and availability of the corresponding filing.

**21. `c22` turn 2**: Does Adobe have an improving operating margin profile as of FY2022?
- earlier turns: T1: Which of the companies had the highest revenue in fiscal year 2022?
- depends on history: **False**; standalone: Does Adobe have an improving operating margin profile as of FY2022?
- label: **either** (acceptable routes: sql, docs)
- owner: agree / disagree: **agree** — Adobe and FY2022 are stated explicitly, so history dependence is correctly False despite the earlier turn. Either fits if SQL and the filing cover operating income and revenue for enough preceding years to assess improvement, not just the FY2022 point.

**22. `fb-00882`**: As of May 26, 2023, what is the total amount Pepsico may borrow under its unsecured revolving credit agreements?
- label: **docs** (acceptable routes: docs)
- why: novel-generated: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — Borrowing capacity under dated revolving credit agreements is contractual detail from notes or an event filing, not a standard annual metric.

**23. `fb-00917`**: What drove operating margin change as of the FY22 for AMD? If operating margin is not a useful metric for a company like this, then please state that and explain why.
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — What drove the change asks for causal explanations, including acquisition-related effects, rather than only a margin calculation. Docs fits.

**24. `fb-00460`**: Was there any change in the number of Best Buy stores between Q2 of FY2024 and FY2023?
- label: **docs** (acceptable routes: docs)
- why: novel-generated: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — Store counts at a quarterly reporting date and their comparison with the specified FY2023 period require operating/quarterly disclosures. The intended FY2023 comparison date should be made explicit.

**25. `fb-00995`**: What are the major products and services that AMD sells as of FY22?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — Major products and services are qualitative business descriptions held in the filing.

**26. `fb-01351`**: How much has the effective tax rate of American Express changed between FY2021 and FY2022?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — Provisional: docs fits if SQL lacks the effective tax rate or either tax expense or pretax income for the two years. If all required annual inputs and the filing are covered, the arithmetic comparison would instead be either.

**27. `c09` turn 2**: What was its revenue in each of the five years to then?
- earlier turns: T1: What drove revenue change as of the FY22 for AMD?
- depends on history: **True**; standalone: What was AMD's revenue in each fiscal year from 2018 to 2022?
- label: **sql** (acceptable routes: sql)
- owner: agree / disagree: **agree** — Its and then resolve to AMD and FY2022 from the earlier turn. The five-year rewrite is FY2018–FY2022, and the stated rule sends a series longer than any single available filing’s coverage to SQL.

**28. `h08`**: Among American Water Works, AES and 3M, which spent the most on capital expenditure relative to revenue in fiscal 2022, and how does its 2022 10-K describe its capital investment plans?
- label: **hybrid** (acceptable routes: hybrid)
- why: the documents half is about the company the database half finds
- owner: agree / disagree: **agree** — SQL ranks FY2022 capex/revenue across the named companies; the winner determines whose 2022 10-K must provide capital-investment plans. This is a dependent hybrid.

**29. `s16`**: Which company had the largest percentage drop in revenue in fiscal 2020 compared with fiscal 2019?
- label: **sql** (acceptable routes: sql)
- why: across companies or more years than any library filing
- owner: agree / disagree: **agree** — Finding the largest percentage revenue decline across the company universe is a SQL ranking. Use matched FY2019/FY2020 observations and a valid, nonzero FY2019 denominator.

**30. `fb-01163`**: Among operations, investing, and financing activities, which brought in the most (or lost the least) cash flow for Nike in FY2023?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree: **agree** — Provisional: docs fits the asserted coverage exception if SQL lacks any of the operating, investing or financing cash-flow totals for Nike FY2023. This is an annual numeric comparison, so complete coverage of all three values plus the filing would make it either.

## Classes

- **docs**: Only the filings can answer: narrative, segments, quarters, 8-Ks, earnings releases, or a figure that sec.annual_metrics does not hold for that company and year.
- **either**: Both can answer: every figure the answer needs is a standard annual metric that sec.annual_metrics holds for that company and fiscal year (checked by query), and the question's filing is in the library. Arithmetic on those figures is allowed.
- **sql**: Only the database can answer in practice: across companies, or over more years than any filing in the library covers.
- **hybrid**: Needs both: a database fact (across companies or years) plus what a filing in the library says. 'dependent' when the documents half needs the database half's answer.
- **neither**: Neither source holds it: companies outside the 32, the future, live market data, or requests outside data analysis.

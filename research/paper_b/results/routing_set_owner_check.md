# Routing set: owner's label check

30 labels drawn at random (seed 20261006) from `docs/eval/routing_set.json`. For each,
mark **agree** or **disagree** (and why). The classes and rules are at the end.

**1. `c15` turn 1**: Has Microsoft increased its debt on balance sheet between FY2023 and the FY2022 period?
- earlier turns: (none)
- depends on history: **False**; standalone: Has Microsoft increased its debt on balance sheet between FY2023 and the FY2022 period?
- label: **either** (acceptable routes: sql, docs)
- owner: agree / disagree:

**2. `fb-00720`**: What drove gross margin change as of the FY2022 for American Express? If gross margin is not a useful metric for a company like this, then please state that and explain why.
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**3. `fb-00566`**: Has Verizon increased its debt on balance sheet between 2022 and the 2021 fiscal period?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**4. `fb-06247`**: What is FY2018 days payable outstanding (DPO) for Walmart? DPO is defined as: 365 * (average accounts payable between FY2017 and FY2018) / (FY2018 COGS + change in inventory between FY2017 and FY2018). Round your answer to two decimal places. Please base your judgments on the information provided primarily in the statement of financial position and the P&L statement.
- label: **either** (acceptable routes: sql, docs)
- why: sec_crosscheck_coverage: all items in database
- owner: agree / disagree:

**5. `c19` turn 1**: Which companies spent more than 15% of their revenue on research and development in fiscal 2022?
- earlier turns: (none)
- depends on history: **False**; standalone: Which companies spent more than 15% of their revenue on research and development in fiscal 2022?
- label: **sql** (acceptable routes: sql)
- owner: agree / disagree:

**6. `fb-04412`**: We need to calculate a reasonable approximation (or exact number if possible) of a financial metric. Basing your judgment by information plainly provided in the balance sheet and the P&L statement, what is Lockheed Martin's FY2020 asset turnover ratio? Asset turnover ratio is defined as: FY2020 revenue / (average total assets between FY2019 and FY2020). Round your answer to two decimal places.
- label: **either** (acceptable routes: sql, docs)
- why: sec_crosscheck_coverage: all items in database
- owner: agree / disagree:

**7. `fb-04980`**: What is the FY2021 capital expenditure amount (in USD billions) for PepsiCo? Respond to the question by assuming the perspective of an investment analyst who can only use the details shown within the statement of cash flows.
- label: **docs** (acceptable routes: docs)
- why: coverage: an item is not in the database
- owner: agree / disagree:

**8. `fb-00540`**: Roughly how many times has AES Corporation sold its inventory in FY2022? Calculate inventory turnover ratio for the FY2022; if conventional inventory management is not meaningful for the company then state that and explain why.
- label: **either** (acceptable routes: sql, docs)
- why: standard annual figures, all in the database: cost_of_revenue, inventory for [2021, 2022]
- owner: agree / disagree:

**9. `fb-01902`**: Which Best Buy product category performed the best (by top line) in the domestic (USA) Market during Q2 of FY2024?
- label: **docs** (acceptable routes: docs)
- why: novel-generated: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**10. `fb-00807`**: Does 3M have a reasonably healthy liquidity profile based on its quick ratio for Q2 of FY2023? If the quick ratio is not relevant to measure liquidity, please state that and explain why.
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**11. `fb-01009`**: What are the geographies that Pepsico primarily operates in as of FY2022?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**12. `fb-10285`**: We need to calculate a financial metric by using information only provided within the balance sheet. Please answer the following question: what is Boeing's year end FY2018 net property, plant, and equipment (in USD millions)?
- label: **either** (acceptable routes: sql, docs)
- why: sec_crosscheck_coverage: all items in database
- owner: agree / disagree:

**13. `c24` turn 1**: Does Corning have positive working capital based on FY2022 data?
- earlier turns: (none)
- depends on history: **False**; standalone: Does Corning have positive working capital based on FY2022 data?
- label: **either** (acceptable routes: sql, docs)
- owner: agree / disagree:

**14. `fb-00702`**: Were there any potential events that are not in Pfizer's standard business operations that substantially increased net income in 2019?
- label: **docs** (acceptable routes: docs)
- why: novel-generated: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**15. `c06` turn 2**: What does its 10-K say about inventory levels?
- earlier turns: T1: What was Nike's revenue in fiscal 2023?
- depends on history: **True**; standalone: What does Nike's fiscal 2023 10-K say about its inventory levels?
- label: **docs** (acceptable routes: docs)
- owner: agree / disagree:

**16. `n15`**: What were Nike's sales at its Paris flagship store last week?
- label: **neither** (acceptable routes: neither)
- why: future, live, personal or not data analysis
- owner: agree / disagree:

**17. `h09`**: Which of Adobe, PayPal and AMD had the highest gross margin in fiscal 2022, and what does its 2022 10-K list as its main competitive risks?
- label: **hybrid** (acceptable routes: hybrid)
- why: the documents half is about the company the database half finds
- owner: agree / disagree:

**18. `fb-00552`**: Has Microsoft increased its debt on balance sheet between FY2023 and the FY2022 period?
- label: **either** (acceptable routes: sql, docs)
- why: standard annual figures, all in the database: long_term_debt for [2022, 2023]
- owner: agree / disagree:

**19. `fb-00499`**: Is 3M a capital-intensive business based on FY2022 data?
- label: **either** (acceptable routes: sql, docs)
- why: standard annual figures, all in the database: capital_expenditure, revenue, property_plant_equipment_net, total_assets, net_income for [2022]
- owner: agree / disagree:

**20. `fb-03882`**: What is Amcor's year end FY2020 net AR (in USD millions)? Address the question by adopting the perspective of a financial analyst who can only use the details shown within the balance sheet.
- label: **either** (acceptable routes: sql, docs)
- why: sec_crosscheck_coverage: all items in database
- owner: agree / disagree:

**21. `c22` turn 2**: Does Adobe have an improving operating margin profile as of FY2022?
- earlier turns: T1: Which of the companies had the highest revenue in fiscal year 2022?
- depends on history: **False**; standalone: Does Adobe have an improving operating margin profile as of FY2022?
- label: **either** (acceptable routes: sql, docs)
- owner: agree / disagree:

**22. `fb-00882`**: As of May 26, 2023, what is the total amount Pepsico may borrow under its unsecured revolving credit agreements?
- label: **docs** (acceptable routes: docs)
- why: novel-generated: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**23. `fb-00917`**: What drove operating margin change as of the FY22 for AMD? If operating margin is not a useful metric for a company like this, then please state that and explain why.
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**24. `fb-00460`**: Was there any change in the number of Best Buy stores between Q2 of FY2024 and FY2023?
- label: **docs** (acceptable routes: docs)
- why: novel-generated: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**25. `fb-00995`**: What are the major products and services that AMD sells as of FY22?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**26. `fb-01351`**: How much has the effective tax rate of American Express changed between FY2021 and FY2022?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

**27. `c09` turn 2**: What was its revenue in each of the five years to then?
- earlier turns: T1: What drove revenue change as of the FY22 for AMD?
- depends on history: **True**; standalone: What was AMD's revenue in each fiscal year from 2018 to 2022?
- label: **sql** (acceptable routes: sql)
- owner: agree / disagree:

**28. `h08`**: Among American Water Works, AES and 3M, which spent the most on capital expenditure relative to revenue in fiscal 2022, and how does its 2022 10-K describe its capital investment plans?
- label: **hybrid** (acceptable routes: hybrid)
- why: the documents half is about the company the database half finds
- owner: agree / disagree:

**29. `s16`**: Which company had the largest percentage drop in revenue in fiscal 2020 compared with fiscal 2019?
- label: **sql** (acceptable routes: sql)
- why: across companies or more years than any library filing
- owner: agree / disagree:

**30. `fb-01163`**: Among operations, investing, and financing activities, which brought in the most (or lost the least) cash flow for Nike in FY2023?
- label: **docs** (acceptable routes: docs)
- why: domain-relevant: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)
- owner: agree / disagree:

## Classes

- **docs**: Only the filings can answer: narrative, segments, quarters, 8-Ks, earnings releases, or a figure that sec.annual_metrics does not hold for that company and year.
- **either**: Both can answer: every figure the answer needs is a standard annual metric that sec.annual_metrics holds for that company and fiscal year (checked by query), and the question's filing is in the library. Arithmetic on those figures is allowed.
- **sql**: Only the database can answer in practice: across companies, or over more years than any filing in the library covers.
- **hybrid**: Needs both: a database fact (across companies or years) plus what a filing in the library says. 'dependent' when the documents half needs the database half's answer.
- **neither**: Neither source holds it: companies outside the 32, the future, live market data, or requests outside data analysis.

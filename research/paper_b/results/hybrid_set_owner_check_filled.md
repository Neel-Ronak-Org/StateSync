# Held-out hybrid set: spot check (10 of 100)

> Review scope: question wording, rankings among the displayed values, and filing identifier/company/year alignment. Raw database rows, aggregation inputs and actual filing passages were not supplied or independently inspected. “ok” below means internal consistency only, not source verification.
>
> Summary: five displayed comparisons are internally consistent; five universe-wide aggregates/rankings need database verification. All ten filing identifiers match their intended company/year, with document content still unverified.

For each item: does the question read naturally, is the reference database result the right answer to its numeric part, and is the filing the one its documents part needs? Write ok, or what is wrong.

## hd30

Among AMD, PepsiCo, MGM Resorts and Boeing, which had the lowest net income in fiscal 2022, and what does that company's 2022 10-K say about its properties and facilities?

- Reference database result: `BA, -4935000000` (all: `PEP, 8910000000; MGM, 1473093000; AMD, 1320000000; BA, -4935000000`)
- Filing: `BOEING_2022_10K`

**Check:** ok (internal consistency only). Boeing has the lowest listed net income: -$4.935bn, below the three positive values. The wording is natural and BOEING_2022_10K matches the selected company/year. The raw database values and actual properties/facilities passage were not supplied for verification.

## hd46

Among Pfizer, Corning and Costco, which had the highest net profit margin in fiscal 2021, and what does that company's 2021 10-K say about any material legal proceedings?

- Reference database result: `PFE, 27.04` (all: `PFE, 27.04; GLW, 13.54; COST, 2.56`)
- Filing: `PFIZER_2021_10K`

**Check:** ok (internal consistency only). Pfizer’s listed 27.04% exceeds Corning’s 13.54% and Costco’s 2.56%; PFIZER_2021_10K matches the winner/year. The wording is natural. The ratio inputs and the filing’s legal-proceedings content remain unverified.

## hd10

Among Boeing, AES and JPMorgan, which had the lowest operating cash flow in fiscal 2022, and what does that company's 2022 10-K say about its liquidity and capital resources?

- Reference database result: `AES, 2715000000` (all: `JPM, 107119000000; BA, 3512000000; AES, 2715000000`)
- Filing: `AES_2022_10K`

**Check:** ok (internal consistency only). AES’s listed operating cash flow of $2.715bn is below Boeing’s $3.512bn and JPMorgan’s $107.119bn. AES_2022_10K matches the winner/year and the liquidity question reads naturally. Raw cash-flow definitions/values and the filing passage remain unverified.

## hd38

Among PepsiCo, Boeing and MGM Resorts, which had the lowest net profit margin in fiscal 2022, and what does that company's 2022 10-K say about what drove its results that year?

- Reference database result: `BA, -7.41` (all: `MGM, 11.22; PEP, 10.31; BA, -7.41`)
- Filing: `BOEING_2022_10K`

**Check:** ok (internal consistency only). Boeing’s listed -7.41% is below PepsiCo’s 10.31% and MGM’s 11.22%; BOEING_2022_10K matches the winner/year. The wording is natural, though “what drove its results” is broad. The margin inputs and filing discussion remain unverified.

## hi18

In fiscal 2019, what was the average current ratio across the companies, and what does Kraft Heinz's 2019 10-K say about its liquidity and capital resources?

- Reference database result: `1.36`
- Filing: `KRAFTHEINZ_2019_10K`

**Check:** Needs database verification. No constituent ratios or eligible-company list are provided, so the mean of 1.36 cannot be recomputed. Clarify “the companies” as the configured universe and specify the arithmetic mean of per-company current ratios and missing-data handling. The two clauses form an independent hybrid; KRAFTHEINZ_2019_10K matches the fixed company/year, but its liquidity passage was not supplied.

## hi20

In fiscal 2017, what was the average net profit margin across the companies, and what does Best Buy's 2017 10-K say about how its business segments are organised?

- Reference database result: `9.1`
- Filing: `BESTBUY_2017_10K`

**Check:** Needs database verification. The 9.1% mean cannot be recomputed without constituent margins and the eligible-company list. Specify the company universe, arithmetic mean of individual margins, and missing-data handling. The two clauses form an independent hybrid and read naturally once the universe is defined; BESTBUY_2017_10K matches the fixed company/year, but segment content was not supplied.

## hi31

In fiscal 2019, how many of the companies reported a net loss, and what does Walmart's 2019 10-K say about its employees and workforce?

- Reference database result: `1`
- Filing: `WALMART_2019_10K`

**Check:** Needs database verification. A count of one net-loss company cannot be checked without all eligible FY2019 net-income rows. Define the company universe and count negative values without treating missing observations as zero. This is an independent hybrid; WALMART_2019_10K matches the fixed company/year, but the workforce passage was not supplied.

## hi13

In fiscal 2022, which three companies had the largest total assets, and what does Verizon's 2022 10-K say about how its business segments are organised?

- Reference database result: `JPMorgan, 3665743000000; Amazon, 462675000000; Verizon, 379680000000`
- Filing: `VERIZON_2022_10K`

**Check:** Needs full-universe database verification. The three supplied asset values are in descending order, and Verizon is the third listed company, but omitted rows are needed to establish that these are the true top three. Define the company universe and treatment of missing values/ties. The independent filing clause is natural and VERIZON_2022_10K matches the fixed company/year; its segment content was not supplied.

## hi36

In fiscal 2022, what was the median operating margin across the companies, and what does AES's 2022 10-K say about the main risks it faces?

- Reference database result: `11.0`
- Filing: `AES_2022_10K`

**Check:** Needs database verification. The median of 11.0% cannot be recomputed without the eligible operating-margin observations. Define the company universe, missing-data handling and the median convention for an even number of observations. This is an independent hybrid; AES_2022_10K matches the fixed company/year, but its risks passage was not supplied.

## hd13

Among General Mills, AMD and 3M, which had the highest total assets at year end in fiscal 2022, and what does that company's 2022 10-K say about its properties and facilities?

- Reference database result: `AMD, 67580000000` (all: `AMD, 67580000000; MMM, 46455000000; GIS, 31090100000`)
- Filing: `AMD_2022_10K`

**Check:** ok (internal consistency only). AMD’s listed $67.580bn exceeds 3M’s $46.455bn and General Mills’ $31.0901bn; AMD_2022_10K matches the winner/year. The wording is natural. The raw asset values and actual properties/facilities passage remain unverified.

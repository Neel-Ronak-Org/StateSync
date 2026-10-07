# Your blind annotation: 18 wrong answers (about 20 minutes)

These are 18 answers that the scorer marked wrong, chosen at random. For each, pick the **one**
category that best describes the main fault. You won't see my labels or what any check said; that's
on purpose, so we can measure how often we agree (Cohen's kappa).

**Categories:**

- **filter value**: wrong literal, a missing filter, or an invented filter ('North Bohemia' vs 'north Bohemia')
- **column meaning**: a plausible but wrong column or table
- **aggregation**: wrong grouping or averaging logic (per district instead of per school)
- **join/fan-out**: something counted more than once
- **ordering/top-k**: LIMIT 1 dropped ties, or NULLs sorted first
- **time window**: wrong date range or date column
- **output format only**: the answer is right but in a different shape (fraction vs %, one name column vs two)
- **hint contradicts data**: BIRD's hint is wrong or misleading, and the model followed it
- **residual gold error**: BIRD's own answer looks wrong
- **other**: anything else (for example the SQL failed)

Reply in chat with lines like `E1: filter value`. You don't need to read every SQL line; the
question, the two results and the answer text are usually enough.

## E1  (q1387, student_club)

**Question:** Which student has been entrusted to manage the budget for the Yearly Kickoff? Hint: name of students means the full name; full name refers to first_name, last_name;'Yearly Kickoff' is an event name;

**Correct (gold) result:** `[['Elijah', 'Allen'], ['Elijah', 'Allen']]` (2 rows)

**System's result:** `[['Elijah Allen'], ['Sacha Harrison']]` (2 rows)

**System's answer text:** The students entrusted to manage the budget for the Yearly Kickoff are Elijah Allen and Sacha Harrison.

<details><summary>SQL (optional)</summary>

Gold: `SELECT T4.first_name, T4.last_name FROM event AS T1 INNER JOIN budget AS T2 ON T1.event_id = T2.link_to_event INNER JOIN expense AS T3 ON T2.budget_id = T3.link_to_budget INNER JOIN member AS T4 ON T3.link_to_member = T4.member_id WHERE T1.event_name = 'Yearly Kickoff' AND T4.position = 'Treasurer';`

System: `SELECT DISTINCT m.first_name || ' ' || m.last_name AS full_name FROM 97b3964026cb47ff.event e JOIN 97b3964026cb47ff.budget b ON b.link_to_event = e.event_id JOIN 97b3964026cb47ff.expense ex ON ex.link_to_budget = b.budget_id JOIN 97b3964026cb47ff.member m ON m.member_id = ex.link_to_member WHERE e.event_name = 'Yearly Kickoff'`

</details>

**Your category:** 

## E2  (q930, formula_1)

**Question:** In which Formula_1 races did Lewis Hamilton rank the highest? Hint: rank the highest refers to min(rank); Lewis Hamilton refers to the full name of the driver; Full name of the driver refers to drivers.forename and drivers.surname;

**Correct (gold) result:** `[['Chinese Grand Prix'], ['Malaysian Grand Prix'], ['Japanese Grand Prix'], ['Chinese Grand Prix'], ['Spanish Grand Prix']]` (37 rows)

**System's result:** `[['Chinese Grand Prix', '2011', '3'], ['Belgian Grand Prix', '2012', '12'], ['Spanish Grand Prix', '2016', '5']]` (3 rows)

**System's answer text:** Lewis Hamilton ranked highest (minimum rank) in these races: - Chinese Grand Prix (2011), round 3 - Belgian Grand Prix (2012), round 12 - Spanish Grand Prix (2016), round 5

<details><summary>SQL (optional)</summary>

Gold: `SELECT name FROM races WHERE raceId IN ( SELECT raceId FROM results WHERE rank = 1 AND driverId = ( SELECT driverId FROM drivers WHERE forename = 'Lewis' AND surname = 'Hamilton' ) )`

System: `SELECT ra.name, ra.year, ra.round FROM dbd54b57e3f84128.drivers dr JOIN dbd54b57e3f84128.results re ON dr.driverid = re.driverid JOIN dbd54b57e3f84128.races ra ON re.raceid = ra.raceid WHERE (dr.forename || ' ' || dr.surname) = 'Lewis Hamilton' AND re.rank = ( SELECT MIN(re2.rank) FROM dbd54b57e3f84128.results re2 JOIN dbd54b57e3f84128.drivers dr2 ON re2.driverid = dr2.driverid WHERE (dr2.forename || ' ' || dr2.surname) = 'Lewis Hamilton' AND re2.rank IS NOT NULL )`

</details>

**Your category:** 

## E3  (q637, codebase_community)

**Question:** State all the tags used by Mark Meckes in his posts that doesn't have comments. Hint: used by Mark Meckes refers to DisplayName = 'Mark Meckes'; Doen't have comments refers to CommentCount = 0; exclude null tags

**Correct (gold) result:** `[['<books>']]` (1 rows)

**System's result:** `[["['books']"]]` (1 rows)

**System's answer text:** Tags used by Mark Meckes in posts with zero comments (excluding null tags): books

<details><summary>SQL (optional)</summary>

Gold: `SELECT DISTINCT p.`Tags` FROM `posts` p JOIN `users` u ON u.`Id` = p.`OwnerUserId` WHERE u.`DisplayName` = 'Mark Meckes' AND p.`CommentCount` = 0 AND p.`Tags` IS NOT NULL;`

System: `SELECT DISTINCT tm.tag FROM f084a0aafeb24880.posts p JOIN f084a0aafeb24880.users u ON p.owneruserid = u.id CROSS JOIN LATERAL regexp_matches(p.tags, '<([^>]+)>', 'g') AS tm(tag) WHERE u.displayname = 'Mark Meckes' AND p.commentcount = 0 AND p.tags IS NOT NULL`

</details>

**Your category:** 

## E4  (q273, toxicology)

**Question:** What is the percentage of element chlorine in carcinogenic molecules? Hint: chlorine refers to element = 'cl'; label = '+' mean molecules are carcinogenic; percentage = DIVIDE(SUM(element = 'cl'); COUNT(molecule_id)) as percentage where label = '+'

**Correct (gold) result:** `[['3.1419284940411703']]` (1 rows)

**System's result:** `[['0.03141928494041170098']]` (1 rows)

**System's answer text:** The percentage of element chlorine (Cl) in carcinogenic molecules (label = '+') is 0.0314192849404117, i.e., approximately 3.1419%.

<details><summary>SQL (optional)</summary>

Gold: `SELECT CAST(COUNT( CASE WHEN T1.element = 'cl' THEN T1.element ELSE NULL END) AS REAL) * 100 / COUNT(T1.element) FROM atom AS T1 INNER JOIN molecule AS T2 ON T1.molecule_id = T2.molecule_id WHERE T2.label = '+'`

System: `SELECT (SUM((a.element = 'cl')::int)::numeric / NULLIF(COUNT(a.molecule_id),0)) AS percentage FROM 426229f583924ad0.molecule m JOIN 426229f583924ad0.atom a ON m.molecule_id = a.molecule_id WHERE m.label = '+'`

</details>

**Your category:** 

## E5  (q89, financial)

**Question:** How many accounts who choose issuance after transaction are staying in East Bohemia region? Hint: A3 contains the data of region; 'POPLATEK PO OBRATU' represents for 'issuance after transaction'.

**Correct (gold) result:** `[['13']]` (1 rows)

**System's result:** `[['0']]` (1 rows)

**System's answer text:** There are 0 accounts in the East Bohemia region that choose issuance after transaction (POPLATEK PO OBRATU).

<details><summary>SQL (optional)</summary>

Gold: `SELECT COUNT(T2.account_id) FROM district AS T1 INNER JOIN account AS T2 ON T1.district_id = T2.district_id WHERE T1.A3 = 'east Bohemia' AND T2.frequency = 'POPLATEK PO OBRATU'`

System: `SELECT COUNT(a.account_id) AS count_accounts FROM b7185de16da64408.account AS a JOIN b7185de16da64408.district AS d ON a.district_id = d.district_id WHERE a.frequency = 'POPLATEK PO OBRATU' AND d.a3 = 'East Bohemia'`

</details>

**Your category:** 

## E6  (q1476, debit_card_specializing)

**Question:** What was the difference in gas consumption between CZK-paying customers and EUR-paying customers in 2012? Hint: Year 2012 can be presented as Between 201201 And 201212; The first 4 strings of the Date values in the yearmonth table can represent year; Difference in Consumption = CZK customers consumption in 2012 - EUR customers consumption in 2012

**Correct (gold) result:** `[['402524570.17']]` (1 rows)

**System's result:** `[['402530620.0']]` (1 rows)

**System's answer text:** Difference in gas consumption (CZK-paying customers minus EUR-paying customers) for 2012 = 402,531,300.0 (as returned by the query).

<details><summary>SQL (optional)</summary>

Gold: `SELECT SUM(IIF(T1.Currency = 'CZK', T2.Consumption, 0)) - SUM(IIF(T1.Currency = 'EUR', T2.Consumption, 0)) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE SUBSTR(T2.Date, 1, 4) = '2012'`

System: `SELECT COALESCE(SUM(CASE WHEN c.currency = 'CZK' THEN y.consumption END),0) - COALESCE(SUM(CASE WHEN c.currency = 'EUR' THEN y.consumption END),0) AS difference FROM dfa531d796da43e7.yearmonth y JOIN dfa531d796da43e7.customers c ON c.customerid = y.customerid WHERE (to_timestamp(y.date || '01', 'YYYYMMDD')::timestamptz BETWEEN '2012-01-01'::timestamptz AND '2012-12-31'::timestamptz)`

</details>

**Your category:** 

## E7  (q24, california_schools)

**Question:** Give the names of the schools with the percent eligible for free meals in K-12 is more than 0.1 and test takers whose test score is greater than or equal to 1500? Hint: Percent eligible for free meals = Free Meal Count (K-12) / Total (Enrollment (K-12)

**Correct (gold) result:** `[['FAME Public Charter'], ['Envision Academy for Arts & Technology'], ['Alameda Science and Technology Institute'], ['Alameda High'], ['Alternatives in Action']]` (1142 rows)

**System's result:** `[['FAME Public Charter'], ['Envision Academy for Arts & Technology'], ['Alameda Science and Technology Institute'], ['Alameda High'], ['Alternatives in Action']]` (1165 rows)

**System's answer text:** I cannot determine which schools meet those criteria from the provided data. The query results include only a single column (School Name) for the first 1,000 rows and do not include Free Meal Count, Enrollment (K-12), test taker counts, or test scores. Without those fields I cannot compute percent eligible for free meals nor filter by test scores ≥1500.

<details><summary>SQL (optional)</summary>

Gold: `SELECT T2."School Name" FROM satscores AS T1 INNER JOIN frpm AS T2 ON T1.cds = T2.CDSCode WHERE CAST(T2."Free Meal Count (K-12)" AS REAL) / T2."Enrollment (K-12)" > 0.1 AND T1.NumGE1500 > 0 AND rtype = 'S'`

System: `SELECT f."School Name" FROM 9240ceb08edb4938.frpm f JOIN 9240ceb08edb4938.satscores s ON f.cdscode = s.cds WHERE (f."Free Meal Count (K-12)"::double precision / NULLIF(f."Enrollment (K-12)"::double precision, 0)) > 0.1 AND s.numge1500 > 0`

</details>

**Your category:** 

## E8  (q173, financial)

**Question:** How often does account number 3 request an account statement to be released? What was the aim of debiting 3539 in total? Hint: k_symbol refers to the purpose of payments

**Correct (gold) result:** `[['POPLATEK MESICNE', 'POJISTNE']]` (1 rows)

**System's result:** `[]` (0 rows)

**System's answer text:** The query returned zero rows, so there are no records in the provided data of account number 3 requesting an account statement. Likewise, there is no data available here about any debit totaling 3539 or about the k_symbol (payment purpose); therefore the aim of debiting 3539 cannot be determined from the returned results.

<details><summary>SQL (optional)</summary>

Gold: `SELECT T1.frequency, T2.k_symbol FROM account AS T1 INNER JOIN (SELECT account_id, k_symbol, SUM(amount) AS total_amount FROM "order" GROUP BY account_id, k_symbol) AS T2 ON T1.account_id = T2.account_id WHERE T1.account_id = 3 AND T2.total_amount = 3539`

System: `SELECT a.frequency, t.k_symbol FROM b7185de16da64408.account a JOIN b7185de16da64408.trans t ON t.account_id = a.account_id WHERE a.account_id = 3 GROUP BY a.frequency, t.k_symbol HAVING SUM(t.amount) = 3539`

</details>

**Your category:** 

## E9  (q892, formula_1)

**Question:** State the driver with the most points scored. Find his full name and the number of points. Hint: the most points scored refers to max(points); Full name of the driver refers to drivers.forename and drivers.surname;

**Correct (gold) result:** `[['Lewis', 'Hamilton', '2382.0']]` (1 rows)

**System's result:** `[['Sebastian', 'Vettel', '397.0']]` (1 rows)

**System's answer text:** The driver with the most points is Sebastian Vettel with 397.0 points.

<details><summary>SQL (optional)</summary>

Gold: `SELECT d.`forename`, d.`surname`, SUM(r.`points`) AS total_points FROM `results` r JOIN `drivers` d ON d.`driverId` = r.`driverId` GROUP BY r.`driverId` ORDER BY total_points DESC LIMIT 1;`

System: `SELECT d.forename AS forename, d.surname AS surname, ds.points AS points FROM dbd54b57e3f84128.driverstandings ds JOIN dbd54b57e3f84128.drivers d ON d.driverid = ds.driverid WHERE ds.points = (SELECT MAX(ds2.points) FROM dbd54b57e3f84128.driverstandings ds2)`

</details>

**Your category:** 

## E10  (q1270, thrombosis_prediction)

**Question:** Among the patients who has a normal anti-scl70, how many of them are female and does not have any symptom? Hint: normal anti-scl70 refers to SC170 IN('negative', '0'); female refers to Sex = 'F'; does not have any symptom refers to symptoms IS NULL; Should consider DISTINCT in the final result;

**Correct (gold) result:** `[['2']]` (1 rows)

**System's result:** `[['19']]` (1 rows)

**System's answer text:** There are 19 distinct female patients with normal anti-scl70 (SC170 IN ('negative','0')) and no recorded symptoms (symptoms IS NULL).

<details><summary>SQL (optional)</summary>

Gold: `SELECT COUNT(DISTINCT T1.ID) FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID INNER JOIN Examination AS T3 ON T3.ID = T2.ID WHERE (T2.SC170 = 'negative' OR T2.SC170 = '0') AND T1.SEX = 'F' AND T3.Symptoms IS NULL`

System: `SELECT COUNT(DISTINCT p.id) FROM be782f3725e34f54.patient p WHERE p.sex = 'F' AND EXISTS (SELECT 1 FROM be782f3725e34f54.laboratory l WHERE l.id = p.id AND lower(l.sc170) IN ('negative','0')) AND NOT EXISTS (SELECT 1 FROM be782f3725e34f54.examination e WHERE e.id = p.id AND e.symptoms IS NOT NULL)`

</details>

**Your category:** 

## E11  (q1481, debit_card_specializing)

**Question:** What is the difference in the annual average consumption of the customers with the least amount of consumption paid in CZK for 2013 between SME and LAM, LAM and KAM, and KAM and SME? Hint: annual average consumption of customer with the lowest consumption in each segment = total consumption per year / the number of customer with lowest consumption in each segment; Difference in annual average = SME's annual average - LAM's annual average; Difference in annual average = LAM's annual average - KAM's annual average; Year 2013 can be presented as Between 201301 And 201312; The first 4 strings of the Date values in the yearmonth table can represent year.

**Correct (gold) result:** `[['-14009.339999999998', '6046.619999999999', '7962.719999999999']]` (1 rows)

**System's result:** `[['-13820.919616699219', '5858.199890136719', '7962.7197265625']]` (1 rows)

**System's answer text:** For 2013 (Between 201301 and 201312) the differences in annual average consumption (CZK) for the customers with the least consumption are: - SME minus LAM: -13,820.919616699219 CZK (SME is lower than LAM by this amount) - LAM minus KAM: 5,858.199890136719 CZK (LAM is higher than KAM by this amount) - KAM minus SME: 7,962.7197265625 CZK (KAM is higher than SME by this amount)

<details><summary>SQL (optional)</summary>

Gold: `WITH customer_consumption AS (SELECT c.Segment, c.CustomerID, SUM(ym.Consumption) AS TotalConsumption, COUNT(DISTINCT ym.CustomerID) AS CustomerCount FROM customers AS c JOIN yearmonth AS ym ON c.CustomerID = ym.CustomerID WHERE ym.Date BETWEEN '201301' AND '201312' AND c.Currency = 'CZK' GROUP BY c.Segment, c.CustomerID), customer_average_consumption AS (SELECT Segment, TotalConsumption, CAST(TotalConsumption AS REAL) / CustomerCount AS AnnualAverageConsumption FROM customer_consumption), segment_min_consumption AS (SELECT Segment, MIN(TotalConsumption) LeastConsumptionCustomer FROM customer_average_consumption GROUP BY Segment), segment_min_consumption_avg AS (SELECT T1.Segment, T2.AnnualAverageConsumption FROM segment_min_consumption T1 JOIN customer_average_consumption T2 ON T1.Segment = T2.Segment AND T1.LeastConsumptionCustomer = T2.TotalConsumption) SELECT SUM(IIF(Segment = 'SME', AnnualAverageConsumption, 0)) - SUM(IIF(Segment = 'LAM', AnnualAverageConsumption, 0)), SUM(IIF(Segment = 'LAM', AnnualAverageConsumption, 0)) - SUM(IIF(Segment = 'KAM', AnnualAverageConsumption, 0)), SUM(IIF(Segment = 'KAM', AnnualAverageConsumption, 0)) - SUM(IIF(Segment = 'SME', AnnualAverageConsumption, 0)) FROM segment_min_consumption_avg`

System: `WITH totals AS ( SELECT c.customerid AS c_customerid, c.segment AS c_segment, SUM(ym.consumption) AS total FROM dfa531d796da43e7.yearmonth ym JOIN dfa531d796da43e7.customers c ON c.customerid = ym.customerid WHERE to_date(ym.date || '01','YYYYMMDD')::timestamptz BETWEEN '2013-01-01'::timestamptz AND '2013-12-31'::timestamptz AND c.segment IN ('SME','LAM','KAM') GROUP BY c.customerid, c.segment ), seg_min AS ( SELECT t.c_segment AS segment, MIN(t.total) AS min_total FROM totals t GROUP BY t.c_segment ), seg_counts AS ( SELECT s.segment, s.min_total, COUNT(*) FILTER (WHERE t.total = s.min_total) AS cnt_min FROM seg_min s JOIN totals t ON t.c_segment = s.segment GROUP BY s.segment, s.min_total ), seg_avg AS ( SELECT sc.segment, (sc.min_total::double precision / NULLIF(sc.cnt_min,0)) AS avg_low FROM seg_counts sc ) SELECT (COALESCE((SELECT sa.avg_low FROM seg_avg sa WHERE sa.segment = 'SME'),0) - COALESCE((SELECT sa.avg_low FROM seg_avg sa WHERE sa.segment = 'LAM'),0)) AS sme_minus_lam, (COALESCE((SELECT sa.avg_low FROM seg_avg sa WHERE sa.segment = 'LAM'),0) - COALESCE((SELECT sa.avg_low FROM seg_avg sa WHERE sa.segment = 'KAM'),0)) AS lam_minus_kam, (COALESCE((SELECT sa.avg_low FROM seg_avg sa WHERE sa.segment = 'KAM'),0) - COALESCE((SELECT sa.avg_low FROM seg_avg sa WHERE sa.segment = 'SME'),0)) AS kam_minus_sme`

</details>

**Your category:** 

## E12  (q47, california_schools)

**Question:** What is the monthly average number of schools that opened in Alameda County under the jurisdiction of the Elementary School District in 1980? Hint: Elementary School District refers to DOC = 52; Monthly average number of schools that opened in 1980 = count(schools that opened in 1980) / 12

**Correct (gold) result:** `[['1.4166666666666667']]` (1 rows)

**System's result:** `[['0E-20']]` (1 rows)

**System's answer text:** Monthly average = 0 schools per month. The query result returned monthly_average = 0, which is the count of schools that opened in 1980 (under Alameda County, DOC = 52) divided by 12.

<details><summary>SQL (optional)</summary>

Gold: `SELECT CAST(COUNT(School) AS REAL) / 12 FROM schools WHERE DOC = 52 AND County = 'Alameda' AND strftime('%Y', OpenDate) = '1980'`

System: `SELECT COUNT(s.cdscode)::numeric / 12 AS monthly_average FROM 9240ceb08edb4938.schools s WHERE s.county = 'Alameda County' AND s.doc = '52' AND s.opendate IS NOT NULL AND s.opendate::timestamptz >= '1980-01-01'::timestamptz AND s.opendate::timestamptz <= '1980-12-31'::timestamptz`

</details>

**Your category:** 

## E13  (q1460, student_club)

**Question:** Write the full name of the member who spent money for water, veggie tray and supplies and include the cost of it. Hint: full name refers to first_name, last name; spent money for refers expense description; expense_description = 'Water, Veggie tray, supplies'

**Correct (gold) result:** `[['Elijah', 'Allen', '28.15']]` (1 rows)

**System's result:** `[['Elijah Allen', '28.15']]` (1 rows)

**System's answer text:** Elijah Allen — cost: 28.15

<details><summary>SQL (optional)</summary>

Gold: `SELECT T2.first_name, T2.last_name, T1.cost FROM expense AS T1 INNER JOIN member AS T2 ON T1.link_to_member = T2.member_id WHERE T1.expense_description = 'Water, Veggie tray, supplies'`

System: `SELECT (m.first_name || ' ' || m.last_name) AS full_name, e.cost FROM 97b3964026cb47ff.expense e JOIN 97b3964026cb47ff.member m ON e.link_to_member = m.member_id WHERE e.expense_description = 'Water, Veggie tray, supplies'`

</details>

**Your category:** 

## E14  (q405, card_games)

**Question:** How many Brazilian Portuguese translated sets are inside the Commander block? Hint: Commander block refer to block = 'Commander'; sets refer to code = setCode; Portuguese refer to language = 'Portuguese (Brasil)'

**Correct (gold) result:** `[['7']]` (1 rows)

**System's result:** `[['0']]` (1 rows)

**System's answer text:** There are 0 Brazilian Portuguese (Portuguese (Brasil)) translated sets in the Commander block (count = 0).

<details><summary>SQL (optional)</summary>

Gold: `SELECT COUNT(T1.id) FROM sets AS T1 INNER JOIN set_translations AS T2 ON T1.code = T2.setCode WHERE T2.language = 'Portuguese (Brazil)' AND T1.block = 'Commander'`

System: `SELECT COUNT(st.id) FROM 2bf53571f75f4c79.set_translations st JOIN 2bf53571f75f4c79.sets s ON st.setcode = s.code WHERE st.language = 'Portuguese (Brasil)' AND s.block = 'Commander'`

</details>

**Your category:** 

## E15  (q1028, european_football_2)

**Question:** In Scotland Premier League, which away team won the most during the 2010 season? Hint: Final result should return the Team.team_long_name; Scotland Premier League refers to League.name = 'Scotland Premier League'; away team refers to away_team_api_id; away team that won the most refers to MAX(SUBTRACT(away_team_goal, home_team_goal) > 0); 2010 season refers to season = '2009/2010'; won the most refers to MAX(COUNT(*));

**Correct (gold) result:** `[['Rangers'], ['Celtic']]` (2 rows)

**System's result:** `[['Celtic']]` (1 rows)

**System's answer text:** Celtic was the away team that won the most in the 2009/2010 Scotland Premier League season.

<details><summary>SQL (optional)</summary>

Gold: `SELECT DISTINCT t.team_long_name FROM Team t JOIN Match m ON t.team_api_id = m.away_team_api_id JOIN League l ON m.league_id = l.id WHERE l.name = 'Scotland Premier League' AND m.season = '2009/2010' AND m.away_team_goal > m.home_team_goal GROUP BY t.team_api_id, t.team_long_name HAVING COUNT(*) = ( SELECT MAX(win_count) FROM ( SELECT COUNT(*) AS win_count FROM Match m2 JOIN League l2 ON m2.league_id = l2.id WHERE l2.name = 'Scotland Premier League' AND m2.season = '2009/2010' AND m2.away_team_goal > m2.home_team_goal GROUP BY m2.away_team_api_id ) );`

System: `SELECT t.team_long_name FROM e1b70dcdfda248fc.match m JOIN e1b70dcdfda248fc.league l ON l.id = m.league_id JOIN e1b70dcdfda248fc.team t ON t.team_api_id = m.away_team_api_id WHERE l.name = 'Scotland Premier League' AND m.season = '2009/2010' AND (m.away_team_goal > m.home_team_goal) GROUP BY t.team_long_name ORDER BY COUNT(*) DESC LIMIT 1`

</details>

**Your category:** 

## E16  (q750, superhero)

**Question:** What is the average weight of all female superheroes? Hint: female refers to gender = 'Female'; average weight refers to AVG(weight_kg)

**Correct (gold) result:** `[['78.50694444444444']]` (1 rows)

**System's result:** `[['60.7795698924731183']]` (1 rows)

**System's answer text:** The average weight of all female superheroes is 60.77956989247312 kg.

<details><summary>SQL (optional)</summary>

Gold: `SELECT AVG(weight_kg) FROM superhero s JOIN gender g ON s.gender_id = g.id WHERE g.gender = 'Female' AND weight_kg > 0;`

System: `SELECT AVG(s.weight_kg) AS average_weight_kg FROM b710015657fa447c.superhero AS s JOIN b710015657fa447c.gender AS g ON s.gender_id = g.id WHERE g.gender = 'Female'`

</details>

**Your category:** 

## E17  (q639, codebase_community)

**Question:** Based on posts posted by Community, calculate the percentage of posts that use the R language. Hint: DIVIDE(COUNT(PostId WHERE TagName = 'r')), (COUNT(PostId WHERE DisplayName = 'Community')) as percentage; R language refers to tagname = 'r'

**Correct (gold) result:** `[['0.19569471624266144']]` (1 rows)

**System's result:** `[['None']]` (1 rows)

**System's answer text:** The query returned a single row with the percentage value as null, so the percentage of posts by Community that use the R tag could not be determined from the provided result. Caveat: No matching data — the result is empty (percentage = null). To compute the percentage you need counts of Community posts and counts of Community posts with TagName = 'r'; re-running the query or checking the underlyi

<details><summary>SQL (optional)</summary>

Gold: `SELECT CAST(SUM(IIF(T3.TagName = 'r', 1, 0)) AS REAL) * 100 / COUNT(T1.Id) FROM users AS T1 INNER JOIN postHistory AS T2 ON T1.Id = T2.UserId INNER JOIN tags AS T3 ON T3.ExcerptPostId = T2.PostId WHERE T1.DisplayName = 'Community'`

System: `SELECT (SUM(CASE WHEN p.tags ILIKE '%<r>%' THEN 1 ELSE 0 END)::numeric * 100) / NULLIF(COUNT(*)::numeric, 0) AS percentage FROM f084a0aafeb24880.posts p WHERE p.ownerdisplayname = 'Community'`

</details>

**Your category:** 

## E18  (q829, superhero)

**Question:** Which publisher created more superheroes: DC or Marvel Comics? Find the difference in the number of superheroes. Hint: DC refers to publisher_name = 'DC Comics'; Marvel Comics refers to publisher_name = 'Marvel Comics'; difference = SUBTRACT(SUM(publisher_name = 'DC Comics'), SUM(publisher_name = 'Marvel Comics'));

**Correct (gold) result:** `[['Marvel Comics', '-163']]` (1 rows)

**System's result:** `[['-163']]` (1 rows)

**System's answer text:** Marvel Comics created more superheroes. The difference (DC Comics total minus Marvel Comics total) is -163, i.e., Marvel has 163 more superheroes than DC in this dataset.

<details><summary>SQL (optional)</summary>

Gold: `SELECT CASE WHEN marvel_cnt > dc_cnt THEN 'Marvel Comics' ELSE 'DC Comics' END AS publisher_with_more_superheroes, dc_cnt - marvel_cnt AS difference FROM ( SELECT SUM(CASE WHEN p.`publisher_name`='Marvel Comics' THEN 1 END) AS marvel_cnt, SUM(CASE WHEN p.`publisher_name`='DC Comics' THEN 1 END) AS dc_cnt FROM `superhero` s JOIN `publisher` p ON s.`publisher_id` = p.`id` ) t;`

System: `SELECT SUM((p.publisher_name = 'DC Comics')::int) - SUM((p.publisher_name = 'Marvel Comics')::int) AS difference FROM b710015657fa447c.superhero s JOIN b710015657fa447c.publisher p ON p.id = s.publisher_id`

</details>

**Your category:** 


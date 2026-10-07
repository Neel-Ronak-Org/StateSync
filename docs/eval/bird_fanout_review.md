# Fan-out set: owner review sample (protocol v2 §3.2)

19 of the 93 questions, chosen at random (seed 20261003). The set is frozen only after this review.

## How to review (about 15 minutes)

For each question, check three things. You don't need to read SQL.

1. **Is the question clear?** Read it as a normal user would. Could it reasonably mean something
   else? A good question has one sensible reading.
2. **Does the correct answer count the right thing?** The "What the correct answer does" line says what
   the gold answer counts or averages. Is that what the question asks for?
3. **Is the hint right and enough?** Hints only explain codes, as BIRD's hints do (for example
   `'VYDAJ'` = withdrawal). Is anything missing or misleading?

"Risky" questions are where a careless join counts things more than once: the naive number shows
what that mistake gives. "Control" questions use the same tables, but the simple join is already
right.

Write OK, or what to fix, on each **Verdict** line, then tell me in chat ("all OK", or "F016:
...").

**Optional: check a number yourself.** Paste a gold query into the database. For example, F001:

```bash
docker exec -it statesync-bird-db psql -U statesync_reader -d bird -c "SET search_path=formula_1; SELECT COUNT(DISTINCT driverid) FROM laptimes WHERE milliseconds < 70000"
```

Change `formula_1` to the question's database and paste its gold query.

## F001 - risky - formula_1

**Question:** How many drivers have recorded at least one lap faster than 1 minute 10 seconds?

**Hint:** lap faster than 1 minute 10 seconds refers to milliseconds < 70000

**What the correct answer does:** it counts each driver once (a driver with 50 fast laps still counts as 1).

| Correct (gold) answer | Naive join gives |
|---|---|
| 29 | 896 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(DISTINCT driverid) FROM laptimes WHERE milliseconds < 70000
```
</details>

**Verdict:** 

## F003 - risky - formula_1

**Question:** What is the average birth year of the drivers who recorded lap times in the 2009 season?

**Hint:** birth year refers to the year of dob; 2009 season refers to races.year = 2009

**What the correct answer does:** it averages over drivers, each driver once (not once per lap they drove).

| Correct (gold) answer | Naive join gives |
|---|---|
| 1,981.2 | 1,980.9 |

<details><summary>Gold SQL</summary>

```sql
SELECT AVG(EXTRACT(YEAR FROM dob)) FROM drivers WHERE driverid IN (SELECT l.driverid FROM laptimes l JOIN races r ON r.raceid = l.raceid WHERE r.year = 2009)
```
</details>

**Verdict:** 

## F004 - risky - formula_1

**Question:** How many circuits have hosted a race in which at least one driver retired with an engine failure?

**Hint:** engine failure refers to status = 'Engine'

**What the correct answer does:** it counts each circuit once (not once per engine failure there).

| Correct (gold) answer | Naive join gives |
|---|---|
| 66 | 1,947 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(DISTINCT ra.circuitid) FROM races ra JOIN results r ON r.raceid = ra.raceid JOIN status s ON s.statusid = r.statusid WHERE s.status = 'Engine'
```
</details>

**Verdict:** 

## F007 - risky - formula_1

**Question:** How many races in 2005 had at least one driver who finished more than 5 places ahead of their grid position?

**Hint:** finished more than 5 places ahead of grid refers to grid - position > 5 with position not null

**What the correct answer does:** it counts each race once (not once per driver who gained places).

| Correct (gold) answer | Naive join gives |
|---|---|
| 18 | 63 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(DISTINCT ra.raceid) FROM races ra JOIN results r ON r.raceid = ra.raceid WHERE ra.year = 2005 AND r.position IS NOT NULL AND r.grid - r.position > 5
```
</details>

**Verdict:** 

## F014 - risky - financial

**Question:** How many accounts have at least one withdrawal transaction of more than 50,000?

**Hint:** withdrawal refers to trans.type = 'VYDAJ'; more than 50,000 refers to amount > 50000

**What the correct answer does:** it counts each account once (not once per big withdrawal).

| Correct (gold) answer | Naive join gives |
|---|---|
| 796 | 2,597 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(DISTINCT account_id) FROM trans WHERE type = 'VYDAJ' AND amount > 50000
```
</details>

**Verdict:** 

## F016 - risky - financial

**Question:** Consider the districts where at least one client born after 1980 lives. What is the average number of inhabitants of these districts?

**Hint:** number of inhabitants refers to A4; born after 1980 refers to the year of birth_date > 1980

**What the correct answer does:** it averages over districts, each district once (not once per young client living there).

| Correct (gold) answer | Naive join gives |
|---|---|
| 151,113.88 | 277,040.12 |

<details><summary>Gold SQL</summary>

```sql
SELECT AVG(CAST(a4 AS BIGINT)) FROM district WHERE district_id IN (SELECT district_id FROM client WHERE EXTRACT(YEAR FROM birth_date) > 1980)
```
</details>

**Verdict:** 

## F021 - control - financial

**Question:** What is the total amount of all loans granted to accounts in the Prague region?

**Hint:** Prague region refers to district.A3 = 'Prague'

**What the correct answer does:** it adds up each loan once; joining a loan to its account and district never repeats it.

| Correct (gold) answer | Naive join gives |
|---|---|
| 12,932,412 | 12,932,412 |

<details><summary>Gold SQL</summary>

```sql
SELECT SUM(amount) FROM loan WHERE account_id IN (SELECT a.account_id FROM account a JOIN district d ON d.district_id = a.district_id WHERE d.a3 = 'Prague')
```
</details>

**Verdict:** 

## F039 - control - european_football_2

**Question:** How many attribute records give a player taller than 190 cm an overall rating above 80?

**Hint:** taller than 190 cm refers to height > 190

**What the correct answer does:** it counts attribute records; the question asks for records, so every row counts.

| Correct (gold) answer | Naive join gives |
|---|---|
| 608 | 608 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(*) FROM player_attributes WHERE overall_rating > 80 AND player_api_id IN (SELECT player_api_id FROM player WHERE height > 190)
```
</details>

**Verdict:** 

## F041 - control - european_football_2

**Question:** How many matches of the 2015/2016 season did Real Madrid CF win at home?

**Hint:** won at home refers to home_team_goal > away_team_goal with Real Madrid CF as the home team

**What the correct answer does:** it counts matches; joining a match to its home team never repeats it.

| Correct (gold) answer | Naive join gives |
|---|---|
| 16 | 16 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(*) FROM match WHERE season = '2015/2016' AND home_team_goal > away_team_goal AND home_team_api_id IN (SELECT team_api_id FROM team WHERE team_long_name = 'Real Madrid CF')
```
</details>

**Verdict:** 

## F046 - risky - thrombosis_prediction

**Question:** Among the patients who have had a special examination, how many had at least one laboratory test with an IgG value above 2000?

**Hint:** a special examination refers to a row in examination

**What the correct answer does:** it counts each patient once (not once per examination x lab test combination).

| Correct (gold) answer | Naive join gives |
|---|---|
| 9 | 43 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(DISTINCT p.id) FROM patient p JOIN examination e ON e.id = p.id JOIN laboratory l ON l.id = p.id WHERE l.igg > 2000
```
</details>

**Verdict:** 

## F051 - risky - toxicology

**Question:** How many carcinogenic molecules have at least one double bond?

**Hint:** carcinogenic refers to label = '+'; double bond refers to bond_type = '='

**What the correct answer does:** it counts each molecule once (not once per double bond).

| Correct (gold) answer | Naive join gives |
|---|---|
| 121 | 634 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(DISTINCT m.molecule_id) FROM molecule m JOIN bond b ON b.molecule_id = m.molecule_id WHERE m.label = '+' AND b.bond_type = '='
```
</details>

**Verdict:** 

## F052 - risky - toxicology

**Question:** Among the molecules that contain at least one sodium atom, what percentage are carcinogenic?

**Hint:** sodium refers to element = 'na'; carcinogenic refers to label = '+'; percentage = carcinogenic molecules / molecules * 100

**What the correct answer does:** it a percentage of molecules, each molecule once (not of sodium atoms).

| Correct (gold) answer | Naive join gives |
|---|---|
| 35.29 | 46.88 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(*) FILTER (WHERE label = '+') * 100.0 / COUNT(*) FROM molecule WHERE molecule_id IN (SELECT molecule_id FROM atom WHERE element = 'na')
```
</details>

**Verdict:** 

## F063 - control - student_club

**Question:** How many attendance records are there for games?

**Hint:** game refers to event.type = 'Game'

**What the correct answer does:** it counts attendance records; the question asks for records, so every row counts.

| Correct (gold) answer | Naive join gives |
|---|---|
| 43 | 43 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(*) FROM attendance WHERE link_to_event IN (SELECT event_id FROM event WHERE type = 'Game')
```
</details>

**Verdict:** 

## F066 - risky - superhero

**Question:** How many superheroes have at least one of the powers Flight, Super Strength or Super Speed?

**Hint:** (none)

**What the correct answer does:** it counts each hero once (a hero with Flight and Super Speed counts as 1).

| Correct (gold) answer | Naive join gives |
|---|---|
| 435 | 815 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(DISTINCT hp.hero_id) FROM hero_power hp JOIN superpower sp ON sp.id = hp.power_id WHERE sp.power_name IN ('Flight', 'Super Strength', 'Super Speed')
```
</details>

**Verdict:** 

## F069 - risky - superhero

**Question:** What is the average weight of the superheroes who have at least one attribute valued above 90?

**Hint:** weight refers to weight_kg

**What the correct answer does:** it averages over heroes, each hero once (not once per high attribute).

| Correct (gold) answer | Naive join gives |
|---|---|
| 535,252.66 | 235,804.11 |

<details><summary>Gold SQL</summary>

```sql
SELECT AVG(weight_kg) FROM superhero WHERE id IN (SELECT hero_id FROM hero_attribute WHERE attribute_value > 90)
```
</details>

**Verdict:** 

## F070 - risky - superhero

**Question:** How many publishers have at least one superhero with Telepathy?

**Hint:** (none)

**What the correct answer does:** it counts each publisher once (not once per telepathic hero).

| Correct (gold) answer | Naive join gives |
|---|---|
| 11 | 91 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(DISTINCT s.publisher_id) FROM superhero s JOIN hero_power hp ON hp.hero_id = s.id JOIN superpower sp ON sp.id = hp.power_id WHERE sp.power_name = 'Telepathy'
```
</details>

**Verdict:** 

## F081 - control - codebase_community

**Question:** How many comments were written by users with a reputation above 10,000?

**Hint:** (none)

**What the correct answer does:** it counts comments; joining a comment to its author never repeats it.

| Correct (gold) answer | Naive join gives |
|---|---|
| 57,439 | 57,439 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(*) FROM comments WHERE userid IN (SELECT id FROM users WHERE reputation > 10000)
```
</details>

**Verdict:** 

## F082 - control - codebase_community

**Question:** What is the highest reputation among the users who earned a Teacher badge?

**Hint:** Teacher badge refers to badges.name = 'Teacher'

**What the correct answer does:** it the highest value is the same however many badges a user has.

| Correct (gold) answer | Naive join gives |
|---|---|
| 87,393 | 87,393 |

<details><summary>Gold SQL</summary>

```sql
SELECT MAX(reputation) FROM users WHERE id IN (SELECT userid FROM badges WHERE name = 'Teacher')
```
</details>

**Verdict:** 

## F088 - control - debit_card_specializing

**Question:** How many monthly consumption records above 100,000 belong to SME customers?

**Hint:** SME refers to segment = 'SME'

**What the correct answer does:** it counts monthly records; the question asks for records, so every row counts.

| Correct (gold) answer | Naive join gives |
|---|---|
| 567 | 567 |

<details><summary>Gold SQL</summary>

```sql
SELECT COUNT(*) FROM yearmonth WHERE consumption > 100000 AND customerid IN (SELECT customerid FROM customers WHERE segment = 'SME')
```
</details>

**Verdict:** 


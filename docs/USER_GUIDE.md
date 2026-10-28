# Using StateSync

How the app is organised, how to set it up for a business, where each thing lives afterwards, and how to
change it. For how it works inside, see [ARCHITECTURE.md](ARCHITECTURE.md).

## The idea in one picture

```
Data sources (added once)              Workspace (one per business)               Use
──────────────────────────             ─────────────────────────────              ─────────────
Databases ── a read-only login ──┐     1. Database      (one of them)      ┐
                                 ├──▶  2. Documents     (any folders)      ├──▶  Ask  ──▶  Dashboards
Documents ── folders of files ───┘     3. About the business (your words)  │     (pin an answer)
                                       4. Table and column notes (approved)│
                                       5. Approved answers (thumbs-up)     ┘
```

- A **data source** is something StateSync can read: a Postgres database (through a read-only login) or a
  folder of documents. You add each one once.
- A **workspace** is one business. It uses **one database** and **any number of document folders**, plus what
  you tell StateSync about that business. Every question is asked *in* a workspace and uses exactly these.
- **Ask** is where questions are asked; **Dashboards** keep answers you pinned, re-running them on a schedule.

## Where everything is (the sidebar)

| Sidebar | Holds | You go here to |
|---|---|---|
| **Ask** (the home page) | conversations, one workspace at a time (the *Workspace:* menu at the top right) | ask questions, read answers, open the **Inspector** (the flow icon under an answer) |
| **Dashboards** | dashboards of the chosen workspace | see pinned answers, rename or delete dashboards |
| **Workspaces** | one card per business | set up or change a workspace |
| **Data sources** | **Databases** tab and **Documents** tab | add a database, upload or delete documents, make folders |

Inside a workspace, the tabs are:

| Tab | What it is | Edit it when |
|---|---|---|
| **Overview** | the five parts of the setup, each *Done* or *To do*, with a button to change it | you want to see what this workspace answers from |
| **Business** | a few lines about the business, in your words | the business or its terms change |
| **Knowledge** | one note per table and column, and business definitions; only *Approved* ones are used | a column's meaning is unclear or the database changed |
| **Approved** | questions whose SQL you confirmed with a thumbs-up | you want to remove one |
| **Documents** | which document folders this workspace searches | you add a folder or want fewer documents searched |
| **Schema** | the database's tables, columns and links, read from the database | (read only) |

## Setting up a new business (about 5 minutes)

1. **Data sources → Databases → Add database.** Host, port, database, a **read-only** login, password, SSL.
   StateSync refuses a login that can write anywhere, then asks which schema to use. The password is stored
   encrypted and never shown again. (*How to create a read-only login* on the same tab gives the SQL.)
2. **Data sources → Documents → New folder**, then upload PDFs, text or Markdown files. Each shows
   *Processing*, then *Ready* (parsed, cut into page-sized passages, indexed for search).
3. **Workspaces → New workspace:** a name, the database from step 1, and optionally a few lines about the
   business. It opens on the **Overview**.
4. **Knowledge → Draft with AI.** A model writes one note per table and column from the names and a few sample
   values. Read them, fix any, then **Approve**. Until approved, notes are not used.
5. **Documents tab:** tick the folder from step 2, **Save**.
6. **Ask in this workspace** (top right). Use the Inspector on the first answers to see what it did.

## Changing things later

| You want to | Do this | What happens |
|---|---|---|
| add documents | Data sources → Documents → the folder → upload | searchable as soon as they show *Ready*; every workspace using the folder sees them |
| remove a document | Data sources → Documents → the bin icon | gone from search immediately |
| let a workspace search another folder | Workspace → Documents → tick it → Save | next question uses it |
| correct a column's meaning | Workspace → Knowledge → edit the note → Save changes → Approve | an edited note is not used until approved again |
| the database gained rows (new companies, new years) | nothing | the router's summary of what the data holds is rebuilt every 10 minutes |
| the database gained tables or columns | Workspace → Knowledge → Draft with AI → Approve | the new ones are drafted for you to approve |
| change the business description | Workspace → Business → Save | used from the next question; earlier versions are kept |
| change the database password or host | Data sources → Databases → delete and add again (then a new workspace on it) | the old login is forgotten |
| teach it a correct answer | Ask → thumbs-up on a correct database answer | asked again, it is answered from that SQL and labelled *Verified* |
| stop using a workspace | Workspace → ⋯ → Delete workspace | its notes and approved answers go; the data sources stay |

## What the labels under an answer mean

| Label | Meaning |
|---|---|
| **Database / Documents / Database + documents** | which sources answered (the router's choice) |
| **New query** | the model wrote this SQL for this question; check it under *How I got this* |
| **Verified** | answered with SQL you approved earlier |
| a caution line | the SQL needed a retry, or a figure in the text was not found in the data |
| **Sources** chips | the SQL, and each document page the answer used |
| dotted number | a figure checked against the data; hover to see where it came from |

## When something looks wrong

Open the **Inspector** on that answer. It shows, in order: which source the router chose and with what
probability, what the router saw about the data, the SQL and its safety checks (or the document search rounds
and the re-ranking), and the timings. Most surprises are visible there: a question routed to the wrong
source, a missing table note, or a document that is not in the workspace's folders.
